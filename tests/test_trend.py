from datetime import datetime, timedelta, timezone

from appointment_notifier.trend import TrendAnalyzer, format_report
from appointment_notifier.models import SlotSignal, TelegramMessage
from appointment_notifier.store import AlertStore
from appointment_notifier.parser import VisaSlotParser


def _row(message_id: int, sent_at: datetime, category: str = "individual_availability") -> dict[str, object]:
    return {"message_id": message_id, "sent_at": sent_at.isoformat(), "category": category}


def test_clusters_posts_and_predicts_from_event_gaps() -> None:
    start = datetime(2026, 7, 1, 4, tzinfo=timezone.utc)
    rows = [
        _row(1, start),
        _row(2, start + timedelta(hours=1)),
        _row(3, start + timedelta(days=7)),
        _row(4, start + timedelta(days=7, hours=2)),
        _row(5, start + timedelta(days=14)),
    ]

    report = TrendAnalyzer("Asia/Kolkata").analyze(rows, "classified observations")

    assert report.matching_posts == 5
    assert report.release_events == 3
    assert report.median_posts_per_event == 2
    assert report.median_gap_days == 7
    assert report.predicted_next.startswith("2026-07-22")
    assert report.confidence == "low"
    assert report.individual_availability_posts == 5


def test_report_separates_bulk_from_individual_posts() -> None:
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    report = TrendAnalyzer().analyze(
        [_row(1, now, "bulk_release"), _row(2, now + timedelta(hours=1))],
        "classified observations",
    )

    assert report.bulk_release_posts == 1
    assert report.individual_availability_posts == 1
    assert "bulk-release posts" in format_report(report)


def test_empty_history_is_explicit() -> None:
    report = TrendAnalyzer().analyze([], "classified observations")

    assert report.confidence == "insufficient data"
    assert format_report(report) == "No matching historical appointment posts have been recorded yet."


def test_report_labels_quantity_as_proxy() -> None:
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    report = TrendAnalyzer().analyze([_row(1, now)], "legacy alerts")

    assert "Quantity proxy" in format_report(report)
    assert "not the number of bookable appointments" in format_report(report)


def test_observations_extend_legacy_baseline(tmp_path) -> None:
    store = AlertStore(tmp_path / "state.sqlite3")
    store.conn.execute(
        """
        insert into alerts (digest, message_id, title, body, sent_at)
        values ('legacy', 1, 'slot', 'old report', '2026-07-01T00:00:00+00:00')
        """
    )
    store.record_observation(
        TelegramMessage(
            message_id=2,
            text="slots available",
            sent_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        ),
        SlotSignal(matched=True, reason="positive"),
    )

    rows, source = store.trend_points()

    assert [row["message_id"] for row in rows] == [1, 2]
    assert source == "classified observations with legacy baseline"


def test_reclassifies_pre_category_observations(tmp_path) -> None:
    store = AlertStore(tmp_path / "state.sqlite3")
    store.record_observation(
        TelegramMessage(1, "", datetime(2026, 8, 1, tzinfo=timezone.utc), has_image=True),
        SlotSignal(matched=True, reason="legacy image"),
    )

    assert store.reclassify_observations(VisaSlotParser()) == 1
    row = store.conn.execute("select matched, category from observed_messages where message_id = 1").fetchone()
    assert tuple(row) == (0, "unknown_image")
