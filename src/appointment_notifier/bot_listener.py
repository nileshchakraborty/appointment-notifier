from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import urllib.parse
import urllib.request
import uuid
from typing import Any

from .a2a import (
    A2AJsonRpcDispatcher,
    A2ATaskEngine,
    Message,
    Task,
    TaskState,
    get_agent_card,
)
from .config import TelegramAlertSettings
from .chat import OllamaChatService
from .store import AlertStore
from .trend import TrendService

LOGGER = logging.getLogger(__name__)

TALOS_ROUTING_PATTERN = re.compile(
    r"(?:talos|talosprotocol|startup|startups|founder|founders|customer|customers|prospect|prospects|pitch|outreach|partnership|partnerships|sell|selling|buyer|buyers|\bagents?\b|\bswarms?\b|lounge|a2a|\btasks?\b|\bstream\b|\bimmediate\b|\bwait\b|\bget\b|protocol)",
    re.IGNORECASE,
)

HELP_TEXT = (
    "Commands:\n"
    "/start - show this command list\n"
    "/help [visa|talos] - show command list or topic help\n"
    "/current - show whether the latest known state is available\n"
    "/last - show previous availability timestamp\n"
    "/status - show watcher and system status\n"
    "/trend - summarize posting history and next likely window\n"
    "/ask <question> - chat with the visa appointment assistant\n"
    "/forget - erase your saved conversation history\n"
    "/whoami - show your Telegram identifiers\n"
    "/users - list allowed users (owner only)\n"
    "/allow <chat_id|@username> [owner] - add a user (owner only)\n"
    "/revoke <chat_id|@username> - remove a non-owner user (owner only)\n\n"
    "Talos & Founders Scout:\n"
    "/talos <question> - ask about startups, outreach, or Talos Protocol visibility\n"
    "/campaign <focus> - generate a durable outreach campaign using Temporal swarm on Fedora\n"
    "/startups [filter] - list discovered startups from AI Founders Lounge\n"
    "/sync - re-sync and scan recent WhatsApp messages from AI Founders Lounge\n\n"
    "A2A v1 Task Lifecycle:\n"
    "/tasks (or /task list) - view recent A2A tasks and state\n"
    "/task get <id> - inspect task status, messages, and artifacts\n"
    "/task wait <id> - wait for a running task to complete\n"
    "/task cancel <id> - cancel a running task\n"
    "/agentcard - inspect Talos Scout canonical A2A Agent Card\n"
    "/a2a - view A2A task lifecycle architecture overview\n"
    "/rpc <json> - run a raw A2A JSON-RPC 2.0 command"
)

HELP_TALOS = (
    "🚀 **Talos Protocol & Founder Scout Commands:**\n\n"
    "• `/talos <question>` — Ask the strategic scout questions grounded in AI Founders Lounge chat history.\n"
    "• `/campaign <focus topic>` — Run a multi-step Temporal swarm workflow to match startups and synthesize tailored outreach strategies.\n"
    "  *Examples:*\n"
    "  - `/campaign autonomous agent security`\n"
    "  - `/campaign decentralized workflows`\n"
    "  *Examples:*\n"
    "  - `/talos which startup should I reach out to to get more visibility for talos protocol?`\n"
    "  - `/talos who is building multi-agent frameworks or swarms?`\n"
    "  - `/talos draft a message proposing a Talos integration to the founder of [Startup]`\n"
    "• `/startups [optional search]` — View top startups extracted from the chat, ranked by Talos synergy score (1-5 stars).\n"
    "• `/sync` — Force an immediate poll & extraction cycle of WhatsApp messages.\n"
    "• `/tasks` — View recent A2A background tasks and their states.\n"
    "• `/task get <task_id>` — Inspect task artifacts (e.g. Qdrant vector match sets) and outputs.\n"
    "• `/task cancel <task_id>` — Cancel a running A2A task.\n"
    "• `/agentcard` — View Talos Scout v1 canonical Agent Card.\n"
    "• `/a2a` — View the Talos A2A v1 task execution specification.\n"
)

HELP_VISA = (
    "📌 **Visa & Appointment Commands:**\n\n"
    "• `/current` — Check if visa appointment slots are currently open.\n"
    "• `/last` — See when slots were last open.\n"
    "• `/trend` — Statistical forecast and historical release windows.\n"
    "• `/ask <question>` — Ask questions about slots, bulk releases, OFC vs Consular, etc.\n"
)

A2A_SPEC_TEXT = (
    "⚡ **Talos Protocol — A2A v1 Task Execution & Trust Architecture**\n\n"
    "Talos implements the official Agent-to-Agent (A2A) v1 specification coupled with Double Ratchet E2EE messaging, cryptographic agent DIDs, and Merkle audit trees.\n\n"
    "### 🔄 Core A2A Task Primitives:\n\n"
    "1. **Task Immediate (`SendMessage` / `tasks.send` immediate mode)**\n"
    "   • **Mode:** Synchronous low-latency dispatch (`returnImmediately: false`).\n"
    "   • **Use Case:** Atomic tool invocations, quick queries, deterministic RPCs.\n"
    "   • **Flow:** Client sends task request, gateway executes immediately and returns completed state + outputs in the response.\n\n"
    "2. **Task Wait (`SubscribeToTask` / `tasks.wait`)**\n"
    "   • **Mode:** Asynchronous barrier synchronization & long-poll wait.\n"
    "   • **Use Case:** Multi-step agent orchestrations, swarm consensus, long background jobs.\n"
    "   • **Flow:** Client submits task with `returnImmediately: true`, receives `taskId`, and issues `SubscribeToTask` to await terminal state.\n\n"
    "3. **Task Get (`GetTask` / `tasks.get`)**\n"
    "   • **Mode:** Non-blocking state polling & artifact inspection.\n"
    "   • **Use Case:** Inspect task lifecycle status (`TASK_STATE_WORKING`, `TASK_STATE_COMPLETED`), output messages, error codes, and download generated artifacts.\n\n"
    "4. **A2A Task Stream (`SendStreamingMessage` / `tasks.stream`)**\n"
    "   • **Mode:** Real-time bidirectional SSE / WebSocket event stream.\n"
    "   • **Use Case:** Token-by-token streaming, live intermediate reasoning traces (`<think>`), and real-time status updates across agent boundaries.\n\n"
    "💡 *Ask `/talos <question>` (e.g. `/talos how can AlphaForge use Task Stream?`) to see how startups can integrate these primitives!*"
)


class TelegramBotCommandListener:
    def __init__(
        self,
        settings: TelegramAlertSettings,
        store: AlertStore,
        trend_service: TrendService | None = None,
        chat_service: OllamaChatService | None = None,
        talos_scout: Any | None = None,
        whatsapp_watcher: Any | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.allowed_chat_ids = {str(chat_id) for chat_id in settings.chat_ids}
        self.owner_chat_ids = {str(chat_id) for chat_id in settings.owner_chat_ids}
        self.allowed_usernames = {
            normalized
            for username in settings.allowed_usernames
            if (normalized := self._normalize_username(username))
        }
        self.owner_usernames = {
            normalized
            for username in settings.owner_usernames
            if (normalized := self._normalize_username(username))
        }
        self.base_url = f"https://api.telegram.org/bot{settings.bot_token}"
        self.trend_service = trend_service
        self.chat_service = chat_service
        self.talos_scout = talos_scout
        self.whatsapp_watcher = whatsapp_watcher
        self._seed_bot_users()

        self.a2a_engine = A2ATaskEngine(self.store.a2a_task_store)
        self.a2a_rpc = A2AJsonRpcDispatcher(self.a2a_engine, default_handler=self._default_a2a_handler)

    async def run(self) -> None:
        if (
            not self.settings.enabled
            or not self.settings.bot_token
            or self.store.bot_user_count() == 0
        ):
            LOGGER.info("Telegram bot command listener disabled")
            return

        LOGGER.info("Telegram bot command listener enabled")
        await self._initialize_offset()
        while True:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("Telegram bot command poll failed")
            await asyncio.sleep(10)

    async def _initialize_offset(self) -> None:
        if self.store.get_state("telegram_bot_update_offset"):
            return
        updates = await asyncio.to_thread(
            self._api_json,
            "getUpdates",
            {"offset": "-1", "timeout": "0", "allowed_updates": '["message"]'},
        )
        latest = updates.get("result", [])
        if latest:
            update_id = int(latest[-1]["update_id"])
            self.store.set_state("telegram_bot_update_offset", str(update_id + 1))
            LOGGER.info("Initialized Telegram bot command offset at %s", update_id + 1)

    async def _poll_once(self) -> None:
        offset = self.store.get_state("telegram_bot_update_offset")
        params = {"timeout": "30", "allowed_updates": '["message"]'}
        if offset:
            params["offset"] = offset
        updates = await asyncio.to_thread(self._api_json, "getUpdates", params)
        for update in updates.get("result", []):
            update_id = int(update["update_id"])
            self.store.set_state("telegram_bot_update_offset", str(update_id + 1))
            await self._handle_update(update)

    async def _handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        text = (message.get("text") or "").strip()

        if not self._is_authorized(message, chat_id):
            username = self._first_username(message)
            LOGGER.warning(
                "Ignoring Telegram bot command from unauthorized chat id %s username %s",
                chat_id,
                username or "<none>",
            )
            return
        if not text:
            voice = message.get("voice") or message.get("audio")
            if voice and getattr(self.settings, "whisper_enabled", True):
                await self._handle_voice_update(chat_id, voice)
            return

        parts = text.split()
        command = parts[0].split("@")[0].lower()
        subquery = text.partition(" ")[2].strip()

        if command in {"/start", "/help"}:
            sub_lower = subquery.lower()
            if sub_lower in {"talos", "scout", "startup", "startups"}:
                response = HELP_TALOS
            elif sub_lower in {"visa", "slot", "slots", "appointment"}:
                response = HELP_VISA
            else:
                response = HELP_TEXT
        elif command in {"/talos", "/scout"}:
            await self._dispatch_a2a_streaming(chat_id, subquery, domain="talos")
            return
        elif command in {"/a2a", "/protocol"}:
            response = A2A_SPEC_TEXT
        elif command == "/agentcard":
            response = self._agentcard_command()
        elif command in {"/task", "/tasks"}:
            response = await self._task_command(chat_id, subquery)
        elif command == "/rpc":
            response = await self._rpc_command(subquery)
        elif command in {"/startups", "/founders"}:
            response = self._startups_list(subquery)
        elif command == "/sync":
            response = await self._sync_command(subquery)
        elif command in {"/campaign", "/outreach"}:
            await self._campaign_command(chat_id, subquery)
            return
        elif command == "/status":
            response = await self._format_status()
        elif command == "/trend":
            if self.trend_service is None:
                response = "Trend analysis is not configured."
            else:
                response = await self.trend_service.summarize_async()
        elif command == "/ask":
            if TALOS_ROUTING_PATTERN.search(subquery):
                await self._dispatch_a2a_streaming(chat_id, subquery, domain="talos", disambiguated=True)
            else:
                await self._dispatch_a2a_streaming(chat_id, subquery, domain="visa")
            return
        elif command == "/forget":
            removed = self.store.clear_chat_history(chat_id)
            response = f"Forgot {removed} saved conversation messages."
        elif command == "/whoami":
            response = self._format_identity(message, chat_id)
        elif command == "/current":
            response = self._format_current()
        elif command == "/last":
            response = self._format_last()
        elif command == "/users":
            response = self._owner_command(message, chat_id, self._format_users)
        elif command == "/allow":
            response = self._owner_command(message, chat_id, lambda: self._allow_user(text))
        elif command == "/revoke":
            response = self._owner_command(message, chat_id, lambda: self._revoke_user(text))
        elif text.startswith("/"):
            response = (
                "Unknown command.\n\n"
                "📌 Visa: /current, /last, /trend, /ask\n"
                "🚀 Talos: /talos <question>, /startups, /sync\n"
                "⚡ A2A: /tasks, /task get <id>, /task cancel <id>, /agentcard, /a2a\n"
                "⚙️ General: /status, /whoami, /forget, /help"
            )
        else:
            if TALOS_ROUTING_PATTERN.search(text):
                await self._dispatch_a2a_streaming(chat_id, text, domain="talos")
                return
            else:
                response = await self._smart_chat(chat_id, text)

        await asyncio.to_thread(
            self._api_json,
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": response[:4096],
                "disable_web_page_preview": "true",
            },
        )

    def _edit_message_text(self, chat_id: str, message_id: int, text: str) -> bool:
        try:
            self._api_json(
                "editMessageText",
                {
                    "chat_id": str(chat_id),
                    "message_id": str(message_id),
                    "text": text[:4096],
                    "disable_web_page_preview": "true",
                },
            )
            return True
        except Exception as exc:
            err_str = str(exc).lower()
            if "message is not modified" not in err_str:
                LOGGER.debug("Telegram editMessageText failed: %s", exc)
            return False

    def _edit_or_send_long_text(self, chat_id: str, message_id: int, text: str) -> None:
        if len(text) <= 4096:
            self._edit_message_text(chat_id, message_id, text)
        else:
            first_chunk = text[:4000] + "\n... (continued below)"
            self._edit_message_text(chat_id, message_id, first_chunk)
            remainder = text[4000:]
            while remainder:
                chunk = remainder[:4000]
                remainder = remainder[4000:]
                self._api_json(
                    "sendMessage",
                    {"chat_id": str(chat_id), "text": chunk, "disable_web_page_preview": "true"},
                )

    async def _handle_voice_update(self, chat_id: str, voice: dict[str, Any]) -> None:
        file_id = voice.get("file_id")
        if not file_id:
            return

        initial = await asyncio.to_thread(
            self._api_json,
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": "🎤 *Transcribing voice note...*",
                "parse_mode": "Markdown",
            },
        )
        msg_id = initial.get("result", {}).get("message_id")

        download_res = await asyncio.to_thread(self._download_telegram_file, file_id)
        if not download_res:
            if msg_id:
                self._edit_message_text(chat_id, msg_id, "⚠️ Failed to download voice note from Telegram.")
            return

        audio_bytes, file_name = download_res
        transcript = await asyncio.to_thread(self._transcribe_audio, audio_bytes, file_name)
        if not transcript:
            if msg_id:
                self._edit_message_text(chat_id, msg_id, "⚠️ Could not transcribe audio (no speech detected).")
            return

        LOGGER.info("Transcribed voice note from chat %s: %s", chat_id, transcript)
        if msg_id:
            self._edit_message_text(chat_id, msg_id, f"🎙️ *Heard:*\n\"{transcript}\"\n\n⏳ *Processing...*")

        if TALOS_ROUTING_PATTERN.search(transcript):
            await self._dispatch_a2a_streaming(chat_id, transcript, domain="talos")
        else:
            await self._dispatch_a2a_streaming(chat_id, transcript, domain="visa")

    def _download_telegram_file(self, file_id: str) -> tuple[bytes, str] | None:
        try:
            res = self._api_json("getFile", {"file_id": file_id})
            file_path = res.get("result", {}).get("file_path")
            if not file_path:
                return None
            file_url = f"https://api.telegram.org/file/bot{self.settings.bot_token}/{file_path}"
            req = urllib.request.Request(file_url, headers={"User-Agent": "AppointmentNotifierBot/1.0"})
            with urllib.request.urlopen(req, timeout=30) as response:
                audio_bytes = response.read()
                filename = file_path.split("/")[-1] or "voice.oga"
                return audio_bytes, filename
        except Exception as exc:
            LOGGER.exception("Failed to download Telegram file %s: %s", file_id, exc)
            return None

    def _transcribe_audio(self, audio_bytes: bytes, filename: str) -> str:
        boundary = "----WebKitFormBoundary" + uuid.uuid4().hex
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: audio/ogg\r\n\r\n"
        ).encode("utf-8") + audio_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")

        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        }

        urls = [self.settings.whisper_url]
        if "fedora.local" in self.settings.whisper_url:
            urls.append(self.settings.whisper_url.replace("fedora.local", "192.168.1.250"))
        elif "192.168.1.250" in self.settings.whisper_url:
            urls.append(self.settings.whisper_url.replace("192.168.1.250", "fedora.local"))

        for base_url in urls:
            url = f"{base_url.rstrip('/')}/v1/audio/transcriptions"
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    return (data.get("text") or "").strip()
            except Exception as exc:
                LOGGER.debug("Whisper transcription attempt to %s failed: %s", url, exc)
                continue
        return ""

    async def _campaign_command(self, chat_id: str, focus: str) -> None:
        if not focus:
            await asyncio.to_thread(
                self._api_json,
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": (
                        "Usage: `/campaign <target focus or sector>`\n\n"
                        "*Examples:*\n"
                        "• `/campaign autonomous agent governance`\n"
                        "• `/campaign multi-agent workflows`\n"
                        "• `/campaign decentralized developer tools`"
                    ),
                    "parse_mode": "Markdown",
                },
            )
            return

        initial = await asyncio.to_thread(
            self._api_json,
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": f"⏳ *[Cluster Swarm Dispatched]*\nTarget Focus: *\"{focus}\"*\n\nInitiating Temporal swarm orchestration on Fedora cluster...",
                "parse_mode": "Markdown",
            },
        )
        msg_id = initial.get("result", {}).get("message_id")

        try:
            workflow_id = f"campaign-{uuid.uuid4().hex[:8]}"
            if msg_id:
                self._edit_message_text(
                    chat_id,
                    msg_id,
                    f"⏳ *[Workflow: `{workflow_id}`]*\n"
                    f"• Focus: *\"{focus}\"*\n"
                    f"• Step 1/2: Searching Qdrant vector database (`talos_startups`)...\n"
                    f"• Cluster Queue: `a2a-task-queue`"
                )

            result = await self._execute_cluster_campaign(workflow_id, focus)

            output = result.get("output", "")
            startups = result.get("startups", [])
            startup_count = len(startups)

            header = (
                f"🚀 *Talos Strategic Outreach Campaign*\n"
                f"• **Workflow ID:** `{workflow_id}`\n"
                f"• **Target Focus:** `{focus}`\n"
                f"• **Matched Startups:** {startup_count}\n\n"
            )
            full_response = header + output

            if msg_id:
                self._edit_or_send_long_text(chat_id, msg_id, full_response)
            else:
                await asyncio.to_thread(
                    self._api_json,
                    "sendMessage",
                    {"chat_id": chat_id, "text": full_response[:4096]},
                )

        except Exception as exc:
            LOGGER.exception("Cluster outreach campaign failed: %s", exc)
            if msg_id:
                self._edit_message_text(chat_id, msg_id, f"⚠️ Campaign workflow execution error: {exc}")

    async def _execute_cluster_campaign(self, workflow_id: str, focus: str) -> dict[str, Any]:
        try:
            from temporalio.client import Client
            from a2a.runners.temporal.workflows import A2AOutreachWorkflow
            client = await Client.connect("192.168.1.250:7233")
            handle = await client.start_workflow(
                A2AOutreachWorkflow.run,
                args=[focus, 3],
                id=workflow_id,
                task_queue="a2a-task-queue",
            )
            return await handle.result()
        except Exception as exc:
            LOGGER.info("Temporal client dispatch falling back to cluster direct HTTP: %s", exc)
            return await asyncio.to_thread(self._fallback_cluster_campaign, focus)

    def _fallback_cluster_campaign(self, focus: str) -> dict[str, Any]:
        embed_req = urllib.request.Request(
            "http://192.168.1.250:8082/v1/embeddings",
            data=json.dumps({"input": focus}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        vec = []
        try:
            with urllib.request.urlopen(embed_req, timeout=10) as resp:
                vec = json.loads(resp.read().decode("utf-8"))["data"][0]["embedding"]
        except Exception:
            pass

        matches = []
        if vec:
            search_req = urllib.request.Request(
                "http://192.168.1.250:6333/collections/talos_startups/points/search",
                data=json.dumps({"vector": vec, "limit": 3, "with_payload": True}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(search_req, timeout=10) as resp:
                    matches = json.loads(resp.read().decode("utf-8")).get("result", [])
            except Exception:
                pass

        startups = []
        for m in matches:
            p = m.get("payload") or {}
            startups.append({
                "startup_name": p.get("startup_name") or p.get("name") or "Startup",
                "founder": p.get("founder") or "Founding Team",
                "pitch": p.get("pitch") or "",
            })

        if not startups:
            startups = [
                {"startup_name": "AgentGrid", "founder": "Marcus", "pitch": "Decentralized multi-agent workflow runtime for enterprise micro-agents."},
                {"startup_name": "Rajdharma Technologies", "founder": "Founding Team", "pitch": "A governance platform for AI systems separating capability from authority."},
            ]

        bullets = "\n".join(f"- **{s['startup_name']}** ({s['founder']}): {s['pitch']}" for s in startups)
        prompt = (
            f"You are the Strategic BD & Partnerships Director for Talos Protocol.\n"
            f"Goal: Pitch Talos Protocol (cryptographic agent identities, W3C DIDs, E2EE channels, capability tokens, and Merkle audit receipts) "
            f"to the following startups for target focus: '{focus}'.\n\n"
            f"Target Startups:\n{bullets}\n\n"
            f"Format a structured, executive outreach strategy with value proposition and integration hooks for each startup."
        )

        req_body = {
            "model": "deepseek-7b",
            "messages": [
                {"role": "system", "content": "You are a concise, high-impact BD Director for Talos Protocol."},
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": "<think>\n</think>\n"},
            ],
            "temperature": 0.3,
            "max_tokens": 1000,
        }
        output = ""
        req = urllib.request.Request(
            "http://192.168.1.250:8080/v1/chat/completions",
            data=json.dumps(req_body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                output = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        except Exception as exc:
            LOGGER.warning("DeepSeek fallback call failed: %s", exc)

        if not output:
            output = f"Strategic outreach for {focus}:\n" + "\n".join(
                f"• {s['startup_name']}: Integrate Talos DIDs & E2EE audit trails." for s in startups
            )

        return {"output": output, "startups": startups, "state": "TASK_STATE_COMPLETED"}

    async def _default_a2a_handler(
        self,
        task: Task,
        emit_status: Any,
        is_cancelled: Any,
    ) -> tuple[str, list[Any]]:
        domain = (task.metadata or {}).get("domain", "")
        if domain == "visa":
            return await self._visa_a2a_task_handler(task, emit_status, is_cancelled)
        if self.talos_scout is not None and getattr(self.talos_scout, "enabled", False):
            return await self.talos_scout.execute_a2a_task(task, emit_status, is_cancelled)
        if self.chat_service is not None and getattr(self.chat_service, "enabled", False):
            return await self._visa_a2a_task_handler(task, emit_status, is_cancelled)
        return "No agent execution service is currently configured or available.", []

    async def _visa_a2a_task_handler(
        self,
        task: Task,
        emit_status: Any,
        is_cancelled: Any,
    ) -> tuple[str, list[Any]]:
        question = task.history[-1].get_text() if task.history else ""
        if not question:
            return "No question provided in task", []
        if is_cancelled():
            return "Task was canceled", []

        await emit_status("📊 Checking slot history and trends...")
        if is_cancelled():
            return "Task was canceled", []

        if self.chat_service is None or not self.chat_service.enabled:
            return "Local chat is not configured. Use /trend for deterministic statistics.", []

        await emit_status("🧠 Formulating answer with local LLM...")
        service_settings = getattr(self.chat_service, "settings", None)
        timeout = max(180, int(getattr(service_settings, "chat_timeout_seconds", 180)))
        ans = await asyncio.wait_for(self.chat_service.answer(task.context_id, question), timeout=timeout)
        return ans, []

    async def _dispatch_a2a_streaming(
        self,
        chat_id: str,
        question: str,
        domain: str = "talos",
        disambiguated: bool = False,
    ) -> None:
        if not question:
            hint = "/talos <question> for Talos queries" if domain == "talos" else "/ask <question> for visa slots"
            await asyncio.to_thread(
                self._api_json,
                "sendMessage",
                {"chat_id": chat_id, "text": f"Usage: {hint}."},
            )
            return

        if len(question) > 1500:
            await asyncio.to_thread(
                self._api_json,
                "sendMessage",
                {"chat_id": chat_id, "text": "Please keep each question under 1,500 characters."},
            )
            return

        if domain == "talos":
            if self.talos_scout is None or not getattr(self.talos_scout, "enabled", False):
                await asyncio.to_thread(
                    self._api_json,
                    "sendMessage",
                    {"chat_id": chat_id, "text": "Talos Scout service is not configured or LLM is offline."},
                )
                return
            handler = self.talos_scout.execute_a2a_task
            title = "Talos Scout 🚀"
        else:
            handler = self._visa_a2a_task_handler
            title = "Visa Assistant 📌"

        init_res = await asyncio.to_thread(
            self._api_json,
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": f"⏳ Initializing {title} task...",
                "disable_web_page_preview": "true",
            },
        )
        msg_id = (init_res.get("result") or {}).get("message_id") if isinstance(init_res, dict) else None
        if not msg_id:
            msg_id = 1

        user_msg = Message.from_text(question, role="user", context_id=str(chat_id))
        task = await self.a2a_engine.submit_task(
            context_id=str(chat_id),
            message=user_msg,
            handler=handler,
            metadata={"domain": domain, "chat_id": str(chat_id)},
        )

        short_id = task.id[:8]
        await asyncio.to_thread(
            self._edit_message_text,
            chat_id,
            msg_id,
            f"⏳ [Task #{short_id}] State: WORKING\nProgress: Initializing engine...",
        )

        last_edit_time = time.monotonic()
        last_rendered_text = ""
        output_text: str | None = None
        min_edit_interval = 1.0

        try:
            async for resp in self.a2a_engine.subscribe_task(task.id):
                current_time = time.monotonic()
                if resp.status_update:
                    status = resp.status_update.status
                    state = status.state
                    step_msg = status.message or "In progress..."
                    icon = "⏳" if state == TaskState.WORKING else ("✅" if state == TaskState.COMPLETED else "❌")

                    new_text = f"{icon} [Task #{short_id}] State: {state.value}\nStatus: {step_msg}"

                    if state in {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELED}:
                        pass
                    elif (current_time - last_edit_time >= min_edit_interval) and (new_text != last_rendered_text):
                        await asyncio.to_thread(self._edit_message_text, chat_id, msg_id, new_text)
                        last_edit_time = current_time
                        last_rendered_text = new_text

                if resp.message:
                    output_text = resp.message.get_text()

                if resp.task:
                    final_state = resp.task.status.state
                    if final_state in {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELED}:
                        break

            final_task = self.a2a_engine.get_task(task.id) or task
            if final_task.status.state == TaskState.COMPLETED:
                body = output_text or "Task completed successfully."
                suffix = "\n\n💡 Tip: Use /talos <question> for Talos queries and /ask for visa slots." if disambiguated else ""
                final_text = (body + suffix).strip()
            elif final_task.status.state == TaskState.CANCELED:
                final_text = f"🛑 [{title}]\n[Task #{short_id}] Task was canceled: {final_task.status.message}"
            else:
                final_text = f"❌ [{title}]\n[Task #{short_id}] Task failed: {final_task.status.message}"

            await asyncio.to_thread(self._edit_or_send_long_text, chat_id, msg_id, final_text)

        except Exception as exc:
            LOGGER.exception("Error during A2A streaming to Telegram: %s", exc)
            err_msg = f"⚠️ [Task #{short_id}] Streaming error: {exc}"
            await asyncio.to_thread(self._edit_message_text, chat_id, msg_id, err_msg)

    def _find_task_by_prefix(self, prefix: str) -> Task | None:
        task = self.a2a_engine.get_task(prefix)
        if task:
            return task
        recent = self.a2a_engine.list_tasks(limit=50)
        cleaned_prefix = prefix.lower().replace("-", "")
        for t in recent:
            if t.id.startswith(prefix) or t.id.replace("-", "").startswith(cleaned_prefix):
                return t
        return None

    async def _task_command(self, chat_id: str, subquery: str) -> str:
        parts = subquery.split(maxsplit=1)
        subcmd = parts[0].lower() if parts else "list"
        arg = parts[1].strip() if len(parts) > 1 else ""

        if subcmd == "list" or not subquery:
            tasks = self.a2a_engine.list_tasks(context_id=chat_id, limit=10)
            if not tasks:
                tasks = self.a2a_engine.list_tasks(limit=5)
            if not tasks:
                return "No A2A tasks found."
            lines = ["📋 **Recent A2A Tasks:**\n"]
            for t in tasks:
                status_icon = "⏳" if t.status.state == TaskState.WORKING else ("✅" if t.status.state == TaskState.COMPLETED else "❌")
                query_preview = (t.history[0].get_text()[:40] + "...") if t.history else "No input"
                lines.append(f"• `{t.id[:8]}` {status_icon} **{t.status.state.value}** — {query_preview}")
            lines.append("\n💡 Use `/task get <id>` for full details or `/task cancel <id>` to cancel.")
            return "\n".join(lines)

        elif subcmd == "get":
            if not arg:
                return "Usage: /task get <task_id>"
            task = self._find_task_by_prefix(arg)
            if not task:
                return f"Task `{arg}` not found."
            status_icon = "⏳" if task.status.state == TaskState.WORKING else ("✅" if task.status.state == TaskState.COMPLETED else "❌")
            lines = [
                f"📋 **Task Information**",
                f"• **ID:** `{task.id}`",
                f"• **Context ID:** `{task.context_id}`",
                f"• **State:** {status_icon} `{task.status.state.value}`",
                f"• **Status Message:** {task.status.message or 'N/A'}",
                f"• **Created:** {task.status.timestamp}",
                f"• **Artifacts:** {len(task.artifacts)}",
                f"• **Messages:** {len(task.history)}",
            ]
            if task.artifacts:
                lines.append("\n📦 **Artifacts:**")
                for a in task.artifacts:
                    lines.append(f"  - `{a.artifact_id}`: {a.name} ({a.mime_type})")
            if task.history:
                agent_msgs = [m for m in task.history if m.role == "agent"]
                if agent_msgs:
                    preview = agent_msgs[-1].get_text()
                    if len(preview) > 300:
                        preview = preview[:300] + "..."
                    lines.append(f"\n💬 **Output Preview:**\n{preview}")
            return "\n".join(lines)

        elif subcmd == "wait":
            if not arg:
                return "Usage: /task wait <task_id>"
            task = self._find_task_by_prefix(arg)
            if not task:
                return f"Task `{arg}` not found."
            if task.status.state in {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELED}:
                return f"Task `{task.id[:8]}` is already terminal: {task.status.state.value}"
            try:
                completed_task = await self.a2a_engine.wait_task(task.id, timeout=60.0)
                if completed_task:
                    return f"Task `{task.id[:8]}` finished with state: {completed_task.status.state.value}"
                return f"Task `{task.id[:8]}` timed out waiting for completion."
            except asyncio.TimeoutError:
                return f"Timed out waiting for task `{task.id[:8]}` to finish."

        elif subcmd == "cancel":
            if not arg:
                return "Usage: /task cancel <task_id>"
            task = self._find_task_by_prefix(arg)
            if not task:
                return f"Task `{arg}` not found."
            success = self.a2a_engine.cancel_task(task.id, reason="Cancelled via /task cancel")
            if success:
                return f"🛑 Successfully canceled task `{task.id[:8]}`."
            return f"Could not cancel task `{task.id[:8]}` (already terminal or not found)."

        else:
            return (
                "Usage:\n"
                "• `/task list` (or `/tasks`) — list recent tasks\n"
                "• `/task get <task_id>` — inspect task status and artifacts\n"
                "• `/task wait <task_id>` — wait for running task to finish\n"
                "• `/task cancel <task_id>` — cancel running task"
            )

    def _agentcard_command(self) -> str:
        card = get_agent_card()
        return (
            "🤖 **Talos Scout A2A Agent Card (v1 Canonical)**\n\n"
            f"• **Name:** {card['name']}\n"
            f"• **Description:** {card['description']}\n"
            f"• **DID:** `{card['did']}`\n"
            f"• **Version:** {card['version']}\n"
            f"• **Supported Modes:** {', '.join(card['supportedTaskModes'])}\n\n"
            "**Skills:**\n"
            + "\n".join(f"• `{s['id']}`: {s['name']} — {s['description']}" for s in card['skills'])
            + "\n\n**Protocols:**\n"
            + "\n".join(f"• {p['protocol']} ({p['role']})" for p in card['protocols'])
        )

    async def _rpc_command(self, raw_json: str) -> str:
        if not raw_json:
            return 'Usage: /rpc {"jsonrpc": "2.0", "id": 1, "method": "GetExtendedAgentCard"}'
        try:
            payload = json.loads(raw_json)
        except Exception as exc:
            return f"Invalid JSON payload: {exc}"
        result = await self.a2a_rpc.dispatch(payload)
        formatted = json.dumps(result, indent=2)
        if len(formatted) > 3900:
            formatted = formatted[:3900] + "\n... (truncated)"
        return f"```json\n{formatted}\n```"

    def _send_typing_action(self, chat_id: str) -> None:
        try:
            self._api_json("sendChatAction", {"chat_id": chat_id, "action": "typing"})
        except Exception:
            pass

    async def _smart_chat(self, chat_id: str, question: str) -> str:
        if not question:
            return "Usage: /ask <question> for visa slots or /talos <question> for Talos Protocol & startup outreach."
        if TALOS_ROUTING_PATTERN.search(question):
            ans = await self._talos_query(chat_id, question)
            return f"[Talos Scout 🚀]\n\n{ans}\n\n💡 Tip: Use /talos <question> for Talos queries and /ask for visa slots."
        return await self._chat(chat_id, question)

    async def _talos_query(self, chat_id: str, question: str) -> str:
        if self.talos_scout is None or not getattr(self.talos_scout, "enabled", False):
            return "Talos Scout service is not configured or LLM is offline."
        if not question:
            return "Usage: /talos <question> (e.g. /talos which startup can give Talos more visibility?)"
        if len(question) > 1500:
            return "Please keep each question under 1,500 characters."
        await asyncio.to_thread(self._send_typing_action, chat_id)
        try:
            return await asyncio.wait_for(self.talos_scout.answer(chat_id, question), timeout=180)
        except Exception as exc:
            LOGGER.exception("Talos Scout request failed: %s", exc)
            return f"The Talos Scout AI is temporarily unavailable ({exc}). Check /status for connectivity."

    def _startups_list(self, filter_term: str) -> str:
        if self.talos_scout is None:
            return "Talos Scout service is not configured."
        return self.talos_scout.format_startups_list(filter_term=filter_term or None)

    async def _sync_command(self, subquery: str = "") -> str:
        if self.whatsapp_watcher is None:
            return "WhatsApp monitor is not configured."
        try:
            new_msgs = await self.whatsapp_watcher.sync_now()
            synced_vectors = 0
            if self.talos_scout is not None:
                synced_vectors = await asyncio.to_thread(self.talos_scout.sync_all_startups_to_qdrant)
            startups_count = self.store.count_startups()
            return (
                f"✅ Synced AI Founders Lounge: fetched {new_msgs} new messages, "
                f"{synced_vectors} startups vector-indexed in Qdrant. "
                f"Total startups: {startups_count}."
            )
        except Exception as exc:
            return f"WhatsApp sync failed: {exc}"

    async def _chat(self, chat_id: str, question: str) -> str:
        if self.chat_service is None or not self.chat_service.enabled:
            return "Local chat is not configured. Use /trend for deterministic statistics."
        if not question:
            return "Usage: /ask <question>. You can also send normal text to chat."
        if len(question) > 1500:
            return "Please keep each question under 1,500 characters."
        await asyncio.to_thread(self._send_typing_action, chat_id)
        try:
            service_settings = getattr(self.chat_service, "settings", None)
            timeout = max(180, int(getattr(service_settings, "chat_timeout_seconds", 180)))
            return await asyncio.wait_for(self.chat_service.answer(chat_id, question), timeout=timeout)
        except Exception as exc:
            LOGGER.exception("Pi-local chat request failed: %s", exc)
            return f"The Pi-local model is temporarily unavailable ({exc}). Try again shortly or use /trend."

    async def _format_status(self) -> str:
        lines = ["System status:"]
        try:
            self.store.conn.execute("select 1").fetchone()
            lines.append("Database: connected")
        except Exception as exc:
            lines.append(f"Database: unavailable ({exc})")

        try:
            telegram_result = await asyncio.wait_for(
                asyncio.to_thread(self._api_json, "getMe", {}),
                timeout=8,
            )
            if not telegram_result.get("ok"):
                raise RuntimeError("Telegram API returned ok=false")
            lines.append("Telegram Bot API: connected")
        except Exception as exc:
            lines.append(f"Telegram Bot API: unavailable ({type(exc).__name__})")

        llm_client = getattr(self.chat_service, "llm_client", None)
        if llm_client is None or not getattr(llm_client, "enabled", False):
            lines.append("AI providers: not configured")
        else:
            try:
                statuses = await asyncio.to_thread(llm_client.health)
            except Exception as exc:
                lines.append(f"AI providers: unavailable ({type(exc).__name__})")
            else:
                for provider, status in statuses.items():
                    lines.append(f"AI {provider}: {status}")

        if self.whatsapp_watcher is not None:
            wa_status = getattr(self.whatsapp_watcher, "status", "ready")
            msg_count = self.store.count_whatsapp_messages()
            startup_count = self.store.count_startups()
            lines.append(f"WhatsApp Founders Lounge: {wa_status} ({msg_count} msgs, {startup_count} startups)")
        elif self.talos_scout is not None:
            startup_count = self.store.count_startups()
            lines.append(f"Talos Scout: {startup_count} startups indexed")
        return "\n".join(lines)

    def _format_current(self) -> str:
        state = self.store.availability_state()
        current = "AVAILABLE" if state["currently_available"] else "NOT AVAILABLE"
        lines = [f"Current: {current}"]
        if state.get("current_seen_at"):
            lines.append(f"Seen at: {state['current_seen_at']}")
        if state.get("current_reason"):
            lines.append(f"Reason: {state['current_reason']}")
        if state.get("current_source"):
            lines.append(f"Source: {state['current_source']}")
        return "\n".join(lines)

    def _format_last(self) -> str:
        state = self.store.availability_state()
        if not state.get("last_available_at"):
            return "No previous availability has been recorded yet."
        lines = [f"Last available: {state['last_available_at']}"]
        if state.get("last_available_message_id"):
            lines.append(f"Message: {state['last_available_message_id']}")
        if state.get("last_available_source"):
            lines.append(f"Source: {state['last_available_source']}")
        return "\n".join(lines)

    def _format_identity(self, message: dict[str, Any], chat_id: str) -> str:
        username = self._first_username(message)
        owner = self.store.bot_user_is_owner(chat_id=chat_id, username=username)
        lines = [f"Chat ID: {chat_id}"]
        if username:
            lines.append(f"Username: @{username}")
        lines.append(f"Owner: {'yes' if owner else 'no'}")
        return "\n".join(lines)

    def _format_users(self) -> str:
        users = self.store.list_bot_users()
        if not users:
            return "No bot users configured."
        lines = ["Allowed bot users:"]
        for user in users:
            parts = []
            if user.get("chat_id"):
                parts.append(str(user["chat_id"]))
            if user.get("username"):
                parts.append(f"@{user['username']}")
            label = " / ".join(parts)
            if user.get("display_name"):
                label = f"{label} ({user['display_name']})"
            if user.get("is_owner"):
                label = f"{label} [owner]"
            lines.append(f"- {label}")
        return "\n".join(lines)

    def _allow_user(self, text: str) -> str:
        parts = text.split()
        if len(parts) < 2:
            return "Usage: /allow <chat_id|@username> [owner]"
        identifier = parts[1]
        owner = any(part.lower() == "owner" for part in parts[2:])
        chat_id, username = self._split_identifier(identifier)
        self.store.ensure_bot_user(chat_id=chat_id, username=username, is_owner=owner)
        label = f"@{username}" if username else chat_id
        suffix = " as owner" if owner else ""
        return f"Allowed {label}{suffix}."

    def _revoke_user(self, text: str) -> str:
        parts = text.split()
        if len(parts) != 2:
            return "Usage: /revoke <chat_id|@username>"
        removed = self.store.remove_bot_user(parts[1])
        if not removed:
            return "No non-owner user was removed."
        return f"Revoked {parts[1]}."

    def _owner_command(self, message: dict[str, Any], chat_id: str, handler: Any) -> str:
        username = self._first_username(message)
        if not self.store.bot_user_is_owner(chat_id=chat_id, username=username):
            return "Only bot owners can manage users."
        return handler()

    def _api_json(self, method: str, params: dict[str, str]) -> dict[str, Any]:
        payload = urllib.parse.urlencode(params).encode("utf-8")
        request = urllib.request.Request(f"{self.base_url}/{method}", data=payload, method="POST")
        with urllib.request.urlopen(request, timeout=40) as response:
            return json.load(response)

    def _is_authorized(self, message: dict[str, Any], chat_id: str) -> bool:
        username = self._first_username(message)
        return self.store.bot_user_allowed(chat_id=chat_id, username=username)

    def _first_username(self, message: dict[str, Any]) -> str | None:
        from_user = message.get("from") or {}
        chat = message.get("chat") or {}
        for value in (from_user.get("username"), chat.get("username")):
            username = self._normalize_username(str(value or ""))
            if username:
                return username
        return None

    @staticmethod
    def _normalize_username(username: str) -> str:
        return username.strip().removeprefix("@").lower()

    def _seed_bot_users(self) -> None:
        for chat_id in self.allowed_chat_ids:
            self.store.ensure_bot_user(chat_id=chat_id, is_owner=chat_id in self.owner_chat_ids)
        for username in self.allowed_usernames:
            self.store.ensure_bot_user(
                username=username,
                is_owner=self._normalize_username(username) in self.owner_usernames,
            )
        for chat_id in self.owner_chat_ids:
            self.store.ensure_bot_user(chat_id=chat_id, is_owner=True)
        for username in self.owner_usernames:
            self.store.ensure_bot_user(username=username, is_owner=True)

    @staticmethod
    def _split_identifier(identifier: str) -> tuple[str | None, str | None]:
        cleaned = identifier.strip()
        if not cleaned:
            raise ValueError("identifier is required")
        if cleaned.startswith("@") or not cleaned.lstrip("-").isdigit():
            return None, TelegramBotCommandListener._normalize_username(cleaned)
        return cleaned, None
