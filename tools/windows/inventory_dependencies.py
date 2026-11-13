"""Generate the Windows offline dependency inventory.

Reads the dependency manifests and emits ``docs/windows/DEPENDENCY_INVENTORY.md``:
every runtime dependency classified per the offline-deployment directive
(Python/runtime, native Windows binary, database, OCR engine, OCR language
data, document-processing, browser, local model/data asset, development-only,
external/network), with the offline-bundle stage that provisions each one.

The output is deterministic (sorted, no timestamps); a test regenerates it
and fails when the committed copy is stale:

    python tools/windows/inventory_dependencies.py
    # or, from pytest:
    python -m pytest tests/unit/test_windows_offline_kit.py
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "docs" / "windows" / "DEPENDENCY_INVENTORY.md"

# Provisioning stage in tools/windows/build_offline_bundle.py for each item.
WHEELS = "wheels"
TESSERACT = "tesseract"
TESSDATA = "tessdata"
RUNTIME = "runtime"
INCLUDED = "bundled"

#: Native Windows components the bundle fetches on the BUILD machine. Each
#: entry pins the exact artifact the bundle manifest records.
NATIVE_BINARIES = [
    ("Tesseract OCR 5.x (UB-Mannheim portable build)", "x64 portable zip",
     "primary OCR engine; no installer, no registry, no network",
     "required", TESSERACT),
    ("FFmpeg (gyan.dev release build)", "x64 shared/freeless zip",
     "video decoding for the OpenCV media path; without it media files "
     "are recorded as unsupported instead of failing the deployment",
     "optional (media formats)", TESSERACT),
]

#: Development-only dependencies (the ``dev`` extra plus tooling). They are
#: installed on build/test machines, never shipped in the offline wheelhouse.
DEV_ONLY = [
    "pytest", "pytest-cov", "ruff", "bandit", "pyzipper", "pip-audit",
    "puppeteer-core (browser smoke tooling; not a Python dependency)",
]

#: External/network integrations that exist in the manifest but are OFF by
#: default and never required for normal operation.
EXTERNAL_OPT_IN = [
    ("sentry-sdk", "error reporting; disabled unless a DSN is configured",
     "EXCLUDE from offline operation: leave unconfigured"),
    ("rollbar", "error reporting; disabled unless a token is configured",
     "EXCLUDE from offline operation: leave unconfigured"),
]


def _requirements() -> list[str]:
    """The requirement lines of requirements.txt (names + specifiers)."""
    lines = []
    for raw in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        lines.append(line)
    return lines


def _runtime_extras() -> list[str]:
    """The optional-dependencies needed at runtime (everything but dev/pst)."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = data["project"]["optional-dependencies"]
    runtime = []
    for name in ("pdf", "office", "ocr", "ebook", "audio", "media", "server"):
        runtime.extend(extras[name])
    return sorted(set(runtime))


def _req_name(spec: str) -> str:
    return re.split(r"[<>=!~\[]", spec.strip(), maxsplit=1)[0].strip().lower().replace("_", "-")


def _base_names() -> set[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return {_req_name(dep) for dep in data["project"]["dependencies"]}


def _classify(req: str) -> tuple[str, str]:
    """(class, provision note) for one requirement line."""
    name = _req_name(req)
    if name in ("pytest", "pytest-cov", "pytest-cov", "ruff", "bandit", "pyzipper"):
        return ("9. Development-only dependency",
                "build/test machines only; never shipped in the offline wheelhouse")
    if name in ("tesseract", "pytesseract"):
        return ("4. OCR engine", "pytesseract wheel; the Tesseract binary comes "
                "from the tesseract stage")
    if name == "rapidocr-onnxruntime":
        return ("4. OCR engine", "wheel; bundled PP-OCRv4 ONNX models load "
                "from disk, never from the network")
    if name in ("opencv-python-headless", "opencv-python"):
        return ("1. Python/runtime dependency", "wheel (headless build: no "
                "libGL/graphics stack needed)")
    if name == "psycopg2-binary":
        return ("3. Database", "client driver wheel; the server is the "
                "bundled pgserver PostgreSQL")
    if name in ("sentry-sdk", "rollbar"):
        return ("10. External/network dependency (opt-in)",
                "disabled unless configured; not required offline")
    if name == "pgserver":
        return ("3. Database", "wheel bundling a complete private PostgreSQL "
                "16 build (binaries included) for the offline cluster")
    return ("1. Python/runtime dependency", "wheel from the offline wheelhouse")


def generate() -> str:
    extras = _runtime_extras()
    base_names = _base_names()

    rows: list[tuple[str, str, str, str]] = []
    seen: set[str] = set()

    def add(req: str) -> None:
        name = _req_name(req)
        if name in seen or name == "libpff-python":
            return
        seen.add(name)
        cls, note = _classify(req)
        rows.append((name, req, cls, note))

    for req in _requirements():
        add(req)
    for req in extras:
        add(req)
    rows.sort(key=lambda row: (row[2], row[0]))

    out: list[str] = []
    out.append("# Windows offline dependency inventory\n")
    out.append("Generated by `tools/windows/inventory_dependencies.py` from "
               "`requirements.txt` and `pyproject.toml`; regenerate and commit "
               "whenever a dependency changes (a test fails on a stale copy).\n")
    out.append("Every dependency required for normal operation on a native, "
               "offline Windows machine is provisioned locally by the offline "
               "bundle (`tools/windows/build_offline_bundle.py`). Nothing here "
               "downloads at runtime: Tesseract reads only its local tessdata; "
               "RapidOCR reads only the models bundled in its wheel; the web "
               "interface serves only vendored scripts, styles and fonts.\n")

    out.append("## Python packages and their classification\n")
    out.append("| Package | Requirement | Class | Offline provisioning |")
    out.append("|---|---|---|---|")
    for name, req, cls, note in rows:
        out.append(f"| {name} | `{req}` | {cls} | {note} |")
    out.append("")

    out.append("## Native Windows binaries\n")
    out.append("| Component | Form | Purpose | Required | Bundle stage |")
    out.append("|---|---|---|---|---|")
    for name, form, purpose, required, stage in NATIVE_BINARIES:
        out.append(f"| {name} | {form} | {purpose} | {required} | {stage} |")
    out.append("")

    out.append("## Database\n")
    out.append("| Component | Form | Provisioning |")
    out.append("|---|---|---|")
    out.append("| PostgreSQL 16 (server, initdb, client tools) | binaries "
               "inside the `pgserver` wheel | `wheels` stage; a private "
               "cluster is created under the data directory by "
               "`tools/windows/local_postgres.py` — no service, no EDB "
               "installer, no network |")
    out.append("| psycopg2-binary | client wheel | `wheels` stage |")
    out.append("")
    out.append("PostgreSQL stays: the data model, migrations and full-text "
               "behaviour are PostgreSQL's. The bundle does not replace it "
               "with an embedded database; it ships one.\n")

    out.append("## OCR engines and language data\n")
    out.append("| Item | Source | Offline behaviour |")
    out.append("|---|---|---|")
    out.append("| Tesseract (primary engine) | `tesseract` stage (portable "
               "build) | reads only `TESSDATA_PREFIX`; never downloads |")
    out.append("| eng / ara / heb language data | `offline-bundle/windows/"
               "tessdata` (tessdata_fast), hashes in `MANIFEST.sha256` | "
               "installed into the bundled tessdata directory |")
    out.append("| RapidOCR (fallback engine) | `rapidocr-onnxruntime` wheel | "
               "PP-OCRv4 models are inside the wheel; load is local; a missing "
               "language is reported, not substituted |")
    out.append("")

    out.append("## Browser and static assets\n")
    out.append("- No browser, driver or Node.js is required at runtime: the "
               "interface is server-rendered HTML with vendored JavaScript, "
               "CSS, Bootstrap Icons and the Inter / Noto Sans Arabic fonts.")
    out.append("- `tests/unit/test_no_remote_runtime_resources.py` fails if a "
               "template, stylesheet or script references a remote resource, "
               "so a CDN link cannot return silently.")
    out.append("- puppeteer-core + a Chromium build are development-only "
               "(browser smoke test) and never ship in the bundle.\n")

    out.append("## Development-only dependencies (not shipped)\n")
    for item in DEV_ONLY:
        out.append(f"- {item}")
    out.append("")

    out.append("## External/network integrations (opt-in, off by default)\n")
    out.append("| Package | Purpose | Offline stance |")
    out.append("|---|---|---|")
    for name, purpose, stance in EXTERNAL_OPT_IN:
        out.append(f"| {name} | {purpose} | {stance} |")
    out.append("")
    out.append("Normal operation performs no outbound connections; "
               "`tools/windows/offline_acceptance.py` monitors and classifies "
               "connection attempts during the offline acceptance test.\n")

    out.append("## What the bundle must contain (release gate)\n")
    out.append("1. Python 3.11 Windows installer (pinned URL + SHA-256 in the "
               "bundle manifest).")
    out.append("2. The complete win_amd64 wheelhouse for the base "
               "requirements plus the runtime extras (`pip install --no-index` "
               "must succeed with nothing else present).")
    out.append("3. The Tesseract portable build and the eng/ara/heb tessdata.")
    out.append("4. The application source archive (`git archive` of the "
               "release commit).")
    out.append("5. `manifest.json` with a SHA-256 for every file; "
               "`build_offline_bundle.py --verify` re-checks it offline.")
    return "\n".join(out) + "\n"


def main() -> int:
    content = generate()
    if "--check" in sys.argv:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != content:
            print("docs/windows/DEPENDENCY_INVENTORY.md is stale; regenerate:",
                  file=sys.stderr)
            return 1
        print("inventory up to date")
        return 0
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(content, encoding="utf-8")
    print(f"wrote {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
