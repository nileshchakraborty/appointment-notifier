from __future__ import annotations

import json
import asyncio
import logging
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from .config import TrendSettings
from .llm import FallbackLlmClient, LlmProviderError, LlmResponse, build_llm_client

if TYPE_CHECKING:
    from .store import AlertStore

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class TrendReport:
    data_source: str
    timezone: str
    first_post: str | None
    last_post: str | None
    matching_posts: int
    bulk_release_posts: int
    individual_availability_posts: int
    last_bulk_release: str | None
    last_individual_availability: str | None
    bulk_release_events: int
    bulk_median_gap_days: float | None
    next_bulk_predicted: str | None
    next_bulk_window_start: str | None
    next_bulk_window_end: str | None
    legacy_posts: int
    unbookable_posts: int
    na_heartbeat_posts: int
    unknown_image_posts: int
    ocr_evidence: tuple[str, ...]
    release_events: int
    events_per_week: float | None
    median_posts_per_event: float | None
    max_posts_per_event: int | None
    median_gap_days: float | None
    peak_weekday: str | None
    peak_two_hour_window: str | None
    predicted_next: str | None
    predicted_window_start: str | None
    predicted_window_end: str | None
    confidence: str
    caveat: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class TrendAnalyzer:
    def __init__(self, timezone_name: str = "Asia/Kolkata", event_gap_hours: int = 6) -> None:
        self.timezone_name = timezone_name
        self.local_tz = ZoneInfo(timezone_name)
        self.event_gap = timedelta(hours=event_gap_hours)

    def analyze(self, rows: list[dict[str, object]], data_source: str, summary: dict[str, int] | None = None) -> TrendReport:
        points = sorted(
            parsed
            for row in rows
            if (parsed := _parse_datetime(str(row.get("sent_at") or ""))) is not None
        )
        caveat = (
            "Counts are matching community posts clustered within six hours, not the "
            "number of bookable appointments. Image-only and community reports can be noisy."
        )
        if not points:
            return TrendReport(
                data_source=data_source,
                timezone=self.timezone_name,
                first_post=None,
                last_post=None,
                matching_posts=0,
                bulk_release_posts=0,
                individual_availability_posts=0,
                last_bulk_release=None,
                last_individual_availability=None,
                bulk_release_events=0,
                bulk_median_gap_days=None,
                next_bulk_predicted=None,
                next_bulk_window_start=None,
                next_bulk_window_end=None,
                legacy_posts=0,
                unbookable_posts=int((summary or {}).get("unbookable", 0)),
                na_heartbeat_posts=int((summary or {}).get("na_heartbeat", 0)),
                unknown_image_posts=int((summary or {}).get("unknown_image", 0)),
                ocr_evidence=(),
                release_events=0,
                events_per_week=None,
                median_posts_per_event=None,
                max_posts_per_event=None,
                median_gap_days=None,
                peak_weekday=None,
                peak_two_hour_window=None,
                predicted_next=None,
                predicted_window_start=None,
                predicted_window_end=None,
                confidence="insufficient data",
                caveat=caveat,
            )

        categories = [str(row.get("category") or "unknown") for row in rows if _parse_datetime(str(row.get("sent_at") or "")) is not None]
        bulk_posts = sum(category == "bulk_release" for category in categories)
        individual_posts = sum(category == "individual_availability" for category in categories)
        dated_rows = [
            (_parse_datetime(str(row.get("sent_at") or "")), str(row.get("category") or ""))
            for row in rows
        ]
        last_bulk = max((point for point, category in dated_rows if point and category == "bulk_release"), default=None)
        last_individual = max((point for point, category in dated_rows if point and category == "individual_availability"), default=None)
        bulk_points = sorted(point for point, category in dated_rows if point and category == "bulk_release")
        bulk_clusters: list[list[datetime]] = []
        for point in bulk_points:
            if not bulk_clusters or point - bulk_clusters[-1][-1] > self.event_gap:
                bulk_clusters.append([point])
            else:
                bulk_clusters[-1].append(point)
        bulk_starts = [cluster[0] for cluster in bulk_clusters]
        bulk_gaps = [
            (current - previous).total_seconds() / 86400
            for previous, current in zip(bulk_starts, bulk_starts[1:])
        ][-12:]
        bulk_median_gap = statistics.median(bulk_gaps) if bulk_gaps else None
        next_bulk = bulk_start = bulk_end = None
        if bulk_median_gap is not None:
            bulk_low, bulk_high = _prediction_bounds(bulk_gaps)
            next_bulk_dt = bulk_starts[-1] + timedelta(days=bulk_median_gap)
            bulk_start_dt = bulk_starts[-1] + timedelta(days=bulk_low)
            bulk_end_dt = bulk_starts[-1] + timedelta(days=bulk_high)
            next_bulk = next_bulk_dt.astimezone(self.local_tz).isoformat(timespec="minutes")
            bulk_start = bulk_start_dt.astimezone(self.local_tz).isoformat(timespec="minutes")
            bulk_end = bulk_end_dt.astimezone(self.local_tz).isoformat(timespec="minutes")
        legacy_posts = sum(category == "legacy" for category in categories)
        ocr_evidence = tuple(
            text[:280].replace("\n", " ")
            for text in (str(row.get("ocr_text") or "") for row in rows)
            if text.strip()
        )[-5:]

        clusters: list[list[datetime]] = []
        for point in points:
            if not clusters or point - clusters[-1][-1] > self.event_gap:
                clusters.append([point])
            else:
                clusters[-1].append(point)

        starts = [cluster[0] for cluster in clusters]
        quantities = [len(cluster) for cluster in clusters]
        span_days = max((points[-1] - points[0]).total_seconds() / 86400, 1.0)
        events_per_week = len(clusters) / (span_days / 7)
        local_starts = [start.astimezone(self.local_tz) for start in starts]
        weekday = _mode([point.strftime("%A") for point in local_starts])
        hour_bin = _mode([(point.hour // 2) * 2 for point in local_starts])
        hour_window = f"{hour_bin:02d}:00-{(hour_bin + 2) % 24:02d}:00" if hour_bin is not None else None

        gaps = [
            (current - previous).total_seconds() / 86400
            for previous, current in zip(starts, starts[1:])
        ]
        recent_gaps = gaps[-12:]
        median_gap = statistics.median(recent_gaps) if recent_gaps else None
        predicted = window_start = window_end = None
        if median_gap is not None:
            low_gap, high_gap = _prediction_bounds(recent_gaps)
            predicted_dt = starts[-1] + timedelta(days=median_gap)
            window_start_dt = starts[-1] + timedelta(days=low_gap)
            window_end_dt = starts[-1] + timedelta(days=high_gap)
            predicted = predicted_dt.astimezone(self.local_tz).isoformat(timespec="minutes")
            window_start = window_start_dt.astimezone(self.local_tz).isoformat(timespec="minutes")
            window_end = window_end_dt.astimezone(self.local_tz).isoformat(timespec="minutes")

        confidence = "low"
        if data_source == "classified observations" and len(clusters) >= 12:
            confidence = "medium"
        elif len(clusters) < 3:
            confidence = "insufficient data"

        return TrendReport(
            data_source=data_source,
            timezone=self.timezone_name,
            first_post=points[0].astimezone(self.local_tz).isoformat(timespec="minutes"),
            last_post=points[-1].astimezone(self.local_tz).isoformat(timespec="minutes"),
            matching_posts=len(points),
            bulk_release_posts=bulk_posts,
            individual_availability_posts=individual_posts,
            last_bulk_release=last_bulk.astimezone(self.local_tz).isoformat(timespec="minutes") if last_bulk else None,
            last_individual_availability=last_individual.astimezone(self.local_tz).isoformat(timespec="minutes") if last_individual else None,
            bulk_release_events=len(bulk_clusters),
            bulk_median_gap_days=round(float(bulk_median_gap), 2) if bulk_median_gap is not None else None,
            next_bulk_predicted=next_bulk,
            next_bulk_window_start=bulk_start,
            next_bulk_window_end=bulk_end,
            legacy_posts=legacy_posts,
            unbookable_posts=int((summary or {}).get("unbookable", 0)),
            na_heartbeat_posts=int((summary or {}).get("na_heartbeat", 0)),
            unknown_image_posts=int((summary or {}).get("unknown_image", 0)),
            ocr_evidence=ocr_evidence,
            release_events=len(clusters),
            events_per_week=round(events_per_week, 2),
            median_posts_per_event=round(float(statistics.median(quantities)), 1),
            max_posts_per_event=max(quantities),
            median_gap_days=round(median_gap, 2) if median_gap is not None else None,
            peak_weekday=weekday,
            peak_two_hour_window=hour_window,
            predicted_next=predicted,
            predicted_window_start=window_start,
            predicted_window_end=window_end,
            confidence=confidence,
            caveat=caveat,
        )


class TrendService:
    def __init__(
        self,
        store: AlertStore,
        settings: TrendSettings,
        llm_client: FallbackLlmClient | None = None,
    ) -> None:
        self.store = store
        self.settings = settings
        self.llm_client = llm_client or build_llm_client(settings)

    def report(self) -> TrendReport:
        snapshot = self.store.latest_trend_snapshot()
        if snapshot:
            try:
                return TrendReport(**snapshot)
            except (TypeError, ValueError):
                LOGGER.warning("Ignoring invalid persisted trend snapshot")
        rows, source = self.store.trend_points()
        return TrendAnalyzer(self.settings.timezone).analyze(rows, source, self.store.classification_summary())

    def refresh_snapshot(self) -> TrendReport:
        rows, source = self.store.trend_points()
        report = TrendAnalyzer(self.settings.timezone).analyze(rows, source, self.store.classification_summary())
        self.store.save_trend_snapshot(report.as_dict(), source=source)
        return report

    def summarize(self, use_llm: bool = True) -> str:
        report = self.report()
        deterministic = format_report(report)
        if not use_llm or not self.llm_client.enabled or report.matching_posts == 0:
            return deterministic
        try:
            explanation = _llm_explanation(report, self.llm_client)
        except (LlmProviderError, OSError, ValueError, TimeoutError) as exc:
            LOGGER.warning("LLM trend explanation unavailable: %s", exc)
            return deterministic + "\n\nLLM explanation unavailable; statistics shown above."
        return deterministic + f"\n\nLLM summary ({explanation.provider}):\n" + explanation.content

    async def summarize_async(self, use_llm: bool = True) -> str:
        report = self.report()
        deterministic = format_report(report)
        if not use_llm or not self.llm_client.enabled or report.matching_posts == 0:
            return deterministic
        try:
            explanation = await asyncio.to_thread(_llm_explanation, report, self.llm_client)
        except (LlmProviderError, OSError, ValueError, TimeoutError) as exc:
            LOGGER.warning("LLM trend explanation unavailable: %s", exc)
            return deterministic + "\n\nLLM explanation unavailable; statistics shown above."
        return deterministic + f"\n\nLLM summary ({explanation.provider}):\n" + explanation.content


def format_report(report: TrendReport) -> str:
    if report.matching_posts == 0:
        return "No matching historical appointment posts have been recorded yet."
    lines = [
        f"Trend ({report.timezone}, {report.confidence} confidence)",
        f"Data: {report.matching_posts} matching posts in {report.release_events} likely release events",
        f"Types: {report.bulk_release_posts} bulk-release posts; {report.individual_availability_posts} individual availability reports",
        f"Excluded: {report.unbookable_posts} invalid/unbookable; {report.na_heartbeat_posts} NA heartbeats; {report.unknown_image_posts} unclassified images",
        f"Range: {report.first_post} to {report.last_post}",
    ]
    if report.last_bulk_release:
        lines.append(f"Last bulk release post: {report.last_bulk_release}")
    if report.last_individual_availability:
        lines.append(f"Last individual availability post: {report.last_individual_availability}")
    if report.bulk_release_events:
        lines.append(f"Bulk history: {report.bulk_release_events} release events")
    if report.next_bulk_predicted:
        lines.append(f"Next bulk-release statistical center: {report.next_bulk_predicted}")
        lines.append(f"Bulk historical window: {report.next_bulk_window_start} to {report.next_bulk_window_end}")
    elif report.bulk_release_events < 2:
        lines.append("Bulk forecast: insufficient historical bulk-release events for a cadence estimate")
    if report.events_per_week is not None and report.median_gap_days is not None:
        lines.append(f"Frequency: {report.events_per_week:g} events/week; median gap {report.median_gap_days:g} days")
    elif report.events_per_week is not None:
        lines.append(f"Frequency: {report.events_per_week:g} events/week; not enough event gaps for a cadence")
    if report.median_posts_per_event is not None:
        lines.append(
            f"Quantity proxy: median {report.median_posts_per_event:g} reports/event; max {report.max_posts_per_event}"
        )
    if report.peak_weekday and report.peak_two_hour_window:
        lines.append(f"Most common start: {report.peak_weekday}, {report.peak_two_hour_window}")
    if report.predicted_next:
        lines.append(f"Next statistical center: {report.predicted_next}")
        lines.append(f"Broad historical window: {report.predicted_window_start} to {report.predicted_window_end}")
    lines.append("Caveat: " + report.caveat)
    if report.ocr_evidence:
        lines.append("OCR evidence (cached, not authoritative): " + " | ".join(report.ocr_evidence))
    return "\n".join(lines)


def _llm_explanation(
    report: TrendReport,
    llm_client: FallbackLlmClient,
) -> LlmResponse:
    prompt = (
        "You explain a deterministic visa appointment posting trend report. "
        "Use only the JSON facts below. Do not change dates, calculate new dates, or claim certainty. "
        "The exact numbers and dates are already displayed, so do not repeat numbers, dates, weekdays, "
        "times, ranges, or intervals. In at most 80 words, explain how to interpret the report and why "
        "confidence is limited. Explain that bulk-release posts are distinct from individual reports, "
        "and that invalid, NA, spam, discussion, and booked-confirmation messages are excluded from "
        "availability forecasting. Explicitly say community posting activity is only a proxy and "
        "cannot predict an embassy release or guarantee a slot.\n\n"
        + json.dumps(report.as_dict(), sort_keys=True)
    )
    return llm_client.complete(
        [{"role": "user", "content": prompt}],
        max_tokens=120,
        temperature=0,
    )


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _mode(values: list[object]) -> object | None:
    if not values:
        return None
    counts = {value: values.count(value) for value in set(values)}
    return min(counts, key=lambda value: (-counts[value], str(value)))


def _prediction_bounds(gaps: list[float]) -> tuple[float, float]:
    if len(gaps) >= 4:
        quartiles = statistics.quantiles(gaps, n=4, method="inclusive")
        return quartiles[0], quartiles[2]
    return min(gaps), max(gaps)
