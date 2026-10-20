from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, AsyncGenerator, Awaitable, Callable

from .models import (
    Artifact,
    Message,
    StreamResponse,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    now_utc_iso,
)
from .store import A2ATaskStore

LOGGER = logging.getLogger(__name__)

# Handler signature: takes (task, emit_event_callback, is_cancelled_check) -> (final_response_text, list_of_artifacts)
TaskExecutionHandler = Callable[
    [Task, Callable[[str, dict[str, Any] | None], Awaitable[None]], Callable[[], bool]],
    Awaitable[tuple[str, list[Artifact]]],
]


class A2ATaskEngine:
    def __init__(self, store: A2ATaskStore) -> None:
        self.store = store
        self._subscribers: dict[str, list[asyncio.Queue[StreamResponse]]] = {}
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._completion_events: dict[str, asyncio.Event] = {}
        self._active_async_tasks: dict[str, asyncio.Task[Any]] = {}

    def _ensure_events(self, task_id: str) -> None:
        if task_id not in self._cancel_events:
            self._cancel_events[task_id] = asyncio.Event()
        if task_id not in self._completion_events:
            self._completion_events[task_id] = asyncio.Event()
        if task_id not in self._subscribers:
            self._subscribers[task_id] = []

    async def _emit_stream_response(self, task_id: str, response: StreamResponse) -> None:
        queues = self._subscribers.get(task_id, [])
        for q in list(queues):
            try:
                q.put_nowait(response)
            except Exception as exc:
                LOGGER.debug("Failed to deliver event to subscriber for task %s: %s", task_id, exc)

    async def execute_immediate(
        self,
        context_id: str,
        message: Message,
        handler: TaskExecutionHandler,
        metadata: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> tuple[Task, Message]:
        """Synchronous A2A Task Immediate execution."""
        task_id = task_id or str(uuid.uuid4())
        self._ensure_events(task_id)

        task = self.store.create_task(
            task_id=task_id,
            context_id=context_id,
            initial_message=message,
            metadata=metadata,
        )

        async def emit_status(step_msg: str, details: dict[str, Any] | None = None) -> None:
            new_status = self.store.update_status(task_id, TaskState.WORKING, message=step_msg, details=details)
            event = TaskStatusUpdateEvent(task_id=task_id, context_id=context_id, status=new_status)
            await self._emit_stream_response(task_id, StreamResponse(status_update=event))

        await emit_status("Task started")

        def check_cancelled() -> bool:
            return self._cancel_events[task_id].is_set()

        try:
            output_text, artifacts = await handler(task, emit_status, check_cancelled)
            if check_cancelled():
                term_status = self.store.update_status(task_id, TaskState.CANCELED, message="Task canceled by user")
                term_event = TaskStatusUpdateEvent(task_id=task_id, context_id=context_id, status=term_status)
                await self._emit_stream_response(task_id, StreamResponse(status_update=term_event))
                self._completion_events[task_id].set()
                updated_task = self.store.get_task(task_id) or task
                return updated_task, Message.from_text("Task was canceled", role="agent", context_id=context_id)

            for art in artifacts:
                self.store.add_artifact(task_id, art)
                art_event = TaskArtifactUpdateEvent(task_id=task_id, context_id=context_id, artifact=art)
                await self._emit_stream_response(task_id, StreamResponse(artifact_update=art_event))

            agent_message = Message.from_text(output_text, role="agent", context_id=context_id)
            self.store.append_message(task_id, agent_message)
            await self._emit_stream_response(task_id, StreamResponse(message=agent_message))

            final_status = self.store.update_status(task_id, TaskState.COMPLETED, message="Task completed successfully")
            final_task = self.store.get_task(task_id) or task
            final_event = TaskStatusUpdateEvent(task_id=task_id, context_id=context_id, status=final_status)
            await self._emit_stream_response(task_id, StreamResponse(status_update=final_event, task=final_task))
            self._completion_events[task_id].set()

            return final_task, agent_message
        except Exception as exc:
            LOGGER.exception("Task %s execution failed: %s", task_id, exc)
            fail_status = self.store.update_status(task_id, TaskState.FAILED, message=str(exc))
            fail_event = TaskStatusUpdateEvent(task_id=task_id, context_id=context_id, status=fail_status)
            await self._emit_stream_response(task_id, StreamResponse(status_update=fail_event))
            self._completion_events[task_id].set()
            raise

    async def submit_task(
        self,
        context_id: str,
        message: Message,
        handler: TaskExecutionHandler,
        metadata: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> Task:
        """Asynchronous A2A task submission: creates task record and starts background runner."""
        task_id = task_id or str(uuid.uuid4())
        self._ensure_events(task_id)

        task = self.store.create_task(
            task_id=task_id,
            context_id=context_id,
            initial_message=message,
            metadata=metadata,
        )

        bg_coro = self._run_background_task(task, handler)
        bg_task = asyncio.create_task(bg_coro)
        self._active_async_tasks[task_id] = bg_task

        def cleanup_task(t: asyncio.Task[Any]) -> None:
            self._active_async_tasks.pop(task_id, None)

        bg_task.add_done_callback(cleanup_task)
        return task

    async def _run_background_task(self, task: Task, handler: TaskExecutionHandler) -> None:
        task_id = task.id
        context_id = task.context_id

        async def emit_status(step_msg: str, details: dict[str, Any] | None = None) -> None:
            new_status = self.store.update_status(task_id, TaskState.WORKING, message=step_msg, details=details)
            event = TaskStatusUpdateEvent(task_id=task_id, context_id=context_id, status=new_status)
            await self._emit_stream_response(task_id, StreamResponse(status_update=event))

        await emit_status("Task started")

        def check_cancelled() -> bool:
            return self._cancel_events[task_id].is_set()

        try:
            output_text, artifacts = await handler(task, emit_status, check_cancelled)
            if check_cancelled():
                term_status = self.store.update_status(task_id, TaskState.CANCELED, message="Task canceled by user")
                term_event = TaskStatusUpdateEvent(task_id=task_id, context_id=context_id, status=term_status)
                await self._emit_stream_response(task_id, StreamResponse(status_update=term_event))
                self._completion_events[task_id].set()
                return

            for art in artifacts:
                self.store.add_artifact(task_id, art)
                art_event = TaskArtifactUpdateEvent(task_id=task_id, context_id=context_id, artifact=art)
                await self._emit_stream_response(task_id, StreamResponse(artifact_update=art_event))

            agent_message = Message.from_text(output_text, role="agent", context_id=context_id)
            self.store.append_message(task_id, agent_message)
            await self._emit_stream_response(task_id, StreamResponse(message=agent_message))

            final_status = self.store.update_status(task_id, TaskState.COMPLETED, message="Task completed successfully")
            final_task = self.store.get_task(task_id) or task
            final_event = TaskStatusUpdateEvent(task_id=task_id, context_id=context_id, status=final_status)
            await self._emit_stream_response(task_id, StreamResponse(status_update=final_event, task=final_task))
        except Exception as exc:
            LOGGER.exception("Background task %s failed: %s", task_id, exc)
            fail_status = self.store.update_status(task_id, TaskState.FAILED, message=str(exc))
            fail_event = TaskStatusUpdateEvent(task_id=task_id, context_id=context_id, status=fail_status)
            await self._emit_stream_response(task_id, StreamResponse(status_update=fail_event))
        finally:
            self._completion_events[task_id].set()

    async def subscribe_task(self, task_id: str) -> AsyncGenerator[StreamResponse, None]:
        """A2A Task Stream: Yields StreamResponse chunks until task transitions to terminal state."""
        self._ensure_events(task_id)
        queue: asyncio.Queue[StreamResponse] = asyncio.Queue()
        self._subscribers[task_id].append(queue)

        task = self.store.get_task(task_id)
        if task:
            # Emit current state as first event
            init_event = TaskStatusUpdateEvent(task_id=task.id, context_id=task.context_id, status=task.status)
            yield StreamResponse(status_update=init_event)
            if task.status.state in {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELED}:
                yield StreamResponse(task=task)
                self._subscribers[task_id].remove(queue)
                return

        try:
            while True:
                resp = await queue.get()
                yield resp
                # Check if terminal
                if resp.task and resp.task.status.state in {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELED}:
                    break
                if resp.status_update and resp.status_update.status.state in {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELED}:
                    break
        finally:
            if queue in self._subscribers.get(task_id, []):
                self._subscribers[task_id].remove(queue)

    async def wait_task(self, task_id: str, timeout: float = 180.0) -> Task | None:
        """A2A Task Wait: Awaits task completion and returns updated Task object."""
        self._ensure_events(task_id)
        task = self.store.get_task(task_id)
        if task and task.status.state in {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELED}:
            return task

        await asyncio.wait_for(self._completion_events[task_id].wait(), timeout=timeout)
        return self.store.get_task(task_id)

    def cancel_task(self, task_id: str, reason: str = "Canceled by user") -> bool:
        """A2A Task Cancel: Signals cancellation to running task."""
        self._ensure_events(task_id)
        task = self.store.get_task(task_id)
        if not task:
            return False
        if task.status.state in {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELED}:
            return False

        self._cancel_events[task_id].set()
        bg = self._active_async_tasks.get(task_id)
        if bg and not bg.done():
            bg.cancel()

        self.store.update_status(task_id, TaskState.CANCELED, message=reason)
        return True

    def get_task(self, task_id: str) -> Task | None:
        return self.store.get_task(task_id)

    def list_tasks(self, context_id: str | None = None, state: TaskState | None = None, limit: int = 20) -> list[Task]:
        return self.store.list_tasks(context_id=context_id, state=state, limit=limit)
