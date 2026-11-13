"""Deterministic, versioned narrative templates (five voices).

Every analytical section speaks in exactly five voices, in this order:

1. **Measure** - what was computed, over what.
2. **Finding** - what the numbers show.
3. **Confidence** - how far the finding can be trusted, citing the
   threshold (and its source) that was applied.
4. **Consequence** - what the finding implies for the reader's work, and
   what it does not imply.
5. **Caveat** - limits of the measure and the next step.

A template set is reviewed code: fixed msgids with ``%(name)s`` placeholders,
under a set id and version. An analysis stores which sentence it chose for
each voice (key + msgid) and the parameters, never prose, so the text is
reproducible from the stored record and renders in any supported language
through the translation catalogs. There is no free text generation anywhere:
the only variable content is the named parameters, and they are values the
analysis measured.

Changing a msgid or the choice rules changes what readers are told; bump the
set version (the version is in each analysis fingerprint and is recorded with
every stored narrative).

Counts and plurals (NARR-01)
----------------------------
A sentence whose wording depends on a count is a :class:`Plural`: a singular
and a plural msgid plus the name of the parameter that holds the count. It is
rendered with gettext's ``ngettext``, so the plural *rule* comes only from the
catalog's ``Plural-Forms`` header (Arabic has six forms, Croatian three,
English, Hebrew and Persian two) and there is no plural logic of our own. One
sentence carries at most one count; a voice that must state several counts
is a *sequence* of sentences (each a whole sentence, never a fragment glued
into another one), and plural-neutral "label: value" wording is used where a
count is only a size.

Stored record formats
---------------------
* format 1 (``record_format=1``, the released ``keyness@1``): each voice is
  ``{"voice", "key", "msgid", "params"}``. Sets in format 1 may not use
  plurals or several sentences per voice, so what they store never changes.
* format 2 (default for new sets): the narrative carries ``"format": 2`` and
  each voice is ``{"voice", "sentences": [{"key", "msgid", "params"}, ...]}``;
  a plural sentence adds ``"msgid_plural"`` and ``"count"`` (the parameter
  name).

:func:`render` reads both, so every narrative ever stored still renders.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from core.criteria.model import sha256_hex

VOICES: Tuple[str, ...] = ("measure", "finding", "confidence", "consequence", "caveat")

_PLACEHOLDER = re.compile(r"%\((\w+)\)s")
_NAME = re.compile(r"[a-z][a-z0-9_]*")

RECORD_FORMATS = (1, 2)


class NarrativeError(ValueError):
    pass


def placeholders(msgid: str) -> Tuple[str, ...]:
    return tuple(sorted(set(_PLACEHOLDER.findall(msgid))))


@dataclass(frozen=True)
class Plural:
    """A sentence whose wording follows a count: ``ngettext(singular, plural,
    params[count])``. ``plural`` must contain every placeholder the sentence
    takes, including ``%(count)s``; ``singular`` may use a subset (a language
    may say "one term" without the digit)."""
    singular: str
    plural: str
    count: str

    def msgids(self) -> Tuple[str, str]:
        return (self.singular, self.plural)


Sentence = Union[str, Plural]
Choice = Tuple[str, Mapping[str, Any]]


def _check_msgid(set_id: str, key: str, msgid: Any) -> None:
    if not isinstance(msgid, str) or msgid.strip() != msgid or not msgid:
        raise NarrativeError(f"{set_id}: {key} needs a trimmed msgid")
    if "%" in _PLACEHOLDER.sub("", msgid).replace("%%", ""):
        raise NarrativeError(f"{set_id}: {key} has a stray '%'")


def sentence_placeholders(sentence: Sentence) -> Tuple[str, ...]:
    return placeholders(sentence.plural if isinstance(sentence, Plural) else sentence)


@dataclass(frozen=True)
class TemplateSet:
    set_id: str
    version: int
    #: sentence key -> msgid (or Plural). Keys are "<voice>.<variant>".
    sentences: Mapping[str, Sentence]
    #: 1 = the released per-voice single-sentence record; 2 = sentences list.
    record_format: int = 2

    def __post_init__(self) -> None:
        if not _NAME.fullmatch(self.set_id or ""):
            raise NarrativeError(f"template set id {self.set_id!r} must be snake_case")
        if not isinstance(self.version, int) or self.version < 1:
            raise NarrativeError(f"{self.set_id}: version must be an integer >= 1")
        if self.record_format not in RECORD_FORMATS:
            raise NarrativeError(f"{self.set_id}: record_format must be one of {RECORD_FORMATS}")
        voices_seen = set()
        for key, sentence in self.sentences.items():
            voice, _, variant = key.partition(".")
            if voice not in VOICES or not _NAME.fullmatch(variant):
                raise NarrativeError(f"{self.set_id}: sentence key {key!r} must be "
                                     f"'<voice>.<variant>' with a voice in {VOICES}")
            if isinstance(sentence, Plural):
                if self.record_format == 1:
                    raise NarrativeError(f"{self.set_id}: {key}: record format 1 has no plurals")
                _check_msgid(self.set_id, key, sentence.singular)
                _check_msgid(self.set_id, key, sentence.plural)
                if sentence.singular == sentence.plural:
                    raise NarrativeError(f"{self.set_id}: {key}: singular and plural are identical")
                takes = placeholders(sentence.plural)
                if sentence.count not in takes:
                    raise NarrativeError(f"{self.set_id}: {key}: the plural msgid must contain "
                                         f"%({sentence.count})s")
                extra = set(placeholders(sentence.singular)) - set(takes)
                if extra:
                    raise NarrativeError(f"{self.set_id}: {key}: singular has placeholders "
                                         f"{sorted(extra)} the plural lacks")
            else:
                _check_msgid(self.set_id, key, sentence)
            voices_seen.add(voice)
        missing = [v for v in VOICES if v not in voices_seen]
        if missing:
            raise NarrativeError(f"{self.set_id}: no sentence for voice(s) {missing}")

    @property
    def key(self) -> str:
        return f"{self.set_id}@{self.version}"

    def fingerprint(self) -> str:
        def value(s: Sentence) -> Any:
            return ({"singular": s.singular, "plural": s.plural, "count": s.count}
                    if isinstance(s, Plural) else s)
        semantic: Dict[str, Any] = {
            "set_id": self.set_id, "version": self.version,
            "sentences": {k: value(v) for k, v in sorted(self.sentences.items())}}
        # Format 1 fingerprints predate the field and must not move.
        if self.record_format != 1:
            semantic["record_format"] = self.record_format
        return sha256_hex(semantic)

    def msgids(self) -> Tuple[str, ...]:
        out: List[str] = []
        for k in sorted(self.sentences):
            s = self.sentences[k]
            out.extend(s.msgids() if isinstance(s, Plural) else (s,))
        return tuple(out)

    def singular_msgids(self) -> Tuple[str, ...]:
        """The catalog keys: plain msgids and plural singulars (a plural
        entry is looked up by its singular msgid)."""
        return tuple((s.singular if isinstance(s, Plural) else s)
                     for s in (self.sentences[k] for k in sorted(self.sentences)))

    def plurals(self) -> Tuple[Plural, ...]:
        return tuple(self.sentences[k] for k in sorted(self.sentences)
                     if isinstance(self.sentences[k], Plural))

    def plural_msgids(self) -> Tuple[Tuple[str, str], ...]:
        """(singular, plural) pairs: the catalog entries that need msgid_plural."""
        return tuple(self.sentences[k].msgids() for k in sorted(self.sentences)
                     if isinstance(self.sentences[k], Plural))

    def _sentence_record(self, key: str, params: Mapping[str, Any]) -> Dict[str, Any]:
        if key not in self.sentences:
            raise NarrativeError(f"{self.set_id}: unknown sentence {key!r}")
        sentence = self.sentences[key]
        given = tuple(sorted(params))
        takes = sentence_placeholders(sentence)
        if given != takes:
            raise NarrativeError(f"{self.set_id}: {key} takes {takes}, got {given}")
        for name, value in params.items():
            if not (value is None or isinstance(value, (str, int, float))) \
                    or isinstance(value, bool):
                raise NarrativeError(f"{self.set_id}: {key}.{name} must be text or a number")
        if isinstance(sentence, Plural):
            n = params[sentence.count]
            if isinstance(n, bool) or not isinstance(n, int) or n < 0:
                raise NarrativeError(f"{self.set_id}: {key}.{sentence.count} is the count "
                                     "and must be a whole number >= 0")
            return {"key": key, "msgid": sentence.singular, "msgid_plural": sentence.plural,
                    "count": sentence.count, "params": dict(params)}
        return {"key": key, "msgid": sentence, "params": dict(params)}

    def compose(self, choices: Sequence[Union[Choice, Sequence[Choice]]]) -> Dict[str, Any]:
        """Stored narrative for ``choices``: per voice, in voice order, one
        ``(sentence_key, params)`` - or, in record format 2, a non-empty list
        of them, all of that voice. Every placeholder must be given, and no
        extra parameter is accepted (an unused value would be invisible)."""
        groups: List[List[Choice]] = []
        for choice in choices:
            if isinstance(choice, tuple) and len(choice) == 2 and isinstance(choice[0], str):
                groups.append([choice])
            else:
                group = list(choice)
                if not group:
                    raise NarrativeError(f"{self.set_id}: a voice needs at least one sentence")
                if len(group) > 1 and self.record_format == 1:
                    raise NarrativeError(f"{self.set_id}: record format 1 has one sentence "
                                         "per voice")
                groups.append(group)
        voices = [{key.partition(".")[0] for key, _ in g} for g in groups]
        if any(len(v) != 1 for v in voices) or [v.pop() for v in voices] != list(VOICES):
            raise NarrativeError(f"{self.set_id}: one sentence per voice, in order {VOICES}")
        out: List[Dict[str, Any]] = []
        for group in groups:
            voice = group[0][0].partition(".")[0]
            records = [self._sentence_record(key, params) for key, params in group]
            if self.record_format == 1:
                out.append(dict({"voice": voice}, **records[0]))
            else:
                out.append({"voice": voice, "sentences": records})
        narrative: Dict[str, Any] = {
            "template_set": self.set_id, "template_version": self.version,
            "template_fingerprint": self.fingerprint(), "voices": out}
        if self.record_format != 1:
            narrative["format"] = self.record_format
        return narrative


def source_ngettext(singular: str, plural: str, n: int) -> str:
    """The msgids' own language (English, nplurals=2, plural=(n != 1)), for
    renderings that are defined to be in the source language (artifacts)."""
    return singular if n == 1 else plural


def render(narrative: Mapping[str, Any], gettext: Callable[[str], str],
           format_number: Optional[Callable[[Any], str]] = None,
           ngettext: Optional[Callable[[str, str, int], str]] = None) -> List[Dict[str, str]]:
    """Text of a stored narrative in the caller's language: each msgid is
    translated (plural sentences through ``ngettext`` with the raw count),
    then filled with its parameters (numbers through ``format_number``, so
    digits follow the locale). Sentences of one voice are joined by a space.
    Returns ``[{"voice", "text"}]`` in voice order. A plural sentence without
    an ``ngettext`` is an error, never a guess."""
    fmt = format_number or (lambda v: f"{v:,}" if isinstance(v, int) else
                            (f"{v:,.2f}" if isinstance(v, float) else str(v)))

    def fill(record: Mapping[str, Any]) -> str:
        params = {k: (fmt(v) if isinstance(v, (int, float)) and not isinstance(v, bool)
                      else ("" if v is None else str(v)))
                  for k, v in record["params"].items()}
        # The sentence's own text is read out of the record into locals before
        # it reaches a translation call. ``gettext``/``ngettext`` are extraction
        # keywords, and the extractor walks the arguments for string constants,
        # so the dict *keys* used here ("msgid", "msgid_plural") were offered to
        # every catalog as a sentence to translate: a plural pair no runtime
        # path ever looks up, which ``msgmerge`` then carried a wrong
        # translation into (NARR-02).
        singular = record["msgid"]
        if record.get("msgid_plural") is not None:
            if ngettext is None:
                raise NarrativeError(f"{record.get('key')}: plural sentence needs ngettext")
            plural = record["msgid_plural"]
            template = ngettext(singular, plural,
                                int(record["params"][record["count"]]))
        else:
            template = gettext(singular)
        return template % params

    lines = []
    for voice in narrative["voices"]:
        records = voice["sentences"] if "sentences" in voice else [voice]
        lines.append({"voice": voice["voice"], "text": " ".join(fill(r) for r in records)})
    return lines
