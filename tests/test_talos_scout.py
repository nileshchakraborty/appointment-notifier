import asyncio
import json
from unittest.mock import MagicMock

from appointment_notifier.bot_listener import TelegramBotCommandListener
from appointment_notifier.config import TalosScoutSettings, TelegramAlertSettings
from appointment_notifier.llm import FallbackLlmClient, FedoraLlmProvider, LlmResponse
from appointment_notifier.store import AlertStore
from appointment_notifier.talos_scout import TalosScoutService


class MockProvider:
    name = "mock"

    def __init__(self, response_text: str = ""):
        self.response_text = response_text
        self.last_messages = []

    def complete(self, messages, *, max_tokens, temperature):
        self.last_messages = messages
        return self.response_text


def test_alert_store_whatsapp_and_startups(tmp_path):
    store = AlertStore(tmp_path / "test.sqlite3")

    # Record WhatsApp message
    saved = store.record_whatsapp_message(
        msg_id="msg-123",
        chat_name="AI Founders Lounge",
        sender_id="user-1@c.us",
        sender_name="Alice",
        text="Launching SwarmX - an autonomous agent framework",
        sent_at="1729000000",
    )
    assert saved is True

    # Duplicate should not crash
    assert store.record_whatsapp_message(
        msg_id="msg-123",
        chat_name="AI Founders Lounge",
        text="duplicate",
    ) is False

    unprocessed = store.get_unprocessed_whatsapp_messages(chat_name="AI Founders Lounge")
    assert len(unprocessed) == 1
    assert unprocessed[0]["msg_id"] == "msg-123"

    # Record startup
    startup_id = store.record_startup(
        startup_name="SwarmX",
        founder_name="Alice",
        founder_handle="@alice_dev",
        pitch="Autonomous multi-agent orchestration framework",
        tech_stack="Python, LangChain",
        talos_synergy_score=5,
        synergy_rationale="Needs DID and E2EE agent communication",
        outreach_hook="Integrate Talos for native cryptographic agent identity",
        links="https://swarmx.dev",
        raw_message_id="msg-123",
    )
    assert startup_id > 0

    top = store.get_top_startups(limit=5)
    assert len(top) == 1
    assert top[0]["startup_name"] == "SwarmX"
    assert top[0]["talos_synergy_score"] == 5

    # Filtered search
    assert len(store.get_top_startups(filter_term="swarm")) == 1
    assert len(store.get_top_startups(filter_term="nonexistent")) == 0

    # Mark message processed
    store.mark_whatsapp_messages_processed(["msg-123"])
    assert len(store.get_unprocessed_whatsapp_messages(chat_name="AI Founders Lounge")) == 0


def test_talos_scout_extract_startups(tmp_path):
    store = AlertStore(tmp_path / "test.sqlite3")
    settings = TalosScoutSettings(enabled=True)

    json_response = json.dumps([
        {
            "startup_name": "AgentMesh",
            "founder_name": "Bob",
            "founder_handle": "@bob_ai",
            "pitch": "Decentralized swarm network for coding agents",
            "tech_stack": "Rust",
            "talos_synergy_score": 5,
            "synergy_rationale": "Direct alignment with Double Ratchet messaging",
            "outreach_hook": "Propose Talos protocol contract integration",
            "links": "https://agentmesh.io",
        }
    ])
    mock_llm = FallbackLlmClient([MockProvider(json_response)])
    scout = TalosScoutService(store, settings, mock_llm)

    messages = [
        {"msg_id": "m1", "sender_name": "Bob", "sender_id": "bob@c.us", "text": "Hey all, I built AgentMesh!"}
    ]
    count = asyncio.run(scout.extract_startups_from_batch(messages))
    assert count == 1

    startups = store.get_top_startups(limit=10)
    assert len(startups) == 1
    assert startups[0]["startup_name"] == "AgentMesh"

    # Test format list
    formatted = scout.format_startups_list()
    assert "AgentMesh" in formatted
    assert "Bob" in formatted
    assert "⭐⭐⭐⭐⭐" in formatted


def test_talos_scout_answer_query(tmp_path):
    store = AlertStore(tmp_path / "test.sqlite3")
    store.record_startup(
        startup_name="AgentMesh",
        pitch="Decentralized swarm network",
        talos_synergy_score=5,
    )
    settings = TalosScoutSettings(enabled=True)
    mock_llm = FallbackLlmClient([MockProvider("You should reach out to AgentMesh because they build swarms.")])
    scout = TalosScoutService(store, settings, mock_llm)

    answer = asyncio.run(scout.answer("123", "Which startup gives more visibility?"))
    assert "reach out to AgentMesh" in answer


def test_bot_listener_talos_and_disambiguation(tmp_path):
    store = AlertStore(tmp_path / "test.sqlite3")
    settings = TelegramAlertSettings(
        enabled=True,
        bot_token="test-token",
        chat_ids=("1422400397",),
        allowed_usernames=("testuser",),
        owner_chat_ids=("1422400397",),
        owner_usernames=(),
    )
    mock_llm = FallbackLlmClient([MockProvider("Strategic Talos recommendation: AgentMesh.")])
    scout = TalosScoutService(store, TalosScoutSettings(), mock_llm)
    listener = TelegramBotCommandListener(settings, store, talos_scout=scout)

    sent = {}
    listener._api_json = lambda method, params: sent.update(method=method, params=params) or {}

    # Test /talos command
    asyncio.run(
        listener._handle_update({
            "message": {
                "chat": {"id": 1422400397},
                "from": {"username": "testuser"},
                "text": "/talos which startup should I reach out to?",
            }
        })
    )
    assert "AgentMesh" in sent["params"]["text"]

    # Test /help talos
    asyncio.run(
        listener._handle_update({
            "message": {
                "chat": {"id": 1422400397},
                "from": {"username": "testuser"},
                "text": "/help talos",
            }
        })
    )
    assert "/talos" in sent["params"]["text"]
    assert "Talos Protocol & Founder Scout Commands" in sent["params"]["text"]

    # Test /help visa
    asyncio.run(
        listener._handle_update({
            "message": {
                "chat": {"id": 1422400397},
                "from": {"username": "testuser"},
                "text": "/help visa",
            }
        })
    )
    assert "/current" in sent["params"]["text"]
    assert "Visa & Appointment Commands" in sent["params"]["text"]

    # Test smart disambiguation on generic /ask with talos keyword
    asyncio.run(
        listener._handle_update({
            "message": {
                "chat": {"id": 1422400397},
                "from": {"username": "testuser"},
                "text": "/ask how can talos protocol partner with startups?",
            }
        })
    )
    assert "[Talos Scout 🚀]" in sent["params"]["text"]
    assert "Tip: Use /talos <question>" in sent["params"]["text"]


def test_fedora_llm_provider_fallback():
    import io
    from unittest.mock import patch

    provider = FedoraLlmProvider(
        url="http://fedora.local:8080",
        fallback_urls=("http://192.168.1.250:8080",),
        model="deepseek-7b",
    )

    response_bytes = io.BytesIO(
        json.dumps({"choices": [{"message": {"content": "Hello from Fedora!"}}]}).encode()
    )

    calls = []

    def mock_urlopen(req, timeout=60):
        calls.append(req.full_url)
        if "fedora.local" in req.full_url:
            raise OSError("DNS lookup failed")
        return response_bytes

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        result = provider.complete([{"role": "user", "content": "hi"}], max_tokens=50, temperature=0.1)

    assert result == "Hello from Fedora!"
    assert any("192.168.1.250" in u for u in calls)


def test_talos_scout_vector_rag(tmp_path):
    import io
    from unittest.mock import patch

    store = AlertStore(tmp_path / "test.sqlite3")
    settings = TalosScoutSettings(
        enabled=True,
        qdrant_url="http://fedora.local:6333",
        embedding_url="http://fedora.local:8082",
    )
    mock_llm = FallbackLlmClient([MockProvider("Analysis with vector grounding.")])
    scout = TalosScoutService(store, settings, mock_llm)

    # 1. Test compute_embedding
    fake_emb = [0.1] * 384
    emb_resp = io.BytesIO(json.dumps({"data": [{"embedding": fake_emb}]}).encode())

    with patch("urllib.request.urlopen", return_value=emb_resp):
        emb = scout.compute_embedding("Test text")
        assert emb == fake_emb

    # 2. Test upsert_startup_vector
    emb_resp2 = io.BytesIO(json.dumps({"data": [{"embedding": fake_emb}]}).encode())
    qdrant_resp = io.BytesIO(json.dumps({"status": "ok", "result": {"operation_id": 1}}).encode())

    responses = [emb_resp2, qdrant_resp]
    with patch("urllib.request.urlopen", side_effect=responses):
        ok = scout.upsert_startup_vector(
            1,
            {
                "startup_name": "AgentCore",
                "pitch": "Agent framework",
                "synergy_rationale": "High synergy with Talos",
            },
        )
        assert ok is True

    # 3. Test search_vector_startups
    emb_resp3 = io.BytesIO(json.dumps({"data": [{"embedding": fake_emb}]}).encode())
    search_resp = io.BytesIO(
        json.dumps({
            "status": "ok",
            "result": [
                {
                    "id": 1,
                    "score": 0.89,
                    "payload": {
                        "startup_name": "AgentCore",
                        "pitch": "Agent framework",
                        "talos_synergy_score": 5,
                        "synergy_rationale": "High synergy with Talos",
                        "outreach_hook": "Let's partner",
                    },
                }
            ],
        }).encode()
    )

    with patch("urllib.request.urlopen", side_effect=[emb_resp3, search_resp]):
        results = scout.search_vector_startups("Which agent framework?", limit=2)
        assert len(results) == 1
        assert results[0]["startup_name"] == "AgentCore"
        assert results[0]["similarity_score"] == 0.89

    # 4. Test answer() incorporating vector matches in prompt
    emb_resp4 = io.BytesIO(json.dumps({"data": [{"embedding": fake_emb}]}).encode())
    search_resp2 = io.BytesIO(
        json.dumps({
            "status": "ok",
            "result": [
                {
                    "id": 1,
                    "score": 0.92,
                    "payload": {
                        "startup_name": "AgentCore",
                        "pitch": "Agent framework",
                        "talos_synergy_score": 5,
                        "synergy_rationale": "High synergy with Talos",
                    },
                }
            ],
        }).encode()
    )

    with patch("urllib.request.urlopen", side_effect=[emb_resp4, search_resp2]):
        ans = asyncio.run(scout.answer("1422400397", "Recommend an agent framework"))
        assert ans == "Analysis with vector grounding."
        # Verify prompt contained vector search result
        provider = mock_llm.providers[0]
        prompt_content = provider.last_messages[1]["content"]
        assert "Semantic Vector Search via Qdrant" in prompt_content
        assert "AgentCore" in prompt_content
