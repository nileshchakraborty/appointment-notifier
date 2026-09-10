from appointment_notifier.parser import VisaSlotParser


def test_positive_slot_message_matches():
    signal = VisaSlotParser().parse("H1B Dropbox slots available in Chennai on May 10")

    assert signal.matched is True
    assert "chennai" in signal.locations
    assert "h1b" in signal.visa_terms


def test_no_slots_message_is_suppressed():
    signal = VisaSlotParser().parse("H1B Dropbox no slots available today")

    assert signal.matched is False
    assert signal.reason == "suppressed by term: no slots available"


def test_channel_na_shorthand_is_suppressed():
    parser = VisaSlotParser()

    assert parser.parse("NA 1 ALL").matched is False
    assert parser.parse("NA1all").matched is False
    assert parser.parse("Na3 all").matched is False
    assert parser.parse("NA 1 H1 , 2 H4 all").matched is False


def test_channel_available_shorthand_matches():
    parser = VisaSlotParser()

    assert parser.parse("Only Ofc Available").matched is True
    assert parser.parse("H1b Jul 2027 Chn/Hyd available").matched is True
    assert parser.parse("H1 July 2027 available").matched is True
    assert parser.parse("Hyd April May 2027 available").matched is True
    assert parser.parse("Dec 8th,9th Available").matched is True
    assert parser.parse("Mumbai VAC Jan available").matched is True
    assert parser.parse("July / aug 2027 slots available").matched is True
    assert parser.parse("June 2026 dates opened").matched is True
    assert parser.parse("Few slots opened, was able to book OFC in Dec and Consular in Mar").matched is True
    assert parser.parse("Ofc 2 - opened but consular posts are empty").matched is True


def test_image_only_message_requires_caption_or_ocr():
    signal = VisaSlotParser().parse("", has_image=True)

    assert signal.matched is False
    assert signal.category == "unknown_image"


def test_image_with_na_caption_is_suppressed():
    signal = VisaSlotParser().parse("NA 1 ALL", has_image=True)

    assert signal.matched is False
    assert signal.reason == "suppressed by channel NA shorthand"


def test_ocr_classified_bookable_image_matches_without_caption():
    signal = VisaSlotParser().parse(
        "",
        has_image=True,
        ocr_text="Calendar 09:00 availability 21 Submit",
        portal_state="bookable",
    )

    assert signal.matched is True
    assert signal.category == "individual_availability"


def test_group_rules_are_suppressed():
    text = """
    THIS IS H1 H4 INPERSON SLOTS AVAILABILITY GROUP ONLY
    POST ONLY AVAILABILITY
    NA means NOT AVAILABLE
    NO DISCUSSIONS IN THIS GROUP
    YOU WILL BE BANNED FOR NOT FOLLOWING RULES
    """

    signal = VisaSlotParser().parse(text)

    assert signal.matched is False


def test_banned_boilerplate_is_suppressed_even_with_helper_text():
    text = """
    ANYONE POSTING OTHER THAN AVAILABILITY WILL BE BANNED FROM THE GROUP
    SELECT EARLIEST AVAILABLE OFC DATE AND YOU MAY FIND CONSULAR APPOINTMENTS
    HAVE PATIENCE AS THERE ARE 1000S OF THEM BOOKING
    """

    signal = VisaSlotParser().parse(text)

    assert signal.matched is False
    assert signal.reason == "suppressed by group rule/admin text"


def test_helper_only_message_is_silent():
    signal = VisaSlotParser().parse("SELECT EARLIEST AVAILABLE OFC DATE AND YOU MAY FIND CONSULAR APPOINTMENTS")

    assert signal.matched is True
    assert signal.silent is True
    assert signal.reason == "silent informational availability signal"


def test_bulk_appointments_opened_is_loud():
    text = """
    JULY AUGUST SEPTEMBER BULK APPOINTMENTS OPENED
    GRAB THEM BEFORE THEY ARE GONE
    NO DISCUSSIONS.. NO BOOKED MESSAGES.. NO THANK YOU MESSAGES
    POST ONLY AVAILABILITY
    CHECK HYDERABAD
    SELECT EARLIEST OFC DATE
    """

    signal = VisaSlotParser().parse(text)

    assert signal.matched is True
    assert signal.silent is False
    assert signal.reason == "positive availability signal"
    assert signal.category == "bulk_release"


def test_unbookable_reports_are_suppressed():
    ghost = VisaSlotParser().parse("Ghost slots every 30 mins with no time or submit buttons")
    broken = VisaSlotParser().parse("Submit button didn't work")

    assert ghost.category == "unbookable"
    assert ghost.matched is False
    assert broken.category == "unbookable"
    assert broken.matched is False


def test_ofc_only_reports_match_with_category():
    partial = VisaSlotParser().parse("OFC available but no consular")
    only_ofc = VisaSlotParser().parse("Only Ofc Available")

    assert partial.category == "ofc_only"
    assert partial.matched is True
    assert only_ofc.category == "ofc_only"
    assert only_ofc.matched is True


def test_potential_ghost_reports_match_with_category():
    ghost_caption = VisaSlotParser().parse("Ghost slot I guess", has_image=True, ocr_text="Schedule OFC Appointment availability 5")
    ghost_text = VisaSlotParser().parse("Ghost slot available in Chennai")

    assert ghost_caption.category == "potential_ghost"
    assert ghost_caption.matched is True
    assert ghost_text.category == "potential_ghost"
    assert ghost_text.matched is True


def test_zero_width_spam_is_suppressed():
    signal = VisaSlotParser().parse("Only\u200bFans private\u200blink")

    assert signal.category == "spam"
    assert signal.matched is False


def test_questions_and_bookings_are_not_availability():
    question = VisaSlotParser().parse("Are H1B appointments available?")
    booked = VisaSlotParser().parse("I booked my H1B appointment")

    assert question.category == "discussion"
    assert booked.category == "booked_confirmation"
    assert not question.matched and not booked.matched


def test_still_available_is_loud_but_question_is_not():
    parser = VisaSlotParser()

    loud = parser.parse("Still available!")
    question = parser.parse("Is it available now")

    assert loud.matched is True
    assert loud.silent is False
    assert question.matched is False


def test_ofc_opened_empty_consular_is_silent():
    signal = VisaSlotParser().parse("Ofc 2 - opened but consular posts are empty")

    assert signal.matched is True
    assert signal.silent is True
    assert signal.reason == "silent informational availability signal"


def test_unrelated_message_does_not_match():
    signal = VisaSlotParser().parse("Good morning everyone")

    assert signal.matched is False


def test_configured_terms_match_words_not_substrings():
    parser = VisaSlotParser(required_terms=("h1", "h1b"), suppress_terms=("na",))

    assert parser.parse("Chennai H1B July available").matched is True
    assert parser.parse("January H1B slots available").matched is True
    assert parser.parse("NA").category == "na_heartbeat"


def test_app_build_alert_titles_and_warning_bodies():
    from datetime import datetime, timezone
    from appointment_notifier.app import AppointmentNotifierApp
    from appointment_notifier.models import TelegramMessage

    app = type("DummyApp", (), {
        "settings": type("Settings", (), {"telegram": type("TG", (), {"channel": "https://t.me/test"})()})(),
        "_build_alert": AppointmentNotifierApp._build_alert,
    })()

    msg = TelegramMessage(101, "Ghost slot I guess", datetime.now(timezone.utc), url="https://t.me/test/101")
    ghost_sig = VisaSlotParser().parse("Ghost slot I guess", has_image=True, ocr_text="Schedule OFC 10/12/2026 availability 5")
    alert = app._build_alert(msg, ghost_sig)
    assert "⚠️ Potential Ghost Slot" in alert.title
    assert "⚠️ Potential Ghost Slot / Unverified Report" in alert.body

    ofc_msg = TelegramMessage(102, "OFC available but no consular", datetime.now(timezone.utc), url="https://t.me/test/102")
    ofc_sig = VisaSlotParser().parse("OFC available but no consular")
    ofc_alert = app._build_alert(ofc_msg, ofc_sig)
    assert "📌 OFC / Biometrics" in ofc_alert.title
    assert "OFC / Biometrics slot availability detected" in ofc_alert.body
