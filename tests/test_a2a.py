from __future__ import annotations

import asyncio
import json
import pytest

from appointment_notifier.a2a import (
    A2AJsonRpcDispatcher,
    A2ATaskEngine,
    A2ATaskStore,
    Artifact,
    Message,
    StreamResponse,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
    get_agent_card,
)
from appointment_notifier.bot_listener import TelegramBotCommandListener
from appointment_notifier.config import TelegramAlertSettings
from appointment_notifier.store import AlertStore


def test_a2a_models_roundtrip():
    part = TextPart(text="Hello world")
    msg = Message(role="user", parts=[part], context_id="ctx-1")
    msg_dict = msg.to_dict()
    restored_msg = Message.from_dict(msg_dict)
    assert restored_msg.role == "user"
    assert restored_msg.get_text() == "Hello world"
    assert restored_msg.context_id == "ctx-1"

    status = TaskStatus(state=TaskState.WORKING, message="Working hard", details={"progress": 50})
    art = Artifact(artifact_id="art-1", name="Data", mime_type="application/json", data={"count": 10})
    task = Task(id="t-1", context_id="ctx-1", status=status, history=[msg], artifacts=[art])
    task_dict = task.to_dict()
    assert task_dict["id"] == "t-1"
    assert task_dict["status"]["state"] == "TASK_STATE_WORKING"
    assert task_dict["artifacts"][0]["artifactId"] == "art-1"

    restored_task = Task.from_dict(task_dict)
    assert restored_task.id == "t-1"
    assert restored_task.status.state == TaskState.WORKING
    assert len(restored_task.history) == 1
    assert len(restored_task.artifacts) == 1


def test_a2a_task_store(tmp_path):
    store = AlertStore(tmp_path / "test.sqlite3")
    a2a_store = store.a2a_task_store

    msg = Message.from_text("Find multi-agent startups", role="user", context_id="chat-1")
    task = a2a_store.create_task(task_id="task-123", context_id="chat-1", initial_message=msg, metadata={"domain": "talos"})

    assert task.id == "task-123"
    assert task.status.state == TaskState.SUBMITTED
    assert len(task.history) == 1

    # Update status
    updated_status = a2a_store.update_status("task-123", TaskState.WORKING, message="Searching vector db")
    assert updated_status.state == TaskState.WORKING

    # Append message
    agent_msg = Message.from_text("Found 3 matches", role="agent", context_id="chat-1")
    a2a_store.append_message("task-123", agent_msg)

    # Add artifact
    art = Artifact(artifact_id="match-1", name="Matches", mime_type="application/json", data=[{"name": "AgentMesh"}])
    a2a_store.add_artifact("task-123", art)

    # Retrieve task
    fetched = a2a_store.get_task("task-123")
    assert fetched is not None
    assert fetched.status.state == TaskState.WORKING
    assert len(fetched.history) == 2
    assert len(fetched.artifacts) == 1
    assert fetched.artifacts[0].name == "Matches"

    # List tasks
    tasks = a2a_store.list_tasks(context_id="chat-1")
    assert len(tasks) == 1
    assert tasks[0].id == "task-123"


@pytest.mark.asyncio
async def test_a2a_engine_immediate(tmp_path):
    store = AlertStore(tmp_path / "test.sqlite3")
    engine = A2ATaskEngine(store.a2a_task_store)

    async def mock_handler(task, emit_status, is_cancelled):
        await emit_status("Step 1 done")
        art = Artifact(artifact_id="art-1", name="Res", mime_type="text/plain", data="result data")
        return "Immediate output", [art]

    user_msg = Message.from_text("Run immediate", role="user", context_id="c1")
    final_task, final_msg = await engine.execute_immediate(
        context_id="c1",
        message=user_msg,
        handler=mock_handler,
    )

    assert final_task.status.state == TaskState.COMPLETED
    assert final_msg.get_text() == "Immediate output"
    assert len(final_task.artifacts) == 1


@pytest.mark.asyncio
async def test_a2a_engine_submit_subscribe_and_wait(tmp_path):
    store = AlertStore(tmp_path / "test.sqlite3")
    engine = A2ATaskEngine(store.a2a_task_store)

    async def async_handler(task, emit_status, is_cancelled):
        await emit_status("Processing in background...")
        await asyncio.sleep(0.05)
        await emit_status("Almost done...")
        art = Artifact(artifact_id="art-async", name="AsyncRes", mime_type="text/plain", data="ok")
        return "Async completed", [art]

    user_msg = Message.from_text("Run async", role="user", context_id="c2")
    task = await engine.submit_task(
        context_id="c2",
        message=user_msg,
        handler=async_handler,
    )

    events: list[StreamResponse] = []
    async for resp in engine.subscribe_task(task.id):
        events.append(resp)

    assert any(e.status_update and "Processing" in (e.status_update.status.message or "") for e in events)
    assert any(e.artifact_update and e.artifact_update.artifact.artifact_id == "art-async" for e in events)
    assert any(e.message and e.message.get_text() == "Async completed" for e in events)

    # Test wait_task on completed task
    completed = await engine.wait_task(task.id, timeout=2.0)
    assert completed is not None
    assert completed.status.state == TaskState.COMPLETED


@pytest.mark.asyncio
async def test_a2a_engine_cancel(tmp_path):
    store = AlertStore(tmp_path / "test.sqlite3")
    engine = A2ATaskEngine(store.a2a_task_store)

    async def long_handler(task, emit_status, is_cancelled):
        await emit_status("Starting long work...")
        for _ in range(50):
            if is_cancelled():
                return "Cancelled early", []
            await asyncio.sleep(0.05)
        return "Finished", []

    user_msg = Message.from_text("Long task", role="user", context_id="c3")
    task = await engine.submit_task(context_id="c3", message=user_msg, handler=long_handler)

    await asyncio.sleep(0.02)
    cancelled = engine.cancel_task(task.id, reason="User stopped task")
    assert cancelled is True

    final_task = engine.get_task(task.id)
    assert final_task is not None
    assert final_task.status.state == TaskState.CANCELED


def test_agent_card_metadata():
    card = get_agent_card()
    assert card["name"] == "Talos Scout & AI Founders Lounge Agent"
    assert card["version"] == "1.0.0"
    assert "did:talos:agent:scout-v1" in card["did"]
    assert "immediate" in card["supportedTaskModes"]
    assert "streaming" in card["supportedTaskModes"]
    assert "wait" in card["supportedTaskModes"]
    assert len(card["skills"]) >= 3
    assert len(card["protocols"]) >= 2


@pytest.mark.asyncio
async def test_a2a_jsonrpc_dispatcher(tmp_path):
    store = AlertStore(tmp_path / "test.sqlite3")
    engine = A2ATaskEngine(store.a2a_task_store)

    async def rpc_handler(task, emit_status, is_cancelled):
        return "RPC output", []

    dispatcher = A2AJsonRpcDispatcher(engine, default_handler=rpc_handler)

    # 1. GetExtendedAgentCard
    res = await dispatcher.dispatch({"jsonrpc": "2.0", "id": 1, "method": "GetExtendedAgentCard"})
    assert res["id"] == 1
    assert res["result"]["name"] == "Talos Scout & AI Founders Lounge Agent"

    # 2. SendMessage (Immediate)
    send_imm = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "SendMessage",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"text": "Hello RPC"}],
                "contextId": "rpc-ctx",
            },
            "configuration": {"returnImmediately": False},
        },
    }
    imm_res = await dispatcher.dispatch(send_imm)
    assert imm_res["id"] == 2
    assert imm_res["result"]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert imm_res["result"]["message"]["parts"][0]["text"] == "RPC output"
    task_id = imm_res["result"]["task"]["id"]

    # 3. GetTask
    get_res = await dispatcher.dispatch({
        "jsonrpc": "2.0",
        "id": 3,
        "method": "GetTask",
        "params": {"taskId": task_id},
    })
    assert get_res["result"]["task"]["id"] == task_id

    # 4. ListTasks
    list_res = await dispatcher.dispatch({
        "jsonrpc": "2.0",
        "id": 4,
        "method": "ListTasks",
        "params": {"contextId": "rpc-ctx"},
    })
    assert len(list_res["result"]["tasks"]) >= 1

    # 5. Invalid method error
    err_res = await dispatcher.dispatch({"jsonrpc": "2.0", "id": 5, "method": "InvalidMethod"})
    assert err_res["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_bot_listener_a2a_commands(tmp_path):
    store = AlertStore(tmp_path / "test.sqlite3")
    settings = TelegramAlertSettings(
        enabled=True,
        bot_token="test-token",
        chat_ids=("1422400397",),
        allowed_usernames=("testuser",),
        owner_chat_ids=("1422400397",),
        owner_usernames=(),
    )
    listener = TelegramBotCommandListener(settings, store)
    sent = {}
    listener._api_json = lambda method, params: sent.update(method=method, params=params) or {}

    # 1. /agentcard
    await listener._handle_update({
        "message": {"chat": {"id": 1422400397}, "from": {"username": "testuser"}, "text": "/agentcard"}
    })
    assert "Talos Scout A2A Agent Card" in sent["params"]["text"]
    assert "did:talos:agent:scout-v1" in sent["params"]["text"]

    # 2. /a2a overview
    await listener._handle_update({
        "message": {"chat": {"id": 1422400397}, "from": {"username": "testuser"}, "text": "/a2a"}
    })
    assert "Talos Protocol — A2A v1 Task Execution" in sent["params"]["text"]

    # 3. /tasks when empty
    await listener._handle_update({
        "message": {"chat": {"id": 1422400397}, "from": {"username": "testuser"}, "text": "/tasks"}
    })
    assert "No A2A tasks found" in sent["params"]["text"]

    # 4. /rpc
    rpc_req = json.dumps({"jsonrpc": "2.0", "id": 99, "method": "GetExtendedAgentCard"})
    await listener._handle_update({
        "message": {"chat": {"id": 1422400397}, "from": {"username": "testuser"}, "text": f"/rpc {rpc_req}"}
    })
    assert "jsonrpc" in sent["params"]["text"]
    assert "Talos Scout & AI Founders Lounge Agent" in sent["params"]["text"]
