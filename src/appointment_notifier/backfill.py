from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from .media import PortalMediaAnalyzer
from .models import TelegramMessage
from .parser import VisaSlotParser
from .store import AlertStore
from .telegram_watcher import _has_image, _select_media_dir

LOGGER = logging.getLogger(__name__)
MEDIA_DOWNLOAD_TIMEOUT_SECONDS = 120


async def backfill(*, api_id: int, api_hash: str, session_path: str, chats: tuple[str, ...], store: AlertStore, parser: VisaSlotParser, media_dirs: tuple[Path, ...], limit: int | None = None, dry_run: bool = False, all_dialogs: bool = False) -> dict[str, int]:
    """Resumably ingest source history without involving the LLM."""
    try:
        from telethon import TelegramClient
    except ImportError as exc:
        raise RuntimeError("Install dependencies with: pip install -e .") from exc
    stats = {"messages": 0, "images": 0, "ocr_cached": 0, "ocr_processed": 0, "matched": 0}
    async with TelegramClient(session_path, api_id, api_hash) as client:
        if all_dialogs:
            targets = [
                (str(dialog.entity.id), dialog.entity)
                async for dialog in client.iter_dialogs()
                if getattr(dialog, "is_channel", False) or getattr(dialog, "is_group", False)
            ]
        else:
            targets = [(chat, await client.get_entity(chat)) for chat in chats]
        for chat, entity in targets:
            chat_key = str(entity.id)
            cursor = store.backfill_cursor(chat_key)
            kwargs = {"limit": limit}
            if cursor:
                kwargs["max_id"] = cursor
            async for message in client.iter_messages(entity, **kwargs):
                message_id = int(message.id)
                model = TelegramMessage(
                    message_id=message_id, text=message.message or "", sent_at=message.date,
                    url=f"https://t.me/{chat.removeprefix('@')}/{message_id}",
                    has_image=_has_image(message), source_chat_id=chat_key,
                )
                stats["messages"] += 1
                if dry_run:
                    continue
                ocr_text = ""
                portal_state = None
                media_sha256 = None
                if model.has_image:
                    stats["images"] += 1
                    directory = _select_media_dir(media_dirs)
                    if directory:
                        media_path = str(directory / f"{chat_key}-{message_id}.image")
                        try:
                            downloaded = await asyncio.wait_for(
                                client.download_media(message, file=media_path),
                                timeout=MEDIA_DOWNLOAD_TIMEOUT_SECONDS,
                            )
                            if downloaded:
                                media_path = str(downloaded)
                                analysis = PortalMediaAnalyzer().analyze(media_path)
                                media_sha256 = analysis.sha256 or None
                                cached = store.get_media_analysis(analysis.sha256) if analysis.sha256 else None
                                if cached:
                                    stats["ocr_cached"] += 1
                                    ocr_text = str(cached.get("ocr_text") or "")
                                    portal_state = str(cached.get("portal_state") or "")
                                else:
                                    store.record_media_analysis(analysis)
                                    stats["ocr_processed"] += 1
                                    ocr_text, portal_state = analysis.ocr_text, analysis.portal_state
                        except Exception:
                            LOGGER.exception("Failed media/OCR for %s:%s", chat_key, message_id)
                        finally:
                            Path(media_path).unlink(missing_ok=True)
                store.record_telegram_message(model, media_sha256=media_sha256)
                signal = parser.parse(model.text, has_image=model.has_image, ocr_text=ocr_text, portal_state=portal_state)
                store.record_observation(model, signal)
                stats["matched"] += int(signal.matched)
                store.set_backfill_cursor(chat_key, message_id)
    return stats


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--chat", action="append", dest="chats", help="Source chat username/ID; repeatable")
    parser.add_argument("--limit", type=int, default=None, help="Maximum messages per chat this run")
    parser.add_argument("--dry-run", action="store_true", help="Enumerate history without writing or OCR")
    parser.add_argument("--all-chats", action="store_true", help="Inventory all accessible Telegram groups/channels")
