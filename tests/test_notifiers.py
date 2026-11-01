import json
import io
from concurrent.futures import ThreadPoolExecutor
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from appointment_notifier.config import TelegramAlertSettings, WhatsAppSettings
from appointment_notifier.models import Alert
from appointment_notifier.notifiers import CompositeNotifier, Notifier, OpenWaWhatsAppNotifier, TelegramBotNotifier, _format_openwa_chat_id
from appointment_notifier.store import AlertStore


def _settings(**overrides) -> WhatsAppSettings:
    values = {
        "enabled": True,
        "provider": "openwa",
        "account_sid": "",
        "auth_token": "",
        "sender": "",
        "recipients": ("whatsapp:+15183317627",),
        "openwa_url": "http://host.docker.internal:8081",
        "openwa_api_key": "test-key",
    }
    values.update(overrides)
    return WhatsAppSettings(**values)


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return b'{"success":true,"data":"message-id"}'


def test_format_openwa_chat_id() -> None:
    assert _format_openwa_chat_id("whatsapp:+1 (518) 331-7627") == "15183317627@c.us"
    assert _format_openwa_chat_id("15183317627@c.us") == "15183317627@c.us"
    assert _format_openwa_chat_id("12345-67890@g.us") == "12345-67890@g.us"


def test_format_openwa_chat_id_rejects_empty_number() -> None:
    with pytest.raises(ValueError, match="Invalid OpenWA recipient"):
        _format_openwa_chat_id("whatsapp:")


def test_openwa_notifier_sends_authenticated_request() -> None:
    notifier = OpenWaWhatsAppNotifier(_settings())
    alert = Alert(
        title="Appointment notifier test",
        body="A visa slot is available.",
        source="test",
        message_id=1,
        sent_at=datetime.now(timezone.utc),
        silent=False,
    )

    with patch("urllib.request.urlopen", return_value=_Response()) as urlopen:
        notifier.send(alert)

    request = urlopen.call_args.args[0]
    assert request.full_url == "http://host.docker.internal:8081/api/messages/sendText"
    assert request.get_header("X-api-key") == "test-key"
    assert json.loads(request.data) == {
        "args": {
            "to": "15183317627@c.us",
            "content": "Appointment notifier test\nA visa slot is available.",
        }
    }


def test_openwa_notifier_requires_api_key() -> None:
    with pytest.raises(ValueError, match="OPENWA_API_KEY"):
        OpenWaWhatsAppNotifier(_settings(openwa_api_key=""))


def test_composite_isolates_failed_channel_and_delivers_healthy_channel() -> None:
    calls = []

    class Broken(Notifier):
        def send(self, alert):
            calls.append("broken")
            raise OSError("network down")

    class Healthy(Notifier):
        def send(self, alert):
            calls.append("healthy")

    CompositeNotifier([Broken(), Healthy()]).send(
        Alert("title", "body", "source", 1, datetime.now(timezone.utc))
    )
    assert calls == ["broken", "healthy"]


def test_telegram_alert_reaches_configured_and_new_allowed_chats_once(tmp_path) -> None:
    store = AlertStore(tmp_path / "state.sqlite3")
    store.ensure_bot_user(chat_id="100", is_owner=True)
    store.ensure_bot_user(username="friend")
    store.bind_bot_user_chat(chat_id="200", username="friend")
    store.ensure_bot_user(username="not_started")
    settings = TelegramAlertSettings(
        enabled=True, bot_token="test-token", chat_ids=("100",),
        allowed_usernames=(), owner_chat_ids=("100",), owner_usernames=(),
    )
    recipients = []

    def fake_urlopen(request, timeout):
        recipients.append(urllib.parse.parse_qs(request.data.decode())["chat_id"][0])
        return _Response()

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        TelegramBotNotifier(settings, store=store).send(
            Alert("Status", "Current: NOT AVAILABLE", "test", 1, datetime.now(timezone.utc))
        )

    assert recipients == ["100", "200"]


def test_telegram_alert_reads_allowed_chats_from_delivery_thread(tmp_path) -> None:
    store = AlertStore(tmp_path / "state.sqlite3")
    store.ensure_bot_user(chat_id="200")
    settings = TelegramAlertSettings(
        enabled=True, bot_token="test-token", chat_ids=("100",),
        allowed_usernames=(), owner_chat_ids=(), owner_usernames=(),
    )
    recipients = []

    def fake_urlopen(request, timeout):
        recipients.append(urllib.parse.parse_qs(request.data.decode())["chat_id"][0])
        return _Response()

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(
                TelegramBotNotifier(settings, store=store).send,
                Alert("Status", "Slot available", "test", 1, datetime.now(timezone.utc)),
            ).result()

    assert recipients == ["100", "200"]


def test_deactivated_recipient_does_not_block_other_allowed_user(tmp_path) -> None:
    store = AlertStore(tmp_path / "state.sqlite3")
    store.ensure_bot_user(chat_id="100")
    store.ensure_bot_user(chat_id="200")
    settings = TelegramAlertSettings(
        enabled=True, bot_token="test-token", chat_ids=("100",),
        allowed_usernames=(), owner_chat_ids=(), owner_usernames=(),
    )
    attempted = []

    def fake_urlopen(request, timeout):
        chat_id = urllib.parse.parse_qs(request.data.decode())["chat_id"][0]
        attempted.append(chat_id)
        if chat_id == "100":
            raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, io.BytesIO(b'{"description":"user is deactivated"}'))
        return _Response()

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        TelegramBotNotifier(settings, store=store).send(
            Alert("Status", "Current: NOT AVAILABLE", "test", 1, datetime.now(timezone.utc))
        )
    assert attempted == ["100", "200"]
