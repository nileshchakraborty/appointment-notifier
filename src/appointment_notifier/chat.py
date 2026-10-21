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
        "an official embassy schedule.\n\n"
        "GUIDELINES FOR ANSWERING:\n"
        "1. For questions about when appointments or slots were last available, seen, or posted, directly answer using the timestamps in the TRUSTED CURRENT REPORT:\n"
        "   - Last OFC/biometrics post: cite exact timestamp\n"
        "   - Last individual availability post: cite exact timestamp\n"
        "   - Last bulk release post: cite exact timestamp\n"
        "   - Last potential ghost slot post: cite exact timestamp\n"
        "   Explain clearly that these represent community reports and telemetry observed by the notifier, not guaranteed live embassy inventory. Do NOT claim you lack real-time data or refuse to answer, because the trusted report below contains the real-time observations.\n"
        "2. For questions about the next bulk appointment, use only the bulk history, bulk cadence, and next bulk-release window fields; never substitute the all-post prediction or merely repeat the last bulk post. Treat bulk-release posts as release signals, not appointment counts.\n"
        "3. Exclude NA heartbeats, invalid/ghost/unbookable reports, spam, and questions from availability claims. Never infer bookable slots from an image alone.\n"
        "4. If the report lacks an answer, state that clearly.\n\n"
        "TRUSTED CURRENT REPORT:\n" + trusted_report
    )
