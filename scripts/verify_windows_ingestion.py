#!/usr/bin/env python3
"""Windows acceptance check for the three ways files enter the engine.

The engine's production platform is Windows, and the three inputs are the
operator's whole experience of ingestion:

1. **one file** chosen from this computer,
2. **a whole folder** chosen (or dragged) from this computer,
3. **a path on the server** typed into the field.

This script drives the real Flask application in-process - the same code the
server runs, the same endpoints the browser calls - and checks each of the
three on the machine it is run on, including the parts only the real filesystem
can answer: whether the names a Windows client sends can actually be *written*
on that host, whether a folder selection keeps its tree, whether a typed path
is accepted, and whether the path the configuration allows is the path the
engine accepts.

It writes nothing to the catalog and ingests nothing:

* the server-path checks are dry runs (they count, they do not read),
* the staged files it creates are deleted again before the script exits.

Reading what it prints
----------------------

``PASS``  checked and correct on this machine.
``FAIL``  wrong on this machine - the detail line says what happened.
``SKIP``  cannot be checked here; the detail says what is missing and how to
          provide it (for example ``--server-path``).
``INFO``  a fact about this machine, printed so the result can be interpreted.

Run it from the repository root with the application's own Python::

    python scripts\\verify_windows_ingestion.py
    python scripts\\verify_windows_ingestion.py --username admin --password ***
    python scripts\\verify_windows_ingestion.py --server-path "C:\\Case 2026-014\\Evidence"
    python scripts\\verify_windows_ingestion.py --json

Exit code is 0 when nothing failed, 1 when any check failed.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import re
import shutil
import sys
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

PASS, FAIL, SKIP, INFO = "PASS", "FAIL", "SKIP", "INFO"
RESULTS: list[dict] = []


# --------------------------------------------------------------------------- #
# Recording
# --------------------------------------------------------------------------- #

def record(section: str, check: str, status: str, detail: str = "") -> None:
    RESULTS.append({"section": section, "check": check, "status": status,
                    "detail": detail})


def ok(section: str, check: str, detail: str = "") -> None:
    record(section, check, PASS, detail)


def bad(section: str, check: str, detail: str = "") -> None:
    record(section, check, FAIL, detail)


def skip(section: str, check: str, detail: str = "") -> None:
    record(section, check, SKIP, detail)


def info(section: str, check: str, detail: str = "") -> None:
    record(section, check, INFO, detail)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

CSRF_META = re.compile(r'name="csrf-token" content="([^"]+)"')
STAGED_DIRS: list[Path] = []


def csrf_token(client) -> str:
    """The token the browser reads from the page's meta tag and sends back."""
    page = client.get("/operations/input")
    match = CSRF_META.search(page.get_data(as_text=True))
    if not match:
        page = client.get("/auth/login")
        match = CSRF_META.search(page.get_data(as_text=True))
    return match.group(1) if match else ""


def login(client, username: str, password: str):
    """Log in exactly as the login form does (JSON body + CSRF header)."""
    token = csrf_token(client)
    return client.post(
        "/auth/login",
        json={"username": username, "password": password},
        headers={"X-CSRFToken": token} if token else {},
    )


def post_files(client, files, relative_paths=None):
    """POST a browser-shaped selection to the staging endpoint.

    ``files`` is a list of ``(name, bytes)`` as the File objects arrive, and
    ``relative_paths`` is what the browser reports for a folder selection
    (``webkitRelativePath``, forward slashes) - or the Windows-shaped spelling
    a client may send instead.
    """
    payload = {
        "files": [(io.BytesIO(content), name) for name, content in files],
    }
    if relative_paths is not None:
        payload["relative_paths"] = json.dumps(relative_paths)
    return client.post(
        "/api/input/uploads",
        data=payload,
        content_type="multipart/form-data",
        headers={"X-CSRFToken": csrf_token(client)},
    )


def track_staged(staged_entries) -> None:
    """Remember the batch directory of every staged path, for cleanup.

    Staged files live in ``<data root>/uploads/<batch>/...``; that batch
    directory is what this script created and what it removes again.
    """
    for entry in staged_entries or []:
        raw = entry["path"] if isinstance(entry, dict) else entry
        parts = Path(raw).parts
        if "uploads" not in parts:
            continue
        batch = Path(*parts[:parts.index("uploads") + 2])
        if batch not in STAGED_DIRS:
            STAGED_DIRS.append(batch)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #

def check_host(client, server_path: str | None) -> None:
    section = "this machine"
    info(section, "platform",
         f"os.name={os.name} sys.platform={sys.platform} "
         f"python={platform.python_version()} release={platform.release()}")
    info(section, "working directory", os.getcwd())

    from core.app_paths import get_data_root
    staging_root = Path(get_data_root()) / "uploads"
    info(section, "staged uploads land in", str(staging_root))
    info(section, "staged uploads directory exists",
         "yes" if staging_root.is_dir() else "no (created on first upload)")

    from core.path_safety import configured_ingestion_roots
    raw_roots = os.environ.get("INGESTION_ROOTS", "")
    roots = configured_ingestion_roots()
    info(section, "INGESTION_ROOTS as configured", raw_roots or "(not set)")
    info(section, "INGESTION_ROOTS as parsed",
         "; ".join(str(r) for r in roots) if roots else "(none - server paths "
         "are disabled by design; uploads still work)")

    if os.name == "nt":
        try:
            import winreg  # noqa: PLC0415 - Windows-only import

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\FileSystem",
            ) as key:
                long_paths, _ = winreg.QueryValueEx(key, "LongPathsEnabled")
            info(section, "Windows long paths enabled",
                 "yes" if long_paths else "no (MAX_PATH 260 applies)")
        except Exception as exc:  # noqa: BLE001 - informational only
            info(section, "Windows long paths enabled", f"unknown ({exc})")

    resp = client.get("/api/input/options-info")
    if resp.status_code != 200:
        bad(section, "capabilities endpoint",
            f"GET /api/input/options-info -> {resp.status_code}")
        return
    body = resp.get_json() or {}
    ok(section, "capabilities endpoint",
       f"upload_available={body.get('upload_available')} "
       f"server_path_import_available={body.get('server_path_import_available')} "
       f"max_staged_path_chars={body.get('max_staged_path_chars')} "
       f"chunk_size_mb={body.get('chunk_size_mb')}")

    if server_path:
        inside = any(
            str(Path(server_path).resolve()).startswith(str(Path(r).resolve()))
            for r in roots
        )
        info(section, "server path supplied for testing",
             f"{server_path} (inside a configured root: {'yes' if inside else 'no'})")


def check_single_file(client) -> None:
    section = "1. one file"
    content = f"single file check {uuid.uuid4().hex}\n".encode()
    name = f"verify-single-{uuid.uuid4().hex[:8]}.txt"
    resp = post_files(client, [(name, content)])
    if resp.status_code != 201:
        bad(section, "staged", f"HTTP {resp.status_code}: "
            f"{resp.get_data(as_text=True)[:300]}")
        return
    body = resp.get_json()
    staged = body.get("staged") or []
    track_staged(staged)
    if len(staged) != 1:
        bad(section, "staged", f"expected 1 staged file, got {len(staged)}: {staged}")
        return
    entry = staged[0]
    on_disk = Path(entry["path"])
    problems = []
    if entry["name"] != name:
        problems.append(f"stored name {entry['name']!r} != sent name {name!r}")
    if not on_disk.is_file():
        problems.append(f"not on disk: {on_disk}")
    elif sha256(on_disk.read_bytes()) != sha256(content):
        problems.append("content on disk differs from what was sent")
    if entry.get("bytes") != len(content):
        problems.append(f"reported {entry.get('bytes')} bytes, sent {len(content)}")
    if problems:
        bad(section, "a file chosen on its own", "; ".join(problems))
    else:
        ok(section, "a file chosen on its own",
           f"{name} -> {on_disk.name}, {len(content)} bytes, content verified")


def check_folder(client) -> None:
    section = "2. a whole folder"
    uid = uuid.uuid4().hex[:8]
    # Exactly the shapes a Windows client sends for one folder selection:
    # what `webkitRelativePath` reports (forward slashes), the backslash
    # spelling, and a drive-qualified path.
    files = [
        (f"report-{uid}.txt", b"top level"),
        (f"page-01-{uid}.txt", b"nested scan"),
        (f"notes-{uid}.txt", b"deep notes"),
    ]
    relative = [
        f"Evidence\\2026\\Case {uid}\\report-{uid}.txt",
        f"Evidence/2026/Case {uid}/scans/page-01-{uid}.txt",
        f"C:\\Users\\analyst\\Desktop\\Case {uid}\\sub\\deep\\notes-{uid}.txt",
    ]
    resp = post_files(client, files, relative)
    if resp.status_code != 201:
        bad(section, "folder selection staged",
            f"HTTP {resp.status_code}: {resp.get_data(as_text=True)[:300]}")
        return
    staged = resp.get_json().get("staged") or []
    track_staged(staged)
    names = sorted(entry["name"] for entry in staged)
    # A drive-qualified name is reduced to what lies below the drive (the
    # browser never sends one - `webkitRelativePath` is relative to the chosen
    # folder - but an API client can, and nothing may escape the batch).
    expected = sorted([
        f"Evidence/2026/Case {uid}/report-{uid}.txt",
        f"Evidence/2026/Case {uid}/scans/page-01-{uid}.txt",
        f"Users/analyst/Desktop/Case {uid}/sub/deep/notes-{uid}.txt",
    ])
    problems = []
    if names != expected:
        problems.append(f"tree not preserved: {names} != {expected}")
    for entry in staged:
        path = Path(entry["path"])
        if not path.is_file():
            problems.append(f"missing on disk: {path}")
        elif path.parent.name and not path.parent.is_dir():
            problems.append(f"parent directory missing: {path.parent}")
    contents = {Path(e["path"]).read_bytes() for e in staged
                if Path(e["path"]).is_file()}
    if contents != {b"top level", b"nested scan", b"deep notes"}:
        problems.append("file contents do not match what was sent")
    if problems:
        bad(section, "folder selection keeps its tree", "; ".join(problems))
    else:
        ok(section, "folder selection keeps its tree",
           f"{len(staged)} files staged with their folders: "
           f"{', '.join(names)}")

    # A selection cannot name its way out of the batch directory.
    escape = post_files(client, [(f"escape-{uid}.txt", b"escape")],
                        [f"..\\..\\..\\escape-{uid}.txt"])
    if escape.status_code != 201:
        bad(section, "a traversal in the name is contained",
            f"HTTP {escape.status_code}")
        return
    escaped = escape.get_json().get("staged") or []
    track_staged(escaped)
    from core.app_paths import get_data_root
    staging_root = (Path(get_data_root()) / "uploads").resolve()
    escaped_path = Path(escaped[0]["path"]).resolve() if escaped else None
    if escaped_path and staging_root in escaped_path.parents \
            and ".." not in escaped_path.parts:
        ok(section, "a traversal in the name is contained",
           f"..\\\\..\\\\..\\\\escape.txt staged as {escaped[0]['name']} inside the batch")
    else:
        bad(section, "a traversal in the name is contained",
            f"staged to {escaped_path}")


def check_awkward_names(client) -> None:
    section = "3. names Windows refuses"
    # A Windows host must be able to *write* its own names; a client on any OS
    # can send names Windows rejects (reserved devices, trailing dots, colons),
    # and losing those files silently is not acceptable.
    cases = [
        ("CON", "_CON"),
        ("CON.txt", "_CON.txt"),
        ("nul.txt", "_nul.txt"),
        ("aux", "_aux"),
        ("com1.txt", "_com1.txt"),
        ("lpt9.log", "_lpt9.log"),
        ("a:b.txt", "a_b.txt"),
        ("trail.", "trail"),
        ("trail ", "trail"),
        ('quote"name.txt', "quote_name.txt"),
        ("pipe|name.txt", "pipe_name.txt"),
        ("star*name.txt", "star_name.txt"),
    ]
    stored, problems = [], []
    for sent, expected in cases:
        # The page sends the name in `relative_paths` (a JSON array), so this
        # checks the name rules rather than multipart header escaping: a raw
        # quote inside a Content-Disposition filename is read as its end by any
        # conforming parser, and the browser escapes it before it is sent.
        resp = post_files(client, [(sent, sent.encode())], [sent])
        if resp.status_code != 201:
            problems.append(f"{sent!r}: HTTP {resp.status_code}")
            continue
        staged = resp.get_json().get("staged") or []
        track_staged(staged)
        if not staged:
            problems.append(f"{sent!r}: nothing staged")
            continue
        entry = staged[0]
        stored.append(f"{sent!r}->{entry['name']!r}")
        path = Path(entry["path"])
        if not path.is_file():
            problems.append(f"{sent!r}: stored name {entry['name']!r} is not "
                            f"writable on this filesystem")
        elif path.read_bytes() != sent.encode():
            problems.append(f"{sent!r}: content differs on disk")
        if entry["name"] != expected:
            problems.append(f"{sent!r}: stored as {entry['name']!r}, "
                            f"expected {expected!r}")
    # A reserved name must also be defused at depth, not only at the top level.
    deep = post_files(client, [("deep-nul.txt", b"deep")],
                      ["Case/NUL/deep-nul.txt"])
    if deep.status_code == 201:
        entries = deep.get_json().get("staged") or []
        track_staged(entries)
        if entries and entries[0]["name"] == "Case/_NUL/deep-nul.txt":
            stored.append("Case/NUL/... -> Case/_NUL/...")
        elif entries:
            problems.append(f"reserved name at depth stored as {entries[0]['name']!r}")
    else:
        problems.append(f"reserved name at depth: HTTP {deep.status_code}")

    if problems:
        bad(section, "awkward names are renamed, never lost", "; ".join(problems))
    else:
        ok(section, "awkward names are renamed, never lost",
           f"{len(stored)} names staged and readable: {', '.join(stored)}")


def check_path_length(client) -> None:
    section = "4. path length"
    body = (client.get("/api/input/options-info").get_json() or {})
    limit = int(body.get("max_staged_path_chars") or 0)
    if not limit:
        skip(section, "staged path length limit", "not reported by options-info")
        return

    # Just under the limit: several long-but-legal components.
    component = "s" * 40
    depth = max(1, (limit - 60) // (len(component) + 1))
    under = "/".join([component] * depth + ["ok.txt"])
    resp = post_files(client, [("ok.txt", b"under the limit")], [under])
    if resp.status_code == 201:
        staged = resp.get_json().get("staged") or []
        track_staged(staged)
        ok(section, f"a deep path under the limit ({len(under)} chars) stages",
           f"{depth} components, limit {limit}")
    else:
        bad(section, "a deep path under the limit stages",
            f"{len(under)} chars, limit {limit}: HTTP {resp.status_code} "
            f"{resp.get_data(as_text=True)[:200]}")

    # Over the limit: refused *before* anything is written, with the numbers in
    # the message rather than an OSError from the filesystem.
    over = "/".join(["x" * 60] * ((limit // 60) + 3) + ["too-long.txt"])
    resp = post_files(client, [("too-long.txt", b"over the limit")], [over])
    text = resp.get_data(as_text=True)
    if resp.status_code == 400 and "PATH_TOO_LONG" in text:
        ok(section, "an over-long path is refused before anything is written",
           f"{len(over)} chars declined against the {limit} char limit")
    else:
        bad(section, "an over-long path is refused before anything is written",
            f"{len(over)} chars, limit {limit}: HTTP {resp.status_code} {text[:200]}")


def check_server_path(client, server_path: str | None) -> None:
    section = "5. a path on the server"
    from core.path_safety import configured_ingestion_roots
    roots = configured_ingestion_roots()

    sources = (client.get("/api/input/sources").get_json() or {}).get("sources") or []
    sides = (client.get("/api/input/sides").get_json() or {}).get("sides") or []
    source = (sources[0].get("name") if sources else "verify-source")
    side = (sides[0].get("name") if sides else "verify-side")

    def dry_run(path: str):
        return client.post(
            "/api/input/jobs",
            json={"path": path, "source": source, "side": side,
                  "recursive": True, "dry_run": True},
            headers={"X-CSRFToken": csrf_token(client)},
        )

    if not roots:
        resp = dry_run("C:\\Windows\\System32" if os.name == "nt" else "/etc")
        if resp.status_code == 400:
            ok(section, "server paths are refused while no root is configured",
               "fail-closed by design; set INGESTION_ROOTS to enable this mode")
        else:
            bad(section, "server paths are refused while no root is configured",
                f"HTTP {resp.status_code}: {resp.get_data(as_text=True)[:200]}")
        skip(section, "a real path is accepted",
             "no INGESTION_ROOTS configured - set it (semicolon-separated, e.g. "
             'set INGESTION_ROOTS=C:\\data\\evidence;D:\\inbox) and restart')
        return

    info(section, "configured roots",
         "; ".join(str(r) for r in roots))

    if not server_path:
        # Every configured root is offered as a candidate, but nothing is read:
        # the operator points the check at the folder they care about.
        skip(section, "a real path is accepted",
             "pass --server-path with a folder inside a root, "
             f"e.g. --server-path \"{roots[0]}\"")
    else:
        resp = dry_run(server_path)
        if resp.status_code == 200:
            preview = (resp.get_json() or {}).get("preview") or {}
            ok(section, "a typed path is accepted and counted",
               f"{server_path}: {preview.get('files_discovered')} file(s), "
               f"{preview.get('files_eligible')} eligible, "
               f"{preview.get('estimated_bytes')} bytes (dry run, nothing read)")
        else:
            bad(section, "a typed path is accepted and counted",
                f"{server_path}: HTTP {resp.status_code} "
                f"{resp.get_data(as_text=True)[:200]}")

        # The same folder spelled the other two ways a Windows operator may
        # type it: forward slashes, and a different case. On Windows both must
        # resolve - a path typed with slashes or with the wrong case is the
        # same folder there, and refusing it would hide a working mode. This
        # host's own rules decide, so on POSIX the case variants are expected
        # to be refused: there they are a *different* directory.
        outcomes = []
        for label, variant in (("forward slashes", server_path.replace("\\", "/")),
                               ("upper case", server_path.upper()),
                               ("lower case", server_path.lower())):
            if variant == server_path:
                continue
            outcomes.append((label, dry_run(variant).status_code))
        if os.name == "nt":
            wrong = [label for label, code in outcomes if code != 200]
            if not wrong:
                ok(section, "every spelling of a Windows path resolves",
                   f"{', '.join(label for label, _ in outcomes)} accepted "
                   f"(case-insensitive, either separator)")
            else:
                bad(section, "every spelling of a Windows path resolves",
                    f"refused by this host: {wrong} {outcomes}")
        else:
            expected = {"upper case": 400, "lower case": 400}
            wrong = [label for label, code in outcomes
                     if code != expected.get(label, 200)]
            if not wrong:
                ok(section, "spellings are judged by this host's rules",
                   f"POSIX is case-sensitive: {outcomes}")
            else:
                bad(section, "spellings are judged by this host's rules",
                    f"{outcomes}")

    # Refusals: outside every root, and traversal that leaves one.
    outside = "C:\\Windows\\System32" if os.name == "nt" else "/etc"
    resp = dry_run(outside)
    if resp.status_code == 400:
        ok(section, "a path outside every root is refused",
           f"{outside} -> 400 (containment, not a crash)")
    else:
        bad(section, "a path outside every root is refused",
            f"{outside}: HTTP {resp.status_code}")

    traversal = f"{roots[0]}{os.sep}..{os.sep}..{os.sep}etc"
    resp = dry_run(traversal)
    if resp.status_code == 400:
        ok(section, "traversal out of a root is refused", f"{traversal} -> 400")
    else:
        bad(section, "traversal out of a root is refused",
            f"{traversal}: HTTP {resp.status_code}")


def check_chunked(client) -> None:
    section = "6. a large file (chunked staging)"
    uid = uuid.uuid4().hex[:8]
    payload = (f"chunked payload {uid}\n".encode()) * 200
    filename = f"large-{uid}.bin"
    start = client.post(
        "/api/input/uploads/chunked/start",
        json={"filename": filename, "size": len(payload),
              "sha256": sha256(payload)},
        headers={"X-CSRFToken": csrf_token(client)},
    )
    if start.status_code not in (200, 201):
        bad(section, "a large file stages in chunks",
            f"start -> HTTP {start.status_code}: "
            f"{start.get_data(as_text=True)[:200]}")
        return
    session = start.get_json()
    upload_id = session["upload_id"]
    chunk_size = int(session.get("chunk_size") or 1024 * 1024)
    chunks = [payload[i:i + chunk_size] for i in range(0, len(payload), chunk_size)]

    uploaded = 0
    for index, chunk in enumerate(chunks):
        resp = client.post(
            f"/api/input/uploads/chunked/{upload_id}/chunk/{index}",
            data={"chunk": (io.BytesIO(chunk), f"chunk-{index}"),
                  "sha256": sha256(chunk)},
            content_type="multipart/form-data",
            headers={"X-CSRFToken": csrf_token(client)},
        )
        if resp.status_code != 200:
            bad(section, "a large file stages in chunks",
                f"chunk {index} -> HTTP {resp.status_code}: "
                f"{resp.get_data(as_text=True)[:200]}")
            client.delete(f"/api/input/uploads/chunked/{upload_id}",
                          headers={"X-CSRFToken": csrf_token(client)})
            return
        uploaded += 1

    complete = client.post(
        f"/api/input/uploads/chunked/{upload_id}/complete",
        json={},
        headers={"X-CSRFToken": csrf_token(client)},
    )
    if complete.status_code != 201:
        bad(section, "a large file stages in chunks",
            f"complete -> HTTP {complete.status_code}: "
            f"{complete.get_data(as_text=True)[:200]}")
        return
    result = complete.get_json()
    track_staged([{"path": result["staged_path"]}])
    path = Path(result["staged_path"])
    problems = []
    if not path.is_file():
        problems.append(f"missing on disk: {path}")
    elif sha256(path.read_bytes()) != sha256(payload):
        problems.append("assembled file differs from what was sent")
    status = client.get(f"/api/input/uploads/chunked/{upload_id}")
    if status.status_code != 404:
        problems.append("the session still exists after completion")
    if problems:
        bad(section, "a large file stages in chunks", "; ".join(problems))
    else:
        ok(section, "a large file stages in chunks",
           f"{uploaded} chunks -> {path.name}, {len(payload)} bytes, "
           f"sha256 verified, session retired")


def check_old_interfaces(client) -> None:
    section = "7. one interface"
    resp = client.get("/upload", follow_redirects=False)
    location = resp.headers.get("Location", "")
    if resp.status_code == 302 and location.endswith("/operations/input"):
        ok(section, "/upload still lands on the one interface",
           f"302 -> {location}")
    else:
        bad(section, "/upload still lands on the one interface",
            f"HTTP {resp.status_code} Location={location!r}")

    page = client.get("/operations/input")
    html = page.get_data(as_text=True)
    wanted = {
        "single file control": 'id="fileInput"',
        "folder control": 'id="folderInput"',
        "folder attribute": "webkitdirectory",
        "server path field": 'id="serverPath"',
        "start button": "Start Analysis",
    }
    missing = [label for label, needle in wanted.items() if needle not in html]
    if page.status_code == 200 and not missing:
        ok(section, "the page offers all three inputs",
           ", ".join(wanted))
    else:
        bad(section, "the page offers all three inputs",
            f"HTTP {page.status_code}, missing: {missing}")

    removed = client.post("/upload/process-path", json={})
    if removed.status_code == 404:
        ok(section, "the retired upload endpoint is gone",
           "POST /upload/process-path -> 404")
    else:
        bad(section, "the retired upload endpoint is gone",
            f"POST /upload/process-path -> {removed.status_code}")

    for asset in ("/static/js/pages/ingestion-studio-page.js",
                  "/static/js/modules/upload/large-file-upload.js"):
        resp = client.get(asset)
        if resp.status_code == 200:
            ok(section, f"page module served: {Path(asset).name}", "")
        else:
            bad(section, f"page module served: {Path(asset).name}",
                f"HTTP {resp.status_code}")


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def run(server_path: str | None, username: str | None = None,
        password: str | None = None) -> int:
    """Run every check; returns 0 when they all ran, 2 when sign-in failed."""
    from apps.web.app import app as flask_app

    client = flask_app.test_client()

    if not username:
        username = input("Administrator username: ").strip()
    if not password:
        import getpass  # noqa: PLC0415 - only needed interactively

        password = getpass.getpass("Password: ")

    resp = login(client, username, password)
    if resp.status_code != 200:
        print(f"\nCould not log in as {username!r}: HTTP {resp.status_code} "
              f"{resp.get_data(as_text=True)[:200]}")
        print("If the password is lost, reset it with: "
              "python scripts\\reset_admin_password.py --username <name> --password <new>")
        return 2

    info("session", "signed in", f"{username}")

    # Each check runs on its own: a surprise inside one of them (a module that
    # will not import on this host, an unexpected response shape) is reported
    # as a failure of that check, and the remaining checks still run. A harness
    # that stops half way is worse than one that never started - the operator
    # would read "no FAIL lines" as "verified".
    checks = [
        ("this machine", lambda: check_host(client, server_path)),
        ("1. one file", lambda: check_single_file(client)),
        ("2. a whole folder", lambda: check_folder(client)),
        ("3. names Windows refuses", lambda: check_awkward_names(client)),
        ("4. path length", lambda: check_path_length(client)),
        ("5. a path on the server",
         lambda: check_server_path(client, server_path)),
        ("6. a large file (chunked staging)", lambda: check_chunked(client)),
        ("7. one interface", lambda: check_old_interfaces(client)),
    ]
    for section, check in checks:
        try:
            check()
        except Exception as exc:  # noqa: BLE001 - reported as a failed check
            bad(section, "the check itself completed",
                f"{type(exc).__name__}: {exc}")
    return 0


def report(as_json: bool) -> int:
    failures = [r for r in RESULTS if r["status"] == FAIL]
    if as_json:
        print(json.dumps({
            "platform": sys.platform,
            "checks": RESULTS,
            "failed": len(failures),
            "passed": sum(1 for r in RESULTS if r["status"] == PASS),
            "skipped": sum(1 for r in RESULTS if r["status"] == SKIP),
        }, indent=2))
        return 1 if failures else 0

    width = max((len(r["check"]) for r in RESULTS), default=10)
    section = None
    for result in RESULTS:
        if result["section"] != section:
            section = result["section"]
            print(f"\n{section.upper()}")
            print("-" * (len(section) + 4))
        line = f"  [{result['status']}] {result['check'].ljust(width)}"
        print(f"{line}  {result['detail']}" if result["detail"] else line)

    passed = sum(1 for r in RESULTS if r["status"] == PASS)
    skipped = sum(1 for r in RESULTS if r["status"] == SKIP)
    print(f"\n{passed} passed, {len(failures)} failed, {skipped} skipped "
          f"on {platform.system()} {platform.release()}")
    if failures:
        print("\nFailed checks:")
        for result in failures:
            print(f"  - {result['check']}: {result['detail']}")
    return 1 if failures else 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--username", help="an administrator account")
    parser.add_argument("--password", help="its password (prompted when omitted)")
    parser.add_argument("--server-path",
                        help="a folder inside INGESTION_ROOTS to dry-run "
                             "(nothing is read)")
    parser.add_argument("--json", action="store_true",
                        help="machine-readable output")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    # Re-runnable: the operator may run this more than once in a session, and
    # a second run must report its own results, not a doubled list.
    RESULTS.clear()
    STAGED_DIRS.clear()
    try:
        code = run(args.server_path, args.username, args.password)
    finally:
        for batch in STAGED_DIRS:
            shutil.rmtree(batch, ignore_errors=True)
        if STAGED_DIRS and not args.json:
            print(f"\nremoved {len(STAGED_DIRS)} staged batch director"
                  f"{'y' if len(STAGED_DIRS) == 1 else 'ies'} created by this check")
    if code == 2:          # could not sign in: nothing was checked
        return 2
    return report(args.json)


if __name__ == "__main__":
    sys.exit(main())
