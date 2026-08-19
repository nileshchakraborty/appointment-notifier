from __future__ import annotations

import asyncio
import json
import logging
import urllib.parse
import urllib.request
from typing import Any

from .config import TelegramAlertSettings
from .chat import OllamaChatService
from .store import AlertStore
from .trend import TrendService

LOGGER = logging.getLogger(__name__)


class TelegramBotCommandListener:
    def __init__(
        self,
        settings: TelegramAlertSettings,
        store: AlertStore,
        trend_service: TrendService | None = None,
        chat_service: OllamaChatService | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.allowed_chat_ids = {str(chat_id) for chat_id in settings.chat_ids}
        self.owner_chat_ids = {str(chat_id) for chat_id in settings.owner_chat_ids}
        self.allowed_usernames = {
            normalized
            for username in settings.allowed_usernames
            if (normalized := self._normalize_username(username))
        }
        self.owner_usernames = {
            normalized
            for username in settings.owner_usernames
            if (normalized := self._normalize_username(username))
        }
        self.base_url = f"https://api.telegram.org/bot{settings.bot_token}"
        self.trend_service = trend_service
        self.chat_service = chat_service
        self._seed_bot_users()

    async def run(self) -> None:
        if (
            not self.settings.enabled
            or not self.settings.bot_token
            or self.store.bot_user_count() == 0
        ):
            LOGGER.info("Telegram bot command listener disabled")
            return

        LOGGER.info("Telegram bot command listener enabled")
        await self._initialize_offset()
        while True:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("Telegram bot command poll failed")
            await asyncio.sleep(10)

    async def _initialize_offset(self) -> None:
        if self.store.get_state("telegram_bot_update_offset"):
            return
        updates = await asyncio.to_thread(
            self._api_json,
            "getUpdates",
            {"offset": "-1", "timeout": "0", "allowed_updates": '["message"]'},
        )
        latest = updates.get("result", [])
        if latest:
            update_id = int(latest[-1]["update_id"])
            self.store.set_state("telegram_bot_update_offset", str(update_id + 1))
            LOGGER.info("Initialized Telegram bot command offset at %s", update_id + 1)

    async def _poll_once(self) -> None:
        offset = self.store.get_state("telegram_bot_update_offset")
        params = {"timeout": "30", "allowed_updates": '["message"]'}
        if offset:
            params["offset"] = offset
        updates = await asyncio.to_thread(self._api_json, "getUpdates", params)
        for update in updates.get("result", []):
            update_id = int(update["update_id"])
            self.store.set_state("telegram_bot_update_offset", str(update_id + 1))
            await self._handle_update(update)

    async def _handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        text = (message.get("text") or "").strip()

        if not self._is_authorized(message, chat_id):
            username = self._first_username(message)
            LOGGER.warning(
                "Ignoring Telegram bot command from unauthorized chat id %s username %s",
                chat_id,
                username or "<none>",
            )
            return
        if not text:
            return

        command = text.split()[0].split("@")[0].lower()
        if command in {"/start", "/help"}:
            response = (
                "Commands:\n"
                "/current - show whether the latest known state is available\n"
                "/last - show previous availability timestamp\n"
                "/status - show watcher status\n"
                "/trend - summarize posting history and next likely window\n"
                "/ask <question> - chat with the Pi-local appointment assistant\n"
                "/forget - erase your saved conversation history\n"
                "/whoami - show your Telegram identifiers\n"
                "/users - list allowed users (owner only)\n"
                "/allow <chat_id|@username> [owner] - add a user (owner only)\n"
                "/revoke <chat_id|@username> - remove a non-owner user (owner only)"
            )
        elif command == "/status":
            response = "Appointment notifier is running. Use /current or /last."
        elif command == "/trend":
            if self.trend_service is None:
                response = "Trend analysis is not configured."
            else:
                response = await self.trend_service.summarize_async()
        elif command == "/ask":
            question = text.partition(" ")[2].strip()
            response = await self._chat(chat_id, question)
        elif command == "/forget":
            removed = self.store.clear_chat_history(chat_id)
            response = f"Forgot {removed} saved conversation messages."
        elif command == "/whoami":
            response = self._format_identity(message, chat_id)
        elif command == "/current":
            response = self._format_current()
        elif command == "/last":
            response = self._format_last()
        elif command == "/users":
            response = self._owner_command(message, chat_id, self._format_users)
        elif command == "/allow":
            response = self._owner_command(message, chat_id, lambda: self._allow_user(text))
        elif command == "/revoke":
            response = self._owner_command(message, chat_id, lambda: self._revoke_user(text))
        elif text.startswith("/"):
            response = "Unknown command. Use /current, /last, /trend, /ask, /forget, /status, /whoami, or /help."
        else:
            response = await self._chat(chat_id, text)

        await asyncio.to_thread(
            self._api_json,
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": response[:4096],
                "disable_web_page_preview": "true",
            },
        )

    async def _chat(self, chat_id: str, question: str) -> str:
        if self.chat_service is None or not self.chat_service.enabled:
            return "Local chat is not configured. Use /trend for deterministic statistics."
        if not question:
            return "Usage: /ask <question>. You can also send normal text to chat."
        if len(question) > 1500:
            return "Please keep each question under 1,500 characters."
        try:
            service_settings = getattr(self.chat_service, "settings", None)
            timeout = max(1, int(getattr(service_settings, "chat_timeout_seconds", 150)))
            return await asyncio.wait_for(self.chat_service.answer(chat_id, question), timeout=timeout)
        except Exception:
            LOGGER.exception("Pi-local chat request failed")
            return "The Pi-local model is temporarily unavailable. Try again shortly or use /trend."

    def _format_current(self) -> str:
        state = self.store.availability_state()
        current = "AVAILABLE" if state["currently_available"] else "NOT AVAILABLE"
        lines = [f"Current: {current}"]
        if state.get("current_seen_at"):
            lines.append(f"Seen at: {state['current_seen_at']}")
        if state.get("current_reason"):
            lines.append(f"Reason: {state['current_reason']}")
        if state.get("current_source"):
            lines.append(f"Source: {state['current_source']}")
        return "\n".join(lines)

    def _format_last(self) -> str:
        state = self.store.availability_state()
        if not state.get("last_available_at"):
            return "No previous availability has been recorded yet."
        lines = [f"Last available: {state['last_available_at']}"]
        if state.get("last_available_message_id"):
            lines.append(f"Message: {state['last_available_message_id']}")
        if state.get("last_available_source"):
            lines.append(f"Source: {state['last_available_source']}")
        return "\n".join(lines)

    def _format_identity(self, message: dict[str, Any], chat_id: str) -> str:
        username = self._first_username(message)
        owner = self.store.bot_user_is_owner(chat_id=chat_id, username=username)
        lines = [f"Chat ID: {chat_id}"]
        if username:
            lines.append(f"Username: @{username}")
        lines.append(f"Owner: {'yes' if owner else 'no'}")
        return "\n".join(lines)

    def _format_users(self) -> str:
        users = self.store.list_bot_users()
        if not users:
            return "No bot users configured."
        lines = ["Allowed bot users:"]
        for user in users:
            parts = []
            if user.get("chat_id"):
                parts.append(str(user["chat_id"]))
            if user.get("username"):
                parts.append(f"@{user['username']}")
            label = " / ".join(parts)
            if user.get("display_name"):
                label = f"{label} ({user['display_name']})"
            if user.get("is_owner"):
                label = f"{label} [owner]"
            lines.append(f"- {label}")
        return "\n".join(lines)

    def _allow_user(self, text: str) -> str:
        parts = text.split()
        if len(parts) < 2:
            return "Usage: /allow <chat_id|@username> [owner]"
        identifier = parts[1]
        owner = any(part.lower() == "owner" for part in parts[2:])
        chat_id, username = self._split_identifier(identifier)
        self.store.ensure_bot_user(chat_id=chat_id, username=username, is_owner=owner)
        label = f"@{username}" if username else chat_id
        suffix = " as owner" if owner else ""
        return f"Allowed {label}{suffix}."

    def _revoke_user(self, text: str) -> str:
        parts = text.split()
        if len(parts) != 2:
            return "Usage: /revoke <chat_id|@username>"
        removed = self.store.remove_bot_user(parts[1])
        if not removed:
            return "No non-owner user was removed."
        return f"Revoked {parts[1]}."

    def _owner_command(self, message: dict[str, Any], chat_id: str, handler: Any) -> str:
        username = self._first_username(message)
        if not self.store.bot_user_is_owner(chat_id=chat_id, username=username):
            return "Only bot owners can manage users."
        return handler()

    def _api_json(self, method: str, params: dict[str, str]) -> dict[str, Any]:
        payload = urllib.parse.urlencode(params).encode("utf-8")
        request = urllib.request.Request(f"{self.base_url}/{method}", data=payload, method="POST")
        with urllib.request.urlopen(request, timeout=40) as response:
            return json.load(response)

    def _is_authorized(self, message: dict[str, Any], chat_id: str) -> bool:
        username = self._first_username(message)
        return self.store.bot_user_allowed(chat_id=chat_id, username=username)

    def _first_username(self, message: dict[str, Any]) -> str | None:
        from_user = message.get("from") or {}
        chat = message.get("chat") or {}
        for value in (from_user.get("username"), chat.get("username")):
            username = self._normalize_username(str(value or ""))
            if username:
                return username
        return None

    @staticmethod
    def _normalize_username(username: str) -> str:
        return username.strip().removeprefix("@").lower()

    def _seed_bot_users(self) -> None:
        for chat_id in self.allowed_chat_ids:
            self.store.ensure_bot_user(chat_id=chat_id, is_owner=chat_id in self.owner_chat_ids)
        for username in self.allowed_usernames:
            self.store.ensure_bot_user(
                username=username,
                is_owner=self._normalize_username(username) in self.owner_usernames,
            )
        for chat_id in self.owner_chat_ids:
            self.store.ensure_bot_user(chat_id=chat_id, is_owner=True)
        for username in self.owner_usernames:
            self.store.ensure_bot_user(username=username, is_owner=True)

    @staticmethod
    def _split_identifier(identifier: str) -> tuple[str | None, str | None]:
        cleaned = identifier.strip()
        if not cleaned:
            raise ValueError("identifier is required")
        if cleaned.startswith("@") or not cleaned.lstrip("-").isdigit():
            return None, TelegramBotCommandListener._normalize_username(cleaned)
        return cleaned, None
