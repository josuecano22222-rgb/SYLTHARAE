#!/usr/bin/env bash
# Rebuild the measurement environment for the scalability harness.
#
# Recreates the harness's virtual environment reproducibly, for example on a
# host where it does not survive between sessions. It deliberately skips two optional requirements that need
# system toolchains not present here (libpff-python needs Python headers and a
# source build of libpff); everything else in requirements.txt is installed.
#
#   tools/scalability/setup_env.sh [venv-path]   (default: <repo>/.venv-scalability)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV="${1:-$REPO_ROOT/.venv-scalability}"

python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --disable-pip-version-check --upgrade pip

# Requirements minus the source-build-only extras.
grep -v -e '^libpff-python' -e '^rapidocr-onnxruntime' "$REPO_ROOT/requirements.txt" \
    > /tmp/requirements_scalability.txt
"$VENV/bin/pip" install -q --disable-pip-version-check -r /tmp/requirements_scalability.txt

# Test + measurement tooling.
"$VENV/bin/pip" install -q --disable-pip-version-check pytest pytest-timeout \
    pgserver psycopg2-binary psutil pandas

"$VENV/bin/python" - <<'PY'
import importlib
required = ("flask", "psycopg2", "psutil", "pgserver", "pytest", "PIL", "fitz",
            "docx", "numpy", "pytesseract", "openpyxl", "ebooklib", "py7zr",
            "pandas")
missing = []
for name in required:
    try:
        importlib.import_module(name)
    except Exception as exc:
        missing.append(f"{name} ({type(exc).__name__})")
print("environment ready" if not missing else f"MISSING: {', '.join(missing)}")
PY
