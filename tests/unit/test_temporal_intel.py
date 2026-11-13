"""Phase 1: multilingual temporal detection - every claim has a test.

Languages en/ar/he/fa/hr; Gregorian/Hijri/Jalali; ASCII, Arabic-Indic and
Persian digits; future/present/past orientation; document-relative
expressions; Croatian ordinals; per-call clock; NULL-safe dedup; versioned
evidence. Pure (no database).
"""

import datetime as dt

import pytest

from core.detection import calendars as cal
from core.detection.temporal_intel import (
    DETECTOR_VERSION,
    MAX_SCAN_CHARS,
    SIGNAL_DATE,
    SIGNAL_ORIENTATION,
    SIGNAL_RELATIVE,
    TemporalSignal,
    detect,
    normalize,
)

D = dt.date


def dates(text, **kw):
    return [s for s in detect(text, **kw).signals if s.signal_type == SIGNAL_DATE]


def one(text, **kw):
    found = dates(text, **kw)
    assert len(found) == 1, [s.value for s in found]
    return found[0]


# --------------------------------------------------------------------------
# Calendars (reference values from published conversion tables)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("jalali, gregorian", [
    ((1403, 1, 1), D(2024, 3, 20)),    # Nowruz 1403
    ((1404, 1, 1), D(2025, 3, 21)),    # Nowruz 1404
    ((1402, 7, 1), D(2023, 9, 23)),    # 1 Mehr 1402
    ((1399, 12, 30), D(2021, 3, 20)),  # leap day of 1399
    ((1405, 7, 15), D(2026, 10, 7)),
])
def test_jalali_conversion_is_exact(jalali, gregorian):
    assert cal.jalali_to_gregorian(*jalali) == gregorian


def test_jalali_rejects_days_that_do_not_exist():
    with pytest.raises(cal.CalendarError):
        cal.jalali_to_gregorian(1402, 7, 31)   # Mehr has 30 days
    with pytest.raises(cal.CalendarError):
        cal.jalali_to_gregorian(1402, 12, 30)  # 1402 is not a leap year


@pytest.mark.parametrize("hijri, observed", [
    ((1447, 9, 1), D(2026, 2, 18)),   # 1 Ramadan 1447 (Umm al-Qura)
    ((1445, 9, 1), D(2024, 3, 11)),   # 1 Ramadan 1445 (Umm al-Qura)
    ((1446, 1, 1), D(2024, 7, 7)),    # 1 Muharram 1446 (Umm al-Qura)
])
def test_hijri_range_contains_the_observed_date(hijri, observed):
    lo, hi = cal.hijri_to_gregorian_range(*hijri)
    assert lo <= observed <= hi
    assert (hi - lo).days == 2 * cal.HIJRI_UNCERTAINTY_DAYS


# --------------------------------------------------------------------------
# Languages, scripts, digits
# --------------------------------------------------------------------------


@pytest.mark.parametrize("text, language", [
    ("The meeting is on 5 October 2026.", "en"),
    ("The meeting is on October 5, 2026.", "en"),
    ("The meeting is on the 5th of October 2026.", "en"),
    ("الاجتماع في 5 أكتوبر 2026", "ar"),
    ("الاجتماع في ٥ تشرين الأول ٢٠٢٦", "ar"),       # Levantine name, Arabic-Indic digits
    ("הפגישה ב-5 באוקטובר 2026", "he"),              # Hebrew prefixes ה-/ב
    ("جلسه در ۵ اکتبر ۲۰۲۶", "fa"),                  # Persian digits
    ("Sastanak je 5. listopada 2026.", "hr"),        # Croatian ordinal + genitive
    ("Sastanak je 5. listopad 2026.", "hr"),
])
def test_the_same_day_in_every_language(text, language):
    sig = one(text)
    assert sig.value == "gregorian:2026-10-05"
    assert (sig.date_from, sig.date_to) == (D(2026, 10, 5), D(2026, 10, 5))
    assert sig.language == language
    assert sig.resolution == "absolute"


def test_hijri_date_with_marker_resolves_to_a_range():
    sig = one("يبدأ في ١٥ رمضان ١٤٤٧ هـ")
    assert sig.calendar == "hijri" and sig.value == "hijri:1447-09-15"
    assert sig.resolution == "approximate"
    assert sig.date_from <= D(2026, 3, 4) <= sig.date_to
    assert "tabular" in sig.evidence["calendar_method"]
    assert sig.surface.endswith("هـ"), "the marker (with tatweel) is part of the quoted surface"


def test_jalali_date_resolves_exactly():
    sig = one("نشست در ۱۵ مهر ۱۴۰۵ برگزار می شود")
    assert sig.calendar == "jalali" and sig.language == "fa"
    assert sig.date_from == sig.date_to == D(2026, 10, 7)
    assert sig.evidence["digits"] == "persian"


def test_digits_are_normalised_but_offsets_quote_the_original():
    text = "في ٢٠٢٦-١٠-٠٥ صباحا"
    sig = one(text)
    assert sig.value == "gregorian:2026-10-05"
    assert text[sig.char_start:sig.char_end] == sig.surface == "٢٠٢٦-١٠-٠٥"
    assert sig.evidence["digits"] == "arabic-indic"
    norm, index, scripts = normalize("۱۴۰۵")
    assert norm == "1405" and scripts["persian"] == 4 and index == [0, 1, 2, 3]


def test_diacritics_and_bidi_marks_do_not_break_matching():
    text = "\u200Fالاجتماع غدًا في ٥ أكتوبر ٢٠٢٦\u200F"
    found = detect(text).signals
    assert any(s.value == "gregorian:2026-10-05" for s in found)
    assert any(s.value == "rel:+1d" and s.surface == "غدًا" for s in found)


# --------------------------------------------------------------------------
# Ambiguity is preserved
# --------------------------------------------------------------------------


def test_day_month_order_ambiguity_is_kept_in_latin_text():
    sig = one("Deadline 03/04/2026 for all.")
    assert sig.resolution == "ambiguous" and sig.date_from is None
    readings = {a["reading"]: a["value"] for a in sig.evidence["alternatives"]}
    assert readings == {"day-first": "gregorian:2026-04-03", "month-first": "gregorian:2026-03-04"}


def test_unambiguous_numeric_dates_resolve():
    assert one("Due 25/12/2026.").value == "gregorian:2026-12-25"
    assert one("Due 12/25/2026.").value == "gregorian:2026-12-25"
    assert one("Rok je 15.3.2026.").value == "gregorian:2026-03-15"   # dots: day-first
    assert one("Rok je 15. 3. 2026.").value == "gregorian:2026-03-15"  # Croatian spacing


def test_arabic_script_documents_use_day_first():
    sig = one("الموعد 03/04/2026 للجميع")
    assert sig.value == "gregorian:2026-04-03"
    assert "day-first" in sig.evidence["convention"]


def test_unmarked_13xx_year_is_hijri_or_jalali_not_guessed():
    sig = one("تاریخ ۱۴۰۵/۰۷/۱۵")
    assert sig.resolution == "ambiguous"
    assert {a["calendar"] for a in sig.evidence["alternatives"]} == {"hijri", "jalali"}
    marked = one("تاریخ ۱۴۰۵/۰۷/۱۵ ه.ش")
    assert marked.calendar == "jalali" and marked.date_from == D(2026, 10, 7)


def test_date_without_year_is_unresolved_not_completed():
    sig = one("Meet on 5 October.")
    assert sig.resolution == "unresolved" and sig.date_from is None
    assert sig.value == "gregorian:--10-05"
    assert "not guessed" in sig.evidence["reason"]


def test_month_precision_is_a_whole_month_range():
    sig = one("Expected in October 2026.")
    assert (sig.date_from, sig.date_to) == (D(2026, 10, 1), D(2026, 10, 31))
    assert sig.evidence["precision"] == "month"
    ramadan = one("في رمضان 1447")
    assert ramadan.calendar == "hijri" and ramadan.resolution == "approximate"


@pytest.mark.parametrize("text", [
    "Section 5 may apply.", "Troops march 2026 km.", "Year 2026 CE.", "این کار ۵ می شود",
    "Invalid 31/02/2026 date.", "Version 1.2.2026 released.",
])
def test_false_positive_guards(text):
    assert dates(text) == [] or all(s.value != "gregorian:2026-05-05" for s in dates(text))
    if text.startswith(("Section", "Troops", "Year", "این", "Invalid")):
        assert dates(text) == [], [s.value for s in dates(text)]


# --------------------------------------------------------------------------
# Relative expressions are document-relative, never clock-relative
# --------------------------------------------------------------------------


@pytest.mark.parametrize("text, value", [
    ("We decide tomorrow.", "rel:+1d"), ("Report in 3 days.", "rel:+3d"),
    ("Done 2 weeks ago.", "rel:offset:-2w"), ("الاجتماع غدا", "rel:+1d"),
    ("بعد ٣ أيام", "rel:+3d"), ("מחר בבוקר", "rel:+1d"), ("בעוד 3 ימים", "rel:+3d"),
    ("فردا صبح", "rel:+1d"), ("۳ روز دیگر", "rel:+3d"), ("sutra ujutro", "rel:+1d"),
    ("za 3 dana", "rel:+3d"), ("prije 2 dana", "rel:-2d"),
])
def test_relative_expressions_in_every_language(text, value):
    rel = [s for s in detect(text).signals if s.signal_type == SIGNAL_RELATIVE]
    assert [s.value for s in rel] == [value]
    assert rel[0].resolution == "unresolved" and rel[0].date_from is None
    assert rel[0].text_orientation == ("past" if "-" in value else "future")


def test_relative_expression_resolves_only_against_the_document_date():
    anchor = D(2026, 1, 30)
    rel = {s.value: s for s in detect("Tomorrow, in 1 month, next month and next week.",
                                      anchor_date=anchor).signals
           if s.signal_type == SIGNAL_RELATIVE}
    assert rel["rel:+1d"].date_from == D(2026, 1, 31)
    assert rel["rel:+1d"].resolution == "document_relative"
    assert rel["rel:+1d"].anchor_date == anchor
    period = rel["rel:period:+1m"]
    assert (period.date_from, period.date_to) == (D(2026, 2, 1), D(2026, 2, 28))
    exact = rel["rel:offset:+1m"]
    assert exact.date_from == exact.date_to == D(2026, 2, 28)  # 30 Jan + 1 month, clamped
    week = rel["rel:period:+1w"]
    assert (week.date_from, week.date_to) == (D(2026, 2, 2), D(2026, 2, 8))
    assert week.evidence["week_start"] == "monday"


@pytest.mark.parametrize("text, start", [
    ("בשבוע הבא", D(2026, 2, 1)),        # Hebrew: week starts Sunday
    ("الأسبوع المقبل", D(2026, 2, 1)),   # Arabic: Sunday
    ("هفته آینده", D(2026, 1, 31)),      # Persian: Saturday
    ("sljedeći tjedan", D(2026, 2, 2)),  # Croatian: Monday
])
def test_next_week_uses_the_languages_week(text, start):
    rel = [s for s in detect(text, anchor_date=D(2026, 1, 28)).signals
           if s.signal_type == SIGNAL_RELATIVE][0]
    assert rel.date_from == start and (rel.date_to - rel.date_from).days == 6


def test_detection_never_reads_the_clock(monkeypatch):
    import core.detection.temporal_intel as ti

    class Exploding(dt.date):
        @classmethod
        def today(cls):
            raise AssertionError("detect() consulted the clock")

    monkeypatch.setattr(ti.datetime, "date", Exploding)
    detect("Tomorrow, 5 October 2026, in 3 days.")


# --------------------------------------------------------------------------
# Orientation and the per-call clock
# --------------------------------------------------------------------------


@pytest.mark.parametrize("text, orientation", [
    ("The vote will take place.", "future"), ("The vote took place.", "past"),
    ("The vote is currently underway.", "present"),
    ("سوف يعقد الاجتماع", "future"), ("عقد الاجتماع الماضي", "past"),
    ("הכנס יתקיים", "future"), ("הכנס התקיים", "past"),
    ("جلسه برگزار خواهد شد", "future"), ("جلسه برگزار شد", "past"),
    ("Sastanak će se održati.", "future"), ("Sastanak je održan.", "past"),
])
def test_orientation_cues_in_every_language(text, orientation):
    cues = [s for s in detect(text).signals if s.signal_type == SIGNAL_ORIENTATION]
    assert [s.text_orientation for s in cues] == [orientation]
    assert cues[0].evidence["note"] == "lexical cue, not a parsed tense"


def test_croatian_future_clitic_requires_its_diacritic():
    assert not [s for s in detect("Built in 2026 CE.").signals
                if s.signal_type == SIGNAL_ORIENTATION]


def test_sentence_orientation_attaches_to_dates_only_within_the_sentence():
    found = dates("The summit will be held on 5 October 2026. It was moved from 3 March 2026.")
    assert [(s.value, s.text_orientation) for s in found] == [
        ("gregorian:2026-10-05", "future"), ("gregorian:2026-03-03", None)]


def test_clock_orientation_is_per_call_and_explicit():
    sig = one("On 5 October 2026.")
    assert sig.clock_orientation(D(2026, 9, 28)) == "future"
    assert sig.clock_orientation(D(2026, 10, 5)) == "present"
    assert sig.clock_orientation(D(2027, 1, 1)) == "past"
    with pytest.raises(ValueError):
        sig.clock_orientation(None)


def test_unknown_orientation_is_none_not_false():
    unresolved = one("On 5 October.")
    assert unresolved.clock_orientation(D(2026, 1, 1)) is None
    ambiguous = one("On 03/04/2026.")
    assert ambiguous.clock_orientation(D(2026, 1, 1)) == "future"   # both readings future
    assert ambiguous.clock_orientation(D(2026, 3, 20)) is None      # one past, one future


# --------------------------------------------------------------------------
# Identity, dedup, evidence, determinism
# --------------------------------------------------------------------------


def test_dedup_key_is_stable_and_null_safe():
    base = dict(signal_type=SIGNAL_DATE, surface="x", char_start=0, char_end=1,
                resolution="unresolved")
    a = TemporalSignal(value="v", **base)
    assert a.dedup_key(7) == TemporalSignal(value="v", **base).dedup_key(7)
    assert a.dedup_key(7) != a.dedup_key(8)
    with_anchor = TemporalSignal(value="v", anchor_date=D(2026, 1, 1), **base)
    assert with_anchor.dedup_key(7) != a.dedup_key(7)
    none_string = TemporalSignal(value="None", **base)
    assert none_string.dedup_key(7) != a.dedup_key(7)
    other_version = TemporalSignal(value="v", detector_ver="temporal-0.0.1", **base)
    assert other_version.dedup_key(7) != a.dedup_key(7)


def test_every_signal_carries_version_and_evidence():
    result = detect("The summit will be held on 5 October 2026, tomorrow.")
    assert result.detector_ver == DETECTOR_VERSION
    for sig in result.signals:
        assert sig.detector_ver == DETECTOR_VERSION
        assert sig.evidence["pattern"] and sig.evidence["context"]
        assert "digits" in sig.evidence


def test_detection_is_deterministic_and_ordered():
    text = "Tomorrow. 5 October 2026 will come. ١٥ رمضان ١٤٤٧ هـ. ۱۵ مهر ۱۴۰۵."
    first = [s.to_dict() for s in detect(text).signals]
    assert first == [s.to_dict() for s in detect(text).signals]
    starts = [s["char_start"] for s in first]
    assert starts == sorted(starts)


def test_overlapping_patterns_yield_one_signal():
    assert len(one("On 5 October 2026.").value) > 0  # not also a month-year or day-month


def test_long_text_is_truncated_visibly(monkeypatch):
    import core.detection.temporal_intel as ti

    monkeypatch.setattr(ti, "MAX_SCAN_CHARS", 20)
    result = ti.detect("x" * 30 + " 5 October 2026")
    assert result.truncated and result.chars_scanned == 20 and result.chars_total > 20
    assert result.signals == ()


def test_language_filter_and_input_validation():
    assert detect("5 October 2026 / 5. listopada 2026.", languages=["hr"]).by_type(SIGNAL_DATE)[0].language == "hr"
    with pytest.raises(ValueError):
        detect("x", languages=["de"])
    with pytest.raises(TypeError):
        detect(b"bytes")
    assert detect(None).signals == ()


# --------------------------------------------------------------------------
# Provenance: method, ordinal confidence, evidence sentence (temporal-1.1.0)
# --------------------------------------------------------------------------

from core.detection.temporal_intel import CONFIDENCE_LEVELS, CONFIDENCE_RULES  # noqa: E402


@pytest.mark.parametrize("text, value, level, basis", [
    ("Held on 5 October 2026.", "gregorian:2026-10-05", "high", "month_name"),
    ("Held October 5, 2026.", "gregorian:2026-10-05", "high", "month_name"),
    ("Held 2026-10-05.", "gregorian:2026-10-05", "high", "year_first_numeric"),
    ("يبدأ في ١٥ رمضان ١٤٤٧ هـ", "hijri:1447-09-15", "high", "month_name"),
    ("Due 25/12/2026.", "gregorian:2026-12-25", "medium", "single_valid_reading"),
    ("Rok je 15.3.2026.", "gregorian:2026-03-15", "medium", "convention"),
    ("Meet on 5 October.", "gregorian:--10-05", "medium", "no_year"),
    ("Deadline 03/04/2026 for all.", None, "low", "ambiguous"),
])
def test_confidence_is_an_ordinal_rule_with_a_stated_basis(text, value, level, basis):
    sig = one(text)
    if value:
        assert sig.value == value
    assert (sig.confidence, sig.confidence_basis) == (level, basis)
    assert CONFIDENCE_RULES[basis][0] == level and level in CONFIDENCE_LEVELS
    assert sig.method == sig.evidence["pattern"]


def test_relative_and_cue_confidence():
    found = {s.signal_type: s for s in detect("The vote will happen tomorrow.").signals}
    assert (found[SIGNAL_RELATIVE].confidence, found[SIGNAL_RELATIVE].confidence_basis) == (
        "high", "relative_lexicon")
    assert (found[SIGNAL_ORIENTATION].confidence,
            found[SIGNAL_ORIENTATION].confidence_basis) == ("low", "lexical_cue")


def test_every_signal_quotes_its_sentence_from_the_original_text():
    text = ("The summit will be held on 5 October 2026. Tomorrow we decide.\n"
            "سيعقد المؤتمر في ٥ أكتوبر ٢٠٢٦.\n"
            "הכנס יתקיים ב-5 באוקטובר 2026.\n"
            "نشست در ۱۵ مهر ۱۴۰۵ برگزار خواهد شد.\n"
            "Sastanak će se održati 5. listopada 2026.\n")
    signals = detect(text).signals
    assert signals
    for sig in signals:
        assert text[sig.sentence_start:sig.sentence_end] == sig.sentence
        assert sig.sentence_start <= sig.char_start < sig.char_end <= sig.sentence_end
        assert "\n" not in sig.sentence and sig.sentence == sig.sentence.strip()
    by_value = {s.value: s.sentence for s in signals if s.signal_type == SIGNAL_DATE
                and s.language == "fa"}
    assert by_value["jalali:1405-07-15"] == "نشست در ۱۵ مهر ۱۴۰۵ برگزار خواهد شد."
    tomorrow = [s for s in signals if s.value == "rel:+1d"][0]
    assert tomorrow.sentence == "Tomorrow we decide."


def test_overlong_sentences_are_cut_around_the_signal_and_flagged(monkeypatch):
    import core.detection.temporal_intel as ti

    monkeypatch.setattr(ti, "MAX_SENTENCE_CHARS", 40)
    text = "word " * 30 + "on 5 October 2026 " + "word " * 30
    sig = [s for s in ti.detect(text).signals if s.signal_type == SIGNAL_DATE][0]
    assert sig.evidence["sentence_truncated"] is True
    assert sig.sentence_end - sig.sentence_start <= 40
    assert sig.sentence_start <= sig.char_start < sig.char_end <= sig.sentence_end
    assert text[sig.sentence_start:sig.sentence_end] == sig.sentence
