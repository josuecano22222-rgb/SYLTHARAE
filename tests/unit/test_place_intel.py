"""Phase 2 unit tests: gazetteer seed, name normalisation and place detection.

The detector runs against the *committed* seed (built into an in-memory
index exactly as the database loader does), so these tests exercise real
Wikidata names in en/ar/he/fa/hr - not a toy list.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from core.detection import place_intel as pi
from core.geo import names as gn
from services.geo import gazetteer as gz

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def seed():
    return gz.load_seed_file()


@pytest.fixture(scope="module")
def gaz(seed):
    rows = []
    for pid, place in enumerate(seed["places"], 1):
        for n in place["names"]:
            rows.append((pid, place["place_key"], place["label"], place["feature_type"],
                         place["country_codes"], n["name"], n["language"], n["script"],
                         n["name_type"], n["homograph"], n["note"]))
    return pi.Gazetteer.from_rows(rows, "ab" * 32)


def detect(gaz, text):
    return pi.detect(text, gazetteer=gaz).signals


def one(gaz, text):
    signals = detect(gaz, text)
    assert len(signals) == 1, [(s.surface, s.value) for s in signals]
    return signals[0]


# --- seed ------------------------------------------------------------------

def test_committed_seed_is_reproducible_from_raw_rows_and_curation():
    proc = subprocess.run([sys.executable, str(ROOT / "tools/gazetteer/build_seed.py"), "--check"],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_seed_integrity_is_verified_and_tampering_is_rejected(seed, tmp_path):
    assert seed["licence"] == "CC0-1.0" and seed["place_count"] == len(seed["places"]) == 223
    assert seed["name_count"] == sum(len(p["names"]) for p in seed["places"])
    tampered = copy.deepcopy(seed)
    tampered["places"][0]["latitude"] = 0.0
    path = tmp_path / "seed.json"
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(gz.SeedIntegrityError):
        gz.load_seed_file(path)


def test_seed_names_cover_all_five_languages_and_name_types(seed):
    langs = {n["language"] for p in seed["places"] for n in p["names"]}
    types = {n["name_type"] for p in seed["places"] for n in p["names"]}
    assert langs == {"en", "ar", "he", "fa", "hr"}
    assert {"endonym", "exonym", "historical", "variant", "unclassified"} <= types
    by_key = {p["place_key"]: p for p in seed["places"]}
    rijeka = {(n["name"], n["language"]): n["name_type"] for n in by_key["wikidata:Q1647"]["names"]}
    assert rijeka[("Rijeka", "hr")] == "endonym" and rijeka[("Fiume", "hr")] == "historical"
    # Shared/disputed places keep every recorded country code - none is picked.
    assert by_key["wikidata:Q1218"]["country_codes"] == ["IL", "PS"]


# --- normalisation ---------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("القَاهِرَة", "القاهره"),          # harakat dropped, teh marbuta -> heh
    ("ﺣَﻠَﺐ", "حلب"),                   # presentation forms (NFKC)
    ("إسطنبول", "اسطنبول"),            # alef with hamza
    ("تل‌آویو", "تلاويو"),              # ZWNJ removed; Persian yeh -> yeh
    ("יְרוּשָׁלַיִם", "ירושלים"),       # niqqud dropped
    ("ג׳דה", "ג'דה"),                  # geresh -> apostrophe
    ("Šibenik", "Šibenik"),            # Latin diacritics and case kept
])
def test_match_key_normalisation(raw, expected):
    assert gn.match_key(raw) == expected


def test_script_detection():
    assert gn.script_of("Zagreb") == "Latn" and gn.script_of("زاغرب") == "Arab"
    assert gn.script_of("זאגרב") == "Hebr" and gn.script_of("Zagreb زاغرب") is None


# --- matching per language -------------------------------------------------

@pytest.mark.parametrize("text,key,language", [
    ("The ministers met in Zagreb on Monday.", "wikidata:Q1435", None),   # en == hr name
    ("اجتمع الوزراء في القَاهِرَة أمس.", "wikidata:Q85", "ar"),
    ("השרים נפגשו בחיפה.", "wikidata:Q41621", "he"),
    ("وزیران در تهران دیدار کردند.", "wikidata:Q3616", "fa"),
    ("Ministri su se sastali u Dubrovniku.", "wikidata:Q1722", "hr"),
])
def test_each_supported_language_is_detected(gaz, text, key, language):
    sig = one(gaz, text)
    assert sig.value == f"place:{key}" and sig.resolution == "identified"
    assert sig.language == language
    assert text[sig.char_start:sig.char_end] == sig.surface
    assert sig.sentence_start <= sig.char_start < sig.char_end <= sig.sentence_end
    assert text[sig.sentence_start:sig.sentence_end] == sig.sentence


def test_offsets_address_the_original_text_including_diacritics(gaz):
    text = "في القَاهِرَة"
    sig = one(gaz, text)
    assert sig.surface == "القَاهِرَة" and sig.char_end == len(text)
    assert sig.confidence == "high" and sig.method == "exact"


def test_hebrew_prefix_cluster_is_stripped_and_recorded(gaz):
    sig = one(gaz, "הם הגיעו ובירושלים נשארו.")
    assert sig.surface == "ירושלים" and sig.evidence["prefix"] == "וב"
    assert sig.method == "he.prefix" and sig.confidence == "medium"
    assert sig.confidence_basis == "affix_unique"


def test_hebrew_multiword_name_across_hyphen_with_prefix(gaz):
    sig = one(gaz, "טסנו מתל אביב-יפו.")
    assert sig.value == "place:wikidata:Q33935" and sig.surface == "תל אביב-יפו"
    assert sig.evidence["prefix"] == "מ"


@pytest.mark.parametrize("text,surface,prefix", [
    ("وصلنا وبيروت هادئة", "بيروت", "و"),
    ("سافر فبغداد", "بغداد", "ف"),
    ("رسالة للقاهرة", "لقاهرة", "ل"),         # ل + ال contracted to للـ
    ("وصل وللقاهرة", "لقاهرة", "ول"),
])
def test_arabic_proclitics(gaz, text, surface, prefix):
    sig = one(gaz, text)
    assert sig.surface == surface and sig.evidence["prefix"] == prefix
    assert sig.method == "ar.proclitic"


def test_exact_match_takes_precedence_over_affix_stripping(gaz):
    sig = one(gaz, "زيارة بغداد")          # ب is part of the name, not a proclitic
    assert sig.method == "exact" and sig.surface == "بغداد"


@pytest.mark.parametrize("text,key,surface", [
    ("Sastanak u Zagrebu.", "wikidata:Q1435", "Zagrebu"),
    ("Putovali smo prema Zagrebom.", "wikidata:Q1435", "Zagrebom"),
    ("Živi u Rijeci.", "wikidata:Q1647", "Rijeci"),     # sibilarisation k -> c
    ("Iz Sarajeva.", "wikidata:Q11194", "Sarajeva"),
])
def test_croatian_case_endings(gaz, text, key, surface):
    sig = one(gaz, text)
    assert sig.value == f"place:{key}" and sig.surface == surface
    assert sig.method == "hr.inflection" and sig.evidence["lemma"]


def test_persian_zwnj_spelling_matches(gaz):
    sig = one(gaz, "پرواز به تل‌آویو")
    assert sig.value == "place:wikidata:Q33935" and sig.surface == "تل‌آویو"


# --- precision guards --------------------------------------------------------

@pytest.mark.parametrize("text", [
    "We split the bill.", "a turkey sandwich", "Parisian cafes", "Romeo arrived",
    "zagreb in lower case", "Kuwaiti dinar",
])
def test_no_partial_or_case_folded_matches(gaz, text):
    assert detect(gaz, text) == ()


def test_punctuation_breaks_multiword_names(gaz):
    signals = detect(gaz, "Kuwait, City")
    assert [s.surface for s in signals] == ["Kuwait"]


def test_longest_name_wins(gaz):
    sig = one(gaz, "Flights to Kuwait City resumed.")
    assert sig.surface == "Kuwait City" and sig.value == "place:wikidata:Q35178"


def test_all_caps_and_possessive(gaz):
    caps = one(gaz, "DATELINE BAGHDAD")
    assert caps.method == "latin.uppercase" and caps.confidence == "medium"
    poss = one(gaz, "London's mayor")
    assert poss.surface == "London" and poss.method == "en.possessive"


# --- ambiguity and homographs -----------------------------------------------

@pytest.mark.parametrize("text,keys", [
    ("Talks in Tripoli.", {"wikidata:Q3579", "wikidata:Q168954"}),
    ("محادثات في طرابلس", {"wikidata:Q3579", "wikidata:Q168954"}),
    ("وصل إلى عمان", {"wikidata:Q3805", "wikidata:Q842"}),      # Amman / Oman
    ("שיחות בטריפולי", {"wikidata:Q3579", "wikidata:Q168954"}),
])
def test_ambiguous_names_keep_every_candidate(gaz, text, keys):
    sig = one(gaz, text)
    assert sig.resolution == "ambiguous" and sig.confidence == "low"
    assert {c["place_key"] for c in sig.evidence["candidates"]} == keys
    assert len(sig.place_ids) == len(keys)
    assert sig.value == "places:" + ",".join(sorted(keys))


def test_homographs_are_kept_with_low_confidence_and_a_reason(gaz):
    sig = one(gaz, "Turkey signed the accord.")
    assert sig.resolution == "identified" and sig.confidence == "low"
    assert sig.confidence_basis == "homograph" and sig.evidence["homograph_notes"]
    he = one(gaz, "שתינו חלב")                       # 'milk' / Aleppo
    assert he.confidence == "low"


def test_every_legacy_gazetteer_name_is_still_detected(gaz):
    legacy = ['Nairobi', 'Mombasa', 'Lagos', 'Abuja', 'Cairo', 'Johannesburg', 'Cape Town',
              'Durban', 'Dar es Salaam', 'Addis Ababa', 'Casablanca', 'Accra', 'Tunis',
              'Algiers', 'Kampala', 'Kigali', 'Dubai', 'Abu Dhabi', 'Doha', 'Riyadh', 'Jeddah',
              'Tel Aviv', 'Istanbul', 'Beirut', 'Tokyo', 'Osaka', 'Yokohama', 'Shanghai',
              'Beijing', 'Shenzhen', 'Guangzhou', 'Hong Kong', 'Singapore', 'Seoul', 'Busan',
              'Mumbai', 'New Delhi', 'Bengaluru', 'Chennai', 'Kolkata', 'Bangkok', 'Jakarta',
              'Kuala Lumpur', 'Manila', 'Ho Chi Minh City', 'Hanoi', 'Karachi', 'Colombo',
              'Dhaka', 'Taipei', 'London', 'Manchester', 'Paris', 'Marseille', 'Berlin',
              'Hamburg', 'Frankfurt', 'Munich', 'Rotterdam', 'Amsterdam', 'Antwerp', 'Brussels',
              'Madrid', 'Barcelona', 'Valencia', 'Lisbon', 'Rome', 'Milan', 'Genoa', 'Athens',
              'Piraeus', 'Zurich', 'Geneva', 'Vienna', 'Warsaw', 'Gdansk', 'Prague',
              'Copenhagen', 'Stockholm', 'Gothenburg', 'Oslo', 'Helsinki', 'Dublin', 'Moscow',
              'Saint Petersburg', 'New York', 'Los Angeles', 'Chicago', 'Houston', 'Miami',
              'Boston', 'Seattle', 'San Francisco', 'Oakland', 'Long Beach', 'Savannah',
              'Charleston', 'Norfolk', 'Newark', 'Atlanta', 'Dallas', 'Denver', 'Toronto',
              'Vancouver', 'Montreal', 'Mexico City', 'Panama City', 'Sao Paulo',
              'Rio de Janeiro', 'Santos', 'Buenos Aires', 'Santiago', 'Bogota', 'Lima',
              'Sydney', 'Melbourne', 'Brisbane', 'Perth', 'Auckland']
    not_unique = {}
    for name in legacy:
        sig = one(gaz, f"Report from {name} today.")
        assert sig.surface == name
        if sig.confidence != "high":
            not_unique[name] = sig.confidence_basis
    # The old scan silently picked one place for these; now they are explicit.
    assert not_unique == {"Tunis": "ambiguous", "Valencia": "ambiguous", "Perth": "ambiguous",
                          "Savannah": "homograph", "New York": "homograph",
                          "Lima": "homograph"}


# --- determinism and versioning ---------------------------------------------

def test_detection_is_deterministic_and_keys_are_distinct(gaz):
    text = "Zagreb, Split and طرابلس. ובירושלים; Zagrebu."
    a, b = detect(gaz, text), detect(gaz, text)
    assert [s.to_dict() for s in a] == [s.to_dict() for s in b]
    keys = [s.dedup_key(7) for s in a]
    assert len(keys) == len(set(keys))


def test_detector_version_embeds_the_gazetteer_fingerprint(seed):
    assert pi.detector_version("0123456789abcdef" * 4) == "places-1.0.0+g0123456789ab"
    with pytest.raises(ValueError):
        pi.detector_version("")


def test_empty_and_non_text_input(gaz):
    assert pi.detect(None, gazetteer=gaz).signals == ()
    with pytest.raises(TypeError):
        pi.detect(b"bytes", gazetteer=gaz)
    with pytest.raises(TypeError):
        pi.detect("x", gazetteer=None)
