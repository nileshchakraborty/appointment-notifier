from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path


MAX_IMAGE_BYTES = 25 * 1024 * 1024


@dataclass(frozen=True)
class MediaAnalysis:
    sha256: str
    ocr_text: str
    features: dict[str, object]
    portal_state: str


class PortalMediaAnalyzer:
    """OCR-first portal classifier. OCR is optional; missing OCR is fail-closed."""

    def analyze(self, path: str | Path) -> MediaAnalysis:
        image_path = Path(path)
        try:
            image_bytes = image_path.read_bytes()
        except OSError:
            return MediaAnalysis("", "", {}, "unknown")
        if len(image_bytes) > MAX_IMAGE_BYTES:
            return MediaAnalysis("", "", {"rejected": "image_too_large"}, "unknown")
        digest = hashlib.sha256(image_bytes).hexdigest()
        text = self._ocr(image_path)
        normalized = " ".join(text.lower().split())
        has_calendar = bool(re.search(r"\b(?:calendar|schedule|select\s+date|\d{1,2}/\d{1,2}/\d{4})\b", normalized))
        has_time_rows = bool(re.search(r"\b\d{1,2}:\d{2}\b", normalized))
        counts = [int(value) for value in re.findall(r"(?:availability|available)\s*[:=-]?\s*(\d+)", normalized)]
        has_positive_count = any(value > 0 for value in counts)
        has_submit = "submit" in normalized
        submit_disabled = bool(re.search(r"(?:submit).{0,25}(?:disabled|grey|gray|inactive)", normalized))
        has_ofc = bool(re.search(r"\b(?:ofc|vac)\b", normalized))
        has_consular = "consular" in normalized
        has_ghost = bool(re.search(r"\bghost\s+slots?\b", normalized))

        if submit_disabled:
            state = "ghost_or_unbookable"
        elif has_ghost:
            state = "potential_ghost"
        elif has_ofc and not has_consular and has_positive_count:
            state = "partial_ofc_only"
        elif has_positive_count and (has_calendar or has_time_rows):
            state = "bookable"
        elif has_time_rows and has_submit and not submit_disabled:
            state = "bookable"
        elif has_calendar and (not has_time_rows and not has_positive_count):
            state = "ghost_or_unbookable"
        elif text:
            state = "unavailable_or_unknown"
        else:
            state = "unknown"
        features = {
            "has_calendar": has_calendar,
            "has_time_rows": has_time_rows,
            "availability_counts": counts,
            "has_positive_count": has_positive_count,
            "has_submit": has_submit,
            "submit_disabled": submit_disabled,
            "has_ofc": has_ofc,
            "has_consular": has_consular,
            "has_ghost": has_ghost,
        }
        return MediaAnalysis(digest, text, features, state)

    @staticmethod
    def _ocr(path: Path) -> str:
        try:
            import pytesseract
            from PIL import Image
        except ImportError:
            return ""
        try:
            return str(pytesseract.image_to_string(Image.open(path))).strip()
        except (OSError, RuntimeError, ValueError):
            return ""


def features_json(analysis: MediaAnalysis) -> str:
    return json.dumps(analysis.features, sort_keys=True)
