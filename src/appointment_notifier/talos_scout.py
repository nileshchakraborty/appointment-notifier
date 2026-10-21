from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.error
import urllib.request
from typing import Any

from .config import TalosScoutSettings
from .llm import FallbackLlmClient
from .store import AlertStore

LOGGER = logging.getLogger(__name__)

TALOS_SYSTEM_PROMPT = """You are the Strategic BD & Partnerships Director for Talos Protocol (talosprotocol.com).

ROLE AND CONTEXT:
- The user is the founder and builder of Talos Protocol seeking target customers, prospects, buyers, and integration partners to SELL and PITCH Talos Protocol to.
- NEVER tell the user to contact Talos Protocol, submit a support form, or visit talosprotocol.com to buy from Talos! The user OWNS and SELLS Talos Protocol.
- When the user asks "who can I reach out to as potential customers", "who should I sell Talos Protocol to", or questions about target clients:
  You must identify concrete target customer segments (ICPs) and specific startups from the AI Founders Lounge (e.g. multi-agent platforms, autonomous agent tooling, enterprise AI swarms, Webeloper, AgentMesh, etc.) who NEED Talos Protocol's DIDs, E2EE channels, capability tokens, and A2A task lifecycles.

About Talos Protocol (talosprotocol.com):
Talos Protocol is the contract-driven trust and execution layer for autonomous AI agents and swarms:
- Cryptographic Identity (DIDs): Verifiable self-sovereign agent identifiers (`did:talos:agent:...`) using Ed25519 and X25519 keypairs.
- Secure Agent-to-Agent (A2A) Messaging: Forward-secret, end-to-end encrypted (E2EE) channels with Double Ratchet sessions.
- Granular Authorization: Capability-based invocation tokens (UCAN / Macaroons) enforcing deterministic permission scopes, spending limits, and tool whitelists.
- Verifiable Audit: Tamper-evident execution logging with BLAKE3 Merkle audit trees and cryptographic receipts.
- A2A v1 Task Execution Primitives:
  * Task Immediate: Synchronous low-latency dispatch (`tasks.send` immediate mode) for atomic tool invocations and queries.
  * Task Wait: Asynchronous barrier tracking (`tasks.wait`) for multi-step agent orchestrations and swarm consensus.
  * Task Get: Non-blocking state polling and artifact inspection (`tasks.get`).
  * A2A Task Stream: Real-time bidirectional streaming of task events, status, and multimodal token chunks (`tasks.stream`).

Target Customer Segments (ICPs):
1. Autonomous Agent Frameworks & Swarms (e.g. multi-agent orchestrators, agent toolkits, swarm platforms needing secure inter-agent communication).
2. Enterprise Automation & B2B AI Agents (e.g. sales, customer support, and code generation agents requiring fine-grained authorization and audit trails).
3. Financial, Crypto, and RegTech AI Agents (requiring cryptographic non-repudiation, Merkle audit proofs, and E2EE).

RESPONSE GUIDELINES:
- Output your solution DIRECTLY, CONCISELY, and AUTHORITATIVELY.
- NEVER output internal monologue, reasoning scratchpad, meta-thinking, or say you don't know what talosprotocol.com does. You represent Talos Protocol directly.
- Recommend specific startups from the indexed database and concrete ICP segments to sell to.
"""

EXTRACTION_PROMPT = """You are an AI investment scout and tech analyst.
Analyze the following WhatsApp chat messages from "AI Founders Lounge".
Identify any founders introducing themselves, launching a product, or describing their startup or tool.

For each startup/product detected, extract a JSON array of objects with the exact schema:
[
  {
    "startup_name": "Name of the startup or project",
    "founder_name": "Name of the founder if known, or null",
    "founder_handle": "WhatsApp handle, username, or phone if visible, or null",
    "pitch": "Concise 1-2 sentence description of what they are building",
    "tech_stack": "Mentioned tools, models, frameworks, or null",
    "talos_synergy_score": 1 to 5 integer (5 = direct fit like agent frameworks, swarms, security, identity, messaging, streaming; 1 = unrelated B2C app),
    "synergy_rationale": "Why this startup is or isn't relevant to Talos Protocol (agent trust, DIDs, E2EE, capability tokens, Task Immediate, Task Wait, Task Get, A2A Task Stream)",
    "outreach_hook": "A 1-2 sentence message Nilesh can send to the founder proposing a Talos integration",
    "links": "Any mentioned website, GitHub, demo, or Twitter links, or null"
  }
]

If no startups or founder intros are found, return an empty array: []
Output ONLY valid JSON, with no markdown code blocks or surrounding text.

Messages to analyze:
"""


def _normalize_lan_candidates(url: str) -> list[str]:
    candidates = [url.rstrip("/")]
    if "fedora.local" in url:
        candidates.append(url.replace("fedora.local", "192.168.1.250").rstrip("/"))
    return candidates


class TalosScoutService:
    def __init__(
        self,
        store: AlertStore,
        settings: TalosScoutSettings,
        llm_client: FallbackLlmClient,
    ) -> None:
        self.store = store
        self.settings = settings
        self.llm_client = llm_client
        self._embedding_candidates = _normalize_lan_candidates(settings.embedding_url)
        self._qdrant_candidates = _normalize_lan_candidates(settings.qdrant_url)

    @property
    def enabled(self) -> bool:
        return self.settings.enabled and self.llm_client.enabled

    def compute_embedding(self, text: str) -> list[float] | None:
        clean_text = text.strip()
        if not clean_text:
            return None

        body = json.dumps(
            {"input": clean_text, "model": self.settings.embedding_model}
        ).encode("utf-8")

        for base_url in self._embedding_candidates:
            url = f"{base_url}/v1/embeddings"
            req = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    emb = data["data"][0]["embedding"]
                    if isinstance(emb, list) and emb:
                        return emb
            except Exception as exc:
                LOGGER.debug("Embedding request to %s failed: %s", url, exc)
        return None

    def upsert_startup_vector(self, startup_id: int, item: dict[str, Any]) -> bool:
        name = str(item.get("startup_name", "")).strip()
        if not name:
            return False

        pitch = str(item.get("pitch", "")).strip()
        rationale = str(item.get("synergy_rationale", "")).strip()
        founder = str(item.get("founder_name", "") or "").strip()
        tech = str(item.get("tech_stack", "") or "").strip()
        embed_text = f"{name}: {pitch}. Relevance to Talos Protocol: {rationale}. Founder: {founder}. Tech: {tech}".strip()

        vector = self.compute_embedding(embed_text)
        if not vector:
            return False

        point_id = startup_id if startup_id > 0 else (abs(hash(name)) % (10**9) + 1)
        point = {
            "id": point_id,
            "vector": vector,
            "payload": {
                "startup_id": startup_id,
                "startup_name": name,
                "founder_name": item.get("founder_name"),
                "founder_handle": item.get("founder_handle"),
                "pitch": pitch,
                "tech_stack": item.get("tech_stack"),
                "talos_synergy_score": item.get("talos_synergy_score", 1),
                "synergy_rationale": rationale,
                "outreach_hook": item.get("outreach_hook"),
                "links": item.get("links"),
            },
        }

        body = json.dumps({"points": [point]}).encode("utf-8")
        for base_url in self._qdrant_candidates:
            url = f"{base_url}/collections/{self.settings.qdrant_collection}/points?wait=true"
            req = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="PUT",
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    if data.get("status") == "ok":
                        LOGGER.info("Indexed startup '%s' into Qdrant (%s)", name, base_url)
                        return True
            except Exception as exc:
                LOGGER.debug("Qdrant upsert to %s failed: %s", url, exc)
        return False

    def search_vector_startups(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        vector = self.compute_embedding(query)
        if not vector:
            return []

        body = json.dumps({
            "vector": vector,
            "limit": limit,
            "with_payload": True,
        }).encode("utf-8")

        for base_url in self._qdrant_candidates:
            url = f"{base_url}/collections/{self.settings.qdrant_collection}/points/search"
            req = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    results = data.get("result", [])
                    matched: list[dict[str, Any]] = []
                    for hit in results:
                        payload = hit.get("payload") or {}
                        score = hit.get("score", 0.0)
                        matched.append({**payload, "similarity_score": score})
                    return matched
            except Exception as exc:
                LOGGER.debug("Qdrant search to %s failed: %s", url, exc)
        return []

    def sync_all_startups_to_qdrant(self) -> int:
        startups = self.store.get_top_startups(limit=200)
        synced = 0
        for s in startups:
            s_id = int(s.get("id") or 0)
            if self.upsert_startup_vector(s_id, s):
                synced += 1
        LOGGER.info("Synchronized %d/%d startups to Qdrant", synced, len(startups))
        return synced

    async def answer(self, chat_id: str, question: str) -> str:
        if not self.enabled:
            return "Talos Scout is not configured or LLM is offline."

        # 1. Semantic search in Qdrant Vector Store
        vector_matches = await asyncio.to_thread(
            self.search_vector_startups,
            question,
            self.settings.vector_search_limit,
        )

        # 2. Broad SQLite records
        top_startups = self.store.get_top_startups(limit=10)
        recent_msgs = self.store.get_unprocessed_whatsapp_messages(
            chat_name=self.settings.founders_chat_name,
            limit=10,
        )

        rag_sections: list[str] = []
        if vector_matches:
            matches_summary = "\n".join(
                f"- **{m.get('startup_name')}** (Founder: {m.get('founder_name') or 'Unknown'} | {m.get('founder_handle') or 'N/A'})\n"
                f"  Pitch: {m.get('pitch')}\n"
                f"  Synergy Score: {m.get('talos_synergy_score')}/5 (Cosine Similarity: {m.get('similarity_score', 0):.2f})\n"
                f"  Rationale: {m.get('synergy_rationale')}\n"
                f"  Outreach Hook: {m.get('outreach_hook') or 'N/A'}\n"
                f"  Links: {m.get('links') or 'None'}"
                for m in vector_matches
            )
            rag_sections.append(f"### Relevant Startups (Semantic Vector Search via Qdrant):\n{matches_summary}")

        if top_startups:
            startups_summary = "\n".join(
                f"- **{s.get('startup_name')}** (Founder: {s.get('founder_name') or 'Unknown'} | {s.get('founder_handle') or 'N/A'})\n"
                f"  Pitch: {s.get('pitch')}\n"
                f"  Synergy Score: {s.get('talos_synergy_score')}/5\n"
                f"  Rationale: {s.get('synergy_rationale')}\n"
                f"  Links: {s.get('links') or 'None'}"
                for s in top_startups
            )
            rag_sections.append(f"### Top Indexed Startups from AI Founders Lounge:\n{startups_summary}")

        if recent_msgs:
            recent_text = "\n".join(
                f"{m.get('sender_name') or 'Anonymous'}: {m.get('text')}"
                for m in recent_msgs[-10:]
            )
            rag_sections.append(f"### Recent Chat Snippets:\n{recent_text}")

        context_text = "\n\n".join(rag_sections) + "\n\n" if rag_sections else "No startups indexed yet.\n\n"

        messages = [
            {"role": "system", "content": TALOS_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"{context_text}User Question: {question}",
            },
        ]

        try:
            result = await asyncio.to_thread(
                self.llm_client.complete,
                messages,
                max_tokens=1500,
                temperature=0.3,
            )
            raw = result.content
            cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
            cleaned = re.sub(r"<think>.*$", "", cleaned, flags=re.DOTALL).strip()
            return cleaned or raw
        except Exception as exc:
            LOGGER.exception("Talos Scout LLM query failed")
            return f"Talos Scout query failed: {exc}"

    async def execute_a2a_task(
        self,
        task: Any,
        emit_status: Any,
        is_cancelled: Any,
    ) -> tuple[str, list[Any]]:
        from .a2a.models import Artifact

        question = task.history[-1].get_text() if task.history else ""
        if not question:
            return "No question provided in task", []

        if is_cancelled():
            return "Task was canceled", []

        await emit_status("🔍 Searching Qdrant vector store (Cosine similarity)...")
        vector_matches = await asyncio.to_thread(
            self.search_vector_startups,
            question,
            self.settings.vector_search_limit,
        )

        if is_cancelled():
            return "Task was canceled", []

        top_startups = self.store.get_top_startups(limit=10)
        recent_msgs = self.store.get_unprocessed_whatsapp_messages(
            chat_name=self.settings.founders_chat_name,
            limit=10,
        )

        artifacts = []
        if vector_matches:
            artifacts.append(
                Artifact(
                    artifact_id=f"vector_matches_{task.id[:8]}",
                    name="Qdrant Vector Matches",
                    mime_type="application/json",
                    data=vector_matches,
                )
            )

        rag_sections: list[str] = []
        if vector_matches:
            matches_summary = "\n".join(
                f"- **{m.get('startup_name')}** (Founder: {m.get('founder_name') or 'Unknown'} | {m.get('founder_handle') or 'N/A'})\n"
                f"  Pitch: {m.get('pitch')}\n"
                f"  Synergy Score: {m.get('talos_synergy_score')}/5 (Cosine Similarity: {m.get('similarity_score', 0):.2f})\n"
                f"  Rationale: {m.get('synergy_rationale')}\n"
                f"  Outreach Hook: {m.get('outreach_hook') or 'N/A'}\n"
                f"  Links: {m.get('links') or 'None'}"
                for m in vector_matches
            )
            rag_sections.append(f"### Relevant Startups (Semantic Vector Search via Qdrant):\n{matches_summary}")

        if top_startups:
            startups_summary = "\n".join(
                f"- **{s.get('startup_name')}** (Founder: {s.get('founder_name') or 'Unknown'} | {s.get('founder_handle') or 'N/A'})\n"
                f"  Pitch: {s.get('pitch')}\n"
                f"  Synergy Score: {s.get('talos_synergy_score')}/5\n"
                f"  Rationale: {s.get('synergy_rationale')}\n"
                f"  Links: {s.get('links') or 'None'}"
                for s in top_startups
            )
            rag_sections.append(f"### Top Indexed Startups from AI Founders Lounge:\n{startups_summary}")

        if recent_msgs:
            recent_text = "\n".join(
                f"{m.get('sender_name') or 'Anonymous'}: {m.get('text')}"
                for m in recent_msgs[-10:]
            )
            rag_sections.append(f"### Recent Chat Snippets:\n{recent_text}")

        context_text = "\n\n".join(rag_sections) + "\n\n" if rag_sections else "No startups indexed yet.\n\n"

        messages = [
            {"role": "system", "content": TALOS_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"{context_text}User Question: {question}",
            },
        ]

        await emit_status("🧠 Synthesizing recommendation with DeepSeek-7B on Fedora...")
        result = await asyncio.to_thread(
            self.llm_client.complete,
            messages,
            max_tokens=1500,
            temperature=0.3,
        )
        raw = result.content
        cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        cleaned = re.sub(r"<think>.*$", "", cleaned, flags=re.DOTALL).strip()
        return cleaned or raw, artifacts

    async def extract_startups_from_batch(self, messages: list[dict[str, Any]]) -> int:
        if not messages or not self.llm_client.enabled:
            return 0

        formatted = "\n---\n".join(
            f"ID: {m['msg_id']}\nAuthor: {m.get('sender_name') or 'Unknown'} ({m.get('sender_id') or 'N/A'})\nText: {m.get('text', '')}"
            for m in messages
        )

        prompt_messages = [
            {"role": "system", "content": "You are a precise JSON extractor. Output valid JSON array only."},
            {"role": "user", "content": EXTRACTION_PROMPT + formatted},
        ]

        try:
            response = await asyncio.to_thread(
                self.llm_client.complete,
                prompt_messages,
                max_tokens=1500,
                temperature=0.1,
            )
            raw = response.content.strip()
            # Strip reasoning model <think>...</think> if present
            raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

            # Extract JSON array using regex or fallback to fence stripping
            match = re.search(r"\[\s*\{.*\}\s*\]", raw, re.DOTALL)
            if match:
                raw_json = match.group(0)
            else:
                raw_json = re.sub(r"^```(?:json)?\s*", "", raw)
                raw_json = re.sub(r"\s*```$", "", raw_json).strip()

            extracted = json.loads(raw_json)
            if not isinstance(extracted, list):
                return 0

            count = 0
            for item in extracted:
                if not isinstance(item, dict) or not item.get("startup_name"):
                    continue
                score = int(item.get("talos_synergy_score") or 1)
                score = max(1, min(5, score))
                s_id = self.store.record_startup(
                    startup_name=str(item.get("startup_name")),
                    founder_name=item.get("founder_name"),
                    founder_handle=item.get("founder_handle"),
                    pitch=str(item.get("pitch", "")),
                    tech_stack=item.get("tech_stack"),
                    talos_synergy_score=score,
                    synergy_rationale=item.get("synergy_rationale"),
                    outreach_hook=item.get("outreach_hook"),
                    links=item.get("links"),
                    raw_message_id=messages[0].get("msg_id"),
                )
                # Also upsert into Qdrant Vector Store
                item["talos_synergy_score"] = score
                self.upsert_startup_vector(s_id, item)
                count += 1

            self.store.mark_whatsapp_messages_processed([m["msg_id"] for m in messages])
            LOGGER.info("Extracted %s startups from %s messages", count, len(messages))
            return count
        except Exception as exc:
            LOGGER.warning("Startup extraction failed: %s", exc)
            return 0

    def format_startups_list(self, filter_term: str | None = None, limit: int = 5) -> str:
        startups = self.store.get_top_startups(limit=limit, filter_term=filter_term)
        if not startups:
            msg = "No startups indexed yet from AI Founders Lounge."
            if filter_term:
                msg += f" (Filter: '{filter_term}')"
            return msg

        lines = [f"🚀 **Top Discovered Startups for Talos Protocol** ({len(startups)} shown):"]
        for i, s in enumerate(startups, 1):
            stars = "⭐" * int(s.get("talos_synergy_score") or 1)
            lines.append(
                f"\n**{i}. {s.get('startup_name')}** ({stars})\n"
                f"• **Founder:** {s.get('founder_name') or 'Unknown'} ({s.get('founder_handle') or 'N/A'})\n"
                f"• **Pitch:** {s.get('pitch')}\n"
                f"• **Talos Synergy:** {s.get('synergy_rationale') or 'N/A'}\n"
                f"• **Hook:** {s.get('outreach_hook') or 'N/A'}"
            )
            if s.get("links"):
                lines.append(f"• **Links:** {s.get('links')}")

        lines.append("\n💡 *Ask questions with `/talos <question>` (e.g. `/talos draft outreach to #1`)*")
        return "\n".join(lines)
