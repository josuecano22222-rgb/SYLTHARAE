"""Live end-to-end smoke test against a running SYLTHARAE server.

Drives the real HTTP API the way a browser behind a TLS-terminating reverse
proxy does (X-Forwarded-Proto/Host, same-origin Referer, CSRF token), so it
exercises the production configuration: Waitress, FLASK_ENV=production,
TRUSTED_PROXY_COUNT=1. See docs/TESTING.md ("Live smoke test").

    python tools/smoke/live_smoke.py install   # first run: setup wizard
    python tools/smoke/live_smoke.py run1      # full functional pass
    # restart the server, then
    python tools/smoke/live_smoke.py run2      # repeat after restart

Configuration (environment):
    SMOKE_BASE_URL     server URL                     (http://127.0.0.1:5055)
                       An https:// URL means a real TLS reverse proxy
                       (deploy/nginx) is in front: proxy headers are then
                       forged on purpose and must be overwritten, and the
                       transport (redirect, HTTP/2, TLS floor) is checked.
    SMOKE_HTTP_URL     plain-HTTP side of that proxy  (http://<host>)
    SMOKE_PUBLIC_HOST  host the "proxy" presents      (syl.example.test)
    SMOKE_EVIDENCE     directory for generated files; must be inside the
                       server's INGESTION_ROOTS       (./smoke-evidence)
    SMOKE_OCR_FONT     TrueType font for the scanned-page fixture
                       (/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf)
    SMOKE_ADMIN_PASSWORD  admin password to install / sign in with (required)
    SMOKE_DB_HOST, SMOKE_DB_PORT, SMOKE_DB_USER, SMOKE_DB_PASSWORD, SMOKE_DB
                       database the wizard installs into; in run1/run2 also
                       read (with psycopg2) to check the audit log's client
                       address

Needs ``requests`` and ``python-docx``. Exit status is non-zero on any FAIL.
"""
import json
import os
import socket
import ssl
import sys
import time
import warnings
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

import requests

BASE = os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1:5055").rstrip("/")
REAL_PROXY = BASE.startswith("https://")
PUBLIC = os.environ.get("SMOKE_PUBLIC_HOST") or (urlsplit(BASE).netloc if REAL_PROXY else "syl.example.test")
HTTP_URL = os.environ.get("SMOKE_HTTP_URL", f"http://{urlsplit(BASE).hostname}").rstrip("/")
FORGED_IP = "203.0.113.66"  # TEST-NET-3: must never reach the audit log
EV = Path(os.environ.get("SMOKE_EVIDENCE", "smoke-evidence")).resolve()
OCR_FONT = os.environ.get("SMOKE_OCR_FONT", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
ADMIN = (os.environ.get("SMOKE_ADMIN_USER", "admin"), os.environ.get("SMOKE_ADMIN_PASSWORD", ""))
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), str(detail)[:300]))
    print(("PASS " if ok else "FAIL ") + name + (f" -- {str(detail)[:300]}" if detail else ""), flush=True)
    return ok


class ProxySession(requests.Session):
    """Behaves like a browser behind a TLS-terminating proxy."""
    def __init__(self):
        super().__init__()
        self.headers["X-Forwarded-Proto"] = "https"
        self.headers["X-Forwarded-Host"] = PUBLIC
        if REAL_PROXY:
            # A real proxy must replace, not extend, what the client claims.
            self.headers["X-Forwarded-For"] = FORGED_IP
        self.token = None

    def request(self, method, url, **kw):
        if not url.startswith("http"):
            url = BASE + url
        kw.setdefault("timeout", 60)
        if method.upper() not in ("GET", "HEAD") and self.token:
            kw.setdefault("headers", {})["X-CSRFToken"] = self.token
        if method.upper() not in ("GET", "HEAD"):
            kw.setdefault("headers", {}).setdefault("Referer", f"https://{PUBLIC}/")
        r = super().request(method, url, **kw)
        for c in self.cookies:
            c.secure = False
        return r

    def refresh_csrf(self):
        r = self.get("/api/csrf-token")
        self.token = r.json().get("csrf_token")
        return r


def make_evidence():
    EV.mkdir(exist_ok=True)
    (EV / "memo.txt").write_text("Internal memo: project zephyrine kickoff on 2026-03-14.\n")
    (EV / "ledger.csv").write_text("id,item,amount\n1,zephyrine licence,1200\n2,travel,340\n")
    import docx
    d = docx.Document()
    d.add_paragraph("Minutes: the zephyrine steering group met.")
    d.save(EV / "minutes.docx")
    with zipfile.ZipFile(EV / "bundle.zip", "w") as z:
        z.writestr("copy_of_memo.txt", (EV / "memo.txt").read_text())  # duplicate content
        z.writestr("inner/notes.txt", "Nested notes mentioning zephyrine and a budget.\n")
    # A scanned page: the keyword exists only as pixels, so finding it proves
    # OCR ran inside the deployed stack (same rendering as the integration tests).
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (1200, 300), "white")
    ImageDraw.Draw(img).text((50, 120), "Scanned exhibit zephyrine 2026",
                             font=ImageFont.truetype(OCR_FONT, 44), fill="black")
    img.save(EV / "scan.png")


def login(s, user, pw):
    s.refresh_csrf()
    r = s.post("/auth/login", json={"username": user, "password": pw})
    s.refresh_csrf()  # session identity changes at login
    return r


def install():
    s = ProxySession()
    r = s.get("/health", allow_redirects=False)
    check("pre-install /health redirects to /setup", r.status_code == 302 and "/setup" in r.headers.get("Location", ""), r.status_code)
    s.refresh_csrf()
    db = {"host": os.environ.get("SMOKE_DB_HOST", "localhost"),
          "port": int(os.environ.get("SMOKE_DB_PORT", "5432")),
          "user": os.environ.get("SMOKE_DB_USER", "postgres"),
          "password": os.environ.get("SMOKE_DB_PASSWORD", ""),
          "database": os.environ.get("SMOKE_DB", "syl_smoke")}
    r = s.post("/api/setup/test-database", json=db)
    check("wizard: test-database", r.status_code == 200, r.text)
    # The same server the test-database step checked (SMOKE_DB_PORT/USER were
    # previously ignored here, installing into port 5432 as postgres).
    body = {"db_host": db["host"], "db_port": db["port"], "db_user": db["user"], "db_password": db["password"],
            "db_name": db["database"], "admin_username": ADMIN[0], "admin_password": ADMIN[1],
            "environment": "production", "flask_host": os.environ.get("SMOKE_FLASK_HOST", "127.0.0.1"), "log_level": "INFO",
            "ingestion_roots": str(EV)}
    t = time.time()
    r = s.post("/api/setup/install", json=body, timeout=600)
    check("wizard: install", r.status_code == 200, f"{r.status_code} {time.time()-t:.0f}s {r.text[:200]}")
    r = s.post("/api/setup/install", json=body)
    check("wizard: second install refused", r.status_code in (403, 409), r.status_code)


def main(phase):
    s = ProxySession()
    r = s.get("/health")
    check(f"[{phase}] /health healthy", r.status_code == 200 and r.json().get("status") == "healthy", r.text[:120])
    check(f"[{phase}] HSTS on proxied HTTPS", "max-age" in r.headers.get("Strict-Transport-Security", ""), r.headers.get("Strict-Transport-Security"))
    if REAL_PROXY:
        plain = requests.get(HTTP_URL + "/health", allow_redirects=False, timeout=30)
        check(f"[{phase}] plain HTTP redirects to HTTPS, without HSTS",
              plain.status_code == 301 and plain.headers.get("Location", "").startswith("https://")
              and "Strict-Transport-Security" not in plain.headers,
              f"{plain.status_code} {plain.headers.get('Location')}")
    else:
        plain = requests.get(BASE + "/health", timeout=30)
        check(f"[{phase}] no HSTS on plain HTTP", "Strict-Transport-Security" not in plain.headers)
    check(f"[{phase}] anonymous API -> 401", s.get("/api/search", params={"query": "x"}).status_code == 401)
    r = login(s, "admin", "wrong-password-123")
    check(f"[{phase}] bad password rejected", r.status_code in (400, 401, 403), r.status_code)
    r = login(s, *ADMIN)
    check(f"[{phase}] admin login", r.status_code == 200, r.text[:150])
    cookie = next((c for c in r.raw.headers.getlist("Set-Cookie") if "session" in c.split("=", 1)[0].lower()), "")
    check(f"[{phase}] session cookie Secure, HttpOnly, SameSite=Lax",
          all(flag in cookie for flag in ("Secure", "HttpOnly", "SameSite=Lax")), cookie.split(";", 1)[-1])
    page_checks(s, phase)
    s2 = ProxySession()
    s2.cookies.update(s.cookies)
    r = s2.post("/api/input/sides", json={"name": "nocsrf"})
    check(f"[{phase}] POST without CSRF token rejected", r.status_code in (400, 403), r.status_code)
    r = s.post("/api/input/sides", json={"name": "xsite"}, headers={"Referer": "https://evil.example/"})
    check(f"[{phase}] cross-site Referer rejected", r.status_code == 400 and "CSRF" in r.text, r.status_code)
    if REAL_PROXY:
        # The failure mode below cannot happen through deploy/nginx: it sets
        # X-Forwarded-Host itself, so a forged value is simply replaced.
        r = s.post("/api/input/sides", json={"name": f"forgedhost{phase}"}, headers={"X-Forwarded-Host": "evil.example"})
        check(f"[{phase}] proxy overwrites a forged X-Forwarded-Host", r.status_code in (200, 201), r.status_code)
    else:
        r = s.post("/api/input/sides", json={"name": "nohost"}, headers={"X-Forwarded-Host": ""})
        check(f"[{phase}] proxy dropping Host -> CSRF 400 (documented)", r.status_code == 400 and "CSRF" in r.text, r.status_code)

    src, side = f"SmokeSource{phase}", f"SmokeSide{phase}"
    r = s.post("/api/input/sources", json={"name": src, "job": "Audit", "importance": 0.8, "country": "NL", "city": "Amsterdam"})
    check(f"[{phase}] create source", r.status_code in (200, 201), r.text[:200])
    r = s.post("/api/input/sides", json={"name": side, "importance": 0.8})
    check(f"[{phase}] create side", r.status_code in (200, 201), r.text[:200])
    r = s.post("/api/input/jobs", json={"path": "/etc", "source": src, "side": side, "recursive": True})
    check(f"[{phase}] path outside INGESTION_ROOTS rejected", r.status_code in (400, 403), f"{r.status_code} {r.text[:120]}")
    r = s.post("/api/input/jobs", json={"path": str(EV), "source": src, "side": side, "recursive": True})
    ok = check(f"[{phase}] submit ingestion job", r.status_code in (200, 201, 202), r.text[:200])
    if ok:
        wait_job(s, r, f"[{phase}] job finished")
    r = s.post("/api/input/jobs", json={"path": str(EV), "source": src, "side": side, "recursive": True})
    if check(f"[{phase}] resubmit the same folder", r.status_code in (200, 201, 202), r.text[:200]):
        job = wait_job(s, r, f"[{phase}] re-ingestion finished")
        check(f"[{phase}] re-ingestion stores no duplicates", duplicates_reported(job), dup_summary(job))
    r = s.get("/api/search", params={"query": "zephyrine"})
    txt = r.text
    check(f"[{phase}] search finds keyword", r.status_code == 200 and "zephyrine" in txt.lower(), txt[:200])
    for fn in ("memo.txt", "ledger.csv", "minutes.docx", "notes.txt", "scan.png"):
        check(f"[{phase}] search result includes {fn}", fn in txt)
    ocr_check(s, phase, r)
    upload_checks(s, phase, src, side)
    stream_check(s, phase)
    r = s.get("/api/notifications")
    check(f"[{phase}] notifications endpoint", r.status_code == 200, r.text[:150])
    r = s.get("/api/import-export/settings/export")
    check(f"[{phase}] settings export", r.status_code == 200, r.status_code)
    r = s.post("/api/search/export", json={"query": "zephyrine", "format": "csv"})
    check(f"[{phase}] search export", r.status_code == 200, f"{r.status_code} {r.text[:120]}")

    user = f"analyst{phase}"
    r = s.post("/api/auth/users", json={"username": user, "password": "Analyst-Temp-Pass1!", "role": "analyst"})
    check(f"[{phase}] admin creates analyst", r.status_code in (200, 201), r.text[:150])
    a = ProxySession()
    login(a, user, "Analyst-Temp-Pass1!")
    r = a.get("/api/search", params={"query": "zephyrine"})
    check(f"[{phase}] analyst gated until password change", r.status_code == 403, r.status_code)
    r = a.post("/auth/change-password", json={"new_password": "Analyst-Real-Pass2!"})
    check(f"[{phase}] password change without the current password refused",
          r.status_code == 400, f"{r.status_code} {r.text[:120]}")
    r = a.post("/auth/change-password", json={"current_password": "Analyst-Temp-Pass1!",
                                              "new_password": "Analyst-Real-Pass2!"})
    check(f"[{phase}] analyst changes password", r.status_code == 200, r.text[:150])
    a.refresh_csrf()
    r = a.get("/api/search", params={"query": "zephyrine"})
    check(f"[{phase}] analyst can search after change", r.status_code == 200, r.status_code)
    r = a.get("/api/settings/database")
    check(f"[{phase}] analyst denied admin settings", r.status_code == 403, r.status_code)
    a.post("/auth/logout")
    check(f"[{phase}] old password refused after change",
          login(ProxySession(), user, "Analyst-Temp-Pass1!").status_code in (400, 401, 403))
    check(f"[{phase}] re-login with the new password", login(ProxySession(), user, "Analyst-Real-Pass2!").status_code == 200)
    r = s.post("/auth/logout")
    check(f"[{phase}] logout", r.status_code in (200, 302), r.status_code)
    check(f"[{phase}] session invalid after logout", s.get("/api/search", params={"query": "x"}).status_code == 401)
    audit_ip_check(phase, user)
    if REAL_PROXY:
        transport_checks(phase)


TERMINAL = ("COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED", "CANCELLED")


def wait_job(s, submitted, name):
    jid = submitted.json().get("job_id") or submitted.json().get("job", {}).get("job_id")
    job = {}
    for _ in range(240):
        job = s.get(f"/api/jobs/{jid}").json()
        job = job.get("job", job)
        if job.get("status") in TERMINAL:
            break
        time.sleep(1)
    check(name, job.get("status") in ("COMPLETED", "COMPLETED_WITH_WARNINGS"),
          dict(status=job.get("status"), **(job.get("result_summary") or {})))
    print("   job:", json.dumps({k: v for k, v in job.items() if k not in ("options",)}, default=str)[:900])
    return job


def dup_summary(job):
    summary = job.get("result_summary") or {}
    return {k: summary.get(k) for k in ("files_total", "files_duplicates", "files_stored")}


def duplicates_reported(job):
    """Everything was seen before: all duplicates, nothing stored again."""
    summary = dup_summary(job)
    return (summary["files_stored"] == 0 and isinstance(summary["files_duplicates"], int)
            and summary["files_duplicates"] > 0)


def page_checks(s, phase):
    """Security headers on a real page, and a static asset it references."""
    r = s.get("/")
    csp = r.headers.get("Content-Security-Policy", "")
    script_src = next((d for d in csp.split(";") if d.strip().startswith("script-src")), "")
    check(f"[{phase}] CSP script-src 'self' without 'unsafe-inline'",
          "'self'" in script_src and "unsafe-inline" not in script_src, script_src.strip())
    check(f"[{phase}] nosniff, framing and referrer headers",
          r.headers.get("X-Content-Type-Options") == "nosniff"
          and (r.headers.get("X-Frame-Options") in ("DENY", "SAMEORIGIN") or "frame-ancestors" in csp)
          and bool(r.headers.get("Referrer-Policy")),
          {h: r.headers.get(h) for h in ("X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy")})
    server = r.headers.get("Server", "")
    check(f"[{phase}] no server software versions disclosed",
          not any(ch.isdigit() for ch in server) and "X-Powered-By" not in r.headers
          and not any(name in server.lower() for name in ("waitress", "werkzeug", "python")), server)
    import re
    css = re.search(r'href="(/static/[^"?]+\.css)', r.text)
    if check(f"[{phase}] page references a stylesheet", css):
        a = s.get(css.group(1))
        check(f"[{phase}] static asset served", a.status_code == 200 and "css" in a.headers.get("Content-Type", ""),
              f"{css.group(1)} {a.status_code} {a.headers.get('Content-Type')}")


def upload_checks(s, phase, src, side):
    """Browser upload -> ingestion -> search -> analysis -> original download."""
    word = f"quasarglyph{phase}"
    body = f"Uploaded through the proxy: {word} appears once.\n".encode()
    r = s.post("/api/input/uploads", files={"files": (f"upload_{phase}.txt", body, "text/plain")})
    if not check(f"[{phase}] upload staged", r.status_code == 201, f"{r.status_code} {r.text[:150]}"):
        return
    staged = r.json()["staged_paths"][0]
    r = s.post("/api/input/jobs", json={"path": staged, "source": src, "side": side, "recursive": True})
    if not check(f"[{phase}] staged upload submitted for ingestion", r.status_code in (200, 201, 202), r.text[:200]):
        return
    wait_job(s, r, f"[{phase}] upload ingestion finished")
    # ``include_snippets`` matters: the fast engine defers snippet building
    # (``snippets_pending: true``) and returns the file name as the snippet, so
    # the word this check looks for is not in the response yet. Asking for
    # snippets is what makes "the content token resolves to this file" a
    # statement the response can carry.
    r = s.get("/api/search", params={"query": word, "include_snippets": "true"})
    ids = sorted({hit.get("file_id") or hit.get("id") for hit in _hits(r) if word in json.dumps(hit)} - {None})
    if not check(f"[{phase}] search finds the uploaded file", r.status_code == 200 and ids, r.text[:200]):
        return
    fid = ids[0]
    for what in ("statistics", "file"):
        a = s.get(f"/api/analysis/{what}/{fid}")
        check(f"[{phase}] analysis: {what}", a.status_code == 200, f"{a.status_code} {a.text[:120]}")
    d = s.get(f"/api/file/{fid}/original/content")
    check(f"[{phase}] original file downloads intact", d.status_code == 200 and d.content == body,
          f"{d.status_code} {len(d.content)} bytes")


def ocr_check(s, phase, search_response):
    """The scan's text came from OCR, and its provenance is stored and served."""
    ids = [h.get("file_id") or h.get("id") for h in _hits(search_response)
           if h.get("file_name") == "scan.png"]
    if not check(f"[{phase}] OCR: scan.png located in results", bool(ids), str(ids)):
        return
    r = s.get(f"/api/file/{ids[0]}/details")
    ocr = {}
    if r.status_code == 200:
        ocr = ((r.json().get("details") or {}).get("extraction_provenance") or {}).get("ocr") or {}
    conf = ocr.get("confidence")
    check(f"[{phase}] OCR provenance: derived, engine, confidence in (0, 1]",
          ocr.get("derived") is True and ocr.get("engine") in ("tesseract", "rapidocr")
          and isinstance(conf, (int, float)) and 0.0 < conf <= 1.0,
          f"{r.status_code} {json.dumps(ocr)[:160]}")


def _hits(r):
    try:
        data = r.json()
    except ValueError:
        return []
    for key in ("results", "files", "items", "data"):
        if isinstance(data.get(key), list):
            return data[key]
    return []


def stream_check(s, phase):
    """Server-sent events must reach the client unbuffered through the proxy."""
    t = time.time()
    try:
        with s.get("/api/jobs/stream", stream=True, timeout=(5, 20)) as r:
            first = next(r.iter_lines(), b"")
            check(f"[{phase}] event stream delivers promptly",
                  r.status_code == 200 and "text/event-stream" in r.headers.get("Content-Type", "") and first,
                  f"{r.status_code} {time.time()-t:.1f}s {first[:60]!r}")
    except requests.RequestException as exc:
        check(f"[{phase}] event stream delivers promptly", False, exc)


def audit_ip_check(phase, user):
    """The audit log holds the client's real address, never a forged one."""
    if not os.environ.get("SMOKE_DB_HOST"):
        print(f"SKIP [{phase}] audit client address (SMOKE_DB_HOST not set)")
        return
    import psycopg2
    conn = psycopg2.connect(host=os.environ["SMOKE_DB_HOST"], port=int(os.environ.get("SMOKE_DB_PORT", "5432")),
                            user=os.environ.get("SMOKE_DB_USER", "postgres"),
                            password=os.environ.get("SMOKE_DB_PASSWORD", ""), dbname=os.environ.get("SMOKE_DB", "syl_smoke"))
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT ip_address FROM audit_log WHERE created_at > now() - interval '15 minutes'"
                        " AND ip_address IS NOT NULL")
            seen = sorted(row[0] for row in cur.fetchall())
    finally:
        conn.close()
    # This client's real address: loopback, over IPv4 or IPv6 ("localhost").
    expected = set(os.environ.get("SMOKE_CLIENT_IP", "127.0.0.1,::1").split(","))
    check(f"[{phase}] audit log records the real client address", seen and FORGED_IP not in seen
          and set(seen) <= expected, seen)


def transport_checks(phase):
    """What deploy/nginx promises: HTTP/2, a TLS floor of 1.2, verified chain."""
    host, port = urlsplit(BASE).hostname, urlsplit(BASE).port or 443
    ctx = ssl.create_default_context(cafile=os.environ.get("REQUESTS_CA_BUNDLE"))
    ctx.set_alpn_protocols(["h2", "http/1.1"])
    with socket.create_connection((host, port), timeout=10) as raw, ctx.wrap_socket(raw, server_hostname=host) as tls:
        check(f"[{phase}] HTTP/2 negotiated (ALPN), TLS {tls.version()}",
              tls.selected_alpn_protocol() == "h2" and tls.version() in ("TLSv1.2", "TLSv1.3"),
              f"{tls.selected_alpn_protocol()} {tls.version()} {tls.cipher()[0]}")
    old = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    old.check_hostname, old.verify_mode = False, ssl.CERT_NONE
    with warnings.catch_warnings():
        # Offering the deprecated versions is the point: nginx must refuse them.
        warnings.simplefilter("ignore", DeprecationWarning)
        old.minimum_version = ssl.TLSVersion.TLSv1
        old.maximum_version = ssl.TLSVersion.TLSv1_1
    try:
        old.set_ciphers("DEFAULT:@SECLEVEL=0")
    except ssl.SSLError:
        pass
    try:
        with socket.create_connection((host, port), timeout=10) as raw, old.wrap_socket(raw, server_hostname=host) as tls:
            accepted = tls.version()
    except (ssl.SSLError, OSError) as exc:
        accepted = None
        detail = type(exc).__name__
    check(f"[{phase}] TLS 1.0/1.1 refused", accepted is None, accepted or detail)


if __name__ == "__main__":
    if len(sys.argv) != 2 or not ADMIN[1]:
        sys.exit(__doc__)
    mode = sys.argv[1]
    if mode == "install":
        make_evidence()
        install()
    else:
        main(mode)
    fails = [n for n, ok, _ in RESULTS if not ok]
    print(f"SUMMARY {mode}: {len(RESULTS)-len(fails)} pass, {len(fails)} fail")
    sys.exit(1 if fails else 0)
