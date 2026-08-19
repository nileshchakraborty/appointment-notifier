import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from appointment_notifier.config import WhatsAppSettings
from appointment_notifier.models import Alert
from appointment_notifier.notifiers import OpenWaWhatsAppNotifier, _format_openwa_chat_id


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
