from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.parse
import urllib.request
from typing import Any

from .config import TelegramAlertSettings
from .chat import OllamaChatService
from .store import AlertStore
from .trend import TrendService

LOGGER = logging.getLogger(__name__)

HELP_TEXT = (
    "Commands:\n"
    "/start - show this command list\n"
    "/help [visa|talos] - show command list or topic help\n"
    "/current - show whether the latest known state is available\n"
    "/last - show previous availability timestamp\n"
    "/status - show watcher and system status\n"
    "/trend - summarize posting history and next likely window\n"
    "/ask <question> - chat with the visa appointment assistant\n"
    "/forget - erase your saved conversation history\n"
    "/whoami - show your Telegram identifiers\n"
    "/users - list allowed users (owner only)\n"
    "/allow <chat_id|@username> [owner] - add a user (owner only)\n"
    "/revoke <chat_id|@username> - remove a non-owner user (owner only)\n\n"
    "Talos & Founders Scout:\n"
    "/talos <question> - ask about startups, outreach, or Talos Protocol visibility\n"
    "/startups [filter] - list discovered startups from AI Founders Lounge\n"
    "/sync - re-sync and scan recent WhatsApp messages from AI Founders Lounge"
)

HELP_TALOS = (
    "🚀 **Talos Protocol & Founder Scout Commands:**\n\n"
    "• `/talos <question>` — Ask the strategic scout questions grounded in AI Founders Lounge chat history.\n"
    "  *Examples:*\n"
    "  - `/talos which startup should I reach out to to get more visibility for talos protocol?`\n"
    "  - `/talos who is building multi-agent frameworks or swarms?`\n"
    "  - `/talos draft a message proposing a Talos integration to the founder of [Startup]`\n"
    "• `/startups [optional search]` — View top startups extracted from the chat, ranked by Talos synergy score (1-5 stars).\n"
    "• `/sync` — Force an immediate poll & extraction cycle of WhatsApp messages.\n"
)

HELP_VISA = (
    "📌 **Visa & Appointment Commands:**\n\n"
    "• `/current` — Check if visa appointment slots are currently open.\n"
    "• `/last` — See when slots were last open.\n"
    "• `/trend` — Statistical forecast and historical release windows.\n"
    "• `/ask <question>` — Ask questions about slots, bulk releases, OFC vs Consular, etc.\n"
)


class TelegramBotCommandListener:
    def __init__(
        self,
        settings: TelegramAlertSettings,
        store: AlertStore,
        trend_service: TrendService | None = None,
        chat_service: OllamaChatService | None = None,
        talos_scout: Any | None = None,
        whatsapp_watcher: Any | None = None,
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
        self.talos_scout = talos_scout
        self.whatsapp_watcher = whatsapp_watcher
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

        parts = text.split()
        command = parts[0].split("@")[0].lower()
        subquery = text.partition(" ")[2].strip()

        if command in {"/start", "/help"}:
            sub_lower = subquery.lower()
            if sub_lower in {"talos", "scout", "startup", "startups"}:
                response = HELP_TALOS
            elif sub_lower in {"visa", "slot", "slots", "appointment"}:
                response = HELP_VISA
            else:
                response = HELP_TEXT
        elif command in {"/talos", "/scout"}:
            response = await self._talos_query(chat_id, subquery)
        elif command in {"/startups", "/founders"}:
            response = self._startups_list(subquery)
        elif command == "/sync":
            response = await self._sync_command(subquery)
        elif command == "/status":
            response = await self._format_status()
        elif command == "/trend":
            if self.trend_service is None:
                response = "Trend analysis is not configured."
            else:
                response = await self.trend_service.summarize_async()
        elif command == "/ask":
            response = await self._smart_chat(chat_id, subquery)
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
            response = (
                "Unknown command.\n\n"
                "📌 Visa: /current, /last, /trend, /ask\n"
                "🚀 Talos: /talos <question>, /startups, /sync\n"
                "⚙️ General: /status, /whoami, /forget, /help"
            )
        else:
            response = await self._smart_chat(chat_id, text)

        await asyncio.to_thread(
            self._api_json,
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": response[:4096],
                "disable_web_page_preview": "true",
            },
        )

    def _send_typing_action(self, chat_id: str) -> None:
        try:
            self._api_json("sendChatAction", {"chat_id": chat_id, "action": "typing"})
        except Exception:
            pass

    async def _smart_chat(self, chat_id: str, question: str) -> str:
        if not question:
            return "Usage: /ask <question> for visa slots or /talos <question> for Talos Protocol & startup outreach."
        if re.search(r"\b(?:talos|startup|startups|founder|founders|agent|agents|swarms|lounge|a2a|task|tasks|stream|immediate|wait|get)\b", question, re.I):
            ans = await self._talos_query(chat_id, question)
            return f"[Talos Scout 🚀]\n\n{ans}\n\n💡 Tip: Use /talos <question> for Talos queries and /ask for visa slots."
        return await self._chat(chat_id, question)

    async def _talos_query(self, chat_id: str, question: str) -> str:
        if self.talos_scout is None or not getattr(self.talos_scout, "enabled", False):
            return "Talos Scout service is not configured or LLM is offline."
        if not question:
            return "Usage: /talos <question> (e.g. /talos which startup can give Talos more visibility?)"
        if len(question) > 1500:
            return "Please keep each question under 1,500 characters."
        await asyncio.to_thread(self._send_typing_action, chat_id)
        try:
            return await asyncio.wait_for(self.talos_scout.answer(chat_id, question), timeout=180)
        except Exception as exc:
            LOGGER.exception("Talos Scout request failed: %s", exc)
            return f"The Talos Scout AI is temporarily unavailable ({exc}). Check /status for connectivity."

    def _startups_list(self, filter_term: str) -> str:
        if self.talos_scout is None:
            return "Talos Scout service is not configured."
        return self.talos_scout.format_startups_list(filter_term=filter_term or None)

    async def _sync_command(self, subquery: str = "") -> str:
        if self.whatsapp_watcher is None:
            return "WhatsApp monitor is not configured."
        try:
            new_msgs = await self.whatsapp_watcher.sync_now()
            synced_vectors = 0
            if self.talos_scout is not None:
                synced_vectors = await asyncio.to_thread(self.talos_scout.sync_all_startups_to_qdrant)
            startups_count = self.store.count_startups()
            return (
                f"✅ Synced AI Founders Lounge: fetched {new_msgs} new messages, "
                f"{synced_vectors} startups vector-indexed in Qdrant. "
                f"Total startups: {startups_count}."
            )
        except Exception as exc:
            return f"WhatsApp sync failed: {exc}"

    async def _chat(self, chat_id: str, question: str) -> str:
        if self.chat_service is None or not self.chat_service.enabled:
            return "Local chat is not configured. Use /trend for deterministic statistics."
        if not question:
            return "Usage: /ask <question>. You can also send normal text to chat."
        if len(question) > 1500:
            return "Please keep each question under 1,500 characters."
        await asyncio.to_thread(self._send_typing_action, chat_id)
        try:
            service_settings = getattr(self.chat_service, "settings", None)
            timeout = max(180, int(getattr(service_settings, "chat_timeout_seconds", 180)))
            return await asyncio.wait_for(self.chat_service.answer(chat_id, question), timeout=timeout)
        except Exception as exc:
            LOGGER.exception("Pi-local chat request failed: %s", exc)
            return f"The Pi-local model is temporarily unavailable ({exc}). Try again shortly or use /trend."

    async def _format_status(self) -> str:
        lines = ["System status:"]
        try:
            self.store.conn.execute("select 1").fetchone()
            lines.append("Database: connected")
        except Exception as exc:
            lines.append(f"Database: unavailable ({exc})")

        try:
            telegram_result = await asyncio.wait_for(
                asyncio.to_thread(self._api_json, "getMe", {}),
                timeout=8,
            )
            if not telegram_result.get("ok"):
                raise RuntimeError("Telegram API returned ok=false")
            lines.append("Telegram Bot API: connected")
        except Exception as exc:
            lines.append(f"Telegram Bot API: unavailable ({type(exc).__name__})")

        llm_client = getattr(self.chat_service, "llm_client", None)
        if llm_client is None or not getattr(llm_client, "enabled", False):
            lines.append("AI providers: not configured")
        else:
            try:
                statuses = await asyncio.to_thread(llm_client.health)
            except Exception as exc:
                lines.append(f"AI providers: unavailable ({type(exc).__name__})")
            else:
                for provider, status in statuses.items():
                    lines.append(f"AI {provider}: {status}")

        if self.whatsapp_watcher is not None:
            wa_status = getattr(self.whatsapp_watcher, "status", "ready")
            msg_count = self.store.count_whatsapp_messages()
            startup_count = self.store.count_startups()
            lines.append(f"WhatsApp Founders Lounge: {wa_status} ({msg_count} msgs, {startup_count} startups)")
        elif self.talos_scout is not None:
            startup_count = self.store.count_startups()
            lines.append(f"Talos Scout: {startup_count} startups indexed")
        return "\n".join(lines)

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
