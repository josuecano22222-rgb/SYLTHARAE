"""The endpoint inventory, generated from the application itself.

Counts of "how many pages does SYLTHARAE have" must not be maintained by hand;
they go stale the moment somebody adds a route, and a stale count is how the
productization map ended up disagreeing with itself. This module asks Flask.

``build_inventory(app)`` walks ``app.url_map.iter_rules()`` and classifies every
endpoint into exactly one bucket:

``USER_INTERFACE``
    A page a person navigates to. Must be owned by an interface in the
    registry - the coverage test fails on an unowned one, and the request gate
    refuses to serve it.
``INTERNAL_PAGE``
    A page the product needs but does not advertise (diagnostics).
``SYSTEM_ENDPOINT``
    Infrastructure: authentication, setup, health, cookies, locale, favicon.
    Never gated by an interface switch and never in navigation.
``ACTION``
    A non-GET endpoint on the application's own pages (delete, upload control).
    Owned by the interface whose page triggers it.
``REDIRECT``
    A route that only exists to keep an old address working.
``API_ENDPOINT``
    A programming surface, not a page (normally under ``/api/...``; a small
    explicit set of legacy JSON routes lives outside that prefix).

The classification is deterministic and its exception lists are declared here,
in the open, so "internal" is a decision somebody made rather than a bucket
things fall into. Endpoints classified as USER_INTERFACE must all be owned; the
rest are allowed to be unowned, because they are not part of the product
surface.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .registry import REGISTRY, endpoints_to_interfaces


class EndpointClass(str, Enum):
    """What kind of thing an endpoint is."""

    USER_INTERFACE = "USER_INTERFACE"
    INTERNAL_PAGE = "INTERNAL_PAGE"
    SYSTEM_ENDPOINT = "SYSTEM_ENDPOINT"
    ACTION = "ACTION"
    REDIRECT = "REDIRECT"
    API_ENDPOINT = "API_ENDPOINT"
    TEST_ENDPOINT = "TEST_ENDPOINT"

    def __str__(self) -> str:
        return self.value


#: Path prefixes registered by the test suite itself (``tests/conftest.py``
#: adds a route under ``/_test/`` to prove the error pipeline sanitizes). They
#: are scaffolding, not product surface, so the inventory classifies them
#: separately and the coverage rule does not apply to them.
TEST_ENDPOINT_PREFIXES: tuple = ("/_test/",)

#: Endpoints that are infrastructure: they are how the application works, not
#: something an operator opens. Declared explicitly so the coverage test can
#: tell "deliberately not an interface" from "somebody forgot".
SYSTEM_ENDPOINTS: frozenset = frozenset({
    "auth.login", "auth.login_page", "auth.logout", "auth.me",
    "auth.change_password", "auth.first_admin_page", "auth.first_admin_create",
    "setup.setup_page", "setup.system_check", "setup.test_database",
    "setup.run_installation", "setup.check_setup_status",
    "health.health", "favicon", "set_language", "get_csrf_token",
    "static",
})

#: Pages kept for diagnostics: reachable, deliberately unadvertised.
INTERNAL_PAGE_ENDPOINTS: frozenset = frozenset({
    "concurrency.dashboard",
})

#: Routes that exist only so an old address keeps working. Verified by the
#: inventory test: each of these must answer with a redirect.
REDIRECT_ENDPOINTS: frozenset = frozenset({
    "files.upload_page",  # /upload -> /operations/input
})

#: JSON contracts that predate the conventional ``/api`` prefix. Their names
#: are explicit because method/path heuristics would mislabel these GET
#: endpoints as human-navigable pages.
JSON_ENDPOINTS: frozenset = frozenset({
    "files.get_active_tasks",  # /upload/active-tasks
    "files.upload_progress",   # /upload/progress/<task_id>
})


@dataclass(frozen=True)
class EndpointRecord:
    """One endpoint of the application, as the application describes it."""

    endpoint: str
    rule: str
    methods: Tuple[str, ...]
    blueprint: str
    module: str
    classification: EndpointClass
    owned_by: Optional[str]
    internal: bool

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["methods"] = list(self.methods)
        data["classification"] = str(self.classification)
        return data


def _blueprint_of(endpoint: str) -> str:
    return endpoint.split(".", 1)[0] if "." in endpoint else "(app)"


def classify_endpoint(endpoint: str, rule: str, methods: Sequence[str]) -> EndpointClass:
    """Which bucket does this endpoint belong in?

    Order matters: an endpoint is an API endpoint or a system endpoint before
    it is a page, and only endpoints that survive those two tests are considered
    part of the product surface.
    """
    if endpoint in SYSTEM_ENDPOINTS:
        return EndpointClass.SYSTEM_ENDPOINT
    if any(rule.startswith(prefix) for prefix in TEST_ENDPOINT_PREFIXES):
        return EndpointClass.TEST_ENDPOINT
    # Not just "/api" at the front: diagnostics blueprints serve JSON from their
    # own subtree (/concurrency/api/...), and a programming surface is not a
    # page wherever it is mounted.
    if rule.startswith("/api") or "/api/" in rule or endpoint in JSON_ENDPOINTS:
        return EndpointClass.API_ENDPOINT
    if endpoint in INTERNAL_PAGE_ENDPOINTS:
        return EndpointClass.INTERNAL_PAGE
    if "GET" not in methods:
        return EndpointClass.ACTION
    return EndpointClass.USER_INTERFACE


def is_infrastructure_endpoint(endpoint: Optional[str], path: str = "") -> bool:
    """Is this an endpoint the interface switches never govern?

    Authentication, setup, health, the locale switcher, the favicon and every
    ``/api`` subtree. The request gate asks this first: these are how the
    application works, not something an operator can switch off, and gating
    them would lock people out of the settings screen that would fix it.
    """
    if endpoint and endpoint in SYSTEM_ENDPOINTS:
        return True
    if path.startswith("/api") or "/api/" in path:
        return True
    if any(path.startswith(prefix) for prefix in TEST_ENDPOINT_PREFIXES):
        return True
    return False


def build_inventory(app, include_api: bool = True) -> List[EndpointRecord]:
    """Every endpoint the application serves, classified.

    Deterministic: sorted by endpoint name, so two runs - and two machines -
    produce the same list.
    """
    owners = endpoints_to_interfaces()
    records: List[EndpointRecord] = []

    for rule in app.url_map.iter_rules():
        endpoint = rule.endpoint
        if endpoint == "static":
            continue
        methods = tuple(sorted(rule.methods - {"HEAD", "OPTIONS"}))
        if not methods:
            continue
        path = str(rule)
        if path.startswith("/static"):
            continue

        classification = classify_endpoint(endpoint, path, methods)
        if endpoint in REDIRECT_ENDPOINTS:
            classification = EndpointClass.REDIRECT
        if classification == EndpointClass.TEST_ENDPOINT:
            # Scaffolding the suite registers on the shared app (see
            # TEST_ENDPOINT_PREFIXES). It is classified so the coverage rule can
            # tell it apart, but it is not shipped surface: listing it made the
            # committed inventory depend on which test modules had run, which is
            # exactly the drift the generated file exists to prevent.
            continue
        if classification == EndpointClass.API_ENDPOINT and not include_api:
            continue

        view = app.view_functions.get(endpoint)
        records.append(EndpointRecord(
            endpoint=endpoint,
            rule=path,
            methods=methods,
            blueprint=_blueprint_of(endpoint),
            module=(getattr(view, "__module__", "") or "").rsplit(".", 1)[-1] if view else "",
            classification=classification,
            owned_by=owners.get(endpoint),
            internal=classification in (EndpointClass.SYSTEM_ENDPOINT,
                                        EndpointClass.INTERNAL_PAGE,
                                        EndpointClass.API_ENDPOINT),
        ))

    records.sort(key=lambda r: r.endpoint)
    return records


def page_records(records: Iterable[EndpointRecord]) -> List[EndpointRecord]:
    """The records that make up the product's page surface."""
    return [r for r in records
            if r.classification in (EndpointClass.USER_INTERFACE, EndpointClass.INTERNAL_PAGE)]


def unowned_user_interfaces(records: Iterable[EndpointRecord]) -> List[EndpointRecord]:
    """Pages a person can navigate to that no interface declares.

    This is the guardrail from the directive: a developer who adds a page and
    forgets to register it gets a failing test naming the endpoint.
    """
    return [r for r in page_records(records)
            if r.classification == EndpointClass.USER_INTERFACE and not r.owned_by]


def counts(records: Iterable[EndpointRecord]) -> Dict[str, int]:
    """How many endpoints of each class, plus how many are owned."""
    records = list(records)
    out: Dict[str, int] = {}
    for record in records:
        key = str(record.classification)
        out[key] = out.get(key, 0) + 1
    totals = {
        "TOTAL": len(records),
        "OWNED": sum(1 for r in records if r.owned_by),
        "USER_INTERFACE_OWNED": sum(
            1 for r in records
            if r.classification == EndpointClass.USER_INTERFACE and r.owned_by),
        "UNOWNED_USER_INTERFACE": len(unowned_user_interfaces(records)),
    }
    # Keep the summary keys out of the classification breakdown so a reader can
    # tell the six endpoint classes apart from the totals.
    out = {**out, **totals}
    return dict(sorted(out.items()))


def registry_coverage(records: Iterable[EndpointRecord]) -> Dict[str, Any]:
    """How much of the application the registry accounts for.

    Generated, never recorded by hand - the productization map quotes this
    function rather than a number somebody typed.
    """
    records = list(records)
    counts_by_class = counts(records)
    pages = page_records(records)
    return {
        "endpoints_total": counts_by_class["TOTAL"],
        "endpoints_owned": counts_by_class["OWNED"],
        "page_endpoints": len(pages),
        "page_endpoints_owned": sum(1 for r in pages if r.owned_by),
        "unowned_user_interface": [r.endpoint for r in unowned_user_interfaces(records)],
        "by_classification": {
            k: v for k, v in counts_by_class.items()
            if k in {str(c) for c in EndpointClass}
        },
        "interfaces_declared": len(REGISTRY),
        "interfaces_with_route": sum(1 for i in REGISTRY if i.route),
    }


def inventory_json(app, indent: Optional[int] = 2) -> str:
    """The inventory as JSON, for the administrative view and for diffing."""
    return json.dumps([r.to_dict() for r in build_inventory(app)], indent=indent)
