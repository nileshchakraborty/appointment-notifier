from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def get_agent_card(
    signing_private_key: ed25519.Ed25519PrivateKey | None = None,
    base_url: str = "http://192.168.1.189:8000",
) -> dict[str, Any]:
    """Return canonical authenticated A2A v1 Agent Card for Talos Scout & Consular Monitor."""
    # Deterministic fallback key if not provided (for testing / unconfigured edge nodes)
    if signing_private_key is None:
        seed = hashlib.sha256(b"talos-scout-pi5-default-seed").digest()
        signing_private_key = ed25519.Ed25519PrivateKey.from_private_bytes(seed)

    pub_key = signing_private_key.public_key()
    raw_pub = pub_key.public_bytes(encoding=Encoding.Raw, format=PublicFormat.Raw)
    fingerprint = hashlib.sha256(raw_pub).hexdigest()[:32]
    did = f"did:talos:agent:scout-v1:{fingerprint}"

    card_body: dict[str, Any] = {
        "@context": [
            "https://www.w3.org/ns/did/v1",
            "https://talosprotocol.com/schemas/agent-card/v1.json",
        ],
        "name": "Talos Scout & AI Founders Lounge Agent",
        "description": "Autonomous edge agent monitoring VAC visa appointments and AI Founders Lounge, evaluating Talos Protocol A2A integrations.",
        "url": base_url,
        "did": did,
        "version": "1.0.0",
        "protocol": "a2a-v1",
        "endpoints": {
            "rpc": f"{base_url}/rpc",
            "stream": f"{base_url}/tasks/stream",
            "didDocument": f"{base_url}/.well-known/did.json",
        },
        "supportedTaskModes": ["immediate", "streaming", "wait", "get"],
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
            "extendedAgentCard": True,
            "extensions": [
                "talos.security.v1",
                "talos.capability.v1",
                "talos.audit.v1",
                "talos.streaming.v1",
            ],
        },
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
        "protocols": [
            {"protocol": "a2a-v1", "role": "server"},
            {"protocol": "jsonrpc-2.0", "role": "bidirectional"},
        ],
        "skills": [
            {
                "id": "scout:read_slots",
                "name": "Consular Slot Scanner",
                "description": "Extracts VAC availability data and generates release event clusters across consular districts.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "consular_district": {
                            "type": "string",
                            "enum": ["HYDERABAD", "NEW_DELHI", "MUMBAI", "CHENNAI", "KOLKATA"],
                        }
                    },
                    "required": ["consular_district"],
                },
            },
            {
                "id": "scout:bulk_cluster",
                "name": "Release Event Clusterer",
                "description": "Clusters community appointment reports into temporal-spatial bulk release events.",
            },
            {
                "id": "talos_scout",
                "name": "Talos Protocol Partnership Scout",
                "description": "Evaluates startup synergy with Talos Protocol DIDs, E2EE channels, capability tokens, and A2A task lifecycles.",
            },
            {
                "id": "founders_ingestion",
                "name": "WhatsApp AI Founders Lounge Ingestion",
                "description": "Extracts startup pitches, founders, tech stacks, and indexes into Fedora Qdrant vector store.",
            },
            {
                "id": "a2a_task_orchestrator",
                "name": "A2A Task Lifecycle Orchestrator",
                "description": "Implements canonical Task Immediate, Task Wait, Task Get, and A2A Task Stream dispatch.",
            },
        ],
        "models": [
            {
                "id": "deepseek-7b",
                "name": "DeepSeek-R1-Distill-Qwen-7B",
                "provider": "fedora.local:8080",
            },
            {
                "id": "bge-small-en-v1.5",
                "name": "BAAI/bge-small-en-v1.5",
                "provider": "fedora.local:8082",
            },
        ],
    }

    serialized = json.dumps(card_body, sort_keys=True).encode("utf-8")
    sig = signing_private_key.sign(serialized)
    sig_b64 = base64.b64encode(sig).decode("ascii")

    return {
        **card_body,
        "proof": {
            "type": "Ed25519Signature2020",
            "verificationMethod": f"{did}#key-1",
            "created": "2026-10-21T00:00:00Z",
            "signature": sig_b64,
        },
    }
