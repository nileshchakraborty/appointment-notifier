from pathlib import Path

from appointment_notifier.media import PortalMediaAnalyzer


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
