from __future__ import annotations

import asyncio
import re

from .config import TrendSettings
from .llm import FallbackLlmClient, build_llm_client
from .store import AlertStore
from .trend import TrendService, format_report


TREND_QUESTION = re.compile(
    r"\b(?:trend|predict|prediction|next|when|frequency|frequent|quantity|how many|posted|release)\b",
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
        report = self.trend_service.report()
        trusted_report = format_report(report)
        history = self.store.recent_chat_messages(
            chat_id,
            self.settings.chat_history_messages,
        )
        messages = [
            {
                "role": "system",
                "content": _system_prompt(trusted_report),
            },
            *history,
            {"role": "user", "content": question},
        ]
        result = await asyncio.to_thread(
            self.llm_client.complete,
            messages,
            max_tokens=self.settings.response_tokens,
            temperature=0.2,
        )
        answer = result.content
        self.store.record_chat_exchange(
            chat_id,
            question,
            answer,
            self.settings.chat_history_messages,
        )
        if TREND_QUESTION.search(question):
            response = trusted_report + "\n\nAssistant:\n" + answer
        else:
            response = answer
        return response + f"\n\nSource: {result.provider}"


def _system_prompt(trusted_report: str) -> str:
    return (
        "You are a small private assistant for an H1B/H4 visa appointment notifier. "
        "Be concise, practical, and honest about uncertainty. You may discuss the notifier, "
        "its observations, and general visa appointment concepts, but you cannot book slots, "
        "access the official portal, or provide legal advice. Never claim that a prediction is "
        "an official embassy schedule. For trend questions, use the trusted report exactly as "
        "given; do not recalculate, replace, or increase its confidence. For questions about the "
        "next bulk appointment, use only the bulk history, bulk cadence, and next bulk-release "
        "window fields; never substitute the all-post prediction or merely repeat the last bulk post. "
        "Treat bulk-release posts "
        "as release signals, not appointment counts. Treat individual availability reports as "
        "community evidence only. Exclude NA heartbeats, invalid/ghost/unbookable reports, spam, "
        "questions, and booked confirmations from availability claims. Never infer bookable slots "
        "from an image alone. If the report lacks an answer, say so.\n\n"
        "TRUSTED CURRENT REPORT:\n" + trusted_report
    )
