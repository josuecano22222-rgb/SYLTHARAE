"""Measure place/temporal detector throughput on ~1 MB of synthetic text.

    python tools/perf/detector_throughput.py

Reads the gazetteer from ``data/gazetteer/places_seed.json`` (no database).
Prints one line per case: characters, signals, seconds. Numbers are for the
machine it runs on; docs/implementation/PLACE_SIGNALS.md records a run.
"""
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.detection import place_intel, temporal_intel  # noqa: E402
from services.geo import gazetteer as gz  # noqa: E402

DENSE = ("Talks opened in Zagreb and moved to Tripoli on 5 October 2026 while observers in "
         "Geneva waited. المحادثات في القاهرة ثم وبيروت وسط ترقب في عمان. "
         "השיחות עברו ובירושלים ומשם לחיפה. وزیران در تهران دیدار کردند. "
         "Sastanak u Rijeci i Splitu završio je bez dogovora.\n")
PROSE = ("The committee reviewed the annual budget and discussed the procurement schedule "
         "for the coming year. " * 10 + "Delegates from Zagreb attended.\n")
SIZE = 1_000_000


def _gazetteer():
    seed = gz.load_seed_file()
    rows = [(i, p["place_key"], p["label"], p["feature_type"], p["country_codes"], n["name"],
             n["language"], n["script"], n["name_type"], n["homograph"], n["note"])
            for i, p in enumerate(seed["places"], 1) for n in p["names"]]
    start = time.perf_counter()
    gazetteer = place_intel.Gazetteer.from_rows(rows, "0" * 64)
    print(f"index build: {gazetteer.name_count} names, {time.perf_counter() - start:.3f} s")
    return gazetteer


def _time(label, fn, text):
    start = time.perf_counter()
    result = fn(text)
    seconds = time.perf_counter() - start
    print(f"{label}: {len(text)} chars, {len(result.signals)} signals, {seconds:.2f} s")


def main():
    gazetteer = _gazetteer()
    dense = DENSE * (SIZE // len(DENSE))
    prose = PROSE * (SIZE // len(PROSE))
    _time("places, typical prose", lambda t: place_intel.detect(t, gazetteer=gazetteer), prose)
    _time("places, dense mixed-language", lambda t: place_intel.detect(t, gazetteer=gazetteer), dense)
    _time("temporal, dense mixed-language", temporal_intel.detect, dense)


if __name__ == "__main__":
    main()
