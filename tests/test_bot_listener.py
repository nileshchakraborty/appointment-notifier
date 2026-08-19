import asyncio

from appointment_notifier.bot_listener import TelegramBotCommandListener
from appointment_notifier.config import TelegramAlertSettings
from appointment_notifier.store import AlertStore


def _listener(tmp_path):
    store = AlertStore(tmp_path / "state.sqlite3")
    settings = TelegramAlertSettings(
        enabled=True,
        bot_token="test-token",
        chat_ids=("1422400397",),
        allowed_usernames=("ni301192",),
        owner_chat_ids=("1422400397",),
        owner_usernames=(),
    )
    return TelegramBotCommandListener(settings, store)


def test_authorizes_numeric_chat_id(tmp_path):
    listener = _listener(tmp_path)

    assert listener._is_authorized({"from": {"username": "someone_else"}}, "1422400397")


def test_authorizes_allowed_username_case_insensitively(tmp_path):
    listener = _listener(tmp_path)

    assert listener._is_authorized({"from": {"username": "NI301192"}}, "999")


def test_rejects_unknown_chat_and_username(tmp_path):
    listener = _listener(tmp_path)

    assert not listener._is_authorized({"from": {"username": "someone_else"}}, "999")


def test_owner_can_allow_and_revoke_user(tmp_path):
    listener = _listener(tmp_path)
    owner_message = {"from": {"username": "owner"}}

    response = listener._owner_command(
        owner_message,
        "1422400397",
        lambda: listener._allow_user("/allow @new_friend"),
    )
    assert response == "Allowed @new_friend."
    assert listener._is_authorized({"from": {"username": "new_friend"}}, "999")

    response = listener._owner_command(
        owner_message,
        "1422400397",
        lambda: listener._revoke_user("/revoke @new_friend"),
    )
    assert response == "Revoked @new_friend."
    assert not listener._is_authorized({"from": {"username": "new_friend"}}, "999")


def test_non_owner_cannot_manage_users(tmp_path):
    listener = _listener(tmp_path)

    response = listener._owner_command(
        {"from": {"username": "ni301192"}},
        "999",
        lambda: listener._allow_user("/allow @new_friend"),
    )

    assert response == "Only bot owners can manage users."
    assert not listener._is_authorized({"from": {"username": "new_friend"}}, "999")


def test_seeded_owner_cannot_be_revoked(tmp_path):
    listener = _listener(tmp_path)

    assert listener._revoke_user("/revoke 1422400397") == "No non-owner user was removed."
    assert listener.store.bot_user_is_owner(chat_id="1422400397", username=None)


def test_authorized_normal_text_routes_to_chat_and_replies(tmp_path):
    listener = _listener(tmp_path)
    calls = []
    sent = {}

    class FakeChat:
        enabled = True

        async def answer(self, chat_id, question):
            calls.append((chat_id, question))
            return "Pi-local answer"

    listener.chat_service = FakeChat()
    listener._api_json = lambda method, params: sent.update(method=method, params=params) or {}

    asyncio.run(
        listener._handle_update(
            {
                "message": {
                    "chat": {"id": 1422400397},
                    "from": {"username": "someone_else"},
                    "text": "When are appointments usually posted?",
                }
            }
        )
    )

    assert calls == [("1422400397", "When are appointments usually posted?")]
    assert sent["method"] == "sendMessage"
    assert sent["params"]["text"] == "Pi-local answer"
