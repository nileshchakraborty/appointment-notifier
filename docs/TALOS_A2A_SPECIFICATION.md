# Talos Protocol Adoption & A2A Integration Specification

**Target Repository:** `appointment-notifier`  
**Date:** October 2026  
**Status:** Integrated & Deployed to Production Edge (Raspberry Pi)

---

## 1. System Integration Architecture

The `appointment-notifier` repository serves as the operational host and gateway for Talos Protocol adoption across our stack, orchestrating:
1. **Telegram Multi-Platform Bot:** Real-time conversational interface with proactive alerts, smart chat, and slash commands.
2. **Talos Scout & Lounge Intelligence:** DeepSeek-R1 powered matchmaking connecting Talos Protocol offerings with founder lounge startups.
3. **A2A Client & Gateway:** Zero-trust agent-to-agent communication implementing `talosprotocol/a2a` and `talosprotocol/talos-a2a`.

```
[ Telegram User ]
        |
        v
[ bot_listener.py ]
   |-- Direct Slash Commands (/talos, /scout, /lounge, /a2a)
   |-- Natural Language Router (TALOS_ROUTING_PATTERN)
        |
        +-----> [ talos_scout.py ] (Founder BD & Startup Matchmaker)
        |          |
        |          v
        |       [ Fedora LLM Provider (DeepSeek-R1 via 192.168.1.250:8080) ]
        |       [ Qdrant Vector Store (Lounge Startups via 192.168.1.250:6333) ]
        |
        +-----> [ a2a_client.py ]
                   |-- InMemoryTaskRunner / TemporalTaskRunner
                   |-- did:talos Cryptographic Signatures
                   |-- ChaCha20-Poly1305 E2EE Envelopes
                   |-- Capability-bounded delegation
```

---

## 2. Component Reference

### 2.1 Intent Routing (`src/appointment_notifier/bot_listener.py`)
- Detects user inquiries regarding Talos Protocol, startups, pitch outreach, agent swarms, and A2A tasks.
- Bypasses generic appointment chat fallbacks directly to `talos_scout` or `a2a_client`.

### 2.2 Talos Scout Engine (`src/appointment_notifier/talos_scout.py`)
- **Role:** Strategic BD & Partnerships Director for Talos Protocol.
- **Perspective:** Focuses on selling, pitching, and partnering with startups from the indexed lounge database (e.g. Webeloper, AerOS, Rajdharma Technologies, Nevrix, AlphaForge).
- **Inference Optimization:** Injects `<think>\n</think>\n` assistant prompt to terminate reasoning overhead on DeepSeek-R1, achieving 3–15 second conversational response times.

### 2.3 A2A Gateway (`src/appointment_notifier/a2a_client.py`)
- Provides synchronous and streaming task execution.
- Packages multimodal payloads (voice PCM / video keyframes).
- Verifies and attaches Merkle audit receipts to outbound agent responses.

---

## 3. Production Deployment
- **Edge Runtime:** Raspberry Pi 4 (`rpi@192.168.1.229`), running containerized Docker instance `appointment-notifier`.
- **Inference Host:** Fedora Server (`nilesh@192.168.1.250`) hosting llama-server and Qdrant vector database.
