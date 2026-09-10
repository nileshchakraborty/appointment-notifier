"""Executable BDD-style scenarios for the notifier's critical user flows."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from appointment_notifier.chat import OllamaChatService
from appointment_notifier.config import TelegramAlertSettings, TrendSettings
from appointment_notifier.llm import LlmResponse
from appointment_notifier.media import PortalMediaAnalyzer
from appointment_notifier.models import SlotSignal, TelegramMessage
from appointment_notifier.parser import VisaSlotParser
from appointment_notifier.store import AlertStore
from appointment_notifier.trend import TrendService


def _settings() -> TrendSettings:
    return TrendSettings(
        ollama_enabled=True,
        ollama_url="http://127.0.0.1:11434",
        ollama_model="test",
        timezone="Asia/Kolkata",
        chat_history_messages=4,
        context_tokens=2048,
        response_tokens=220,
    )


def test_bdd_bulk_and_individual_counts(tmp_path):
    # Given a source containing both release types
    store = AlertStore(tmp_path / "state.sqlite3")
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    store.record_observation(TelegramMessage(1, "July August bulk appointments opened", now), SlotSignal(True, "bulk", category="bulk_release"))
    store.record_observation(TelegramMessage(2, "H1B slots available in Hyderabad", now), SlotSignal(True, "individual", category="individual_availability"))

    # When the trend report is generated
    report = TrendService(store, _settings(), _FakeLlm()).report()

    # Then the categories stay separate
    assert report.bulk_release_posts == 1
    assert report.individual_availability_posts == 1


def test_bdd_invalid_portal_does_not_match(tmp_path, monkeypatch):
    # Given OCR identifies an invalid portal layout
    image = tmp_path / "ghost.png"
    image.write_bytes(b"ghost")
    monkeypatch.setattr(PortalMediaAnalyzer, "_ocr", staticmethod(lambda _: "Calendar no time Submit disabled"))
    analysis = PortalMediaAnalyzer().analyze(image)

    # When the screenshot is classified, Then it cannot alert
    signal = VisaSlotParser().parse("", has_image=True, ocr_text=analysis.ocr_text, portal_state=analysis.portal_state)
    assert analysis.portal_state == "ghost_or_unbookable"
    assert signal.matched is False
    assert signal.category == "unbookable"


def test_bdd_media_cache_reuses_analysis(tmp_path, monkeypatch):
    # Given a stable screenshot hash and OCR result
    image = tmp_path / "portal.png"
    image.write_bytes(b"same image")
    monkeypatch.setattr(PortalMediaAnalyzer, "_ocr", staticmethod(lambda _: "Calendar Submit"))
    analyzer = PortalMediaAnalyzer()
    first = analyzer.analyze(image)
    store = AlertStore(tmp_path / "state.sqlite3")
    store.record_media_analysis(first)

    # When the same image is looked up again, Then the cached result is available
    cached = store.get_media_analysis(first.sha256)
    assert cached is not None
    assert cached["ocr_text"] == "Calendar Submit"


def test_bdd_ask_bulk_question_returns_and_persists(tmp_path):
    # Given a trusted report and local assistant
    store = AlertStore(tmp_path / "state.sqlite3")
    store.record_observation(
        TelegramMessage(1, "bulk appointments opened", datetime(2026, 8, 1, tzinfo=timezone.utc)),
        SlotSignal(True, "bulk", category="bulk_release"),
    )
    fake = _FakeLlm()
    service = OllamaChatService(store, TrendService(store, _settings(), fake), _settings(), fake)

    # When the exact user question is asked
    response = asyncio.run(service.answer("chat-1", "when was the last bulk appointment"))

    # Then a provider response is returned and persisted
    assert "Source: bdd-provider" in response
    assert store.recent_chat_messages("chat-1", 4)[0]["content"] == "when was the last bulk appointment"


def test_bdd_legacy_signal_storage_does_not_crash(tmp_path):
    # Given a store receiving a legacy-shaped signal without OCR attributes
    store = AlertStore(tmp_path / "state.sqlite3")
    legacy_signal = type("LegacySignal", (), {
        "matched": False, "reason": "legacy", "silent": False,
        "available_state": None, "category": "unknown", "locations": (), "visa_terms": (),
    })()

    # When the observation is saved, Then it succeeds
    store.record_observation(TelegramMessage(1, "legacy", datetime.now(timezone.utc)), legacy_signal)
    assert store.conn.execute("select count(*) from observed_messages").fetchone()[0] == 1


class _FakeLlm:
    enabled = True

    def complete(self, messages, *, max_tokens, temperature):
        return LlmResponse("Bulk release history is available in the trusted report.", "bdd-provider")
