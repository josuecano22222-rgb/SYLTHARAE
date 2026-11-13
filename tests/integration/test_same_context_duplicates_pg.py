"""Exact repeats are one record; nothing else is touched.

An exact repeat has the same source, the same side, the same file name, the
same content hash and the same details (size, type, date). The earliest record
stays. The same content under another name, another side or another source is a
different record and must survive. Shared content and its contexts always
survive the cleanup.
"""

from __future__ import annotations

import datetime

from tests.integration._seed import connect, document, side, source


def _ids_for_hash(cur, hash_id):
    cur.execute(
        "SELECT p.id FROM paths p JOIN hash_contexts c ON c.id = p.context_id"
        " WHERE c.hash_id = %s ORDER BY p.id", (hash_id,))
    return [row[0] for row in cur.fetchall()]


def test_exact_repeats_are_removed_and_everything_else_is_kept(pg_db):
    from database.services.dedup_service import DeduplicationService

    conn = connect(pg_db)
    with conn.cursor() as cur:
        a, b = source(cur), source(cur)
        s1, s2 = side(cur), side(cur)
        body = "same repeated body for the exact-repeat cleanup"
        p1, h, c_a_s1 = document(cur, source_id=a, side_id=s1, text=body, file_name="x.txt")
        # exact repeat: same name, same content, same details
        p2, _, _ = document(cur, source_id=a, side_id=s1, text=body,
                            file_name="x.txt", hash_id=h)
        # same content, different name: a different record, kept
        p3, _, _ = document(cur, source_id=a, side_id=s1, text=body,
                            file_name="x copy.txt", hash_id=h)
        p4, _, c_a_s2 = document(cur, source_id=a, side_id=s2, text=body,
                                 file_name="x.txt", hash_id=h)
        p5, _, c_b_s1 = document(cur, source_id=b, side_id=s1, text=body,
                                 file_name="x.txt", hash_id=h)
    conn.commit()

    service = DeduplicationService(lambda: conn)
    groups = [g for g in service.same_context_duplicates() if g["context_id"] == c_a_s1]
    assert len(groups) == 1
    assert groups[0]["keep_id"] == p1
    assert groups[0]["remove_ids"] == [p2]

    other_contexts = {g["context_id"] for g in service.same_context_duplicates()}
    assert c_a_s2 not in other_contexts, "another side is a different record"
    assert c_b_s1 not in other_contexts, "another source is a different record"

    result = service.remove_same_context_duplicates()
    assert result["removed"] >= 1

    with conn.cursor() as cur:
        assert _ids_for_hash(cur, h) == [p1, p3, p4, p5], "only the exact repeat goes"
        cur.execute("SELECT COUNT(*) FROM hash_contexts WHERE hash_id = %s", (h,))
        assert cur.fetchone()[0] == 3, "every context of the shared content survives"
        cur.execute("SELECT COUNT(*) FROM hashs WHERE id = %s", (h,))
        assert cur.fetchone()[0] == 1, "the shared content itself must survive"
        cur.execute("SELECT COUNT(*) FROM contents WHERE hash_id = %s", (h,))
        assert cur.fetchone()[0] == 1, "extracted text must survive"
    conn.close()


def test_a_different_size_or_date_is_not_an_exact_repeat(pg_db):
    from database.services.dedup_service import DeduplicationService

    conn = connect(pg_db)
    with conn.cursor() as cur:
        a, s1 = source(cur), side(cur)
        body = "same content, different details"
        p1, h, c = document(cur, source_id=a, side_id=s1, text=body,
                            file_name="d.txt", file_date=datetime.date(2026, 1, 1))
        p2, _, _ = document(cur, source_id=a, side_id=s1, text=body, file_name="d.txt",
                            file_date=datetime.date(2026, 2, 2), hash_id=h)
    conn.commit()

    service = DeduplicationService(lambda: conn)
    assert c not in {g["context_id"] for g in service.same_context_duplicates()}
    service.remove_same_context_duplicates()
    with conn.cursor() as cur:
        assert _ids_for_hash(cur, h) == [p1, p2]
    conn.close()


def test_ingesting_an_exact_repeat_writes_no_second_record(pg_db):
    """The root fix: the same file from another folder is one record."""
    import hashlib
    from database.services.dedup_service import DeduplicationService

    conn = connect(pg_db)
    service = DeduplicationService(lambda: conn)
    with conn.cursor() as cur:
        a, s1 = source(cur), side(cur)
    conn.commit()
    digest = hashlib.sha256(b"ingest once, from two folders").hexdigest()
    details = dict(file_name="report.txt", file_size=28, file_type="txt",
                   file_date=datetime.date(2026, 3, 3), date_creation=datetime.date(2026, 3, 3),
                   processing_status="processed")

    first = service.register_content(digest, a, s1,
                                     dict(details, file_path="/inbox/a/report.txt"))
    second = service.register_content(digest, a, s1,
                                      dict(details, file_path="/inbox/b/report.txt"))
    assert second["duplicate"] is True
    assert second["path_id"] == first["path_id"]

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM paths WHERE id = %s OR context_id = %s",
                    (first["path_id"], first["context_id"]))
        assert cur.fetchone()[0] == 1
    conn.close()


def test_the_same_name_in_another_side_is_still_recorded(pg_db):
    import hashlib
    from database.services.dedup_service import DeduplicationService

    conn = connect(pg_db)
    service = DeduplicationService(lambda: conn)
    with conn.cursor() as cur:
        a, s1, s2 = source(cur), side(cur), side(cur)
    conn.commit()
    digest = hashlib.sha256(b"same bytes, two sides").hexdigest()
    details = dict(file_name="shared.txt", file_size=21, file_type="txt",
                   file_date=datetime.date(2026, 4, 4), date_creation=datetime.date(2026, 4, 4),
                   processing_status="processed")
    first = service.register_content(digest, a, s1, dict(details, file_path="/x/shared.txt"))
    other = service.register_content(digest, a, s2, dict(details, file_path="/x/shared.txt"))
    assert other["duplicate"] is False
    assert other["path_id"] != first["path_id"]
    conn.close()
