from __future__ import annotations

from typing import Any


def get_agent_card() -> dict[str, Any]:
    """Return canonical A2A v1 Agent Card for Talos Scout & Lounge Ingestion."""
    return {
        "name": "Talos Scout & AI Founders Lounge Agent",
        "description": "Autonomous scout monitoring AI Founders Lounge, extracting startups, and evaluating Talos Protocol A2A integrations (Task Immediate, Task Wait, Task Get, A2A Task Stream).",
        "url": "http://192.168.1.229:8081",
        "did": "did:talos:agent:scout-v1:sha256:7f83b1657ff1fc53b92dc18148a1d65dfc2d4b1fa3d677284addd200126d9069",
        "version": "1.0.0",
        "protocol": "a2a-v1",
        "supportedTaskModes": ["immediate", "streaming", "wait", "get"],
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
            "extendedAgentCard": True,
        },
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
        "protocols": [
            {"protocol": "a2a-v1", "role": "server"},
            {"protocol": "jsonrpc-2.0", "role": "bidirectional"},
        ],
        "skills": [
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
