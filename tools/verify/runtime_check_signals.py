"""Runtime check of Horizon / Signal Explorer against a running server.

    python tools/verify/runtime_check_signals.py <base-url> <state-dir>

Requires a server started against the database from ``runtime_provision.py``
(same ``<state-dir>``). Talks HTTP only - no application imports - so what is
verified is the running process: authentication, the page, the three APIs and
their contract. Responses are saved under ``<state-dir>/evidence/`` and the
session cookie under ``<state-dir>/session.txt`` (for the page harness).

The session cookie is carried by hand: a production-mode server marks it
``Secure``, which HTTP clients rightly refuse to send over plain HTTP; this
check must not weaken the server's cookie policy to run.
"""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REF = "2026-10-01"
failures = []


def check(condition, message):
    print(("PASS " if condition else "FAIL ") + message)
    if not condition:
        failures.append(message)


class Client:
    def __init__(self, base):
        self.base = base.rstrip("/")
        self.cookie = None

    def login(self, username, password):
        """The browser's flow: a CSRF token for this session, then the login."""
        status, raw, _ = self.request("GET", "/api/csrf-token")
        self.csrf = json.loads(raw)["csrf_token"] if status == 200 else None
        return self.request("POST", "/auth/login", {"username": username, "password": password})

    def request(self, method, path, body=None, params=None):
        url = self.base + path + ("?" + urllib.parse.urlencode(params, doseq=True) if params else "")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self.cookie:
            req.add_header("Cookie", self.cookie)
        if method != "GET" and getattr(self, "csrf", None):
            req.add_header("X-CSRFToken", self.csrf)

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None

        opener = urllib.request.build_opener(NoRedirect)
        try:
            resp = opener.open(req, timeout=30)
        except urllib.error.HTTPError as exc:
            resp = exc
        for header in resp.headers.get_all("Set-Cookie") or []:
            if header.split("=", 1)[0].strip() in ("session", "syltharae_session") or \
                    "session" in header.split("=", 1)[0]:
                self.cookie = header.split(";", 1)[0]
        raw = resp.read()
        return resp.status, raw, resp.headers


def main(base, state_dir):
    state = Path(state_dir)
    info = json.loads((state / "env.json").read_text())
    ids = info["ids"]
    evidence = state / "evidence"
    evidence.mkdir(exist_ok=True)

    anon = Client(base)
    status, _, _ = anon.request("GET", "/health")
    check(status == 200, f"GET /health -> {status}")
    for path in ("/api/signals", "/api/signals/horizon", "/api/signals/1"):
        status, _, _ = anon.request("GET", path)
        check(status == 401, f"anonymous GET {path} -> {status} (401 expected)")

    viewer = Client(base)
    username, password = info["users"]["viewer"]
    status, raw, _ = viewer.login(username, password)
    check(status == 200 and viewer.cookie, f"viewer login -> {status}")

    status, raw, headers = viewer.request("GET", "/signals")
    html = raw.decode()
    (evidence / "signals_page.html").write_text(html)
    check(status == 200, f"GET /signals as viewer -> {status}")
    csp = headers.get("Content-Security-Policy", "")
    check("'unsafe-inline'" not in csp.split("script-src", 1)[-1].split(";", 1)[0],
          "page CSP forbids inline script")
    check('id="signals-page-data"' in html and 'data-page-type="signals"' in html,
          "page carries its contract data and signals page type")

    def api(path, **params):
        status, raw, _ = viewer.request("GET", path, params=params)
        body = json.loads(raw)
        name = path.strip("/").replace("/", "_") + ("_" + "_".join(
            f"{k}-{v}" for k, v in sorted(params.items()) if k != "reference_date") if params else "")
        (evidence / f"{name[:120]}.json").write_text(json.dumps(body, indent=2, ensure_ascii=False))
        return status, body

    status, hz = api("/api/signals/horizon", reference_date=REF)
    check(status == 200, f"GET /api/signals/horizon -> {status}")
    counts = {b["key"]: b["signals"] for b in hz["buckets"]}
    print("     buckets:", counts, "undated:", hz["undated"], "coverage:",
          {k: hz["coverage"][k] for k in ("matching_contents", "current", "not_measured")})
    check(counts == {"overdue": 1, "week": 2, "month": 1, "quarter": 1, "later": 2, "past": 0},
          "horizon buckets match the stored documents")
    check(hz["undated"]["ambiguous"] == 1, "ambiguous 03/04/2026 counted as undated")
    check(hz["coverage"]["current"] == 2 and hz["coverage"]["not_measured"] == 0,
          "coverage: both documents measured by the current detector")
    languages = sorted({i["language"] or "-" for i in hz["items"]})
    check({"he", "fa", "hr", "en"} <= set(languages), f"horizon items in he/fa/hr/en: {languages}")

    status, again = api("/api/signals/horizon", reference_date=REF)
    check(again["query_fingerprint"] == hz["query_fingerprint"] and again["items"] == hz["items"],
          "horizon is deterministic across requests")

    status, ex = api("/api/signals", reference_date=REF, source_id=ids["source_alpha"])
    check(status == 200 and ex["contents"] == 1, f"explorer filtered by source -> {status}")
    detectors = {f["value"]: f["count"] for f in ex["facets"]["detector"]}
    check(detectors.get("places") == 2 and ex["total"] == sum(detectors.values()),
          f"explorer facets are exact: {detectors} total={ex['total']}")

    tripoli = [i for i in ex["items"] if i["surface"] == "Tripoli"]
    check(len(tripoli) == 1 and len(tripoli[0]["places"]) == 2, "Tripoli is ambiguous (2 places)")
    status, detail = api(f"/api/signals/{tripoli[0]['signal_id']}", reference_date=REF)
    check(status == 200 and detail["run"]["current_version"] is True
          and detail["occurrences"]["items"][0]["file_path"] == "/runtime/alpha.txt",
          "signal detail: run, current version, occurrence path")

    check(hz.get("read_consistency") == "repeatable_read_snapshot",
          f"horizon read from one snapshot ({hz.get('read_consistency')})")
    status, bad = api("/api/signals/horizon", detector="places")
    check(status == 400, f"horizon refuses the places detector -> {status}")
    status, wild = api("/api/signals", evidence_text="%")
    check(status == 200 and wild["total"] == 0, "LIKE wildcard in evidence_text is literal")
    # Identifiers are ASCII positive integers: a superscript must not reach
    # int() (whose message would leak), other scripts' digits are not ids.
    for param, value in (("hash_id", "\u00b2"), ("hash_id", "\u0663"), ("source_id", "\u00b2"),
                         ("saved_search_id", "0")):
        status, bad = api("/api/signals", **{param: value})
        message = (bad.get("error") or {}).get("message", "")
        check(status == 400 and param in message and "int()" not in message,
              f"{param}={value!r} -> {status} {message!r}")

    (state / "session.txt").write_text(viewer.cookie or "")
    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
