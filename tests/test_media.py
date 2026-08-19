from pathlib import Path

from appointment_notifier.media import PortalMediaAnalyzer
from appointment_notifier.telegram_watcher import _select_media_dir


def test_portal_layout_detects_bookable_screenshot(tmp_path, monkeypatch):
    image = tmp_path / "portal.png"
    image.write_bytes(b"fixture")
    monkeypatch.setattr(
        PortalMediaAnalyzer,
        "_ocr",
        staticmethod(lambda path: "Schedule OFC Appointment HYDERABAD VAC Consular 09:00 availability 21 Submit"),
    )

    result = PortalMediaAnalyzer().analyze(image)

    assert result.portal_state == "bookable"
    assert result.features["has_submit"] is True
    assert result.features["availability_counts"] == [21]


def test_portal_layout_rejects_ghost_screenshot(tmp_path, monkeypatch):
    image = tmp_path / "portal.png"
    image.write_bytes(b"fixture")
    monkeypatch.setattr(
        PortalMediaAnalyzer,
        "_ocr",
        staticmethod(lambda path: "Calendar HYDERABAD VAC ghost slots no time submit disabled"),
    )

    assert PortalMediaAnalyzer().analyze(image).portal_state == "ghost_or_unbookable"


def test_media_staging_selects_first_writable_candidate(tmp_path):
    first = tmp_path / "drive1"
    second = tmp_path / "drive2"

    assert _select_media_dir((first, second)) == first


def test_oversized_media_is_rejected_before_ocr(tmp_path, monkeypatch):
    image = tmp_path / "large.png"
    image.write_bytes(b"x" * (25 * 1024 * 1024 + 1))
    called = False

    def fail_ocr(path):
        nonlocal called
        called = True
        raise AssertionError("OCR should not run for oversized media")

    monkeypatch.setattr(PortalMediaAnalyzer, "_ocr", staticmethod(fail_ocr))
    result = PortalMediaAnalyzer().analyze(image)

    assert result.portal_state == "unknown"
    assert result.features["rejected"] == "image_too_large"
    assert called is False
