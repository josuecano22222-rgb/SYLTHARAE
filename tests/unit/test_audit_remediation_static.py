"""Static and unit-level regression tests for the v2.1.1 audit findings.

Finding IDs refer to ``docs/AUDIT_REPORT.md``.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
JS_PAGES = ROOT / "static" / "js" / "pages"


# --- AUDIT-XSS-01 ----------------------------------------------------------
# Database-sourced values (file names, words, paths, sources, types, statuses)
# are interpolated into innerHTML templates. Each must go through an escaper.
RAW_SINKS = {
    "dashboard-page.js": [
        r"\$\{\s*word\.word\s*\}", r"\$\{\s*file\.(name|type|source|path|status)\b[^}]*\}",
        r"\$\{\s*path\.name\s*\}",
    ],
    "path-analysis-page.js": [
        r"\$\{\s*node\.name\s*\}", r"\$\{\s*wordText\s*\}",
        r"\$\{\s*file\.(name|type|status)\b[^}]*\}",
    ],
}


@pytest.mark.parametrize("page", sorted(RAW_SINKS))
def test_page_does_not_interpolate_raw_database_values(page):
    text = (JS_PAGES / page).read_text(encoding="utf-8")
    offenders = []
    for pattern in RAW_SINKS[page]:
        for match in re.finditer(pattern, text):
            line = text.count("\n", 0, match.start()) + 1
            source_line = text.splitlines()[line - 1]
            if "console." in source_line or "===" in match.group(0):
                continue  # log output / a comparison, not markup
            offenders.append(f"{page}:{line}: {match.group(0)}")
    assert not offenders, "unescaped interpolation:\n" + "\n".join(offenders)


@pytest.mark.parametrize("page", sorted(RAW_SINKS))
def test_page_imports_the_shared_escapers(page):
    text = (JS_PAGES / page).read_text(encoding="utf-8")
    assert re.search(r"import\s*\{[^}]*escapeAttribute[^}]*\}\s*from\s*['\"]\.\./modules/core/utils\.js", text)


# --- AUDIT-CSRF-01 ---------------------------------------------------------
CSRF_CALLS = {
    "analysis-batch-page.js": ["/analysis/batch/process", "/retry"],
    "keywords-list-page.js": ["merge-all-duplicates"],
    "search-advanced-page.js": ["/api/search/history"],
}


@pytest.mark.parametrize("page,needle", [(p, n) for p, ns in CSRF_CALLS.items() for n in ns])
def test_state_changing_fetch_sends_the_csrf_token(page, needle):
    text = (JS_PAGES / page).read_text(encoding="utf-8")
    found = False
    for match in re.finditer(r"fetch\(", text):
        call = text[match.start():match.start() + 900]
        head = call.split(")", 1)[0] if "`" not in call[:120] else call[:300]
        if needle not in head and needle not in call[:300]:
            continue
        if re.search(r"method:\s*['\"](POST|PUT|PATCH|DELETE)", call):
            found = True
            assert "X-CSRFToken" in call, f"{page}: fetch to {needle} has no X-CSRFToken"
    assert found, f"{page}: no state-changing fetch matching {needle!r}"


# --- AUDIT-PY-02: duplicate dict keys silently discarded entries ----------------
def _duplicate_keys(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    dups = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            seen = set()
            for key in node.keys:
                if isinstance(key, ast.Constant):
                    if key.value in seen:
                        dups.append((key.value, key.lineno))
                    seen.add(key.value)
    return dups


def test_search_algorithms_has_no_duplicate_literal_keys():
    assert _duplicate_keys(ROOT / "Api" / "services" / "search_algorithms.py") == []


# --- AUDIT-DATE-01: date defaults evaluated once at import ---------------------
@pytest.mark.parametrize("module,qualname", [
    ("database.database.repository.paths_repo", "PathsRepository"),
    ("database.database.repository.sides_repo", "SidesRepository"),
    ("database.database.repository.sources_repo", "SourcesRepository"),
])
def test_no_date_default_is_frozen_at_import(module, qualname):
    import datetime as dt
    import importlib

    cls = getattr(importlib.import_module(module), qualname)
    for name, fn in inspect.getmembers(cls, inspect.isfunction):
        for param in inspect.signature(fn).parameters.values():
            assert not isinstance(param.default, (dt.date, dt.datetime)), (
                f"{qualname}.{name}({param.name}=...) is frozen at import time")


# --- AUDIT-ARCH-01: 7z members were only size-checked after extraction ---------
def test_7z_declared_size_is_rejected_before_anything_is_written(tmp_path):
    py7zr = pytest.importorskip("py7zr")
    from core.archive_safety import ArchiveSafetyError, ExtractionPolicy, extract_7z

    archive = tmp_path / "big.7z"
    with py7zr.SevenZipFile(archive, "w") as zf:
        zf.writestr(b"\0" * 200_000, "zeros.bin")
    out = tmp_path / "out"
    policy = ExtractionPolicy(max_file_size=10_000, max_compression_ratio=10**9)
    with pytest.raises(ArchiveSafetyError):
        extract_7z(archive, out, policy)
    written = [p for p in out.rglob("*") if p.is_file()] if out.exists() else []
    assert written == []


def test_7z_total_declared_size_is_rejected(tmp_path):
    py7zr = pytest.importorskip("py7zr")
    from core.archive_safety import ArchiveSafetyError, ExtractionPolicy, extract_7z

    archive = tmp_path / "many.7z"
    with py7zr.SevenZipFile(archive, "w") as zf:
        for i in range(3):
            zf.writestr(b"a" * 6_000, f"f{i}.txt")
    policy = ExtractionPolicy(max_file_size=10_000, max_bytes=15_000, max_compression_ratio=10**9)
    with pytest.raises(ArchiveSafetyError):
        extract_7z(archive, tmp_path / "out", policy)


def test_7z_within_limits_still_extracts(tmp_path):
    py7zr = pytest.importorskip("py7zr")
    from core.archive_safety import extract_7z

    archive = tmp_path / "ok.7z"
    with py7zr.SevenZipFile(archive, "w") as zf:
        zf.writestr(b"hello", "hello.txt")
    extract_7z(archive, tmp_path / "out")
    assert (tmp_path / "out" / "hello.txt").read_bytes() == b"hello"


# --- AUDIT-DEP-01: maintained PDF / video libraries ------------------------------
def test_pdf_preview_uses_pypdf_when_available():
    pytest.importorskip("pypdf")
    from Api.services import file_preview

    assert file_preview.PyPDF2.__name__ == "pypdf"


def test_requirements_pin_patched_dependencies():
    for req in ("requirements.txt", "offline-bundle/requirements.txt"):
        text = (ROOT / req).read_text(encoding="utf-8")
        active = [l.strip() for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]
        assert not any(l.lower().startswith("pypdf2") for l in active), req
        assert any(l.lower().startswith("pypdf") for l in active), req
        assert any(l.startswith("Pillow>=12.3") for l in active), req


# --- AUDIT-UI-01: registry icon names follow the registry's own format -----------
def test_registry_icons_are_bare_bootstrap_names():
    from core.interfaces.registry import REGISTRY

    bad = [(i.id, i.icon) for i in REGISTRY if " " in (i.icon or "")]
    assert bad == []


# --- AUDIT-PROXY-01: reverse-proxy headers are trusted only on request -----------
@pytest.mark.parametrize("count,expected", [("", False), ("0", False), ("junk", False), ("1", True)])
def test_proxy_headers_are_trusted_only_when_configured(tmp_path, count, expected):
    import os
    import subprocess
    import sys

    env = dict(os.environ, APP_DATA_DIR=str(tmp_path), TRUSTED_PROXY_COUNT=count)
    code = ("from apps.web.app import app; from werkzeug.middleware.proxy_fix import ProxyFix; "
            "print('PROXYFIX', isinstance(app.wsgi_app, ProxyFix))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=180)
    assert f"PROXYFIX {expected}" in out.stdout, out.stderr[-2000:]
