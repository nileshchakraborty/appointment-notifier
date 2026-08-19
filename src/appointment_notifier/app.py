from __future__ import annotations

import asyncio
import logging

from .bot_listener import TelegramBotCommandListener
from .chat import OllamaChatService
from .config import AppSettings
from .llm import build_llm_client
from .models import Alert, SlotSignal, TelegramMessage
from .media import PortalMediaAnalyzer
from .notifiers import CompositeNotifier
from .parser import VisaSlotParser
from .store import AlertStore
from .telegram_watcher import TelegramWatcher
from .trend import TrendService

LOGGER = logging.getLogger(__name__)


class AppointmentNotifierApp:
    def __init__(
        self,
        settings: AppSettings,
        parser: VisaSlotParser,
        store: AlertStore,
        notifier: CompositeNotifier,
    ) -> None:
        self.settings = settings
        self.parser = parser
        self.store = store
        self.notifier = notifier
        self.media_analyzer = PortalMediaAnalyzer()
        reclassified = self.store.reclassify_observations(self.parser)
        if reclassified:
            LOGGER.info("Reclassified %s historical observations", reclassified)

    async def handle_message(self, message: TelegramMessage) -> None:
        ocr_text = ""
        portal_state = None
        if message.image_path:
            analysis = self.media_analyzer.analyze(message.image_path)
            cached = self.store.get_media_analysis(analysis.sha256)
            if cached:
                ocr_text = str(cached.get("ocr_text") or "")
                portal_state = str(cached.get("portal_state") or "")
            else:
                self.store.record_media_analysis(analysis)
                ocr_text = analysis.ocr_text
                portal_state = analysis.portal_state
        signal = self.parser.parse(
            message.text,
            has_image=message.has_image,
            ocr_text=ocr_text,
            portal_state=portal_state,
        )
        self.store.record_observation(message, signal)
        if not signal.matched:
            if signal.available_state is False:
                self.store.set_availability(False, message, signal.reason)
            LOGGER.debug("Message %s ignored: %s", message.message_id, signal.reason)
            return

        alert = self._build_alert(message, signal)
        self.store.set_availability(True, message, signal.reason, alert)

        if not self.store.is_new(message):
            LOGGER.info("Message %s already alerted", message.message_id)
            return

        self.store.record_alert(message, alert)
        LOGGER.info("Sending alert for Telegram message %s", message.message_id)
        self.notifier.send(alert)

    async def run(self) -> None:
        watcher = TelegramWatcher(
            api_id=self.settings.telegram.api_id,
            api_hash=self.settings.telegram.api_hash,
            session_path=str(self.settings.telegram.session_path),
            channel=self.settings.telegram.channel,
            media_dirs=self.settings.media_temp_dirs,
        )
        llm_client = build_llm_client(self.settings.trend)
        trend_service = TrendService(self.store, self.settings.trend, llm_client)
        chat_service = OllamaChatService(
            self.store,
            trend_service,
            self.settings.trend,
            llm_client,
        )
        bot_listener = TelegramBotCommandListener(
            self.settings.telegram_alert,
            self.store,
            trend_service=trend_service,
            chat_service=chat_service,
        )
        await asyncio.gather(
            watcher.run(self.settings.telegram.history_limit, self.handle_message),
            bot_listener.run(),
        )

    def _build_alert(self, message: TelegramMessage, signal: SlotSignal) -> Alert:
        source = message.url or self.settings.telegram.channel
        text = message.text.strip()
        body_parts = []
        if signal.silent:
            body_parts.append("Silent informational alert.")
        if signal.category == "bulk_release":
            body_parts.append("Bulk appointment release detected.")
        elif signal.category == "individual_availability":
            body_parts.append("Individual availability report detected.")
        if text:
            body_parts.append(text)
        if message.has_image:
            body_parts.append("Telegram message contains an image attachment.")
        body_parts.extend(["", f"Source: {source}"])
        if message.sent_at:
            body_parts.append(f"Telegram time: {message.sent_at.isoformat()}")
        return Alert(
            title=("Bulk visa appointment release may be available"
                   if signal.category == "bulk_release"
                   else "Visa appointment slot may be available"),
            body="\n".join(part for part in body_parts if part is not None),
            source=source,
            message_id=message.message_id,
            sent_at=message.sent_at,
            silent=signal.silent,
        )
