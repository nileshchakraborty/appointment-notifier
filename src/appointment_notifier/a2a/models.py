from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


class TaskState(str, enum.Enum):
    SUBMITTED = "TASK_STATE_SUBMITTED"
    WORKING = "TASK_STATE_WORKING"
    COMPLETED = "TASK_STATE_COMPLETED"
    FAILED = "TASK_STATE_FAILED"
    CANCELED = "TASK_STATE_CANCELED"
    INPUT_REQUIRED = "TASK_STATE_INPUT_REQUIRED"

    @classmethod
    def from_str(cls, val: str) -> TaskState:
        normalized = val.strip().upper()
        if not normalized.startswith("TASK_STATE_"):
            normalized = f"TASK_STATE_{normalized}"
        for member in cls:
            if member.value == normalized:
                return member
        return cls.WORKING


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class TaskStatus:
    state: TaskState
    message: str | None = None
    details: dict[str, Any] | None = None
    timestamp: str = field(default_factory=now_utc_iso)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "state": self.state.value,
            "timestamp": self.timestamp,
        }
        if self.message:
            data["message"] = self.message
        if self.details:
            data["details"] = self.details
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskStatus:
        return cls(
            state=TaskState.from_str(str(data.get("state", "TASK_STATE_WORKING"))),
            message=data.get("message"),
            details=data.get("details"),
            timestamp=str(data.get("timestamp") or now_utc_iso()),
        )


@dataclass
class TextPart:
    type: Literal["text"] = "text"
    text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"type": "text", "text": self.text}


@dataclass
class Message:
    role: Literal["user", "agent", "system"]
    parts: list[dict[str, Any]]
    message_id: str | None = None
    context_id: str | None = None
    created_at: str = field(default_factory=now_utc_iso)

    @classmethod
    def from_text(cls, text: str, role: Literal["user", "agent", "system"] = "user", context_id: str | None = None) -> Message:
        return cls(
            role=role,
            parts=[{"type": "text", "text": text}],
            context_id=context_id,
        )

    def get_text(self) -> str:
        texts = []
        for p in self.parts:
            if isinstance(p, dict) and p.get("type") == "text":
                texts.append(str(p.get("text", "")))
            elif isinstance(p, TextPart):
                texts.append(p.text)
        return " ".join(texts).strip()

    def to_dict(self) -> dict[str, Any]:
        parts_data = []
        for p in self.parts:
            if hasattr(p, "to_dict"):
                parts_data.append(p.to_dict())
            elif isinstance(p, dict):
                parts_data.append(p)
        data: dict[str, Any] = {
            "role": self.role,
            "parts": parts_data,
            "createdAt": self.created_at,
        }
        if self.message_id:
            data["messageId"] = self.message_id
        if self.context_id:
            data["contextId"] = self.context_id
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Message:
        return cls(
            role=data.get("role", "user"),
            parts=data.get("parts") or [],
            message_id=data.get("messageId"),
            context_id=data.get("contextId"),
            created_at=str(data.get("createdAt") or now_utc_iso()),
        )


@dataclass
class Artifact:
    artifact_id: str
    name: str
    mime_type: str = "application/json"
    data: Any = None
    created_at: str = field(default_factory=now_utc_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifactId": self.artifact_id,
            "name": self.name,
            "mimeType": self.mime_type,
            "data": self.data,
            "createdAt": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Artifact:
        return cls(
            artifact_id=str(data.get("artifactId") or ""),
            name=str(data.get("name") or "artifact"),
            mime_type=str(data.get("mimeType") or "application/json"),
            data=data.get("data"),
            created_at=str(data.get("createdAt") or now_utc_iso()),
        )


@dataclass
class Task:
    id: str
    context_id: str
    status: TaskStatus
    history: list[Message] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=now_utc_iso)
    updated_at: str = field(default_factory=now_utc_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "contextId": self.context_id,
            "status": self.status.to_dict(),
            "history": [m.to_dict() for m in self.history],
            "artifacts": [a.to_dict() for a in self.artifacts],
            "metadata": self.metadata,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Task:
        status_data = data.get("status") or {}
        return cls(
            id=str(data["id"]),
            context_id=str(data.get("contextId") or "default"),
            status=TaskStatus.from_dict(status_data),
            history=[Message.from_dict(m) for m in data.get("history", [])],
            artifacts=[Artifact.from_dict(a) for a in data.get("artifacts", [])],
            metadata=data.get("metadata") or {},
            created_at=str(data.get("createdAt") or now_utc_iso()),
            updated_at=str(data.get("updatedAt") or now_utc_iso()),
        )


@dataclass
class TaskStatusUpdateEvent:
    task_id: str
    context_id: str
    status: TaskStatus
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "taskId": self.task_id,
            "contextId": self.context_id,
            "status": self.status.to_dict(),
            "metadata": self.metadata,
        }


@dataclass
class TaskArtifactUpdateEvent:
    task_id: str
    context_id: str
    artifact: Artifact

    def to_dict(self) -> dict[str, Any]:
        return {
            "taskId": self.task_id,
            "contextId": self.context_id,
            "artifact": self.artifact.to_dict(),
        }


@dataclass
class StreamResponse:
    task: Task | None = None
    message: Message | None = None
    status_update: TaskStatusUpdateEvent | None = None
    artifact_update: TaskArtifactUpdateEvent | None = None

    def to_dict(self) -> dict[str, Any]:
        if self.status_update:
            return {"statusUpdate": self.status_update.to_dict()}
        if self.artifact_update:
            return {"artifactUpdate": self.artifact_update.to_dict()}
        if self.message:
            return {"message": self.message.to_dict()}
        if self.task:
            return {"task": self.task.to_dict()}
        return {}
