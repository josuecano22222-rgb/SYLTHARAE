"""Place-name detection against the database gazetteer (Phase 2).

This is **gazetteer matching, not named-entity recognition**: a signal means
"this span equals (after normalisation) a name recorded for these places",
never "the author refers to this place". Nothing is inferred from context;
where a name belongs to several places every candidate is kept and the signal
is ``ambiguous``; where a name is also a common word (curated ``homograph``)
the signal is kept with ``low`` confidence.

Matching (``core.geo.names.match_key`` on both names and text):

* ``exact``          - whole tokens equal a name (multi-word names across
  whitespace/hyphens only);
* ``latin.uppercase``- an ALL-CAPS Latin token sequence equals a name
  uppercased (headings, cables);
* ``en.possessive``  - an English possessive (``London's``);
* ``he.prefix``      - Hebrew proclitic cluster before a Hebrew name
  (``ו ה ב כ ל מ ש``, e.g. ``ובירושלים``); offsets cover the name only, the
  prefix is recorded in evidence;
* ``ar.proclitic``   - Arabic ``و``/``ف`` then ``ب``/``ك``/``ل`` before an
  Arabic name, including ``ل`` + ``ال`` -> ``لل`` (``للقاهرة``);
* ``hr.inflection``  - Croatian case endings of single-word Croatian names
  (masculine/neuter ``-a -u -om/-em``, feminine ``-e -i -u -om`` with
  sibilarisation k->c, g->z, h->s before ``-i``). A small paradigm, not a
  morphological analyser: irregular stems (fleeting a, plurals) are missed.

Affix-based methods apply only when no exact match starts at that token.
Leftmost-longest: the longest name starting at a token wins; equal-length
names are merged into one signal whose candidates are all their places.

Confidence is ordinal (``CONFIDENCE_RULES``), not a probability.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from core.detection import textspan
from core.detection.signal_model import ContentSignal
from core.geo import names as geo_names

DETECTOR_NAME = "places"
#: Code version. The stored ``detector_ver`` also carries the gazetteer
#: fingerprint (``detector_version``) so a gazetteer change makes every
#: content's place signals stale for re-detection.
DETECTOR_BASE_VERSION = "places-1.0.0"
SIGNAL_PLACE = "place_mention"
SIGNAL_TYPES = (SIGNAL_PLACE,)
RESOLUTIONS = ("identified", "ambiguous")
MAX_SCAN_CHARS = 20_000_000

CONFIDENCE_RULES = {
    "exact_unique": ("high", "whole-token match of a name recorded for exactly one place; "
                             "the name is not a curated homograph"),
    "affix_unique": ("medium", "match after stripping a recorded affix (Hebrew prefix, Arabic "
                               "proclitic, Croatian case ending, English possessive) or of an "
                               "all-caps form, for exactly one place; not a homograph"),
    "homograph": ("low", "the matched name is curated as also being a common word or "
                         "personal name"),
    "ambiguous": ("low", "the matched name is recorded for more than one place; all "
                         "candidates are kept"),
}

METHODS = ("exact", "latin.uppercase", "en.possessive", "he.prefix", "ar.proclitic",
           "hr.inflection")

_HE_LETTERS = "והבכלמש"
_AR_CONJ = ("", "و", "ف")
_AR_PREP = ("ب", "ك", "ل")
_HR_PALATAL = ("č", "ć", "ž", "š", "đ", "j", "c", "lj", "nj", "dž")
_HR_SIBILANT = {"k": "c", "g": "z", "h": "s"}
_VOWELS = set("aeiouAEIOU")


def detector_version(fingerprint: str) -> str:
    if not fingerprint or len(fingerprint) < 12:
        raise ValueError("a gazetteer fingerprint is required")
    return f"{DETECTOR_BASE_VERSION}+g{fingerprint[:12]}"


@dataclass(frozen=True)
class NameEntry:
    place_id: int
    place_key: str
    label: str
    feature_type: str
    country_codes: Tuple[str, ...]
    name: str
    language: str
    script: str
    name_type: str
    homograph: bool = False
    note: Optional[str] = None


@dataclass(frozen=True)
class PlaceSignal(ContentSignal):
    """A place mention; ``place_ids`` are the candidate ``geo_places`` ids."""
    detector_ver: str = DETECTOR_BASE_VERSION
    place_ids: Tuple[int, ...] = field(default=(), compare=False)


@dataclass(frozen=True)
class DetectionResult:
    signals: Tuple[PlaceSignal, ...]
    detector_ver: str
    anchor_date: None
    chars_total: int
    chars_scanned: int
    truncated: bool
    detector: str = DETECTOR_NAME


def _he_prefixes() -> List[str]:
    out = set()
    for n in (1, 2, 3):
        for combo in itertools.permutations(_HE_LETTERS, n):
            p = "".join(combo)
            if "ו" in p[1:] or "ה" in p[:-1]:
                continue  # ו only first (conjunction), ה only last (article)
            out.add(p)
    return sorted(out, key=lambda p: (len(p), p))


_HE_PREFIXES = _he_prefixes()


def _hr_forms(lemma: str) -> Dict[str, str]:
    """Inflected form -> ending, for a single-word Croatian name."""
    if len(lemma) < 3 or not lemma.isalpha():
        return {}
    last = lemma[-1]
    palatal = lemma.lower().endswith(_HR_PALATAL)
    if last == "a":
        stem = lemma[:-1]
        sib = stem[:-1] + _HR_SIBILANT.get(stem[-1], stem[-1])
        return {stem + "e": "e", sib + "i": "i", stem + "u": "u", stem + "om": "om"}
    if last in "oe":
        stem = lemma[:-1]
        return {stem + "a": "a", stem + "u": "u",
                stem + ("em" if stem.lower().endswith(_HR_PALATAL) else "om"): "om"}
    if last.isalpha() and last not in _VOWELS and last.lower() not in "yw":
        return {lemma + "a": "a", lemma + "u": "u", lemma + ("em" if palatal else "om"): "om"}
    return {}


class Gazetteer:
    """In-memory index over the gazetteer's names (built once per fingerprint)."""

    def __init__(self, entries: Iterable[NameEntry], fingerprint: str):
        self.fingerprint = fingerprint
        self.version = detector_version(fingerprint)
        self.by_tokens: Dict[Tuple[str, ...], List[NameEntry]] = {}
        for entry in entries:
            toks = geo_names.name_tokens(entry.name)
            if not toks:
                continue
            self.by_tokens.setdefault(toks, []).append(entry)
        self.first: Dict[str, List[Tuple[str, ...]]] = {}
        self.upper_first: Dict[str, List[Tuple[str, ...]]] = {}
        self.hr_forms: Dict[str, List[Tuple[Tuple[str, ...], str]]] = {}
        for toks, group in self.by_tokens.items():
            self.first.setdefault(toks[0], []).append(toks)
            if group[0].script == "Latn":
                self.upper_first.setdefault(toks[0].upper(), []).append(toks)
                if len(toks) == 1 and any(e.language == "hr" for e in group):
                    for form, ending in _hr_forms(toks[0]).items():
                        self.hr_forms.setdefault(form, []).append((toks, ending))
        for table in (self.first, self.upper_first):
            for key in table:
                table[key].sort(key=lambda t: (-len(t), t))
        self.name_count = sum(len(g) for g in self.by_tokens.values())

    @classmethod
    def from_rows(cls, rows: Iterable[Sequence], fingerprint: str) -> "Gazetteer":
        """Rows of (place_id, place_key, label, feature_type, country_codes, name,
        language, script, name_type, homograph, note)."""
        return cls((NameEntry(r[0], r[1], r[2], r[3], tuple(r[4] or ()), r[5], r[6], r[7],
                              r[8], bool(r[9]), r[10]) for r in rows), fingerprint)


@dataclass
class _Match:
    first: int            # first token index
    last: int             # last token index
    ns: int               # normalised start (after any prefix)
    ne: int               # normalised end
    entries: List[NameEntry]
    method: str
    prefix: str = ""
    suffix: str = ""
    lemma: Optional[str] = None


def _script(token: str) -> Optional[str]:
    return geo_names.script_of(token)


class _Scanner:
    def __init__(self, gaz: Gazetteer, norm: str):
        self.gaz = gaz
        self.norm = norm
        self.toks = geo_names.tokens(norm)
        self.text = [norm[s:e] for s, e in self.toks]

    def _follows(self, i: int, tup: Tuple[str, ...], upper: bool = False) -> bool:
        for k in range(1, len(tup)):
            j = i + k
            if j >= len(self.toks):
                return False
            want = tup[k].upper() if upper else tup[k]
            if self.text[j] != want:
                return False
            if not geo_names.is_separator(self.norm[self.toks[j - 1][1]:self.toks[j][0]]):
                return False
        return True

    def _exact(self, i: int, token: str, *, lang_filter=None, method="exact",
               ns_offset=0, prefix="") -> List[_Match]:
        out = []
        for tup in self.gaz.first.get(token, ()):
            if not self._follows(i, tup):
                continue
            entries = self.gaz.by_tokens[tup]
            if lang_filter:
                entries = [e for e in entries if e.language in lang_filter]
            if not entries:
                continue
            last = i + len(tup) - 1
            out.append(_Match(i, last, self.toks[i][0] + ns_offset, self.toks[last][1],
                              list(entries), method, prefix=prefix))
        return out

    def matches_at(self, i: int) -> List[_Match]:
        token = self.text[i]
        found = self._exact(i, token)
        if found:
            return found
        script = _script(token)
        if script == "Latn":
            if token.isupper() and len(token) > 1:
                for tup in self.gaz.upper_first.get(token, ()):
                    if self._follows(i, tup, upper=True):
                        last = i + len(tup) - 1
                        found.append(_Match(i, last, self.toks[i][0], self.toks[last][1],
                                            list(self.gaz.by_tokens[tup]), "latin.uppercase"))
                if found:
                    return found
            if token.endswith("'s") and len(token) > 3:
                # Single-word names only: in "New York's" the clitic is on
                # the last token, which exact multi-word matching rejects.
                stem = token[:-2]
                entries = [e for e in self.gaz.by_tokens.get((stem,), ()) if e.language == "en"]
                if entries:
                    return [_Match(i, i, self.toks[i][0], self.toks[i][1] - 2, entries,
                                   "en.possessive", suffix="'s")]
            for lemma_toks, ending in self.gaz.hr_forms.get(token, ()):
                entries = [e for e in self.gaz.by_tokens[lemma_toks] if e.language == "hr"]
                if entries:
                    found.append(_Match(i, i, self.toks[i][0], self.toks[i][1], entries,
                                        "hr.inflection", suffix=ending, lemma=lemma_toks[0]))
            return found
        if script == "Hebr":
            for prefix in _HE_PREFIXES:
                if len(token) - len(prefix) < 2 or not token.startswith(prefix):
                    continue
                found = self._exact(i, token[len(prefix):], lang_filter={"he"},
                                    method="he.prefix", ns_offset=len(prefix), prefix=prefix)
                if found:
                    return found
            return []
        if script == "Arab":
            for conj in _AR_CONJ:
                if not token.startswith(conj):
                    continue
                rest = token[len(conj):]
                options = []
                if rest.startswith("لل") and len(rest) > 3:
                    options.append(("ل", "ال" + rest[2:], 1))
                for prep in _AR_PREP:
                    if rest.startswith(prep) and len(rest) - 1 >= 2:
                        options.append((prep, rest[1:], 1))
                if conj and len(rest) >= 2:
                    options.append(("", rest, 0))
                for prep, stem, consumed in options:
                    prefix = conj + prep
                    found = self._exact(i, stem, lang_filter={"ar"}, method="ar.proclitic",
                                        ns_offset=len(conj) + consumed, prefix=prefix)
                    if found:
                        # For للـ the offsets start at the contracted article
                        # (ل of ال, alef elided) - the prefix is the first ل.
                        return found
            return []
        return []


def _span(original: str, index: List[int], ns: int, ne: int) -> Tuple[int, int]:
    end = index[ne - 1] + 1
    while end < len(original) and (textspan.dropped(original[end])
                                   or original[end] in "\u200c\u200d"):
        end += 1
    return index[ns], end


def detect(text: Optional[str], *, gazetteer: Gazetteer) -> DetectionResult:
    """Detect gazetteer place names in ``text``."""
    if text is None:
        text = ""
    if not isinstance(text, str):
        raise TypeError("text must be str")
    if not isinstance(gazetteer, Gazetteer):
        raise TypeError("a Gazetteer is required")
    total = len(text)
    truncated = total > MAX_SCAN_CHARS
    scanned = text[:MAX_SCAN_CHARS] if truncated else text
    norm, index = geo_names.normalize_with_index(scanned)
    scanner = _Scanner(gazetteer, norm)
    sentences = textspan.SentenceIndex(textspan.sentences(norm))

    def span(ns, ne):
        return _span(scanned, index, ns, ne)

    signals: List[PlaceSignal] = []
    i = 0
    while i < len(scanner.toks):
        found = scanner.matches_at(i)
        if not found:
            i += 1
            continue
        longest = max(m.last for m in found)
        best = [m for m in found if m.last == longest]
        signals.append(_build(best, scanned, norm, span, sentences, gazetteer))
        i = longest + 1
    signals.sort(key=lambda s: (s.char_start, s.char_end, s.value))
    return DetectionResult(signals=tuple(signals), detector_ver=gazetteer.version,
                           anchor_date=None, chars_total=total, chars_scanned=len(scanned),
                           truncated=truncated)


def _build(best: List[_Match], original: str, norm: str, span, sentences,
           gazetteer: Gazetteer) -> PlaceSignal:
    head = best[0]
    entries: Dict[Tuple[int, str, str], NameEntry] = {}
    for m in best:
        for e in m.entries:
            entries[(e.place_id, e.language, e.name)] = e
    chosen = sorted(entries.values(), key=lambda e: (e.place_key, e.language, e.name))
    places: Dict[str, NameEntry] = {}
    for e in chosen:
        places.setdefault(e.place_key, e)
    keys = sorted(places)
    languages = sorted({e.language for e in chosen})
    homograph = any(e.homograph for e in chosen)
    if len(keys) > 1:
        basis = "ambiguous"
    elif homograph:
        basis = "homograph"
    elif head.method == "exact":
        basis = "exact_unique"
    else:
        basis = "affix_unique"
    start, end = span(head.ns, head.ne)
    evidence = {
        "pattern": head.method, "normalized": norm[head.ns:head.ne],
        "gazetteer": gazetteer.fingerprint[:12],
        "candidates": [{"place_key": k, "label": places[k].label,
                        "feature_type": places[k].feature_type,
                        "country_codes": list(places[k].country_codes)} for k in keys],
        "matched_names": [{"place_key": e.place_key, "name": e.name, "language": e.language,
                           "name_type": e.name_type, "homograph": e.homograph}
                          for e in chosen],
        "languages": languages,
        "note": "gazetteer match, not named-entity recognition",
    }
    if head.prefix:
        p_start = span(head.ns - len(head.prefix), head.ns)[0]
        evidence["prefix"] = original[p_start:start]
    if head.suffix:
        evidence["suffix"] = head.suffix
    if head.lemma:
        evidence["lemma"] = head.lemma
    notes = sorted({e.note for e in chosen if e.homograph and e.note})
    if notes:
        evidence["homograph_notes"] = notes
    level = CONFIDENCE_RULES[basis][0]
    ctx_start, ctx_end = max(0, start - 80), min(len(original), end + 80)
    evidence["context"] = original[ctx_start:ctx_end].strip()
    sentence, s_start, s_end, cut = textspan.quote_sentence(
        original, span, sentences, head.ns, start, end)
    if cut:
        evidence["sentence_truncated"] = True
    value = f"place:{keys[0]}" if len(keys) == 1 else "places:" + ",".join(keys)
    return PlaceSignal(
        signal_type=SIGNAL_PLACE, value=value, surface=original[start:end],
        char_start=start, char_end=end,
        resolution="identified" if len(keys) == 1 else "ambiguous",
        language=languages[0] if len(languages) == 1 else None,
        evidence=evidence, detector_ver=gazetteer.version, method=head.method,
        confidence=level, confidence_basis=basis, sentence=sentence,
        sentence_start=s_start, sentence_end=s_end,
        place_ids=tuple(sorted({places[k].place_id for k in keys})))
