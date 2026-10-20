from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .config import TalosScoutSettings, WhatsAppSettings
from .store import AlertStore
from .talos_scout import TalosScoutService

LOGGER = logging.getLogger(__name__)


class WhatsAppFoundersWatcher:
    def __init__(
        self,
        settings: TalosScoutSettings,
        whatsapp_settings: WhatsAppSettings,
        store: AlertStore,
        talos_scout: TalosScoutService,
    ) -> None:
        self.settings = settings
        self.whatsapp_settings = whatsapp_settings
        self.store = store
        self.talos_scout = talos_scout
        self.base_url = whatsapp_settings.openwa_url.rstrip("/")
        self.api_key = whatsapp_settings.openwa_api_key
        self.session = settings.openwa_session
        self._target_chat_id: str | None = None
        self._last_sync_status: str = "initialized"

    @property
    def status(self) -> str:
        return self._last_sync_status

    async def run(self) -> None:
        if not self.settings.enabled:
            LOGGER.info("WhatsApp Founders Lounge watcher disabled")
            return

        LOGGER.info("Starting WhatsApp Founders Lounge watcher for chat '%s'", self.settings.founders_chat_name)
        while True:
            try:
                await self.sync_now()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOGGER.warning("WhatsApp sync cycle encountered an error: %s", exc)
                self._last_sync_status = f"sync error: {exc}"
            await asyncio.sleep(self.settings.poll_interval_seconds)

    async def sync_now(self) -> int:
        """Poll latest messages from target chat and extract new startups."""
        chat_id = await self._resolve_target_chat_id()
        if not chat_id:
            self._last_sync_status = f"Chat '{self.settings.founders_chat_name}' not found"
            LOGGER.debug("Target chat '%s' not resolved yet", self.settings.founders_chat_name)
            return 0

        messages = await self._fetch_chat_messages(chat_id, count=30)
        new_count = 0
        for m in messages:
            msg_id = str(m.get("id") or m.get("msgId") or "")
            if not msg_id:
                continue
            text = str(m.get("body") or m.get("text") or "").strip()
            if not text:
                continue
            sender = m.get("sender") or {}
            sender_id = str(sender.get("id") or m.get("from") or "")
            sender_name = str(sender.get("pushname") or sender.get("formattedName") or sender.get("name") or "")
            sent_at = str(m.get("t") or m.get("timestamp") or "")

            saved = self.store.record_whatsapp_message(
                msg_id=msg_id,
                chat_name=self.settings.founders_chat_name,
                sender_id=sender_id,
                sender_name=sender_name,
                text=text,
                sent_at=sent_at,
                raw_json=json.dumps(m),
            )
            if saved:
                new_count += 1

        self._last_sync_status = f"connected ({new_count} new messages)"

        if self.settings.auto_extract:
            unprocessed = self.store.get_unprocessed_whatsapp_messages(
                chat_name=self.settings.founders_chat_name,
                limit=20,
            )
            if unprocessed:
                await self.talos_scout.extract_startups_from_batch(unprocessed)

        return new_count

    async def _resolve_target_chat_id(self) -> str | None:
        if self._target_chat_id:
            return self._target_chat_id

        chats = await self._fetch_all_chats()
        target = self.settings.founders_chat_name.lower()
        for chat in chats:
            name = str(chat.get("name") or chat.get("formattedTitle") or "").lower()
            if target in name:
                self._target_chat_id = str(chat.get("id"))
                LOGGER.info("Resolved WhatsApp chat '%s' to ID %s", self.settings.founders_chat_name, self._target_chat_id)
                return self._target_chat_id
        return None

    async def _fetch_all_chats(self) -> list[dict[str, Any]]:
        # OpenWA v5 uses /api/chats/getAll; v4 used /api/getAllChats or /getAllChats
        endpoints = [
            f"{self.base_url}/api/chats/getAll",
            f"{self.base_url}/api/getAllChats",
            f"{self.base_url}/getAllChats",
            f"{self.base_url}/chats",
            f"{self.base_url}/api/{self.session}/chats",
        ]
        return await asyncio.to_thread(self._request_first_success, endpoints)

    async def _fetch_chat_messages(self, chat_id: str, count: int = 30) -> list[dict[str, Any]]:
        # In v5, POST /api/messages/getAll with JSON body is the primary reliable method
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["x-api-key"] = self.api_key
            headers["Authorization"] = f"Bearer {self.api_key}"

        v5_url = f"{self.base_url}/api/messages/getAll"
        payload = json.dumps({"chatId": chat_id, "includeMe": True, "includeNotifications": False}).encode("utf-8")
        try:
            req = urllib.request.Request(v5_url, data=payload, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=12) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    msgs = data.get("data")
                    if isinstance(msgs, list) and msgs:
                        return msgs[-count:]
        except Exception as exc:
            LOGGER.debug("OpenWA v5 POST messages/getAll failed: %s", exc)

        quoted_id = urllib.parse.quote(chat_id)
        fallback_endpoints = [
            f"{self.base_url}/api/getAllMessagesInChat?chatId={quoted_id}&count={count}",
            f"{self.base_url}/getAllMessagesInChat?chatId={quoted_id}&count={count}",
            f"{self.base_url}/messages/{quoted_id}?count={count}",
            f"{self.base_url}/api/{self.session}/messages/{quoted_id}?count={count}",
        ]
        return await asyncio.to_thread(self._request_first_success, fallback_endpoints)

    def _request_first_success(self, endpoints: list[str]) -> list[dict[str, Any]]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["x-api-key"] = self.api_key
            headers["Authorization"] = f"Bearer {self.api_key}"

        for url in endpoints:
            try:
                req = urllib.request.Request(url, headers=headers, method="GET")
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode("utf-8"))
                        if isinstance(data, list):
                            return data
                        if isinstance(data, dict):
                            result = data.get("response") or data.get("result") or data.get("data")
                            if isinstance(result, list):
                                return result
            except Exception as exc:
                LOGGER.debug("OpenWA GET %s failed: %s", url, exc)
        return []
