"""Release acceptance (§20): export -> inspect -> restore -> verify.

The app must be able to restore its own backups: ExportService produces a
zip(backup.json) over the evidence tables; ImportService validates it and
(restore_data=True) replaces the ALLOWED_TABLES rows inside one transaction.
Runs against the disposable test database only.
"""
import io
import time
import uuid
import zipfile

import pytest

from Api.services.export_service import ExportService
from Api.services.import_service import ImportService


@pytest.fixture()
def seeded_corpus(tmp_path, monkeypatch):
    """Ingest a tiny corpus so the database holds real evidence rows."""
    root = tmp_path / "rt_corpus"
    root.mkdir()
    uid = uuid.uuid4().hex
    body = f"roundtrip acceptance lynx {uid} " * 12
    (root / "a.txt").write_text(body, encoding="utf-8")
    (root / "b.log").write_text(f"delta echo {uid}\n" * 6, encoding="utf-8")
    monkeypatch.setenv("INGESTION_ROOTS", str(root))

    from services.ingesting.service import IngestionRequest, IngestionService

    svc = IngestionService()
    req = IngestionRequest(
        path=str(root), source=f"rt-src-{uid}", side=f"rt-side-{uid}",
        options=None, dry_run=False, created_by="tester",
    )
    result = svc.run(req)
    assert result.success, result.errors
    return root


def _counts():
    from Api.utils.utils import get_connection, return_connection

    conn = get_connection()
    try:
        cur = conn.cursor()
        out = {}
        for t in ("words", "words_hashs", "hashs", "contents", "paths",
                  "sources", "sides", "titles_content"):
            cur.execute(f"SELECT count(*) FROM {t}")
            out[t] = cur.fetchone()[0]
        return out
    finally:
        return_connection(conn)


class TestBackupRoundtrip:
    def test_export_then_restore_preserves_evidence(
        self, app, seeded_corpus,
    ):
        before = _counts()
        assert before["hashs"] >= 1 and before["words"] >= 1

        # 1. create backup
        buf = ExportService.export_database_backup(include_data=True)
        payload = buf.read()

        # 2. inspect backup
        z = zipfile.ZipFile(io.BytesIO(payload))
        assert "backup.json" in z.namelist()
        for banned in ("users", "sessions", "schema_migrations"):
            assert banned not in json_tables(payload), banned

        # 3. dry-run validation (no writes)
        dry = ImportService.import_database_backup(
            backup_file=io.BytesIO(payload), restore_data=False,
        )
        assert dry.get("valid") is True, str(dry)[:400]

        # 4. restore into the (disposable) test database
        out = ImportService.import_database_backup(
            backup_file=io.BytesIO(payload), restore_data=True,
        )
        assert out.get("valid") is True or out.get("success") is True, str(out)[:400]

        # 5. verify restored data
        after = _counts()
        for table, n in before.items():
            assert after[table] == n, f"{table}: {before[table]} -> {after[table]}"


def json_tables(payload: bytes) -> set:
    import json

    z = zipfile.ZipFile(io.BytesIO(payload))
    data = json.loads(z.read("backup.json"))
    return set((data.get("tables") or {}).keys())
