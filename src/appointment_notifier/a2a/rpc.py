from __future__ import annotations

import logging
from typing import Any

from .agent_card import get_agent_card
from .engine import A2ATaskEngine, TaskExecutionHandler
from .models import Message, TaskState

LOGGER = logging.getLogger(__name__)

ALLOWED_A2A_METHODS = {
    "SendMessage",
    "SendStreamingMessage",
    "GetTask",
    "SubscribeToTask",
    "CancelTask",
    "ListTasks",
    "GetExtendedAgentCard",
    "agent/getAuthenticatedExtendedCard",
    "agent/card",
    "agent.card",
    "tasks/send",
    "tasks.send",
    "tasks/get",
    "tasks.get",
    "tasks/cancel",
    "tasks.cancel",
    "tasks/list",
    "tasks.list",
}


class A2AJsonRpcDispatcher:
    def __init__(self, engine: A2ATaskEngine, default_handler: TaskExecutionHandler) -> None:
        self.engine = engine
        self.default_handler = default_handler

    async def dispatch(self, payload: dict[str, Any]) -> dict[str, Any]:
        req_id = payload.get("id")

        if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32600, "message": "Invalid Request", "data": {"details": "jsonrpc must be '2.0'"}},
            }

        method = payload.get("method")
        if not isinstance(method, str) or method not in ALLOWED_A2A_METHODS:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": "Method not found", "data": {"method": method}},
            }

        params = payload.get("params") or {}
        if not isinstance(params, dict):
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32602, "message": "Invalid params", "data": {"details": "params must be object"}},
            }

        try:
            if method in {"GetExtendedAgentCard", "agent/getAuthenticatedExtendedCard", "agent/card", "agent.card"}:
                card = get_agent_card()
                return {"jsonrpc": "2.0", "id": req_id, "result": card}

            elif method in {"SendMessage", "tasks/send", "tasks.send"}:
                msg_data = params.get("message")
                if not msg_data or not isinstance(msg_data, dict):
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32602, "message": "Invalid params", "data": {"details": "message object required"}},
                    }
                message = Message.from_dict(msg_data)
                config = params.get("configuration") or {}
                return_immediately = bool(config.get("returnImmediately", False))
                context_id = message.context_id or "default"

                if return_immediately:
                    task = await self.engine.submit_task(
                        context_id=context_id,
                        message=message,
                        handler=self.default_handler,
                        metadata=params.get("metadata"),
                    )
                    return {"jsonrpc": "2.0", "id": req_id, "result": {"task": task.to_dict()}}
                else:
                    task, agent_msg = await self.engine.execute_immediate(
                        context_id=context_id,
                        message=message,
                        handler=self.default_handler,
                        metadata=params.get("metadata"),
                    )
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "task": task.to_dict(),
                            "message": agent_msg.to_dict(),
                        },
                    }

            elif method in {"GetTask", "tasks/get", "tasks.get"}:
                task_id = params.get("taskId") or params.get("id")
                if not task_id:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32602, "message": "Invalid params", "data": {"details": "taskId required"}},
                    }
                task = self.engine.get_task(str(task_id))
                if not task:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32000, "message": "Task not found", "data": {"taskId": task_id}},
                    }
                return {"jsonrpc": "2.0", "id": req_id, "result": {"task": task.to_dict()}}

            elif method in {"CancelTask", "tasks/cancel", "tasks.cancel"}:
                task_id = params.get("taskId") or params.get("id")
                if not task_id:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32602, "message": "Invalid params", "data": {"details": "taskId required"}},
                    }
                ok = self.engine.cancel_task(str(task_id), reason=params.get("reason", "Canceled via A2A CancelTask"))
                return {"jsonrpc": "2.0", "id": req_id, "result": {"taskId": task_id, "canceled": ok}}

            elif method in {"ListTasks", "tasks/list", "tasks.list"}:
                context_id = params.get("contextId")
                state_str = params.get("state")
                state = TaskState.from_str(state_str) if state_str else None
                limit = int(params.get("limit", 20))
                tasks = self.engine.list_tasks(context_id=context_id, state=state, limit=limit)
                return {"jsonrpc": "2.0", "id": req_id, "result": {"tasks": [t.to_dict() for t in tasks]}}

            elif method == "SubscribeToTask":
                task_id = params.get("taskId")
                if not task_id:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32602, "message": "Invalid params", "data": {"details": "taskId required"}},
                    }
                task = await self.engine.wait_task(str(task_id), timeout=params.get("timeout", 180.0))
                if not task:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32000, "message": "Task not found or timed out", "data": {"taskId": task_id}},
                    }
                return {"jsonrpc": "2.0", "id": req_id, "result": {"task": task.to_dict()}}

            elif method == "CancelTask":
                task_id = params.get("taskId")
                if not task_id:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32602, "message": "Invalid params", "data": {"details": "taskId required"}},
                    }
                success = self.engine.cancel_task(str(task_id), reason=str(params.get("reason", "Canceled via RPC")))
                return {"jsonrpc": "2.0", "id": req_id, "result": {"canceled": success, "taskId": task_id}}

            elif method == "ListTasks":
                ctx_id = params.get("contextId")
                state_str = params.get("status")
                state = TaskState.from_str(state_str) if state_str else None
                limit = int(params.get("pageSize", 20))
                tasks = self.engine.list_tasks(context_id=ctx_id, state=state, limit=limit)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "tasks": [t.to_dict() for t in tasks],
                        "hasMore": False,
                    },
                }

            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": "Method not implemented", "data": {"method": method}},
            }

        except Exception as exc:
            LOGGER.exception("A2A RPC dispatch failed for method %s: %s", method, exc)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32603, "message": "Internal error", "data": {"details": str(exc)}},
            }
