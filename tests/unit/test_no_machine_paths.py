"""Shipped files must not carry paths or settings of the machine that made them.

The release archive is everything tracked in Git; this scans the same text
files (the working tree, minus what is never shipped). It found
``tools/scalability/setup_env.sh`` defaulting to one machine's virtualenv
(``/home/user/.venv``). Example paths shown to users as placeholders, such as
"C:/Users/Documents/file.pdf or /home/user/file.pdf", are fine: they are not
where anything is read from.
"""

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Never shipped (ignored or generated locally); the archive check covers the rest.
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "build", "dist",
             ".pytest_cache", ".ruff_cache", ".mypy_cache", "offline-bundle"}
TEXT_SUFFIXES = {".py", ".sh", ".md", ".txt", ".toml", ".cfg", ".ini", ".yml", ".yaml",
                 ".json", ".conf", ".service", ".html", ".js", ".mjs", ".css", ".example",
                 ".po", ".pot", ".sql"}

FORBIDDEN = {
    "virtualenv default under a home directory": re.compile(r"/home/[^/\s\"']+/\.venv\b"),
    "sandbox preview host": re.compile(r"\be2b\.app\b"),
    "link-local sandbox address": re.compile(r"\b169\.254\.\d{1,3}\.\d{1,3}\b"),
    "pgserver temporary socket": re.compile(r"/tmp/pgdata_"),
}


def _is_text(path):
    return path.suffix in TEXT_SUFFIXES or path.name in {"Dockerfile", ".gitignore", ".env.example"}


def _shipped_text_files():
    """Tracked files when Git metadata is present (the archive is built from them).

    Without it (an unpacked release archive) walk the tree, skipping what is
    never shipped, including ``data/``, where the running application and the
    tests write settings backups and other local state.
    """
    import subprocess

    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True,
                             check=True, timeout=60).stdout.decode("utf-8")
        tracked = [ROOT / name for name in out.split("\0") if name]
    except (OSError, subprocess.SubprocessError):
        tracked = None
    if tracked:
        for path in tracked:
            if _is_text(path) and path.is_file():
                yield path
        return
    for dirpath, dirnames, filenames in os.walk(ROOT):
        rel = Path(dirpath).relative_to(ROOT)
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS
                       and not (rel == Path(".") and d == "data")
                       and not (rel == Path("static") and d == "dist")]
        for name in filenames:
            path = Path(dirpath) / name
            if _is_text(path):
                yield path


def _read(path):
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""


def test_no_file_contains_the_checkout_path():
    # Whatever machine runs this, its own absolute path must not be baked in.
    me = str(ROOT)
    this_file = Path(__file__).resolve()
    hits = [str(p.relative_to(ROOT)) for p in _shipped_text_files()
            if p.resolve() != this_file and me in _read(p)]
    assert not hits, f"files contain this checkout's absolute path {me}: {hits}"


def test_no_machine_specific_paths_or_hosts():
    this_file = Path(__file__).resolve()
    hits = []
    for path in _shipped_text_files():
        if path.resolve() == this_file:
            continue
        text = _read(path)
        for what, pattern in FORBIDDEN.items():
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                hits.append(f"{path.relative_to(ROOT)}:{line}: {what}: {match.group(0)}")
    assert not hits, "\n".join(hits)


def test_the_scan_covers_the_scripts_it_was_written_for():
    scanned = {p.relative_to(ROOT).as_posix() for p in _shipped_text_files()}
    assert "tools/scalability/setup_env.sh" in scanned
    assert "deploy/nginx/syltharae.conf" in scanned
    assert "tools/smoke/live_smoke.py" in scanned
