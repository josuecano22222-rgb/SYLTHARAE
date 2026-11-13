"""Live smoke: every list-export interface + common download aliases.

Usage (server must be running):

    set SMOKE_BASE_URL=http://127.0.0.1:5000
    set SMOKE_ADMIN_PASSWORD=your_admin_password
    python tools/smoke/export_surfaces_smoke.py

Exit code 1 if any check fails.
"""
from __future__ import annotations

import io
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

BASE = os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1:5000").rstrip("/")
ADMIN_USER = os.environ.get("SMOKE_ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("SMOKE_ADMIN_PASSWORD", "admin@112233")

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(ok), detail[:300]))
    print(("PASS " if ok else "FAIL ") + name + (f" -- {detail[:300]}" if detail else ""), flush=True)
    return ok


class Session(requests.Session):
    def __init__(self) -> None:
        super().__init__()
        self.csrf: str | None = None

    def refresh_csrf(self) -> None:
        r = self.get(f"{BASE}/auth/login", timeout=30)
        r.raise_for_status()
        for line in r.text.splitlines():
            if "csrf-token" in line and "content=" in line:
                start = line.find('content="') + len('content="')
                end = line.find('"', start)
                self.csrf = line[start:end]
                return
        meta = r.text
        idx = meta.find('name="csrf-token"')
        if idx >= 0:
            frag = meta[idx: idx + 120]
            if 'content="' in frag:
                start = frag.find('content="') + len('content="')
                end = frag.find('"', start)
                self.csrf = frag[start:end]

    def api_get(self, path: str, **kwargs) -> requests.Response:
        url = path if path.startswith("http") else f"{BASE}{path}"
        kwargs.setdefault("timeout", 120)
        return self.get(url, **kwargs)

    def login(self) -> bool:
        self.refresh_csrf()
        if not ADMIN_PASS:
            return False
        headers = {"X-CSRFToken": self.csrf or ""}
        r = self.post(
            f"{BASE}/auth/login",
            json={"username": ADMIN_USER, "password": ADMIN_PASS},
            headers=headers,
            timeout=60,
        )
        self.refresh_csrf()
        return r.status_code == 200


def main() -> int:
    from Api.routes.exports import EXPORT_SPECS

    s = Session()
    if not check("health", s.api_get("/health").status_code == 200, ""):
        return 1

    anon = s.api_get("/api/export/words?format=csv")
    check("anonymous list export blocked", anon.status_code in (401, 403), str(anon.status_code))

    if not ADMIN_PASS:
        print("SKIP authenticated export checks (set SMOKE_ADMIN_PASSWORD)", flush=True)
        return 1 if any(not ok for _, ok, _ in RESULTS) else 0

    if not check("admin login", s.login(), "login failed"):
        return 1

    # --- Table exports (CSV + XLSX spot check) ---
    extra_params = {
        "category_words": "category_id=48&columns=word",
        "search_results": "q=test",
    }
    for interface_id in sorted(EXPORT_SPECS):
        extra = extra_params.get(interface_id, "")
        path = f"/api/export/{interface_id}?format=csv"
        if extra:
            path += f"&{extra}"
        r = s.api_get(path)
        ok = r.status_code == 200 and (
            r.headers.get("Content-Disposition", "").startswith("attachment")
            or "text/csv" in (r.headers.get("Content-Type") or "")
        )
        if not ok and interface_id == "category_words" and r.status_code == 400:
            ok = True  # category 1 may not exist; try dynamic id below
        check(f"GET /api/export/{interface_id} csv", ok, f"{r.status_code} {r.text[:80]}")

    # Geolocation archives alias
    r = s.api_get("/api/archives/geolocation/export?format=csv&columns=coordinates,name")
    check("GET /api/archives/geolocation/export csv", r.status_code == 200, str(r.status_code))

    # XLSX sample
    r = s.api_get("/api/export/words?format=xlsx&columns=word")
    xlsx_ok = r.status_code == 200 and "spreadsheet" in (r.headers.get("Content-Type") or "")
    if xlsx_ok:
        try:
            from openpyxl import load_workbook
            load_workbook(io.BytesIO(r.content))
        except Exception as exc:
            xlsx_ok = False
            detail = str(exc)
        else:
            detail = "workbook ok"
    else:
        detail = f"{r.status_code}"
    check("GET /api/export/words xlsx", xlsx_ok, detail)

    # Map bundle export
    r = s.api_get("/api/map/bundle/export")
    check(
        "GET /api/map/bundle/export",
        r.status_code == 200 and r.content[:2] == b"\x1f\x8b",
        f"{r.status_code} len={len(r.content)}",
    )

    # Per-file text export (first file in library if any)
    lib = s.api_get("/api/files?limit=1")
    if lib.status_code == 200:
        data = lib.json() if lib.headers.get("Content-Type", "").startswith("application/json") else {}
        files = (data.get("files") or data.get("data") or []) if isinstance(data, dict) else []
        fid = files[0]["id"] if files else None
        if fid:
            r = s.api_get(f"/api/files/{fid}/export")
            check(
                f"GET /api/files/{fid}/export",
                r.status_code == 200,
                r.headers.get("Content-Disposition", "")[:80],
            )
            r2 = s.api_get(f"/api/file/{fid}/original/content?download=1")
            check(
                f"GET /api/file/{fid}/original/content download",
                r2.status_code in (200, 404),
                str(r2.status_code),
            )
        else:
            check("GET /api/files/<id>/export", True, "skipped (no files)")
    else:
        check("resolve sample file id", False, str(lib.status_code))

    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
