#!/usr/bin/env python3
"""Build ``data/gazetteer/places_seed.json`` from the raw Wikidata rows.

Inputs (all committed and tracked in git - ``.gitignore`` re-includes
``data/gazetteer/``; ``tests/unit/test_gazetteer_packaging.py`` guards it):

* ``data/gazetteer/raw/wdqs_batch*.txt`` - WDQS output rows of
  ``tools/gazetteer/wikidata_query.rq``;
* ``tools/gazetteer/titles.tsv`` - the enwiki titles queried and their
  curated feature type;
* ``data/gazetteer/curation.json`` - reviewed corrections and additions,
  each with a reason.

The output is deterministic (sorted, canonical JSON) and carries the SHA-256
of its canonical ``places`` payload, so the loader can prove which seed a
database holds. ``--check`` rebuilds in memory and fails if the committed
seed differs (run by the test suite).

Name classification (UNGEGN terms): a label is an ``endonym`` when it equals
(case-insensitively, after ``core.geo.names.match_key``) one of the item's
P1705 native labels, an ``exonym`` when the item has native labels and it
equals none of them, and ``unclassified`` when the item has no native label
to compare with. ``historical``/``variant`` come only from curation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.geo.names import (LANGUAGES, LANGUAGE_SCRIPT, comparison_key,  # noqa: E402
                            match_key, script_of)

RAW_DIR = ROOT / "data" / "gazetteer" / "raw"
TITLES = ROOT / "tools" / "gazetteer" / "titles.tsv"
CURATION = ROOT / "data" / "gazetteer" / "curation.json"
OUTPUT = ROOT / "data" / "gazetteer" / "places_seed.json"

SEED_FORMAT = 1
SEED_VERSION = "2026-09-28.2"
RETRIEVED = "2026-09-28"
FEATURE_TYPES = ("city", "country", "region")
NAME_TYPE_ORDER = ("endonym", "exonym", "unclassified", "variant", "historical")
LABEL_LANGS = ("en", "ar", "he", "fa", "hr")


class SeedError(ValueError):
    pass


def _qid_num(qid: str) -> int:
    return int(qid[1:])


def parse_raw() -> List[Dict]:
    rows = []
    for path in sorted(RAW_DIR.glob("wdqs_batch*.txt")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            if not line.startswith("@@"):
                raise SeedError(f"{path.name}:{n}: row does not start with @@")
            parts = line[2:].replace("\\|", "\x00").split("\x00")
            if len(parts) != 10:
                raise SeedError(f"{path.name}:{n}: expected 10 fields, got {len(parts)}")
            title, qid, wkt, ccs, en, ar, he, fa, hr, native = parts
            if not re.fullmatch(r"Q\d+", qid):
                raise SeedError(f"{path.name}:{n}: bad QID {qid!r}")
            m = re.fullmatch(r"Point\((-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?)\)", wkt)
            if not m:
                raise SeedError(f"{path.name}:{n}: {qid} has no point coordinate")
            rows.append({
                "title": title, "qid": qid, "lon": float(m.group(1)), "lat": float(m.group(2)),
                "codes": [c for c in ccs.split(",") if c],
                "labels": dict(zip(LABEL_LANGS, (en, ar, he, fa, hr))),
                "native": _split_native(native, f"{path.name}:{n}"),
                "file": path.name,
            })
    return rows


_NATIVE = re.compile(r"(.*?)@([A-Za-z]{2,3}(?:-[A-Za-z0-9]+)*)(?:/|$)")


def _split_native(field: str, where: str) -> List[str]:
    """Split the "/"-joined ``text@lang`` list. A value may itself contain
    "/" (Khoekhoe ``!Huni //hÄb``), so split only after a language tag."""
    out, pos = [], 0
    while pos < len(field):
        m = _NATIVE.match(field, pos)
        if not m or not m.group(1):
            raise SeedError(f"{where}: cannot parse native labels at {field[pos:]!r}")
        out.append(f"{m.group(1)}@{m.group(2)}")
        pos = m.end()
    return out


def parse_titles() -> Dict[str, str]:
    out = {}
    for line in TITLES.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        title, feature = line.split("\t")
        if feature not in FEATURE_TYPES:
            raise SeedError(f"titles.tsv: bad feature type {feature!r} for {title!r}")
        out[title] = feature
    return out


def _title_base(title: str) -> str:
    return re.sub(r"\s*\([^)]*\)$", "", title).split(",")[0].strip()


def _lang(tag: str) -> str:
    return tag.split("-")[0].lower()


def build() -> Dict:
    rows = parse_raw()
    titles = parse_titles()
    curation = json.loads(CURATION.read_text(encoding="utf-8"))
    by_qid = {}
    for row in rows:
        if row["qid"] in by_qid:
            raise SeedError(f"duplicate item {row['qid']}")
        if row["title"] not in titles:
            raise SeedError(f"{row['qid']}: title {row['title']!r} not in titles.tsv")
        by_qid[row["qid"]] = row
    missing = set(titles) - {r["title"] for r in rows}
    if missing:
        raise SeedError(f"titles without a raw row: {sorted(missing)}")

    def need(qid, what):
        if qid not in by_qid:
            raise SeedError(f"curation {what}: unknown item {qid}")
        return by_qid[qid]

    for entry in curation["country_codes"]:
        row = need(entry["qid"], "country_codes")
        if row["codes"]:
            raise SeedError(f"curation country_codes: {entry['qid']} already has {row['codes']}")
        row["codes"] = list(entry["codes"])
    for entry in curation["drop_native"]:
        row = need(entry["qid"], "drop_native")
        if entry["native"] not in row["native"]:
            raise SeedError(f"curation drop_native: {entry['native']!r} not on {entry['qid']}")
        row["native"].remove(entry["native"])
    for entry in curation["replace_label"]:
        row = need(entry["qid"], "replace_label")
        if row["labels"][entry["language"]] != entry["from"]:
            raise SeedError(f"curation replace_label: {entry['qid']} {entry['language']} "
                            f"label is {row['labels'][entry['language']]!r}")
        row["labels"][entry["language"]] = entry["to"]
    split = {}
    for entry in curation["split_label"]:
        row = need(entry["qid"], "split_label")
        label = row["labels"][entry["language"]]
        if entry["separator"] not in label:
            raise SeedError(f"curation split_label: {entry['qid']} label has no separator")
        split[(entry["qid"], entry["language"])] = [p.strip() for p in label.split(entry["separator"])]

    places = []
    for qid in sorted(by_qid, key=_qid_num):
        row = by_qid[qid]
        for code in row["codes"]:
            if not re.fullmatch(r"[A-Z]{2}", code):
                raise SeedError(f"{qid}: bad country code {code!r}")
        if not row["codes"]:
            raise SeedError(f"{qid}: no country code (add a reviewed curation entry)")
        natives = []
        for item in row["native"]:
            text, _, tag = item.rpartition("@")
            if not text or not tag:
                raise SeedError(f"{qid}: malformed native label {item!r}")
            natives.append((text.strip(), _lang(tag)))
        native_keys = {comparison_key(t) for t, _ in natives}

        def classify(name: str) -> str:
            if comparison_key(name) in native_keys:
                return "endonym"
            return "exonym" if natives else "unclassified"

        names: Dict[Tuple[str, str], Dict] = {}

        def add(name, language, name_type, source, reason=None):
            name = name.strip()
            if not name:
                return
            script = script_of(match_key(name))
            if script != LANGUAGE_SCRIPT[language]:
                raise SeedError(f"{qid}: {language} name {name!r} is not in script "
                                f"{LANGUAGE_SCRIPT[language]} (got {script})")
            key = (language, match_key(name))
            if key in names:
                existing = names[key]
                if name_type in ("historical", "variant") or existing["name_type"] in (
                        "historical", "variant"):
                    raise SeedError(f"{qid}: curated {language} name {name!r} duplicates "
                                    f"an existing {existing['name_type']} name")
                if name_type == "endonym" and existing["name_type"] != "endonym":
                    existing.update(name_type="endonym", source=source)
                return
            entry = {"name": name, "language": language, "script": script,
                     "name_type": name_type, "source": source, "homograph": False,
                     "note": reason}
            names[key] = entry

        for text, lang in natives:
            if lang in LANGUAGES:
                add(text, lang, "endonym", "wikidata:P1705")
        for lang in LABEL_LANGS:
            parts = split.get((qid, lang)) or [row["labels"][lang]]
            for label in parts:
                if label:
                    add(label, lang, classify(label), "wikidata:label")
        base = _title_base(row["title"])
        if base and (("en", match_key(base)) not in names):
            add(base, "en", classify(base), "wikipedia:title")
        display = row["labels"]["en"] or base
        places.append({
            "place_key": f"wikidata:{qid}", "label": display,
            "feature_type": titles[row["title"]], "country_codes": sorted(row["codes"]),
            "latitude": row["lat"], "longitude": row["lon"],
            "source_title": row["title"], "_names": names,
        })

    by_key = {p["place_key"]: p for p in places}
    for entry in curation["names"]:
        place = by_key.get(f"wikidata:{entry['qid']}")
        if place is None:
            raise SeedError(f"curation names: unknown item {entry['qid']}")
        if entry["name_type"] not in ("historical", "variant"):
            raise SeedError(f"curation names: {entry['name']!r} has type {entry['name_type']!r}")
        qid = entry["qid"]
        names = place["_names"]
        key = (entry["language"], match_key(entry["name"]))
        if key in names:
            raise SeedError(f"curation names: {qid} already has {entry['language']} "
                            f"{entry['name']!r} as {names[key]['name_type']}")
        script = script_of(key[1])
        if script != LANGUAGE_SCRIPT[entry["language"]]:
            raise SeedError(f"curation names: {entry['name']!r} is not {entry['language']} script")
        names[key] = {"name": entry["name"], "language": entry["language"], "script": script,
                      "name_type": entry["name_type"], "source": entry["source"],
                      "homograph": False, "note": entry["reason"]}

    for entry in curation["homographs"]:
        key = (entry["language"], match_key(entry["name"]))
        hits = [p["_names"][key] for p in places if key in p["_names"]]
        if not hits:
            raise SeedError(f"curation homographs: no {entry['language']} name {entry['name']!r}")
        for hit in hits:
            hit["homograph"] = True
            hit["note"] = "; ".join(x for x in (hit["note"], f"homograph: {entry['reason']}") if x)

    for place in places:
        names = place.pop("_names")
        place["names"] = sorted(names.values(), key=lambda n: (
            n["language"], NAME_TYPE_ORDER.index(n["name_type"]), n["name"]))
        if not place["names"]:
            raise SeedError(f"{place['place_key']}: no names")

    payload = canonical(places)
    return {
        "format": SEED_FORMAT, "seed_version": SEED_VERSION,
        "source": "Wikidata (https://www.wikidata.org/), CC0 1.0; enwiki titles as item keys",
        "licence": "CC0-1.0", "retrieved": RETRIEVED,
        "query": "tools/gazetteer/wikidata_query.rq",
        "content_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "place_count": len(places),
        "name_count": sum(len(p["names"]) for p in places),
        "places": places,
    }


def canonical(places) -> str:
    return json.dumps(places, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def render(seed: Dict) -> str:
    return json.dumps(seed, ensure_ascii=False, sort_keys=True, indent=1) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="fail if the committed seed differs from a fresh build")
    args = parser.parse_args(argv)
    try:
        text = render(build())
    except SeedError as exc:
        print(f"gazetteer seed build failed: {exc}", file=sys.stderr)
        return 2
    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != text:
            print(f"{OUTPUT.relative_to(ROOT)} is stale; run tools/gazetteer/build_seed.py",
                  file=sys.stderr)
            return 1
        print("gazetteer seed is up to date")
        return 0
    OUTPUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
