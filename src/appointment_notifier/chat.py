from __future__ import annotations

import asyncio
import logging
import re

from .config import TrendSettings
from .llm import FallbackLlmClient, build_llm_client
from .store import AlertStore
from .trend import TrendService, format_report

LOGGER = logging.getLogger(__name__)


TREND_QUESTION = re.compile(
    r"\b(?:trend|predict|prediction|next|when|frequency|frequent|quantity|how many|posted|release)\b",
    re.IGNORECASE,
)

TALOS_REDIRECT_PATTERN = re.compile(
    r"(?:talos|talosprotocol|startup|startups|founder|founders|customer|customers|prospect|prospects|pitch|outreach|partnership|partnerships|sell|selling|buyer|buyers|\bagents?\b|\bswarms?\b|lounge|a2a|\btasks?\b|\bstream\b|\bimmediate\b|\bwait\b|\bget\b|protocol)",
    re.IGNORECASE,
)


class OllamaChatService:
    def __init__(
        self,
        store: AlertStore,
        trend_service: TrendService,
        settings: TrendSettings,
        llm_client: FallbackLlmClient | None = None,
    ) -> None:
        self.store = store
        self.trend_service = trend_service
        self.settings = settings
        self.llm_client = llm_client or build_llm_client(settings)

    @property
    def enabled(self) -> bool:
        return self.llm_client.enabled

    async def answer(self, chat_id: str, question: str) -> str:
        if TALOS_REDIRECT_PATTERN.search(question):
            return (
                "💡 This query appears to be for Talos Protocol & Startup Scout.\n\n"
                f"Please use: `/talos {question}` to query the strategic scout with live vector search."
            )
        report = self.trend_service.report()
        trusted_report = format_report(report)
        history = self.store.recent_chat_messages(
            chat_id,
            self.settings.chat_history_messages,
        )
        # Filter out history messages from unrelated Talos or company pricing discussions
        filtered_history = [
            m for m in history
            if not TALOS_REDIRECT_PATTERN.search(str(m.get("content", "")))
        ]
        messages = [
            {
                "role": "system",
                "content": _system_prompt(trusted_report),
            },
            *filtered_history,
            {"role": "user", "content": question},
        ]
        result = await asyncio.to_thread(
            self.llm_client.complete,
            messages,
            max_tokens=self.settings.response_tokens,
            temperature=0.2,
        )
        answer = result.content.strip()
        self.store.record_chat_exchange(
            chat_id,
            question,
            answer,
            self.settings.chat_history_messages,
        )
        LOGGER.info("Chat answered for %s using %s", chat_id, result.provider)
        return answer


def _system_prompt(trusted_report: str) -> str:
    return (
        "You are a friendly, direct assistant for an H1B/H4 visa appointment notifier.\n"
        "Answer the user's question directly and conversationally in 1-3 sentences. "
        "Do NOT include meta-commentary like 'Based on the TRUSTED CURRENT REPORT', 'According to the report', "
        "or quote internal variable names. Speak naturally as the notifier.\n\n"
        "CRITICAL RULES FOR AVAILABILITY & TIMESTAMPS:\n"
        "1. Prioritize the MOST RECENT observation first. Check the timestamps under 'Last individual availability post', "
        "'Last OFC/biometrics post', and 'Last potential ghost slot post' before mentioning older bulk releases.\n"
        "2. Clearly distinguish between individual/OFC slots (often observed recently, e.g. today or this week) and rare bulk releases (which may have happened months ago).\n"
        "3. If asked when appointments were last available, state the latest date and time observed (convert/present cleanly in IST/user timezone).\n"
        "4. Be honest about uncertainty: these represent community reports and telemetry observed by the notifier, not guaranteed live embassy inventory.\n"
        "5. For questions about the next bulk appointment, use the bulk cadence and statistical forecast fields. If there is insufficient data, explain that simply.\n\n"
        "OBSERVED NOTIFIER TELEMETRY & RECENT REPORTS:\n" + trusted_report
    )
