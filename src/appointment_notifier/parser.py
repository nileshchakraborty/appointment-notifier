from __future__ import annotations

import re
import unicodedata

from .models import SlotSignal


DEFAULT_REQUIRED_TERMS = (
    "h1",
    "h1b",
    "h-1",
    "h-1b",
    "h4",
    "h-4",
    "visa",
    "vac",
    "dropbox",
    "ofc",
    "consular",
    "appointment",
    "date",
    "dates",
    "slot",
    "slots",
    "jan",
    "january",
    "feb",
    "february",
    "mar",
    "march",
    "apr",
    "april",
    "may",
    "jun",
    "june",
    "jul",
    "july",
    "aug",
    "august",
    "sep",
    "sept",
    "september",
    "oct",
    "october",
    "nov",
    "november",
    "dec",
    "december",
)

DEFAULT_SUPPRESS_TERMS = (
    "no slot available",
    "no slots available",
    "no slots",
    "no h1 slots",
    "no h4 slots",
    "no consular available",
    "no consular slots",
    "no ofc available",
    "no ofc slots",
    "no appointment",
    "not available",
    "nothing available",
    "no dates",
    "no update",
    "closed",
    "unavailable",
)

POSITIVE_PATTERNS = (
    re.compile(r"\bavailable\b", re.IGNORECASE),
    re.compile(r"\bslots?\s+(?:are\s+)?available\b", re.IGNORECASE),
    re.compile(r"\bslots?\s+opened\b", re.IGNORECASE),
    re.compile(r"\bappointments?\s+(?:are\s+)?available\b", re.IGNORECASE),
    re.compile(r"\bappointments?\s+opened\b", re.IGNORECASE),
    re.compile(r"\bdates?\s+opened\b", re.IGNORECASE),
    re.compile(r"\bopened\s+up\b", re.IGNORECASE),
    re.compile(r"\b(?:available|opened|open)\s+(?:now|today|for|in)\b", re.IGNORECASE),
    re.compile(r"\bsaw\s+available\b", re.IGNORECASE),
    re.compile(r"\b\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)\b", re.IGNORECASE),
    re.compile(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{1,2}\b", re.IGNORECASE),
)

CHANNEL_NA_PATTERNS = (
    re.compile(r"^\s*n\s*/?\s*a\s*\d*\s*(?:all|h1|h4|,|reschedule|$)", re.IGNORECASE),
)

ADMIN_SUPPRESS_ALWAYS_PATTERNS = (
    re.compile(r"\banyone\s+posting\s+other\s+than\s+availability\s+will\s+be\s+banned\b", re.IGNORECASE),
)

RULE_ONLY_PATTERNS = (
    re.compile(r"\bthis\s+is\s+h1\s+h4\s+inperson\s+slots\s+availability\s+group\s+only\b", re.IGNORECASE),
    re.compile(r"\bpost\s+only\s+availability\b", re.IGNORECASE),
    re.compile(r"\bno\s+discussions?\b", re.IGNORECASE),
    re.compile(r"\bno\s+thank\s+you\b", re.IGNORECASE),
    re.compile(r"\bno\s+booked\s+messages?\b", re.IGNORECASE),
    re.compile(r"\byou\s+will\s+be\s+banned\b", re.IGNORECASE),
    re.compile(r"\bread\s+pinned\s+msgs?\b", re.IGNORECASE),
    re.compile(r"\bquestions?\s+ping\s+admins?\b", re.IGNORECASE),
    re.compile(r"\bjoin\s+inperson\s+q\s+and\s+a\s+group\b", re.IGNORECASE),
)

HELPER_ONLY_PATTERNS = (
    re.compile(r"\bselect\s+earliest\s+available\s+ofc\s+date\b", re.IGNORECASE),
    re.compile(r"\byou\s+may\s+find\s+consular\s+appointments\b", re.IGNORECASE),
    re.compile(r"\bhave\s+patience\b", re.IGNORECASE),
    re.compile(r"\bportal\s+will\s+be\s+slow\b", re.IGNORECASE),
    re.compile(r"\bno\s+need\s+to\s+rush\b", re.IGNORECASE),
)

SILENT_INFO_PATTERNS = (
    re.compile(r"\bselect\s+earliest\s+available\s+ofc\s+date\b", re.IGNORECASE),
    re.compile(r"\byou\s+may\s+find\s+consular\s+appointments\b", re.IGNORECASE),
    re.compile(r"\bofc\s+\d+\s*[-–—]?\s*opened\b", re.IGNORECASE),
)

LOUD_INFO_PATTERNS = (
    re.compile(r"^\s*still\s+available!?\s*$", re.IGNORECASE),
    re.compile(r"\bbulk\s+appointments?\s+opened\b", re.IGNORECASE),
    re.compile(r"\bgrab\s+them\s+before\s+they\s+are\s+gone\b", re.IGNORECASE),
    re.compile(r"\blot\s+available\b", re.IGNORECASE),
    re.compile(r"\bplenty\s+available\b", re.IGNORECASE),
    re.compile(r"\bmany\s+available\b", re.IGNORECASE),
)

BULK_PATTERNS = (
    re.compile(r"\bbulk\s+(?:appointments?|slots?|dates?)\b", re.IGNORECASE),
    re.compile(r"\b(?:bulk|mass)\s+(?:release|opening|opened|available)\b", re.IGNORECASE),
    re.compile(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s*[-–/]\s*(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)", re.IGNORECASE),
)

INVALID_PATTERNS = (
    re.compile(r"\bghost\s+slots?\b", re.IGNORECASE),
    re.compile(r"\bno\s+(?:time|time\s+slots?|submit\s+buttons?)\b", re.IGNORECASE),
    re.compile(r"\b(?:submit\s+button|slot)\s+(?:did\s+not|does\s+not|doesn't|didn't)\s+work\b", re.IGNORECASE),
    re.compile(r"\bnot\s+bookable\b", re.IGNORECASE),
    re.compile(r"\b(?:ofc|vac)\s+(?:available|opened)\b.*\bno\s+consular\b", re.IGNORECASE),
)

SPAM_PATTERNS = (
    re.compile(r"\bonlyfans\b", re.IGNORECASE),
    re.compile(r"\bprivate\s+(?:link|video)\b", re.IGNORECASE),
)

BOOKED_PATTERNS = (
    re.compile(r"\b(?:booked|got|received|confirmed)\s+(?:an?\s+)?(?:appointment|slot|date)", re.IGNORECASE),
    re.compile(r"\b(?:booked|confirmed|received)\b.{0,40}\b(?:appointment|slot|date)\b", re.IGNORECASE),
    re.compile(r"\bwas\s+able\s+to\s+book\b", re.IGNORECASE),
)

QUESTION_PATTERNS = (
    re.compile(r"^\s*(?:is|are|was|were|can|could|does|did|has|have|when|where|how|anyone)\b", re.IGNORECASE),
    re.compile(r"\?\s*$"),
)

LOCATIONS = (
    "chennai",
    "chn",
    "hyderabad",
    "hyd",
    "kolkata",
    "mumbai",
    "new delhi",
    "delhi",
)

VISA_TERMS = ("h1", "h1b", "h-1", "h-1b", "h4", "h-4", "dropbox", "ofc", "consular")


class VisaSlotParser:
    def __init__(
        self,
        required_terms: tuple[str, ...] = (),
        suppress_terms: tuple[str, ...] = (),
    ) -> None:
        self.required_terms = tuple(term.lower() for term in required_terms) or DEFAULT_REQUIRED_TERMS
        self.suppress_terms = tuple(term.lower() for term in suppress_terms) or DEFAULT_SUPPRESS_TERMS

    def parse(
        self,
        text: str,
        has_image: bool = False,
        *,
        ocr_text: str = "",
        portal_state: str | None = None,
    ) -> SlotSignal:
        source_text = " ".join(part for part in (text, ocr_text) if part)
        normalized = " ".join(_clean_text(source_text).lower().split())
        if not normalized and not has_image:
            return SlotSignal(False, "empty message")

        compact = re.sub(r"[^a-z0-9,/]+", "", normalized)
        if any(pattern.search(text) or pattern.search(compact) for pattern in CHANNEL_NA_PATTERNS):
            return SlotSignal(False, "suppressed by channel NA shorthand", available_state=False, category="na_heartbeat", ocr_text=ocr_text, portal_state=portal_state)

        if self._first_pattern(normalized, SPAM_PATTERNS):
            return SlotSignal(False, "suppressed by spam pattern", category="spam", ocr_text=ocr_text, portal_state=portal_state)

        booked_hit = self._first_pattern(normalized, BOOKED_PATTERNS)
        positive_hint = self._first_pattern(normalized, POSITIVE_PATTERNS)
        if booked_hit and not positive_hint:
            return SlotSignal(False, "appointment booking confirmation", category="booked_confirmation", ocr_text=ocr_text, portal_state=portal_state)

        suppress_hit = self._first_contains(normalized, self.suppress_terms)
        if suppress_hit:
            return SlotSignal(False, f"suppressed by term: {suppress_hit}", available_state=False, category="unbookable", ocr_text=ocr_text, portal_state=portal_state)

        admin_suppress_hit = self._first_pattern(normalized, ADMIN_SUPPRESS_ALWAYS_PATTERNS)
        rule_hit = self._first_pattern(normalized, RULE_ONLY_PATTERNS)
        helper_hit = self._first_pattern(normalized, HELPER_ONLY_PATTERNS)
        silent_hit = self._first_pattern(normalized, SILENT_INFO_PATTERNS)
        loud_hit = self._first_pattern(normalized, LOUD_INFO_PATTERNS)
        positive_hit = positive_hint

        if admin_suppress_hit:
            return SlotSignal(False, "suppressed by group rule/admin text", category="admin", ocr_text=ocr_text, portal_state=portal_state)

        if rule_hit and not (silent_hit or loud_hit or positive_hit):
            return SlotSignal(False, "suppressed by group rule/admin text", category="admin", ocr_text=ocr_text, portal_state=portal_state)

        if helper_hit and not (silent_hit or loud_hit or positive_hit):
            return SlotSignal(False, "suppressed by helper-only text", category="discussion", ocr_text=ocr_text, portal_state=portal_state)

        invalid_hit = self._first_pattern(normalized, INVALID_PATTERNS)
        if invalid_hit:
            return SlotSignal(False, "unbookable or invalid appointment report", available_state=False, category="unbookable", ocr_text=ocr_text, portal_state=portal_state)

        if self._first_pattern(normalized, QUESTION_PATTERNS):
            return SlotSignal(False, "discussion or question", category="discussion", ocr_text=ocr_text, portal_state=portal_state)

        if portal_state in {"ghost_or_unbookable", "partial_ofc_only", "unavailable_or_unknown"}:
            return SlotSignal(False, f"portal classified as {portal_state}", available_state=False, category="unbookable", ocr_text=ocr_text, portal_state=portal_state)

        silent = bool(silent_hit and not loud_hit)

        if has_image:
            locations = tuple(location for location in LOCATIONS if location in normalized)
            visa_terms = tuple(term for term in VISA_TERMS if term in normalized)
            if not (positive_hit or loud_hit or portal_state == "bookable"):
                return SlotSignal(False, "image requires caption or OCR classification", category="unknown_image", ocr_text=ocr_text, portal_state=portal_state)
            return SlotSignal(
                True,
                "individual availability report with image",
                locations,
                visa_terms,
                silent,
                available_state=True,
                category="bulk_release" if self._first_pattern(normalized, BULK_PATTERNS) else "individual_availability",
                ocr_text=ocr_text,
                portal_state=portal_state,
            )

        required_hit = self._first_contains(normalized, self.required_terms)
        if not (required_hit or loud_hit):
            return SlotSignal(False, "missing required visa or appointment term", category="discussion", ocr_text=ocr_text, portal_state=portal_state)

        if not (positive_hit or loud_hit or silent_hit):
            return SlotSignal(False, "missing positive availability signal", category="discussion", ocr_text=ocr_text, portal_state=portal_state)

        locations = tuple(location for location in LOCATIONS if location in normalized)
        visa_terms = tuple(term for term in VISA_TERMS if term in normalized)
        is_bulk = bool(self._first_pattern(normalized, BULK_PATTERNS))
        reason = "silent informational availability signal" if silent else "positive availability signal"
        return SlotSignal(True, reason, locations, visa_terms, silent, available_state=True,
                          category="bulk_release" if is_bulk else "individual_availability",
                          ocr_text=ocr_text, portal_state=portal_state)

    @staticmethod
    def _first_contains(text: str, terms: tuple[str, ...]) -> str | None:
        # Terms are configuration, not arbitrary substrings.  A suppress term
        # such as ``NA`` must not match ``Chennai`` or ``January`` and ``H1``
        # must not match ``H1B``.  Preserve spaces inside multi-word phrases.
        for term in terms:
            escaped = re.escape(term.strip().lower()).replace(r"\ ", r"\s+")
            if re.search(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", text.lower()):
                return term
        return None

    @staticmethod
    def _first_pattern(text: str, patterns: tuple[re.Pattern[str], ...]) -> str | None:
        return next((pattern.pattern for pattern in patterns if pattern.search(text)), None)


def _clean_text(text: str) -> str:
    """Normalize Telegram zero-width/format characters before classification."""
    normalized = unicodedata.normalize("NFKC", text or "")
    return "".join(char for char in normalized if unicodedata.category(char) != "Cf")
