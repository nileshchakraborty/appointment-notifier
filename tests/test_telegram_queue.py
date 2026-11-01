import asyncio
import urllib.error
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from appointment_notifier.app import AppointmentNotifierApp
from appointment_notifier.config import KafkaQueueSettings, TelegramAlertSettings
from appointment_notifier.models import Alert, TelegramMessage
from appointment_notifier.notifiers import CompositeNotifier, TelegramBotNotifier
from appointment_notifier.parser import VisaSlotParser
from appointment_notifier.store import AlertStore
from appointment_notifier.telegram_queue import process_telegram_job


def _alert() -> Alert:
    return Alert("Slot report", "H1B slots available", "source", 42, datetime.now(timezone.utc))


def test_outbox_keeps_recipient_jobs_separate_and_deduplicated(tmp_path) -> None:
    store = AlertStore(tmp_path / "state.sqlite3")
    message = TelegramMessage(42, "H1B slots available", datetime.now(timezone.utc), source_chat_id="channel")
    assert store.enqueue_telegram_alert(message, _alert(), {"100", "200"}) == 2
    assert store.enqueue_telegram_alert(message, _alert(), {"100", "200"}) == 0
    assert store.telegram_job_counts() == {"pending": 2}
    assert len(store.due_telegram_jobs()) == 2


def test_source_cursor_never_moves_backwards(tmp_path) -> None:
    store = AlertStore(tmp_path / "state.sqlite3")
    store.advance_source_cursor("channel", 42)
    store.advance_source_cursor("channel", 41)
    assert store.get_state("source_cursor:channel") == "42"


def test_transient_failure_retries_then_delivers_without_realerting(tmp_path) -> None:
    store = AlertStore(tmp_path / "state.sqlite3")
    message = TelegramMessage(42, "H1B slots available", datetime.now(timezone.utc), source_chat_id="channel")
    store.enqueue_telegram_alert(message, _alert(), {"100"})
    job_id = store.due_telegram_jobs()[0]
    calls = []

    class Flaky:
        def send_to(self, chat_id, alert):
            calls.append(chat_id)
            if len(calls) == 1:
                raise urllib.error.URLError("offline")

    now = datetime.now(timezone.utc)
    notifier = Flaky()
    assert process_telegram_job(store, notifier, job_id, now=now) == "retry"
    assert process_telegram_job(store, notifier, job_id, now=now + timedelta(seconds=1)) == "waiting"
    assert process_telegram_job(store, notifier, job_id, now=now + timedelta(seconds=3)) == "delivered"
    assert process_telegram_job(store, notifier, job_id, now=now + timedelta(seconds=4)) == "delivered"
    assert calls == ["100", "100"]
    assert store.telegram_job_counts() == {"delivered": 1}


def test_permanent_failure_only_ends_its_recipient_job(tmp_path) -> None:
    store = AlertStore(tmp_path / "state.sqlite3")
    message = TelegramMessage(42, "H1B slots available", datetime.now(timezone.utc), source_chat_id="channel")
    store.enqueue_telegram_alert(message, _alert(), {"100", "200"})
    jobs = {store.get_telegram_job(job_id)["chat_id"]: job_id for job_id in store.due_telegram_jobs()}

    class Sender:
        def send_to(self, chat_id, alert):
            if chat_id == "100":
                raise urllib.error.HTTPError("https://example.invalid", 403, "Forbidden", {}, None)

    assert process_telegram_job(store, Sender(), jobs["100"]) == "dead"
    assert process_telegram_job(store, Sender(), jobs["200"]) == "delivered"
    assert store.telegram_job_counts() == {"dead": 1, "delivered": 1}


def test_queue_path_persists_jobs_before_any_telegram_send(tmp_path) -> None:
    store = AlertStore(tmp_path / "state.sqlite3")
    store.ensure_bot_user(chat_id="200")
    alert_settings = TelegramAlertSettings(
        enabled=True, bot_token="test-token", chat_ids=("100",),
        allowed_usernames=(), owner_chat_ids=(), owner_usernames=(),
    )
    settings = SimpleNamespace(
        telegram=SimpleNamespace(channel="channel"),
        telegram_alert=alert_settings,
        kafka_queue=KafkaQueueSettings(enabled=True),
        dry_run=False,
    )
    notifier = CompositeNotifier([TelegramBotNotifier(alert_settings, store=store)])
    app = AppointmentNotifierApp(settings, VisaSlotParser(), store, notifier)
    message = TelegramMessage(42, "H1B slots available in Hyderabad", datetime.now(timezone.utc), source_chat_id="channel")

    asyncio.run(app.handle_message(message))
    asyncio.run(app.handle_message(message))

    assert store.telegram_job_counts() == {"pending": 2}
    assert store.has_alert(message)
    assert store.get_state("source_cursor:channel") == "42"
