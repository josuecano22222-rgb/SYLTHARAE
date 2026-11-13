"""NARR-01: plural-aware narrative sentences.

The engine chooses plural forms only through gettext ``ngettext`` and the
catalogs' own ``Plural-Forms`` (ar nplurals=6, hr 3, en/he/fa 2), stores one
count per plural sentence, keeps the released record format of keyness@1
unchanged, and renders every stored format.

The per-language expectations below are hand-written grammar facts (which
noun form a count takes), not values read back from the catalogs, so a
wrong form in a catalog fails here.
"""

import gettext
import os

import pytest

from core.analytics import measures, thresholds
from core.analytics.kinds import (KEYNESS, KEYNESS_TEMPLATES, KEYNESS_TEMPLATES_V2, KEYNESS_V2,
                                  MEASURED)
from core.analytics.narrative import (VOICES, NarrativeError, Plural, TemplateSet, render,
                                      source_ngettext)

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
TRANSLATIONS = os.path.join(ROOT, "translations")
LANGUAGES = ("en", "ar", "he", "fa", "hr")
COUNTS = (0, 1, 2, 3, 11, 100)


def _catalog(lang):
    return gettext.translation("messages", TRANSLATIONS, languages=[lang])


def _base(**extra):
    return {**{f"{v}.a": v for v in VOICES}, **extra}


# ------------------------------------------------------------------ engine

def test_plural_sentences_are_validated():
    ok = Plural("%(n)s item", "%(n)s items", "n")
    TemplateSet("x", 1, _base(**{"finding.p": ok}))
    with pytest.raises(NarrativeError, match="must contain %\\(n\\)s"):
        TemplateSet("x", 1, _base(**{"finding.p": Plural("one item", "items", "n")}))
    with pytest.raises(NarrativeError, match="singular has placeholders"):
        TemplateSet("x", 1, _base(**{"finding.p": Plural("%(n)s %(m)s item", "%(n)s items",
                                                         "n")}))
    with pytest.raises(NarrativeError, match="identical"):
        TemplateSet("x", 1, _base(**{"finding.p": Plural("%(n)s x", "%(n)s x", "n")}))
    with pytest.raises(NarrativeError, match="stray"):
        TemplateSet("x", 1, _base(**{"finding.p": Plural("%(n)s x", "%(n)s x 5%", "n")}))
    with pytest.raises(NarrativeError, match="record format 1 has no plurals"):
        TemplateSet("x", 1, _base(**{"finding.p": ok}), record_format=1)
    with pytest.raises(NarrativeError, match="record_format"):
        TemplateSet("x", 1, _base(), record_format=3)


def test_the_count_must_be_a_whole_number():
    t = TemplateSet("x", 1, _base(**{"finding.p": Plural("%(n)s item", "%(n)s items", "n")}))
    with pytest.raises(NarrativeError, match="text or a number"):
        t.compose([("measure.a", {}), ("finding.p", {"n": True}), ("confidence.a", {}),
                   ("consequence.a", {}), ("caveat.a", {})])
    for bad in (1.5, -1, "3", None):
        with pytest.raises(NarrativeError, match="count"):
            t.compose([("measure.a", {}), ("finding.p", {"n": bad}), ("confidence.a", {}),
                       ("consequence.a", {}), ("caveat.a", {})])


def test_a_voice_may_be_several_sentences_of_that_voice_only():
    t = TemplateSet("x", 1, _base(**{"measure.b": "b"}))
    stored = t.compose([[("measure.a", {}), ("measure.b", {})], ("finding.a", {}),
                        ("confidence.a", {}), ("consequence.a", {}), ("caveat.a", {})])
    assert stored["format"] == 2
    assert [s["key"] for s in stored["voices"][0]["sentences"]] == ["measure.a", "measure.b"]
    assert render(stored, lambda s: s)[0] == {"voice": "measure", "text": "measure b"}
    with pytest.raises(NarrativeError, match="in order"):
        t.compose([[("measure.a", {}), ("finding.a", {})], ("finding.a", {}),
                   ("confidence.a", {}), ("consequence.a", {}), ("caveat.a", {})])
    with pytest.raises(NarrativeError, match="at least one"):
        t.compose([[], ("finding.a", {}), ("confidence.a", {}), ("consequence.a", {}),
                   ("caveat.a", {})])
    legacy = TemplateSet("x", 1, _base(**{"measure.b": "b"}), record_format=1)
    with pytest.raises(NarrativeError, match="one sentence per voice"):
        legacy.compose([[("measure.a", {}), ("measure.b", {})], ("finding.a", {}),
                        ("confidence.a", {}), ("consequence.a", {}), ("caveat.a", {})])


def test_render_uses_ngettext_with_the_raw_count_and_formats_numbers_after():
    t = TemplateSet("x", 1, _base(**{"finding.p": Plural("%(n)s item", "%(n)s items", "n")}))
    stored = t.compose([("measure.a", {}), ("finding.p", {"n": 12345}), ("confidence.a", {}),
                        ("consequence.a", {}), ("caveat.a", {})])
    record = stored["voices"][1]["sentences"][0]
    assert record == {"key": "finding.p", "msgid": "%(n)s item", "msgid_plural": "%(n)s items",
                      "count": "n", "params": {"n": 12345}}
    seen = []

    def ngettext(s, p, n):
        seen.append(n)
        return p

    assert render(stored, lambda s: s, ngettext=ngettext)[1]["text"] == "12,345 items"
    assert seen == [12345], "the plural form is chosen from the raw count, not the text"
    with pytest.raises(NarrativeError, match="needs ngettext"):
        render(stored, lambda s: s)


def test_keyness_v1_keeps_its_released_record_and_fingerprint_shape():
    assert KEYNESS_TEMPLATES.record_format == 1 and not KEYNESS_TEMPLATES.plurals()
    # The format-1 fingerprint does not include the new field (its value is
    # pinned in core/reporting/definitions.lock.json through the analysis).
    assert "record_format" not in repr({"sentences": dict(KEYNESS_TEMPLATES.sentences)})
    result = KEYNESS.compute(_inputs(significant=1), {"direction": "over"})
    assert "format" not in result["narrative"]
    assert all(set(v) == {"voice", "key", "msgid", "params"}
               for v in result["narrative"]["voices"])


def test_a_stored_v1_record_renders_verbatim():
    """A literal keyness@1 record as m0026 stores it (captured shape)."""
    t = KEYNESS_TEMPLATES.sentences
    stored = {"template_set": "keyness", "template_version": 1,
              "template_fingerprint": KEYNESS_TEMPLATES.fingerprint(),
              "voices": [
                  {"voice": "measure", "key": "measure.not_measurable",
                   "msgid": t["measure.not_measurable"], "params": {}},
                  {"voice": "finding", "key": "finding.none",
                   "msgid": t["finding.none"], "params": {"critical": 15.13}},
                  {"voice": "confidence", "key": "confidence.not_measurable",
                   "msgid": t["confidence.not_measurable"], "params": {}},
                  {"voice": "consequence", "key": "consequence.none",
                   "msgid": t["consequence.none"], "params": {}},
                  {"voice": "caveat", "key": "caveat.unknown_counts",
                   "msgid": t["caveat.unknown_counts"], "params": {"unknown_rows": 4}}]}
    text = render(stored, lambda s: s)
    assert text[1]["text"].startswith("No term reaches G2 15.13:")
    assert "4 word records without a count" in text[4]["text"]
    ar = render(stored, _catalog("ar").gettext)
    assert ar[1]["text"] != text[1]["text"] and "15.13" in ar[1]["text"]


# ------------------------------------------------------------------ keyness@2

def _inputs(significant, shown=None, unknown=0):
    shown = significant if shown is None else shown
    c, d = 10_000, 20_000
    rows = []
    # Contract-valid rows (the kind rejects anything else): all in the "over"
    # direction (a/c > b/d), ranked by G2, and only the first `significant`
    # of them reach 15.13 - the listing may never show more significant
    # terms than the exact count.
    for i in range(shown):
        a, b = ((400 - i, 50) if i < significant else (27, 50))
        rows.append({"term": f"t{i:03d}", "target_freq": a, "reference_freq": b,
                     "g2": measures.log_likelihood(a, b, c, d),
                     "log_ratio": measures.log_ratio(a, b, c, d)})
    return {"ranked": {"rows": rows, "truncated": False, "semantics": "top_n"},
            "totals": {"rows": [{"target_tokens": c, "reference_tokens": d,
                                 "target_contents": 3, "reference_contents": 7,
                                 "unknown_count_rows": unknown,
                                 "significant_terms": significant}],
                       "semantics": "exact"}}


def test_keyness_v2_says_1_term_not_1_terms():
    """The NARR-01 defect: v1 printed "1 terms"."""
    v1 = render(KEYNESS.compute(_inputs(1), {"direction": "over"})["narrative"],
                lambda s: s)
    assert v1[1]["text"].startswith("1 terms are used"), "the defect being fixed"
    result = KEYNESS_V2.compute(_inputs(1, unknown=1), {"direction": "over"})
    assert result["state"] == MEASURED
    text = render(result["narrative"], lambda s: s, ngettext=source_ngettext)
    assert "Listed: 1 term used more often in the selection." in text[0]["text"]
    assert text[1]["text"].startswith("1 term is used significantly more often")
    assert "The strongest is \"t000\"" in text[1]["text"]
    assert "1 word record without a count was left out" in text[4]["text"]
    many = render(KEYNESS_V2.compute(_inputs(3, unknown=2), {"direction": "over"})["narrative"],
                  lambda s: s, ngettext=source_ngettext)
    assert many[1]["text"].startswith("3 terms are used significantly")
    assert "2 word records without a count were left out" in many[4]["text"]


def test_keyness_v2_is_deterministic_and_measures_match_v1():
    a = KEYNESS_V2.compute(_inputs(2), {"direction": "over"})
    b = KEYNESS_V2.compute(_inputs(2), {"direction": "over"})
    assert a == b
    v1 = KEYNESS.compute(_inputs(2), {"direction": "over"})
    assert v1["measures"] == a["measures"] and v1["rows"] == a["rows"]
    assert a["narrative"]["template_version"] == 2
    assert a["narrative"]["template_fingerprint"] == KEYNESS_TEMPLATES_V2.fingerprint()


def test_keyness_v2_not_measurable_and_threshold_source():
    none = _inputs(0)
    none["totals"]["rows"][0]["target_contents"] = 0
    r = KEYNESS_V2.compute(none, {"direction": "over"})
    assert r["reason"] == "no_target"
    assert [v["sentences"][0]["key"] for v in r["narrative"]["voices"]][1] == "finding.no_target"
    ok = KEYNESS_V2.compute(_inputs(1), {"direction": "over"})
    conf = ok["narrative"]["voices"][2]["sentences"][0]["params"]
    assert conf["source"] == thresholds.LL_P0001.source and conf["critical"] == 15.13


# ------------------------------------------------------------------ languages

#: Hand-written grammar facts: a fragment every rendering of n must contain.
EXPECTED_LISTED = {
    "en": {0: "0 terms", 1: "1 term used", 2: "2 terms", 3: "3 terms", 11: "11 terms",
           100: "100 terms"},
    # Arabic: 0 and 100 take the singular genitive, 1 "one term" without the
    # digit, 2 the dual, 3-10 the plural, 11-99 the singular accusative.
    "ar": {0: "0 مصطلح ", 1: "مصطلح واحد", 2: "مصطلحان", 3: "3 مصطلحات", 11: "11 مصطلحًا",
           100: "100 مصطلح "},
    # Hebrew: 1 "one term" (singular), everything else plural.
    "he": {0: "0 מונחים", 1: "מונח אחד", 2: "2 מונחים", 3: "3 מונחים", 11: "11 מונחים",
           100: "100 מונחים"},
    # Persian: the noun stays singular after numerals; the verb is singular
    # for 0 and 1 (plural=(n > 1)) and plural above.
    "fa": {0: "رفته است.", 1: "رفته است.", 2: "رفته‌اند", 3: "رفته‌اند", 11: "رفته‌اند",
           100: "رفته‌اند"},
    # Croatian: 1/21 pojam, 2-4 pojma, 0/5-20/100 pojmova.
    "hr": {0: "0 pojmova", 1: "1 pojam", 2: "2 pojma", 3: "3 pojma", 11: "11 pojmova",
           100: "100 pojmova"},
}


@pytest.mark.parametrize("lang", LANGUAGES)
@pytest.mark.parametrize("n", COUNTS)
def test_listed_count_takes_the_right_form_in_every_language(lang, n):
    cat = _catalog(lang)
    stored = KEYNESS_V2.compute(_inputs(min(n, 1), shown=n), {"direction": "over"})["narrative"]
    text = render(stored, cat.gettext, ngettext=cat.ngettext)[0]["text"]
    assert EXPECTED_LISTED[lang][n] in text, text


@pytest.mark.parametrize("lang, n, fragment", [
    ("ar", 2, "مصطلحان يُستخدمان"), ("ar", 3, "3 مصطلحات تُستخدم"),
    ("ar", 11, "11 مصطلحًا تُستخدم"), ("ar", 100, "100 مصطلح يُستخدم"),
    ("ar", 1, "مصطلح واحد يُستخدم"),
    ("hr", 1, "1 pojam koristi se"), ("hr", 2, "2 pojma koriste se"),
    ("hr", 21, "21 pojam koristi se"), ("hr", 11, "11 pojmova koristi se"),
    ("he", 1, "מונח אחד משמש"), ("he", 3, "3 מונחים משמשים"),
    ("fa", 1, "به کار رفته است"), ("fa", 2, "به کار رفته‌اند"),
    ("en", 1, "1 term is used"), ("en", 11, "11 terms are used"),
])
def test_significant_count_agrees_in_every_language(lang, n, fragment):
    cat = _catalog(lang)
    stored = KEYNESS_V2.compute(_inputs(n, shown=min(n, 3)), {"direction": "over"})["narrative"]
    text = render(stored, cat.gettext, ngettext=cat.ngettext)[1]["text"]
    assert fragment in text, text
    assert "15.13" in text or "١٥" in text, "the threshold survives translation"


@pytest.mark.parametrize("lang", LANGUAGES)
def test_every_plural_entry_has_one_form_per_catalog_class(lang):
    """Every count class the catalog defines renders a non-empty, filled
    sentence for every keyness@2 plural - no class falls back to English."""
    cat = _catalog(lang)
    for p in KEYNESS_TEMPLATES_V2.plurals():
        forms = {cat.ngettext(p.singular, p.plural, n) for n in range(0, 130)}
        assert all(forms)
        params = {name: 7 for name in ("shown", "significant", "unknown_rows")}
        params["critical"] = 15.13
        for form in forms:
            form % params          # every form fills without a missing key
        if lang != "en":
            assert not forms & {p.singular, p.plural}, (lang, p.singular)
