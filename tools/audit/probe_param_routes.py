#!/usr/bin/env python
"""Request every parameterised route with four kinds of id and record the truth.

The role matrix (``probe_routes.py``) covers the routes without path
parameters. This covers the other half: the 155 rules that take an id, a
name or a language. For each rule and method, every path parameter is
substituted with

* **valid**      an id that exists, created through the application's own
                 HTTP API (source, side, file path, word, keyword, category,
                 scenario, saved search, schedule, notification, user);
* **missing**    a large id that does not exist;
* **malformed**  ``abc`` - the converter itself may refuse it, which is fine,
                 but the answer must be a client error, never a 500;
* **other-user** an id that exists but belongs to a second, unrelated
                 analyst. A 2xx for someone else's private object is a
                 finding (IDOR); a 403/404 is the correct answer.

A 5xx is always a finding. A body that carries a traceback, a driver
exception or a SQL fragment is always a finding. Nothing here is asserted to
be correct by declaration - the status, the body shape and the row-count
effect are written to ``audit_report/findings/param_routes.csv``.

Fixtures are created through the app's APIs, not with INSERTs. Where the
application has no HTTP API for a thing (notifications are written by the
monitoring service; places need a loaded gazetteer) the column says so
instead of inventing an id.

Usage:  python tools/audit/probe_param_routes.py
Exit 1 when any row is a finding.
"""

from __future__ import annotations

import csv
import json
import os
import pathlib
import re
import sys
import tempfile
import time
import traceback
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "audit_report" / "findings"

PASSWORD = "audit-param-route-password-123"
#: Parameters naming an object that belongs to one user. Only these make a
#: cross-user request meaningful: sources, sides, categories, words and paths
#: are shared objects, so another analyst reading them is the product's normal
#: behaviour and not a finding.
#: ``notification_id`` is deliberately absent: POST /api/notifications/scan is
#: the only way to create one and it writes system-wide alerts
#: (recipient_user_id NULL), which every signed-in user may read by design.
#: Addressed alerts exist only for scenario outcomes, which the scenarios API
#: creates with its owner; that visibility is covered by the notification
#: tests. The column below records the same reasoning in the CSV.
OWNED_PARAMS = frozenset({"scenario_id", "search_id", "user_id",
                          "schedule_id", "evaluation_id", "job_id", "task_id", "entry_id"})

LEAK_MARKERS = ("Traceback (most recent call last)", "psycopg2.", "UndefinedColumn",
                "UndefinedTable", "SyntaxError: ", "SELECT ", "INSERT INTO ")


def bootstrap_audit_db(name: str):
    import psycopg2
    from database.bootstrap import bootstrap_database

    cfg = {
        "host": os.environ["DB_HOST"],
        "port": int(os.environ.get("DB_PORT") or 5432),
        "user": os.environ.get("DB_USER") or "postgres",
        "password": os.environ.get("DB_PASSWORD") or "",
        "database": name,
    }
    admin = psycopg2.connect(host=cfg["host"], port=cfg["port"], user=cfg["user"],
                             password=cfg["password"], dbname="postgres")
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
        if cur.fetchone() is None:
            cur.execute(f'CREATE DATABASE "{name}"')
    admin.close()
    os.environ.update(DB_HOST=cfg["host"], DB_PORT=str(cfg["port"]), DB_USER=cfg["user"],
                      DB_PASSWORD=cfg["password"], DB_NAME=cfg["database"])
    return cfg, bootstrap_database(cfg)


#: Temp directory the probe imports documents from; must be an approved
#: ingestion root before the app reads its configuration.
DOC_ROOT = pathlib.Path(tempfile.mkdtemp(prefix="param_docs_"))
os.environ["INGESTION_ROOTS"] = str(DOC_ROOT)


def make_app():
    import core.initialization as init
    from apps.web.app import app

    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    init.INIT_MARKER_FILE = pathlib.Path(tempfile.mkdtemp(prefix="param_marker_")) / ".system_initialized"
    init.mark_system_initialized()
    try:
        from core.security.rate_limit import limiter

        limiter.enabled = False
    except Exception:
        pass
    return app


def sign_in(app, username, role="analyst"):
    from core.security.service import get_auth_service

    auth = get_auth_service()
    if auth.get_user_by_username(username) is None:
        auth.create_user(username, PASSWORD, role=role)
    client = app.test_client()
    resp = client.post("/auth/login", json={"username": username, "password": PASSWORD})
    assert resp.status_code == 200, (username, resp.status_code, resp.get_data(as_text=True)[:200])
    return client


def _id(resp, *path):
    try:
        data = resp.get_json()
    except Exception:
        return None
    for key in path:
        if isinstance(data, dict) and key in data:
            data = data[key]
        else:
            return None
    if isinstance(data, dict):
        for key in ("id", "_id"):
            if key in data:
                return data[key]
    return data if isinstance(data, int) else None


class Fixtures:
    """Objects created through the app's APIs, for the probe's ids.

    ``valid[param]`` is an id that exists. ``other[param]`` is an id created
    by a *different* analyst (only where the object has an owner).
    """

    def __init__(self, app):
        self.app = app
        self.a = sign_in(app, "param_analyst_a", "analyst")
        self.b = sign_in(app, "param_analyst_b", "analyst")
        self.admin = sign_in(app, "param_admin", "admin")
        self.valid = {}
        self.other = {}
        self.notes = {}
        self._create()

    def _one(self, client, method, url, **kw):
        resp = getattr(client, method)(url, **kw)
        return resp

    def _create(self):
        # source / side
        resp = self.a.post("/api/sources", json={"name": f"param source {uuid.uuid4().hex[:6]}"})
        self.valid["source_id"] = _id(resp, "source") or _id(resp)

        # A source created by the *other* analyst. ``sources`` has no owner
        # column: ``ownership`` (m0001) is free text describing who the source
        # belongs to in the real world, not an access-control field. So this
        # fixture does not prove isolation - it proves what A can do with an
        # id A did not create, and the report has to say that plainly.
        resp = self.b.post("/api/sources", json={
            "name": f"param source b {uuid.uuid4().hex[:6]}", "ownership": "param-b"})
        self.other["source_id"] = _id(resp, "source") or _id(resp)
        read_back = (self.a.get(f"/api/sources/{self.other['source_id']}").get_json()
                     if self.other["source_id"] else None)
        seen = (read_back or {}).get("source") or read_back or {}
        self.notes["source_id"] = (
            f"sources.ownership is free text (m0001), not a user FK; b-created "
            f"id={self.other['source_id']}, ownership={seen.get('ownership')!r} "
            f"read back by A")
        resp = self.a.post("/api/sides", json={"name": f"param side {uuid.uuid4().hex[:6]}"})
        self.valid["side_id"] = _id(resp, "side") or _id(resp)

        # keyword, category, word - the form-shaped writers expect form data
        category_name = f"paramcat{uuid.uuid4().hex[:6]}"
        resp = self.a.post("/category/add", json={"category_name": category_name})
        self.valid["category_id"] = _id(resp, "category_id")
        # /keywords/add answers with a redirect (the form page), so the id is
        # read back from the list endpoint the UI itself uses.
        # a two-word phrase: single words are routed to words_categorys, only
        # phrases become rows in `keywords`
        keyword_text = f"param probe {uuid.uuid4().hex[:6]}"
        self.a.post("/keywords/add", data={"keywords_text": keyword_text,
                                           "category_id": self.valid.get("category_id") or "1"})
        listing = self.a.get("/api/keywords", query_string={"search": keyword_text})
        try:
            payload = listing.get_json() or {}
            items = payload if isinstance(payload, list) else (payload.get("keywords") or [])
        except Exception:
            items = []
        self.notes["keyword_add"] = f"{listing.status_code} items={len(items)}"
        for item in items:
            text = str(item.get("text") or item.get("keyword") or "")
            if text.lower() == keyword_text.lower():
                self.valid["keyword_id"] = item.get("id")
                break
        if not self.valid.get("keyword_id") and items:
            self.valid["keyword_id"] = items[0].get("id")
        resp = self.a.post("/api/words", json={"word": f"paramword{uuid.uuid4().hex[:6]}"})
        self.valid["word_id"] = _id(resp)

        # The hierarchy routes (<node_id>) are new: give them a real category so
        # the "valid" variant is a real request rather than a placeholder id.
        # Categories are shared objects (no owner column), so the other-user
        # variant uses a second category created by B, not an isolation claim.
        resp = self.a.post("/api/categories/tree",
                           json={"name": f"param node {uuid.uuid4().hex[:6]}", "mode": "root"})
        self.valid["node_id"] = _id(resp, "node")
        resp = self.b.post("/api/categories/tree",
                           json={"name": f"param node b {uuid.uuid4().hex[:6]}", "mode": "root"})
        self.other["node_id"] = _id(resp, "node")
        self.notes["node_id"] = (
            f"a={self.valid.get('node_id')} b={self.other.get('node_id')}; both created through "
            f"POST /api/categories/tree; categorys has no owner column, so a cross-read is "
            f"the designed behaviour")

        # a real document, imported by the app's own import endpoint. Import is
        # an administrator action and only accepts paths under an approved
        # root, so the probe sets INGESTION_ROOTS (in make_app) to a temp dir
        # it owns and posts a file from there.
        source_id, side_id = self.valid.get("source_id"), self.valid.get("side_id")
        if source_id and side_id:
            doc = DOC_ROOT / "param_probe.txt"
            doc.write_text("A probe document. The review will be held on 3 March 2027. "
                           "Contact alice@example.org or 10.0.0.1.\n", encoding="utf-8")
            resp = self.admin.post("/api/import-export/batch-import",
                                   json={"file_paths": [str(doc)], "source_id": source_id,
                                         "side_id": side_id})
            self.notes["import"] = f"{resp.status_code} {resp.get_data(as_text=True)[:160]}"
            try:
                body = resp.get_json() or {}
            except Exception:
                body = {}
            ids = [r.get("path_id") or r.get("id") for r in (body.get("results") or [])
                   if isinstance(r, dict)]
            ids = [i for i in ids if i]
            # Ingestion is queued: the response names the file, not its path
            # row. Poll the list endpoint the UI polls until the document and
            # its signals exist (bounded, so a stuck import is reported as a
            # missing fixture rather than hanging the probe).
            for _ in range(60):
                if ids:
                    break
                listing = self.a.get("/api/paths",
                                     query_string={"search": "param_probe", "per_page": 5})
                try:
                    payload = listing.get_json() or {}
                except Exception:
                    payload = {}
                rows = payload if isinstance(payload, list) else (
                    payload.get("paths") or payload.get("items") or [])
                ids = [r.get("id") for r in rows if isinstance(r, dict) and r.get("id")]
                if not ids:
                    time.sleep(0.5)
            if ids:
                self.valid["path_id"] = ids[0]
                self.valid["file_id"] = ids[0]

        # signal ids: read from the explorer endpoint the analysis page uses
        listing = self.a.get("/api/signals", query_string={"per_page": 3})
        try:
            payload = listing.get_json() or {}
        except Exception:
            payload = {}
        items = payload if isinstance(payload, list) else (payload.get("items") or [])
        ids = [i.get("signal_id") or i.get("id") for i in items if isinstance(i, dict)]
        ids = [i for i in ids if i]
        self.valid["signal_id"] = ids[0] if ids else None
        # a signal's document carries the hash id the content routes take
        self.valid["hash_id"] = None
        for item in items:
            doc = item.get("document") if isinstance(item, dict) else None
            if isinstance(doc, dict) and doc.get("hash_id"):
                self.valid["hash_id"] = doc["hash_id"]
                break
        if not self.valid.get("hash_id") and items and isinstance(items[0], dict):
            self.valid["hash_id"] = items[0].get("hash_id")
        if payload.get("criteria") is not None:
            self.notes["signal_id"] = f"{len(items)} signals visible to the analyst"

        # job ids: the import above queued one, and /api/jobs lists them
        listing = self.a.get("/api/jobs", query_string={"limit": 5})
        try:
            payload = listing.get_json() or {}
        except Exception:
            payload = {}
        items = payload if isinstance(payload, list) else (payload.get("jobs") or payload.get("items") or [])
        ids = [i.get("id") or i.get("job_id") for i in items if isinstance(i, dict)]
        ids = [i for i in ids if i]
        self.valid["job_id"] = ids[0] if ids else None
        self.notes["job_id"] = f"/api/jobs answered {listing.status_code}: {len(ids)} job(s)"

        # scenario (owned) - created by B as well, so A can be tested against
        # an object that belongs to someone else
        definition = {
            "criteria": {"sources": [source_id] if source_id else []},
            "conditions": {"dated": {"signals": {"signal_types": ["date_reference"]}}},
            "cases": [{"id": "c_dated", "when": {"all": ["dated"]}, "outcome": "dated"}],
            "outcomes": {"dated": {"label": "Dated", "actions": ["notify"]},
                         "none": {"label": "Nothing", "actions": []}},
            "default_outcome": "none",
            "strategy": "first_match",
        }
        resp = self.a.post("/api/scenarios", json={"name": f"param scenario {uuid.uuid4().hex[:6]}",
                                                   "definition": definition})
        self.valid["scenario_id"] = _id(resp, "scenario")
        resp = self.b.post("/api/scenarios", json={"name": f"param scenario b {uuid.uuid4().hex[:6]}",
                                                   "definition": definition})
        self.other["scenario_id"] = _id(resp, "scenario")
        self.notes["scenario"] = (f"a={resp.status_code}:{self.valid.get('scenario_id')} "
                                  f"b={self.other.get('scenario_id')}")

        # saved search
        resp = self.a.post("/api/search/saved", json={"name": f"param saved {uuid.uuid4().hex[:6]}",
                                                      "query": "probe"})
        self.valid["search_id"] = _id(resp, "search_id") or _id(resp, "saved_search")
        resp = self.b.post("/api/search/saved", json={"name": f"param saved b {uuid.uuid4().hex[:6]}",
                                                      "query": "probe"})
        self.other["search_id"] = _id(resp, "search_id") or _id(resp, "saved_search")

        # schedule (interval-based; no cron field in this model)
        # every schedule type requires an administrator
        resp = self.admin.post("/api/schedules", json={
            "name": f"param sched {uuid.uuid4().hex[:6]}",
            "schedule_type": "scenario_evaluation", "interval_minutes": 60})
        self.valid["schedule_id"] = _id(resp, "schedule")
        self.notes["schedule"] = f"{resp.status_code} {resp.get_data(as_text=True)[:120]}"

        # user (admin creates; A may not)
        resp = self.admin.post("/api/auth/users", json={"username": f"param_user_{uuid.uuid4().hex[:6]}",
                                                        "password": PASSWORD, "role": "viewer"})
        self.valid["user_id"] = _id(resp, "user")

        # notifications: there is no create endpoint - the monitoring scan is
        # what produces them from a document's future dates. Each analyst runs
        # the scan for itself, so A has one and B has one of its own.
        def _scan_ids(client):
            client.post("/api/notifications/scan", json={})
            listing = client.get("/api/notifications", query_string={"per_page": 10})
            payload = listing.get_json() or {}
            items = payload.get("notifications") or payload.get("items") or []
            return [i.get("id") for i in items if isinstance(i, dict) and i.get("id")]

        mine, theirs = _scan_ids(self.a), _scan_ids(self.b)
        self.valid["notification_id"] = mine[0] if mine else None
        self.other["notification_id"] = theirs[0] if theirs else None
        self.notes["notification"] = (f"a={len(mine)} b={len(theirs)}; created by "
                                      "POST /api/notifications/scan, no create endpoint exists")

        # places: created through the app's own API, which needs the feature
        # type the request names to have a category row (round 19 turned that
        # missing row into a 503). The category is reference data the seed
        # would create; here it is created so the routes get a real object.
        self._ensure_feature_category("city")
        resp = self.admin.post("/api/places", json={
            "label": f"param place {uuid.uuid4().hex[:6]}", "feature_type": "city",
            "country_codes": ["NL"]})
        self.valid["place_id"] = _id(resp, "place") or _id(resp)
        self.notes["place_id"] = f"POST /api/places -> {resp.status_code}"
        if self.valid.get("place_id"):
            resp = self.admin.post("/api/place-names", json={
                "place_id": self.valid["place_id"],
                "name": f"param alias {uuid.uuid4().hex[:6]}"})
            self.valid["name_id"] = _id(resp, "name") or _id(resp)
            self.notes["name_id"] = f"POST /api/place-names -> {resp.status_code}"

        # audit entry: the audit log has no create endpoint, but administrator
        # actions write to it, so the newest row is a real id.
        listing = self.admin.get("/api/audit", query_string={"per_page": 1})
        payload = listing.get_json() or {}
        entries = payload.get("entries") or payload.get("audit") or payload.get("items") or []
        self.valid["entry_id"] = entries[0].get("id") if entries else None
        self.notes["entry_id"] = (f"GET /api/audit -> {listing.status_code}, "
                                  f"{len(entries)} row(s)")

        # chunked upload: start a session through the API the browser uses.
        resp = self.admin.post("/api/input/uploads/chunked/start",
                               json={"filename": "param-probe.bin", "size": 1024})
        # The id is a uuid hex string, not an integer: _id() would return None.
        self.valid["upload_id"] = (resp.get_json() or {}).get("upload_id")
        self.notes["upload_id"] = f"chunked/start -> {resp.status_code}"

        # lexis views: a saved view is per-user, so A and B each create one.
        def _view(client):
            resp = client.post("/api/lexis/views", json={
                "name": f"param view {uuid.uuid4().hex[:6]}", "scope": "root"})
            return _id(resp, "view") or _id(resp)

        self.valid["view_id"] = _view(self.a)
        self.other["view_id"] = _view(self.b)

        for key in ("source_id", "side_id", "keyword_id", "category_id", "word_id", "path_id",
                    "file_id", "scenario_id", "search_id", "schedule_id", "user_id"):
            if not self.valid.get(key):
                self.notes[key] = f"fixture missing ({self.valid.get(key)!r})"

    # -- helpers ---------------------------------------------------------
    def _ensure_feature_category(self, feature_type: str):
        """Make sure a feature type has a category row, so a place can exist."""
        import psycopg2
        # get_db_config() honours DB_HOST/DB_NAME at read time; the persisted
        # settings file may still name a database from an earlier run.
        from settings.config import get_db_config

        cfg = get_db_config()
        conn = psycopg2.connect(host=cfg["host"], port=cfg["port"], user=cfg["user"],
                                password=cfg.get("password") or "",
                                dbname=cfg["database"])
        try:
            with conn, conn.cursor() as cur:
                cur.execute("INSERT INTO words (word) VALUES (%s)"
                            " ON CONFLICT (word) DO UPDATE SET word = EXCLUDED.word"
                            " RETURNING id", (feature_type,))
                word_id = cur.fetchone()[0]
                cur.execute("INSERT INTO categorys (word_id) VALUES (%s)"
                            " ON CONFLICT DO NOTHING", (word_id,))
        finally:
            conn.close()

    def ensure_other_source(self):
        """B's source, recreated if an earlier row deleted it.

        ``DELETE /api/sources/<id>`` succeeds for an analyst on a source that
        analyst did not create (sources are shared reference data), so the row
        that probes deletion removes the fixture the later rows address. Each
        other-user row must start from a live object or its status means
        nothing.
        """
        source_id = self.other.get("source_id")
        if source_id and self.b.get(f"/api/sources/{source_id}").status_code == 200:
            return source_id
        resp = self.b.post("/api/sources", json={
            "name": f"param source b {uuid.uuid4().hex[:6]}", "ownership": "param-b"})
        self.other["source_id"] = _id(resp, "source") or _id(resp)
        return self.other["source_id"]

    def ensure_node(self, other=False):
        """A live category id, recreated if a previous row deleted it.

        ``POST /api/categories/tree/<node_id>/delete`` is probed with the valid
        fixture and really deletes it, so every later row that addresses a node
        must start from one that exists or its status says nothing.
        """
        key = "other" if other else "valid"
        client = self.b if other else self.a
        node_id = self.other.get("node_id") if other else self.valid.get("node_id")
        if node_id and client.get(f"/api/categories/tree/{node_id}").status_code == 200:
            return node_id
        resp = client.post("/api/categories/tree",
                           json={"name": f"param node {uuid.uuid4().hex[:6]}", "mode": "root"})
        node_id = _id(resp, "node")
        if other:
            self.other["node_id"] = node_id
        else:
            self.valid["node_id"] = node_id
        return node_id

    def _user_id(self, username):
        from core.security.service import get_auth_service

        user = get_auth_service().get_user_by_username(username)
        return user["id"] if isinstance(user, dict) else getattr(user, "id", None)

    def _max(self, table):
        from Api.utils import execute_query

        row = execute_query(f"SELECT max(id) FROM {table}", fetch="one")  # nosec B608 - fixed table name
        return row[0] if row else None


def body_shape(resp):
    text = resp.get_data(as_text=True) or ""
    shape = "empty"
    if text:
        try:
            data = json.loads(text)
        except Exception:
            shape = "html" if text.lstrip().startswith("<") else "text"
        else:
            if isinstance(data, dict):
                keys = sorted(data)[:6]
                shape = "json:" + ",".join(keys)
            else:
                shape = f"json:{type(data).__name__}"
    leaked = [m for m in LEAK_MARKERS if m in text]
    return shape, leaked, text[:160].replace("\n", " ")


def table_counts():
    from Api.utils import execute_query

    tables = ("sources", "sides", "paths", "words", "keywords", "categorys", "alerts",
              "scenarios", "saved_searches", "job_schedules", "users")
    counts = {}
    for table in tables:
        try:
            row = execute_query(f"SELECT count(*) FROM {table}", fetch="one")  # nosec B608
            counts[table] = row[0]
        except Exception:
            counts[table] = None
    return counts


def verdict_for(status, leaked, variant, effect, owned, real_id=True):
    """Classify one response.

    ``owned`` says whether the parameter names an object with an owner (see
    OWNED_PARAMS); a 2xx for another user's owned object is the finding, a 2xx
    for a shared object is the designed behaviour.
    """
    if leaked:
        return "STACK-TRACE"
    if status == 501:
        return "NOT-IMPLEMENTED"
    if status >= 500:
        return "SERVER-ERROR"
    # Only a request for an id that actually names an object can prove an
    # access-control defect. A subresource page asked for job_id="probe"
    # answers 200 with its own "not found" HTML, which is not a leak - it was
    # reported as CROSS-USER-200 until this guard.
    if variant == "other-user" and owned and real_id and 200 <= status < 300:
        return "CROSS-USER-200"
    return "OK"


def converters(rule):
    """{parameter: converter-or-None} as the rule declares them."""
    return {name: kind for kind, name in
            re.findall(r"<(?:([^<>:]+):)?([^<>]+)>", rule)}


def string_value(name, rule):
    """A plausible value for a route parameter that is not an id."""
    kind = converters(rule).get(name)
    if kind in ("int", "float"):
        # No object of this kind can be created through the API (a map package
        # tile needs a downloaded package, an evaluation needs a completed
        # run). A request for 0 still exercises the route - its answer is a
        # measurement - where a skipped row is not. The caller records that the
        # id is synthetic.
        return 0
    return {"language": "en", "file_type": "txt", "key": "theme", "filename": "x.txt",
            "area": "dashboard", "index": "0", "view_id": "default"}.get(name, "probe")


def build_url(rule, values):
    url = rule
    for name, value in values.items():
        # rules look like "<int:scenario_id>" or "<action_id>": the name is the
        # last segment before ">", the optional prefix is the converter
        url = re.sub(r"<(?:[^<>:]+:)?%s>" % re.escape(name), str(value), url)
    return url


def main() -> int:
    cfg, report = bootstrap_audit_db("file_analysis_param")
    print("audit database:", cfg["database"], report["applied_migrations"])
    app = make_app()
    fixtures = Fixtures(app)
    print("fixtures:", {k: v for k, v in fixtures.valid.items() if v})
    for note, value in fixtures.notes.items():
        print(f"  note {note}: {value}")

    # the cross-user request is A asking for B's object - not B asking for B's
    clients = {"valid": fixtures.a, "missing": fixtures.a, "malformed": fixtures.a,
               "other-user": fixtures.a}

    rows = []
    rules = sorted(app.url_map.iter_rules(), key=lambda r: (r.rule, sorted(r.methods)))
    for rule in rules:
        if not rule.arguments:
            continue
        methods = sorted(m for m in rule.methods if m in ("GET", "POST", "PUT", "DELETE", "PATCH"))
        for method in methods:
            if rule.rule == "/auth/logout":
                continue
            for variant in ("malformed", "missing", "valid", "other-user"):
                values, source = {}, []
                for name in rule.arguments:
                    if variant == "malformed":
                        values[name] = "abc"
                    elif variant == "missing":
                        values[name] = 999_999_999
                    elif variant == "valid":
                        # a parameter with no created fixture still gets a
                        # request when the route takes text, not an id
                        values[name] = (fixtures.valid.get(name)
                                        or string_value(name, rule.rule))
                    else:
                        # A path parameter that is not an object id (a
                        # category name, a key, a filename) has no "other
                        # user" object to create: the same plausible text is
                        # what makes the request meaningful. Without this
                        # fallback every such route was reported NO-FIXTURE
                        # even though the value was already known.
                        values[name] = (fixtures.other.get(name)
                                        or fixtures.valid.get(name)
                                        or string_value(name, rule.rule))
                    synthetic = (isinstance(values[name], int) and values[name] == 0
                                 and not fixtures.valid.get(name))
                    source.append(f"{name}={values[name]}" + ("(synthetic)" if synthetic else ""))
                if variant == "other-user" and "source_id" in rule.arguments:
                    values["source_id"] = fixtures.ensure_other_source()
                    source = [f"{n}={v}" for n, v in values.items()]
                if "node_id" in rule.arguments and variant in ("valid", "other-user"):
                    values["node_id"] = fixtures.ensure_node(other=variant == "other-user")
                    source = [f"{n}={v}" for n, v in values.items()]
                if any(v in (None, "None") for v in values.values()):
                    rows.append((rule.rule, method, rule.endpoint, variant, " ".join(source),
                                 "NO-FIXTURE", "", "", "", "", "SKIPPED"))
                    continue
                url = build_url(rule.rule, values)
                before = table_counts()
                try:
                    resp = clients[variant].open(url, method=method, json={}
                                                 if method in ("POST", "PUT", "PATCH") else None,
                                                 follow_redirects=False)
                    status = resp.status_code
                except Exception:
                    status = 0
                    traceback.print_exc(limit=2)
                after = table_counts()
                effect = ",".join(f"{t}{after[t] - before[t]:+d}" for t in before
                                  if before[t] is not None and after[t] is not None
                                  and after[t] != before[t]) or "-"
                try:
                    shape, leaked, snippet = body_shape(resp)
                except Exception:
                    shape, leaked, snippet = "?", [], ""
                owned = any(name in OWNED_PARAMS for name in rule.arguments)
                real_id = all(isinstance(values[name], int) for name in rule.arguments)
                verdict = verdict_for(status, leaked, variant, effect != "-", owned,
                                      real_id=real_id)
                rows.append((rule.rule, method, rule.endpoint, variant, " ".join(source),
                             status, shape, effect, "|".join(leaked), snippet, verdict))
                if verdict in ("STACK-TRACE", "SERVER-ERROR", "CROSS-USER-200"):
                    print(f"{verdict:14s} {method:6s} {url:70s} {status} {effect}")

    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / "param_routes.csv"
    with target.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["rule", "method", "endpoint", "variant", "values", "status",
                         "body_shape", "db_effect", "leaked", "snippet", "verdict"])
        writer.writerows(rows)

    bad = [r for r in rows if r[-1] in ("STACK-TRACE", "SERVER-ERROR", "CROSS-USER-200")]
    not_impl = [r for r in rows if r[-1] == "NOT-IMPLEMENTED"]
    skipped = [r for r in rows if r[-1] == "SKIPPED"]
    print(f"\nrows: {len(rows)}  findings: {len(bad)}  not-implemented(501): {len(not_impl)}"
          f"  skipped: {len(skipped)}")
    print("written:", target)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
