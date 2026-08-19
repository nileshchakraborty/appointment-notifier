from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class TelegramMessage:
    message_id: int
    text: str
    sent_at: datetime | None
    url: str | None = None
    has_image: bool = False
    image_path: str | None = None


@dataclass(frozen=True)
class SlotSignal:
    matched: bool
    reason: str
    locations: tuple[str, ...] = ()
    visa_terms: tuple[str, ...] = ()
    silent: bool = False
    available_state: bool | None = None
    category: str = "unknown"
    ocr_text: str = ""
    portal_state: str | None = None


@dataclass(frozen=True)
class Alert:
    title: str
    body: str
    source: str
    message_id: int
    sent_at: datetime | None
    silent: bool = False
