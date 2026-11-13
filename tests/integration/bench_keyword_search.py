"""Measurement of keyword search (not a regression test, so not collected by
default): query count, rows fetched, tracemalloc peak and wall time for the
API search and the HTML search, on seeded data.

Run:  pytest tests/integration/bench_keyword_search.py -s
The last line printed is BENCH_JSON.
"""
import gc, json, time, tracemalloc, uuid
import pytest
from _seed import connect, remove_seeded
from test_keyword_search_bounded import seed_keywords  # noqa: E402 (same directory)

SIZES = [2500, 10000]


@pytest.fixture(scope="module")
def corpora(pg_db):
    out, ledgers = {}, []
    for n in SIZES:
        tag = f"bench{n}x" + uuid.uuid4().hex[:4]
        ledgers.append(seed_keywords(pg_db, tag, n, words=40, categories=4, seed=99, max_usage=6))
        out[n] = tag
    yield out
    for ledger in ledgers:
        remove_seeded(pg_db, ledger)


def measure(fn, instrument_modules):
    # instrument_modules: module-level dicts (globals) that the measured code reads
    # execute_query from. Api.utils re-exports a second copy of utils.py, so the
    # routes' functions must be patched through their own __globals__.
    calls = {"n": 0, "rows": 0}
    originals = {}
    for g in instrument_modules:
        if "execute_query" not in g or id(g) in originals:
            continue
        real = g["execute_query"]
        originals[id(g)] = (g, real)

        def wrap(real):
            def run(sql, params=(), fetch="all", *a, **kw):
                calls["n"] += 1
                out = real(sql, params, fetch, *a, **kw)
                if isinstance(out, list):
                    calls["rows"] += len(out)
                elif out:
                    calls["rows"] += 1
                return out
            return run
        g["execute_query"] = wrap(real)
    import Api.utils.utils as _u
    try:
        _u.invalidate_query_cache()  # cold: the old path caches word lookups
        fn()  # warm-up (not measured)
        calls.update(n=0, rows=0)
        _u.invalidate_query_cache()
        gc.collect()
        t0 = time.perf_counter(); fn(); wall = time.perf_counter() - t0
        calls_ok = dict(calls)
        _u.invalidate_query_cache()
        gc.collect(); tracemalloc.start()
        fn()
        _, peak = tracemalloc.get_traced_memory(); tracemalloc.stop()
    finally:
        for g, real in originals.values():
            g["execute_query"] = real
    return {"queries": calls_ok["n"], "rows_fetched": calls_ok["rows"],
            "peak_KiB": round(peak / 1024, 1), "wall_ms": round(wall * 1000, 1)}


def test_measure_keyword_search(admin_client, pg_db, corpora, capsys):
    import Api.routes.keywords as routes
    import Api.utils.utils as utils
    results = {}
    G = [routes.__dict__, routes.batch_load_keywords_from_rows.__globals__,
         routes.get_keywords_with_usage.__globals__]
    for n, tag in corpora.items():
        q = f"{tag}-hit"
        def api_page1():
            r = admin_client.get("/api/keywords", query_string={"q": q, "sort": "usage_count", "order": "desc", "per_page": 50, "page": 1})
            assert r.status_code == 200
        def api_deep():
            r = admin_client.get("/api/keywords", query_string={"q": q, "sort": "usage_count", "order": "desc", "per_page": 50, "page": 40})
            assert r.status_code == 200
        def html_page1():
            routes.get_keywords_with_usage(search_term=q, page=1, per_page=50, sort_by="usage_count", sort_order="desc")
        results[f"{n} api_search_p1"] = measure(api_page1, G)
        results[f"{n} api_search_p40"] = measure(api_deep, G)
        results[f"{n} html_search_p1"] = measure(html_page1, G)
    with capsys.disabled():
        print("\nBENCH_JSON " + json.dumps(results, sort_keys=True))
