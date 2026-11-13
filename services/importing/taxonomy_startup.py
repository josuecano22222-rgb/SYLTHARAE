"""Startup reconciliation: the workbook is authoritative, so prove it on boot.

The web app calls :func:`reconcile` once, next to the scheduler's guarded start.
The point is that an operator who edits ``classification/domain_data.xlsx`` and
restarts the service does not also have to remember a command: the difference is
computed, reported, and - in ``apply`` mode - written.

Four modes, from ``SYLTHARAE_TAXONOMY_SYNC``:

===========  ==================================================================
``0``/``off``  do nothing at all
``check``      compare and say what is pending (the default)
``apply``      write the difference, with the same guard rails as the command
               line: a workbook that breaks a rule is refused and nothing is
               written; a write is snapshotted so it can be undone
===========  ==================================================================

The fingerprint of the last applied run lives under ``APP_DATA_DIR``
(:mod:`services.importing.taxonomy_sync_service`), so a restart with an
unchanged workbook costs one SHA-256 of the file and no database work.

This module returns plain data rather than logging directly, so the behaviour
can be tested without a Flask app and without reading log output.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

APPLY_MODES = ("apply", "1", "yes", "true")
OFF_MODES = ("0", "off", "no", "false")


def mode_from_env(default: str = "check") -> str:
    import os

    return (os.environ.get("SYLTHARAE_TAXONOMY_SYNC") or default).strip().lower()


#: A stable advisory-lock key for "one process may apply the workbook".
APPLY_LOCK_KEY = 0x5C4E_1D02


def _claim_apply_lock(service):
    """``True`` (claimed), ``False`` (someone else has it) or ``None`` (no DB).

    Best effort by design: a service without a connection (a fake in a unit
    test, a CLI run over its own connection) keeps the old behaviour, because the
    lock is a guard against a second *process*, not a correctness requirement.
    """
    factory = getattr(service, "_connect", None)
    if factory is None:
        return None
    try:
        conn = factory()
    except Exception:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (APPLY_LOCK_KEY,))
            acquired = bool(cur.fetchone()[0])
        conn.commit()
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
        return None
    if not acquired:
        try:
            conn.close()
        except Exception:
            pass
        return False
    _claim_apply_lock.held = conn
    return True


def _release_apply_lock(service) -> None:
    conn = getattr(_claim_apply_lock, "held", None)
    if conn is None:
        return
    _claim_apply_lock.held = None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (APPLY_LOCK_KEY,))
        conn.commit()
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def reconcile(mode: Optional[str] = None,
              service: Optional[Any] = None) -> Dict[str, Any]:
    """Compare the workbook with the database, and apply it in ``apply`` mode.

    Returns ``{"action": ..., ...}`` where ``action`` is one of
    ``disabled``, ``unchanged``, ``drift``, ``applied``, ``refused``,
    ``already_in_sync`` or ``error``. Never raises: a startup step that throws
    would stop the web app from serving, which is a worse outcome than a stale
    taxonomy with a warning in the log.
    """
    mode = (mode or mode_from_env()).strip().lower()
    if mode in OFF_MODES:
        return {"action": "disabled", "mode": mode}
    try:
        from services.importing import taxonomy_sync
        from services.importing.taxonomy_sync_service import (
            TaxonomySyncRequest, TaxonomySyncService, read_state, write_state,
        )

        service = service or TaxonomySyncService()
        path = service.resolve_data_file(None)
        fingerprint = taxonomy_sync.fingerprint(path)
        if read_state().get("fingerprint") == fingerprint:
            return {"action": "unchanged", "path": path, "fingerprint": fingerprint}

        if mode not in APPLY_MODES:
            result = service.drift()
            if not result.success:
                return {"action": "refused", "path": path, "fingerprint": fingerprint,
                        "errors": list(result.errors)}
            pending = result.stats.get("planned_changes")
            if pending:
                return {"action": "drift", "path": path, "fingerprint": fingerprint,
                        "pending": pending, "plan": result.stats.get("plan") or {}}
            # Nothing to do: record the fingerprint so the next start is a hash.
            write_state(fingerprint=fingerprint, data_file=Path(path).name,
                        written={}, applied_by="startup-check")
            return {"action": "already_in_sync", "path": path,
                    "fingerprint": fingerprint}

        # Two instances booting together must not both apply: one writes, the
        # other reports that it declined. A session-level advisory lock on the
        # apply path only - a check costs nothing and must never be blocked.
        lock = _claim_apply_lock(service)
        if lock is False:
            return {"action": "already_syncing", "path": path,
                    "fingerprint": fingerprint,
                    "reason": "another process is applying the workbook"}
        try:
            result = service.run(
                TaxonomySyncRequest(apply=True, created_by="startup"))
        finally:
            _release_apply_lock(service)
        if not result.success:
            return {"action": "refused", "path": path, "fingerprint": fingerprint,
                    "errors": list(result.errors)}
        if not result.applied:
            write_state(fingerprint=fingerprint, data_file=Path(path).name,
                        written={}, applied_by="startup")
            return {"action": "already_in_sync", "path": path,
                    "fingerprint": fingerprint}
        # The service records its own fingerprint; recording it here as well
        # makes the fast path the responsibility of the component that reads
        # it, and keeps a fake or alternate service honest.
        write_state(fingerprint=fingerprint, data_file=Path(path).name,
                    written=result.stats.get("written") or {}, applied_by="startup")
        return {"action": "applied", "path": path, "fingerprint": fingerprint,
                "written": result.stats.get("written") or {},
                "has_snapshot": result.snapshot is not None}
    except Exception as exc:                        # pragma: no cover - defensive
        return {"action": "error", "error": f"{exc.__class__.__name__}: {exc}"}
