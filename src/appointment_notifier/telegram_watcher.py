from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

from .models import TelegramMessage

LOGGER = logging.getLogger(__name__)


class TelegramWatcher:
    def __init__(self, api_id: int, api_hash: str, session_path: str, channel: str) -> None:
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_path = session_path
        self.channel = channel
        Path(self.session_path).parent.mkdir(parents=True, exist_ok=True)

    async def run(self, history_limit: int, on_message) -> None:
        try:
            from telethon import TelegramClient, events
        except ImportError as exc:
            raise RuntimeError("Install dependencies with: pip install -e .") from exc

        async with TelegramClient(self.session_path, self.api_id, self.api_hash) as client:
            entity = await client.get_entity(self.channel)

            LOGGER.info("Checking last %s messages from %s", history_limit, self.channel)
            recent_messages = [
                message
                async for message in client.iter_messages(entity, limit=history_limit)
            ]
            for message in reversed(recent_messages):
                await self._deliver(message, client, on_message)

            @client.on(events.NewMessage(chats=entity))
            async def handler(event) -> None:
                await self._deliver(event.message, client, on_message)

            LOGGER.info("Watching %s", self.channel)
            await client.run_until_disconnected()

    async def _deliver(self, message, client, on_message) -> None:
        path = None
        if _has_image(message):
            media_dir = Path(tempfile.gettempdir()) / "appointment-notifier-media"
            media_dir.mkdir(parents=True, exist_ok=True)
            path = str(media_dir / f"{int(message.id)}.image")
            try:
                downloaded = await client.download_media(message, file=path)
                path = str(downloaded) if downloaded else None
            except (OSError, RuntimeError) as exc:
                LOGGER.warning("Unable to download Telegram image %s: %s", message.id, exc)
                path = None
        try:
            await on_message(_to_model(message, self.channel, path))
        finally:
            if path:
                try:
                    Path(path).unlink(missing_ok=True)
                except OSError:
                    LOGGER.debug("Unable to remove temporary media %s", path)


def run_async(coro):
    return asyncio.run(coro)


def _to_model(message, channel: str, image_path: str | None = None) -> TelegramMessage:
    message_id = int(message.id)
    url_channel = channel.removeprefix("@")
    return TelegramMessage(
        message_id=message_id,
        text=message.message or "",
        sent_at=message.date,
        url=f"https://t.me/{url_channel}/{message_id}" if url_channel else None,
        has_image=_has_image(message),
        image_path=image_path,
    )


def _has_image(message) -> bool:
    if getattr(message, "photo", None):
        return True
    document = getattr(message, "document", None)
    mime_type = getattr(document, "mime_type", "") if document else ""
    return mime_type.startswith("image/")
