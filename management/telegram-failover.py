"""Run isolated visa or Talos Telegram polling on Fedora during a Pi outage."""

from __future__ import annotations

import asyncio
import logging
import os
import urllib.error
from dataclasses import replace
from pathlib import Path

from appointment_notifier.app import AppointmentNotifierApp
from appointment_notifier.bot_listener import TelegramBotCommandListener
from appointment_notifier.chat import OllamaChatService
from appointment_notifier.config import load_settings
from appointment_notifier.llm import build_llm_client
from appointment_notifier.notifiers import build_notifier
from appointment_notifier.parser import VisaSlotParser
from appointment_notifier.store import AlertStore
from appointment_notifier.talos_scout import TalosScoutService
from appointment_notifier.telegram_watcher import TelegramWatcher
from appointment_notifier.trend import TrendService


class PrimaryPollerActive(BaseException):
    """A second getUpdates poller appeared; yield to the primary process."""


async def supervise(name: str, run) -> None:
    delay = 1
    while True:
        try:
            await run()
            delay = 1
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("%s stopped; retrying in %ss", name, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)


async def main() -> None:
    scope = os.environ.get("BOT_SCOPE", "")
    if scope not in {"visa", "talos"}:
        raise ValueError("BOT_SCOPE must be visa or talos")
    root = Path(__file__).resolve().parent
    os.environ["SQLITE_PATH"] = str(root / f"{scope}.sqlite3")
    os.environ["FEDORA_LLM_URL"] = "http://127.0.0.1:8080"
    os.environ["TALOS_QDRANT_URL"] = "http://127.0.0.1:6333"
    os.environ["TALOS_EMBEDDING_URL"] = "http://127.0.0.1:8082"
    if scope == "visa":
        os.environ["TALOS_SCOUT_ENABLED"] = "false"
        os.environ["WHATSAPP_ENABLED"] = "false"
        os.environ["IMESSAGE_ENABLED"] = "false"
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = load_settings(require_telegram=scope == "visa")
    bot_settings = settings.telegram_alert
    if scope == "talos":
        token_file = Path(os.environ["TALOS_BOT_TOKEN_FILE"])
        bot_settings = replace(bot_settings, bot_token=token_file.read_text().strip())
    store = AlertStore(settings.sqlite_path)
    if not store.get_state("telegram_bot_update_offset"):
        # Process pending outage messages rather than dropping the newest one.
        store.set_state("telegram_bot_update_offset", "0")
    llm = build_llm_client(settings.trend)
    trend = TrendService(store, settings.trend, llm)
    chat = OllamaChatService(store, trend, settings.trend, llm)
    scout = TalosScoutService(store, settings.talos_scout, llm) if scope == "talos" else None
    listener = TelegramBotCommandListener(
        bot_settings, store, trend_service=trend, chat_service=chat,
        talos_scout=scout, scope=scope,
    )
    original_api = listener._api_json

    def api_json(method: str, params: dict[str, str]) -> dict:
        try:
            result = original_api(method, params)
        except urllib.error.HTTPError as exc:
            if method == "getUpdates" and exc.code == 409:
                raise PrimaryPollerActive() from exc
            raise
        if method in {"sendMessage", "editMessageText"}:
            logging.info("Telegram %s succeeded for %s bot", method, scope)
        return result

    listener._api_json = api_json
    tasks = [asyncio.create_task(listener.run(), name=f"{scope} bot listener")]
    if scope == "visa":
        notifier = build_notifier(
            bot_settings, settings.email, settings.sms, settings.whatsapp,
            settings.imessage, settings.dry_run, store=store,
        )
        app = AppointmentNotifierApp(
            settings, VisaSlotParser(settings.required_terms, settings.suppress_terms),
            store, notifier,
        )
        watcher = TelegramWatcher(
            settings.telegram.api_id, settings.telegram.api_hash,
            str(settings.telegram.session_path), settings.telegram.channel,
            settings.media_temp_dirs,
        )
        tasks.append(asyncio.create_task(
            supervise("Visa channel watcher", lambda: watcher.run(0, app.handle_message)),
            name="visa channel watcher",
        ))
    try:
        await asyncio.gather(*tasks)
    except PrimaryPollerActive:
        logging.warning("Another Telegram poller is active; stopping %s failover", scope)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        store.close()


if __name__ == "__main__":
    asyncio.run(main())
