"""Bounded keyword search, without a database.

A fake ``run`` answers the three query shapes the module uses (the keyword
keyset stream, the words lookup, the usage counts), so the ordering rules,
chunk boundaries, page clamping and the memory-bounding structure are checked
against an independent oracle written from the old (full-load) behaviour.
"""
import random
import re

import pytest

from core import keyword_search as ks
from core.serialization import pack_int_list


class FakeDb:
    """Keywords, words and usage held in memory; ``run`` answers the SQL the
    module issues. ``queries`` records each call so tests can check bounds."""

    def __init__(self, keywords, words, usage):
        # keywords: {id: (word_ids, category_id)}; words: {id: text}; usage: {id: n}
        self.keywords = keywords
        self.words = words
        self.usage = usage
        self.queries = []

    def run(self, sql, params=(), fetch="all"):
        self.queries.append((sql, tuple(params) if params else ()))
        if "FROM keywords k" in sql and "ORDER BY k.id" in sql:
            after = params[0]
            limit = params[-1]
            # Conditions in this fake are only category equality, when present.
            cat = params[1] if len(params) == 3 else None
            ids = sorted(k for k in self.keywords if k > after
                         and (cat is None or self.keywords[k][1] == cat))
            return [(k, pack_int_list(self.keywords[k][0]), self.keywords[k][1])
                    for k in ids[:limit]]
        if "FROM words WHERE" in sql:
            wanted = params[0]
            return [(w, self.words[w]) for w in wanted if w in self.words]
        if "FROM keywords_hashs" in sql:
            wanted = params[0]
            return [(k, self.usage[k]) for k in wanted if self.usage.get(k)]
        if "FROM categorys" in sql:
            return [(c, f"cat{c}") for c in params[0]]
        raise AssertionError(f"unexpected query: {sql[:80]}")


def make_db(n, *, seed=7, words=30):
    rng = random.Random(seed)
    word_text = {w: (("hit" if w % 4 == 0 else "miss") + f"{w:02d}") for w in range(1, words + 1)}
    keywords, usage = {}, {}
    seen = set()
    kid = 0
    while len(keywords) < n:
        combo = tuple(rng.randint(1, words) for _ in range(rng.randint(1, 3)))
        if combo in seen:
            continue
        seen.add(combo)
        kid += 1
        keywords[kid] = (list(combo), rng.randint(1, 4))
        usage[kid] = rng.choice([0, 0, 1, 2, 2, 3, 5, 5, 8])
    return FakeDb(keywords, word_text, usage)


def text_of(db, kid):
    return " ".join(db.words[w] for w in db.keywords[kid][0] if w in db.words)


def oracle_html(db, needle, sort_by, descending):
    """The HTML page's old order: one sort with a tuple key; ties by id in the
    sort direction."""
    rows = []
    for kid in db.keywords:
        text = text_of(db, kid)
        if needle is not None and needle not in text.lower():
            continue
        rows.append((kid, text, db.usage.get(kid, 0)))
    if sort_by == "text":
        key = lambda r: (r[1].lower(), r[0])
    elif sort_by == "id":
        key = lambda r: (r[0],)
    elif sort_by == "status":
        key = lambda r: (r[2] > 0, r[2], r[0])
    else:
        key = lambda r: (r[2], r[0])
    return [r[0] for r in sorted(rows, key=key, reverse=descending)]


def oracle_api(db, needle, sort_by, descending):
    """The API's old order: a stable sort on the column over rows in id order,
    so ties keep id ascending in both directions."""
    rows = []
    for kid in sorted(db.keywords):
        text = text_of(db, kid)
        if needle is not None and needle not in text.lower():
            continue
        rows.append((kid, text, db.usage.get(kid, 0)))
    if sort_by == "usage_count":
        return [r[0] for r in sorted(rows, key=lambda r: r[2], reverse=descending)]
    if sort_by == "text":
        return [r[0] for r in sorted(rows, key=lambda r: r[1].lower(), reverse=descending)]
    if sort_by == "id":
        return [r[0] for r in sorted(rows, key=lambda r: r[0], reverse=descending)]
    if sort_by == "status":
        return [r[0] for r in sorted(rows, key=lambda r: (r[2] > 0, r[2]), reverse=descending)]
    return [r[0] for r in rows]  # date_creation: not sorted, id ascending


def traverse(db, *, size, needle, rank, chunk=ks.CHUNK, conditions=(), params=()):
    """Every page in order, the way the table walks Show More."""
    _, total = ks.keyword_page(db.run, page=1, size=size, needle=needle,
                               conditions=list(conditions), params=list(params),
                               rank=rank, chunk=chunk)
    pages = max(1, -(-total // size))
    got = []
    for page in range(1, pages + 1):
        rows, t = ks.keyword_page(db.run, page=page, size=size, needle=needle,
                                  conditions=list(conditions), params=list(params),
                                  rank=rank, chunk=chunk)
        assert t == total
        got.extend(r[0] for r in rows)
    return total, got


# ---------------------------------------------------------------- ordering


@pytest.mark.parametrize("sort_by", ["usage_count", "text", "id", "status"])
@pytest.mark.parametrize("descending", [True, False])
@pytest.mark.parametrize("needle", [None, "hit"])
def test_html_order_matches_the_old_order(sort_by, descending, needle):
    db = make_db(300, seed=3)
    total, got = traverse(db, size=17, needle=needle,
                          rank=ks.html_rank(sort_by, descending))
    expected = oracle_html(db, needle, sort_by, descending)
    assert total == len(expected)
    assert got == expected


@pytest.mark.parametrize("sort_by", ["usage_count", "text", "id", "status", "date_creation"])
@pytest.mark.parametrize("descending", [True, False])
def test_api_order_matches_the_old_order(sort_by, descending):
    db = make_db(300, seed=5)
    total, got = traverse(db, size=23, needle="hit",
                          rank=ks.api_rank(sort_by, descending))
    expected = oracle_api(db, "hit", sort_by, descending)
    assert total == len(expected)
    assert got == expected


def test_the_two_paths_break_ties_differently_on_purpose():
    # Ties on usage: the HTML page follows the direction for the id, the API
    # keeps ids ascending. The two must not be merged.
    db = FakeDb({1: ([1], 1), 2: ([1], 1), 3: ([1], 1)},
                {1: "hit"}, {1: 5, 2: 5, 3: 5})
    html = oracle_html(db, None, "usage_count", True)
    api = oracle_api(db, None, "usage_count", True)
    assert html == [3, 2, 1]
    assert api == [1, 2, 3]


# ---------------------------------------------------------- traversal & totals


@pytest.mark.parametrize("chunk", [1, 2, 7, 1000])
def test_every_chunk_size_gives_the_same_rows_and_total(chunk):
    db = make_db(120, seed=11)
    _, reference = traverse(db, size=10, needle="hit",
                            rank=ks.api_rank("id", False), chunk=1000)
    total, got = traverse(db, size=10, needle="hit",
                          rank=ks.api_rank("id", False), chunk=chunk)
    assert got == reference
    assert total == len(oracle_api(db, "hit", "id", False))


def test_no_duplicates_and_no_loss_across_page_boundaries():
    db = make_db(257, seed=13)
    total, got = traverse(db, size=10, needle=None, rank=ks.api_rank("id", False))
    assert total == 257
    assert len(got) == len(set(got)) == 257
    assert got == sorted(db.keywords)


def test_the_last_page_is_partial_when_the_total_does_not_divide():
    db = make_db(25, seed=17)
    rows, total = ks.keyword_page(db.run, page=3, size=10, needle=None,
                                  conditions=[], params=[], rank=ks.api_rank("id", False))
    assert total == 25 and len(rows) == 5


def test_an_exact_multiple_leaves_a_full_last_page():
    db = make_db(30, seed=19)
    rows, total = ks.keyword_page(db.run, page=3, size=10, needle=None,
                                  conditions=[], params=[], rank=ks.api_rank("id", False))
    assert total == 30 and len(rows) == 10


def test_an_empty_result_is_empty_and_counts_zero():
    db = make_db(40, seed=23)
    rows, total = ks.keyword_page(db.run, page=1, size=10, needle="no-such-text",
                                  conditions=[], params=[], rank=ks.api_rank("id", False))
    assert rows == [] and total == 0


def test_a_page_past_the_end_returns_no_rows_for_the_caller_to_decide():
    db = make_db(25, seed=29)
    rows, total = ks.keyword_page(db.run, page=9, size=10, needle=None,
                                  conditions=[], params=[], rank=ks.api_rank("id", False))
    assert rows == [] and total == 25


def test_a_text_with_no_words_does_not_match_and_is_not_counted():
    db = FakeDb({1: ([99], 1), 2: ([1], 1)}, {1: "hit"}, {})
    _, total = ks.keyword_page(db.run, page=1, size=10, needle=None,
                               conditions=[], params=[], rank=ks.api_rank("id", False))
    # needle=None counts every keyword row; a search does not count the textless one.
    assert total == 2
    _, found = ks.keyword_page(db.run, page=1, size=10, needle="hit",
                               conditions=[], params=[], rank=ks.api_rank("id", False))
    assert found == 1


# ------------------------------------------------------------ bounded memory


def test_the_count_pass_asks_for_no_usage_and_counts_correctly():
    db = make_db(200, seed=31)
    rows, total = ks._scan(db.run, needle="hit", conditions=[], params=[],
                           rank=None, keep=0, chunk=ks.CHUNK)
    assert rows == []
    assert total == len(oracle_api(db, "hit", "id", False))
    assert not [q for q in db.queries if "FROM keywords_hashs" in q[0]], \
        "the count pass must not ask for usage"


def test_usage_is_asked_per_chunk_and_never_for_more_than_a_chunk():
    db = make_db(200, seed=33)
    ks.keyword_page(db.run, page=1, size=10, needle="hit",
                    conditions=[], params=[], rank=ks.api_rank("id", False), chunk=16)
    usage_calls = [q for q in db.queries if "FROM keywords_hashs" in q[0]]
    assert usage_calls, "the page pass should ask for usage"
    assert all(len(q[1][0]) <= 16 for q in usage_calls)


def test_a_page_keeps_only_its_depth_of_candidates(monkeypatch):
    db = make_db(500, seed=37)
    kept = []
    real = ks.heapq.nsmallest

    def spy(n, iterable, key=None):
        kept.append(n)
        return real(n, iterable, key=key)

    monkeypatch.setattr(ks.heapq, "nsmallest", spy)
    ks.keyword_page(db.run, page=4, size=10, needle="hit",
                    conditions=[], params=[], rank=ks.api_rank("id", False))
    assert kept == [40], "the heap is the requested depth, not the total"


def test_an_absurd_page_never_builds_a_heap(monkeypatch):
    db = make_db(500, seed=41)
    calls = []
    real = ks.heapq.nsmallest
    monkeypatch.setattr(ks.heapq, "nsmallest",
                        lambda n, it, key=None: calls.append(n) or real(n, it, key=key))
    rows, total = ks.keyword_page(db.run, page=10**9, size=200, needle="hit",
                                  conditions=[], params=[], rank=ks.api_rank("id", False))
    assert rows == [] and total > 0
    assert calls == [], "a page past the end must cost only the count pass"


def test_chunks_are_keyset_ordered_and_limited():
    db = make_db(50, seed=43)
    ks.keyword_page(db.run, page=1, size=10, needle=None,
                    conditions=[], params=[], rank=ks.api_rank("id", False), chunk=8)
    kw_queries = [q for q in db.queries if "FROM keywords k" in q[0]]
    assert {q[1][-1] for q in kw_queries} == {8}, "every chunk is limited to the chunk size"
    # Two streams (count pass, then page pass). Each starts at id 0 and its
    # chunk starts only increase, so nothing is repeated within a stream.
    streams, current = [], []
    for q in kw_queries:
        if q[1][0] == 0 and current:
            streams.append(current)
            current = []
        current.append(q[1][0])
    streams.append(current)
    assert len(streams) == 2
    for st in streams:
        assert st[0] == 0 and st == sorted(set(st))


# -------------------------------------------------------------- decode rules


def test_decode_mirrors_the_old_text_rules():
    words = {1: "alpha", 2: "beta", 3: "gamma"}
    db = FakeDb({}, words, {})
    rows = [
        (10, pack_int_list([1, 3]), 1),   # "alpha gamma", order kept
        (11, pack_int_list([3, 99, 2]), 1),  # unknown word 99 is skipped
        (12, pack_int_list([99]), 1),        # nothing known: no text
        (13, None, 1),                       # no blob: no text
        (0, pack_int_list([1]), 1),          # id 0 is skipped, as before
    ]
    texts = ks.decode_texts(db.run, rows)
    assert texts == {10: "alpha gamma", 11: "gamma beta"}


def test_a_blob_that_will_not_unpack_gives_no_text_and_no_crash():
    db = FakeDb({}, {1: "alpha"}, {})
    texts = ks.decode_texts(db.run, [(5, b"not-a-list", 1)])
    assert texts == {}


def test_a_database_error_is_raised_not_turned_into_no_rows():
    def broken(sql, params=(), fetch="all"):
        raise RuntimeError("connection lost")

    with pytest.raises(RuntimeError):
        ks.keyword_page(broken, page=1, size=10, needle="x",
                        conditions=[], params=[], rank=ks.api_rank("id", False))


def test_category_names_are_none_where_there_is_no_category():
    db = FakeDb({}, {}, {})
    assert ks.category_names(db.run, [None]) == {}
    assert ks.category_names(db.run, [3, None, 3]) == {3: "cat3"}
