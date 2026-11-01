import asyncio

from appointment_notifier.bot_listener import TelegramBotCommandListener
from appointment_notifier.a2a import Message, Task, TaskState, TaskStatus
from appointment_notifier.config import TelegramAlertSettings
from appointment_notifier.store import AlertStore


def _listener(tmp_path, scope="combined"):
    store = AlertStore(tmp_path / "state.sqlite3")
    settings = TelegramAlertSettings(
        enabled=True,
        bot_token="test-token",
        chat_ids=("1422400397",),
        allowed_usernames=("ni301192",),
        owner_chat_ids=("1422400397",),
        owner_usernames=(),
    )
    return TelegramBotCommandListener(settings, store, scope=scope)


def test_visa_bot_redirects_talos_without_running_talos(tmp_path):
    listener = _listener(tmp_path, scope="visa")
    sent = {}
    listener._api_json = lambda method, params: sent.update(method=method, params=params) or {}

    asyncio.run(listener._handle_update({"message": {
        "chat": {"id": 1422400397}, "text": "/talos find startups",
    }}))

    assert sent["method"] == "sendMessage"
    assert "@talosprotocolaibot" in sent["params"]["text"]


def test_talos_bot_redirects_visa_status(tmp_path):
    listener = _listener(tmp_path, scope="talos")
    sent = {}
    listener._api_json = lambda method, params: sent.update(method=method, params=params) or {}

    asyncio.run(listener._handle_update({"message": {
        "chat": {"id": 1422400397}, "text": "/current",
    }}))

    assert sent["method"] == "sendMessage"
    assert "@usvisaavaibalitynotifierinbot" in sent["params"]["text"]


def test_talos_bot_routes_plain_text_to_talos(tmp_path):
    listener = _listener(tmp_path, scope="talos")
    calls = []

    async def dispatch(chat_id, question, domain):
        calls.append((chat_id, question, domain))

    listener._dispatch_a2a_streaming = dispatch
    asyncio.run(listener._handle_update({"message": {
        "chat": {"id": 1422400397}, "text": "Which startup should we contact?",
    }}))

    assert calls == [("1422400397", "Which startup should we contact?", "talos")]


def test_current_is_unknown_before_first_channel_observation(tmp_path):
    listener = _listener(tmp_path, scope="visa")
    assert "unknown" in listener._format_current()


def test_failed_reply_does_not_acknowledge_update(tmp_path):
    listener = _listener(tmp_path, scope="visa")
    listener.store.set_state("telegram_bot_update_offset", "4")
    listener._api_json = lambda method, params: {"result": [{"update_id": 4, "message": {"chat": {"id": 1422400397}, "text": "/help"}}]}

    async def fail(_update):
        raise RuntimeError("reply failed")

    listener._handle_update = fail
    try:
        asyncio.run(listener._poll_once())
    except RuntimeError:
        pass
    else:
        assert False, "poll should surface the reply failure"
    assert listener.store.get_state("telegram_bot_update_offset") == "4"


def test_completed_task_recovers_result_when_stream_subscription_misses_message(tmp_path):
    listener = _listener(tmp_path, scope="talos")
    task = Task(
        id="task-1", context_id="chat-1",
        status=TaskStatus(state=TaskState.COMPLETED),
        history=[Message.from_text("Who first?"), Message.from_text("Pitch Rajdharma first.", role="agent")],
    )
    assert listener._completed_task_output(task, None) == "Pitch Rajdharma first."


def test_talos_reply_uses_saved_result_when_task_finishes_before_subscription(tmp_path):
    listener = _listener(tmp_path, scope="talos")
    sent = []

    class FakeScout:
        enabled = True

        async def execute_a2a_task(self, _task, _emit_status, _is_cancelled):
            return "Pitch Rajdharma first.", []

    listener.talos_scout = FakeScout()
    listener._api_json = lambda method, params: sent.append((method, params)) or {"result": {"message_id": 10}}
    original_submit = listener.a2a_engine.submit_task

    async def submit_after_completion(*args, **kwargs):
        task = await original_submit(*args, **kwargs)
        await listener.a2a_engine.wait_task(task.id)
        return task

    listener.a2a_engine.submit_task = submit_after_completion
    asyncio.run(listener._dispatch_a2a_streaming("1422400397", "Who first?", domain="talos"))

    assert sent[-1][0] == "editMessageText"
    assert sent[-1][1]["text"] == "Pitch Rajdharma first."


def test_authorizes_numeric_chat_id(tmp_path):
    listener = _listener(tmp_path)

    assert listener._is_authorized({"from": {"username": "someone_else"}}, "1422400397")


def test_authorizes_allowed_username_case_insensitively(tmp_path):
    listener = _listener(tmp_path)

    assert listener._is_authorized({"from": {"username": "NI301192"}}, "999")


def test_allowed_username_is_bound_after_private_start(tmp_path):
    listener = _listener(tmp_path, scope="visa")
    listener._api_json = lambda method, params: {"ok": True}

    asyncio.run(listener._handle_update({"message": {
        "chat": {"id": 999, "type": "private"},
        "from": {"username": "NI301192"},
        "text": "/start",
    }}))

    user = next(user for user in listener.store.list_bot_users() if user["username"] == "ni301192")
    assert user["chat_id"] == "999"
    assert not user["is_owner"]


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


def test_help_command_lists_supported_commands(tmp_path):
    listener = _listener(tmp_path)
    sent = {}
    listener._api_json = lambda method, params: sent.update(method=method, params=params) or {}

    asyncio.run(
        listener._handle_update(
            {
                "message": {
                    "chat": {"id": 1422400397},
                    "from": {"username": "someone_else"},
                    "text": "/help",
                }
            }
        )
    )

    assert sent["method"] == "sendMessage"
    response = sent["params"]["text"]
    assert response.startswith("Commands:")
    for command in (
        "/current",
        "/last",
        "/status",
        "/trend",
        "/ask",
        "/forget",
        "/whoami",
        "/users",
        "/allow",
        "/revoke",
    ):
        assert command in response


def test_status_reports_backend_and_ai_health(tmp_path):
    listener = _listener(tmp_path)

    class FakeLlm:
        enabled = True

        def health(self):
            return {"nvidia": "unavailable (HTTP 401)", "ollama": "connected"}

    class FakeChat:
        llm_client = FakeLlm()

    listener.chat_service = FakeChat()
    listener._api_json = lambda method, params: {"ok": True}

    response = asyncio.run(listener._format_status())

    assert "Database: connected" in response
    assert "Telegram Bot API: connected" in response
    assert "AI nvidia: unavailable (HTTP 401)" in response
    assert "AI ollama: connected" in response


def test_handle_voice_update_routes_to_streaming(tmp_path, monkeypatch):
    listener = _listener(tmp_path)
    sent_messages = []
    listener._api_json = lambda method, params: {"ok": True, "result": {"message_id": 123}}
    listener._download_telegram_file = lambda file_id: (b"fake_audio_bytes", "voice.oga")
    listener._transcribe_audio = lambda audio, filename: "which startups are building agents"

    dispatched = []

    async def fake_dispatch(chat_id, query, domain):
        dispatched.append((chat_id, query, domain))

    monkeypatch.setattr(listener, "_dispatch_a2a_streaming", fake_dispatch)
    monkeypatch.setattr(listener, "_edit_message_text", lambda cid, mid, txt: True)

    update = {
        "update_id": 999,
        "message": {
            "chat": {"id": 1422400397},
            "from": {"username": "ni301192"},
            "voice": {"file_id": "voice_file_123"},
        },
    }

    asyncio.run(listener._handle_update(update))

    assert len(dispatched) == 1
    assert dispatched[0][0] == "1422400397"
    assert dispatched[0][1] == "which startups are building agents"
    assert dispatched[0][2] == "talos"


def test_campaign_command_dispatches_cluster_workflow(tmp_path, monkeypatch):
    listener = _listener(tmp_path)
    sent = []
    listener._api_json = lambda method, params: sent.append((method, params)) or {"ok": True, "result": {"message_id": 456}}
    listener._edit_message_text = lambda cid, mid, txt: True

    async def fake_execute(wfid, focus):
        return {
            "output": "Pitched Talos Protocol to AgentGrid and AerOS.",
            "startups": [{"startup_name": "AgentGrid"}, {"startup_name": "AerOS"}],
            "state": "TASK_STATE_COMPLETED",
        }

    monkeypatch.setattr(listener, "_execute_cluster_campaign", fake_execute)

    update = {
        "update_id": 1000,
        "message": {
            "chat": {"id": 1422400397},
            "from": {"username": "ni301192"},
            "text": "/campaign enterprise agent governance",
        },
    }

    asyncio.run(listener._handle_update(update))

    assert len(sent) >= 1
    assert any("Cluster Swarm Dispatched" in s[1].get("text", "") for s in sent)


