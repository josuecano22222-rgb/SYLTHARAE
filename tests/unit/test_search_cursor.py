from core.search.cursor import decode_cursor, encode_cursor, parse_relevance_cursor, relevance_cursor
from core.search.normalize import pagination_query_signature, search_cache_key


def test_cursor_requires_matching_signature():
    sig = search_cache_key(query="report", sort_by="relevance", sort_order="desc")
    token = relevance_cursor(1.5, 42, "desc", query_signature=sig)
    assert parse_relevance_cursor(token, expected_signature=sig) == (1.5, 42, "desc")
    assert parse_relevance_cursor(token, expected_signature="other") is None


def test_pagination_signature_stable_for_route_and_service_inputs():
    """Mint/validate must use the same helper (regression: mixed filter keys → 400)."""
    sig = pagination_query_signature(
        query="This is a test",
        analyst_scope="uncategorized",
        file_statuses=["Read"],
        case_sensitive=True,
        whole_word=True,
        sort_by="relevance",
        sort_order="desc",
    )
    token = relevance_cursor(5.0, 327, "desc", query_signature=sig)
    assert parse_relevance_cursor(token, expected_signature=sig) == (5.0, 327, "desc")


def test_cursor_rejects_wrong_signature():
    sig = search_cache_key(query="a", sort_by="relevance", sort_order="desc")
    token = relevance_cursor(1.0, 1, "desc", query_signature=sig)
    data = decode_cursor(token)
    assert data is not None
    data["sig"] = "tampered"
    bad = encode_cursor(data)
    assert parse_relevance_cursor(bad, expected_signature=sig) is None


def test_tiebreak_round_trips_the_date_and_null_date():
    import datetime as dt
    from core.search.cursor import parse_relevance_cursor_tiebreak, relevance_cursor
    sig = "sig-tb"
    dated = relevance_cursor(2.0, 7, "desc", query_signature=sig,
                             file_date=dt.date(2020, 2, 29), file_date_known=True)
    assert parse_relevance_cursor_tiebreak(dated, expected_signature=sig) == (True, dt.date(2020, 2, 29))
    undated = relevance_cursor(2.0, 7, "desc", query_signature=sig,
                               file_date=None, file_date_known=True)
    assert parse_relevance_cursor_tiebreak(undated, expected_signature=sig) == (True, None)


def test_legacy_v2_cursor_has_no_recorded_date():
    from core.search.cursor import parse_relevance_cursor_tiebreak, relevance_cursor
    sig = "sig-legacy"
    legacy = relevance_cursor(3.0, 9, "desc", query_signature=sig)
    assert parse_relevance_cursor_tiebreak(legacy, expected_signature=sig) == (False, None)
    assert parse_relevance_cursor_tiebreak(legacy, expected_signature="other") is None
