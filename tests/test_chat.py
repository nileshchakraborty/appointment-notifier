import asyncio
from datetime import datetime, timezone

from appointment_notifier.chat import OllamaChatService
from appointment_notifier.config import TrendSettings
from appointment_notifier.llm import LlmResponse
from appointment_notifier.models import SlotSignal, TelegramMessage
from appointment_notifier.store import AlertStore
from appointment_notifier.trend import TrendService


def _settings() -> TrendSettings:
    return TrendSettings(
        ollama_enabled=True,
        ollama_url="http://127.0.0.1:11434",
        ollama_model="gemma3:1b",
        timezone="Asia/Kolkata",
        chat_history_messages=4,
        context_tokens=2048,
        response_tokens=220,
    )


def test_chat_persists_bounded_history_and_prefixes_trend(tmp_path) -> None:
    store = AlertStore(tmp_path / "state.sqlite3")
    store.record_observation(
        TelegramMessage(1, "slots available", datetime(2026, 8, 1, tzinfo=timezone.utc)),
        SlotSignal(matched=True, reason="positive"),
    )
    class FakeLlmClient:
        enabled = True

        def complete(self, messages, *, max_tokens, temperature):
            return LlmResponse(
                content="Low confidence; keep monitoring.",
                provider="test-provider",
            )

    llm_client = FakeLlmClient()
    trend_service = TrendService(store, _settings(), llm_client)
    service = OllamaChatService(store, trend_service, _settings(), llm_client)

    response = asyncio.run(service.answer("chat-1", "When is the next release?"))

    assert response.startswith("Trend (Asia/Kolkata")
    assert "Low confidence; keep monitoring." in response
    assert response.endswith("Source: test-provider")
    assert store.recent_chat_messages("chat-1", 10) == [
        {"role": "user", "content": "When is the next release?"},
        {"role": "assistant", "content": "Low confidence; keep monitoring."},
    ]


def test_chat_history_is_isolated_and_erasable(tmp_path) -> None:
    store = AlertStore(tmp_path / "state.sqlite3")
    store.record_chat_exchange("one", "hello", "hi", 4)
    store.record_chat_exchange("two", "private", "answer", 4)

    assert store.clear_chat_history("one") == 2
    assert store.recent_chat_messages("one", 4) == []
    assert len(store.recent_chat_messages("two", 4)) == 2
