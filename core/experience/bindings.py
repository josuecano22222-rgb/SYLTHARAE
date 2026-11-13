"""What the screens actually bind, and whether it really exists.

The Action Registry says what an action *is*. The screen declarations say where
an action is presented. Neither says whether pressing the control does anything
- and that gap is exactly how a page keeps a link to a route nobody built. The
Reprocess link on the file detail page pointed at ``/file/<id>/reprocess`` for
months; nothing served that path, and nothing noticed, because the link lived in
a template and the route list lived in Flask.

This module closes that gap by reading the markup and asking the application:

* **What each screen binds** (``scan``): every element carrying
  ``data-action`` / ``data-record-action`` / ``data-confirm-action``, together
  with the address it points at - an ``href`` literal, a ``url_for('endpoint')``
  call, or the ``data-record-endpoint`` a prepared action surface renders. No
  application needed, so a unit test and the audit document can both use it.
* **What each screen names in words** (``scan``, ``kind="literal"``): an action
  written as a macro argument (``action='jobs.cancel'``) or as a string in a
  page script (``if (actionId === 'files.delete')``). A control that reaches a
  route through a page function is still a binding, and a page can name an
  action that way without any attribute ever appearing in markup.
* **What a screen prepares** (``scan``, ``kind="prepared"``): an action id
  named in the file a screen declares as its binding source - the Python that
  hands a prepared surface to a template. The record actions of the file library
  live there and nowhere else, which is the point of declaring the source.
* **Whether that address exists** (``check``): every literal is resolved against
  ``app.url_map``. A link to a route that is not registered is reported as a
  problem, by file and line, because a control that answers with 404 is worse
  than no control at all.

It also reports the two directions that go wrong quietly:

* an action id in markup that the registry does not know (a rename left half
  done), and
* a registered action an interface's markup binds that the registry does not
  attribute to that interface.

Nothing here performs anything, and nothing here decides whether an action is
allowed: the server still authorises every request. This is a check about
whether the control a reader can see leads anywhere.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = PROJECT_ROOT / "templates"
PAGE_SCRIPTS = PROJECT_ROOT / "static" / "js" / "pages"

#: Attributes that name an action. `data-action` is the toolbar's, the record
#: attribute is the surface's, and the confirm attribute is the dialog's own
#: record of what it is asking about.
ACTION_ATTRIBUTES: Tuple[str, ...] = (
    "data-action", "data-record-action", "data-confirm-action",
)

#: The files a binding can live in. Hand-written markup and the page scripts
#: that drive them; not the shared components, which render whatever they are
#: handed and therefore bind nothing themselves.
SKIP_PARTS = ("components/",)

# A page also uses ``data-action`` for local dispatch keys (``edit``, ``view``
# and similar). Only the namespaced form is an Action Registry id, so counting
# those local keys as bindings would inflate coverage and make an action look
# bound when the registry cannot name it.
_ACTION_ATTR = re.compile(
    r"data-(?:action|record-action|confirm-action)\s*=\s*"
    r"[\"'](?P<id>[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*)[\"']")
_HREF_LITERAL = re.compile(r"href\s*=\s*[\"'](?P<url>/[^\"'{]*)[\"']")
_URL_FOR = re.compile(r"url_for\(\s*[\"'](?P<endpoint>[\w.]+)[\"']")
_RECORD_ENDPOINT = re.compile(
    r"data-record-endpoint\s*=\s*[\"'](?P<url>[^\"'{]*)[\"']")
_VARIABLE_HREF = re.compile(
    r"href\s*=\s*[\"'](?P<url>[^\"']*\{\{[^\"']*)[\"']")

#: An action named as a value rather than as an attribute: a macro argument in
#: a template (``confirm_dialog(..., action='jobs.cancel')``), or a string in a
#: page script that drives the control. Restricted to ids the registry knows, so
#: a MIME type in a JavaScript file can never be mistaken for an action.
_NAMED_VALUE = re.compile(
    r"(?<![\w-])(?:action|confirm_action)\s*=\s*[\"'](?P<id>[a-z][a-z_]*\.[a-z][a-z_]*)[\"']")
_QUOTED_ID = re.compile(r"[\"'](?P<id>[a-z][a-z_]*\.[a-z][a-z_]*)[\"']")
_ATTRIBUTE_VALUE = re.compile(r"data-[\w-]+\s*=\s*$")

#: A literal that is built from a template variable - ``/file/{{ file[0] }}/x``
#: - cannot be matched as a string. It is reported with its shape so a reviewer
#: can see it, and the route check turns ``{{ ... }}`` into a wildcard segment.
_PLACEHOLDER = re.compile(r"\{\{.*?\}\}")


def _element(text: str, start: int, end: int) -> str:
    """The whole tag an attribute sits in.

    The address of an action lives in the same element as the attribute that
    names the action, and elements are written across several lines - searching
    only forward from the attribute is how the Reprocess link's ``href`` was
    missed in the first place.
    """
    open_at = text.rfind("<", 0, start)
    close_at = text.find(">", end)
    if open_at == -1 or close_at == -1:
        return text[start:end]
    return text[open_at:close_at + 1]


def _relative(path: Path) -> str:
    """A path a report can name, whether or not it is in this repository.

    A test may scan a file it wrote in a temporary directory; a report that
    raises on it is a report nobody can use.
    """
    try:
        return str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def _context(text: str, start: int, limit: int = 300) -> str:
    """A short, single-line excerpt around a hit, for a report a human reads."""
    return " ".join(text[max(0, start - 120):start + limit].split())[:300]


def _source_files() -> List[Path]:
    files = [p for p in sorted(TEMPLATES.rglob("*.html"))
             if not any(part in str(p.relative_to(PROJECT_ROOT)).replace("\\", "/")
                        for part in SKIP_PARTS)]
    files += sorted(PAGE_SCRIPTS.glob("*.js"))
    return files


def _binding_sources() -> List[Path]:
    """The non-template files the screens declare as their binding sources."""
    try:
        from .declarations import BINDING_SOURCES

        return [PROJECT_ROOT / relative
                for relatives in BINDING_SOURCES.values() for relative in relatives]
    except Exception:  # pragma: no cover - a deliberately quiet fallback
        return []


def _known_ids() -> Tuple[str, ...]:
    """The registry's ids, asked for lazily so this module stays importable
    while the registry is still being built (and so a bare script can import a
    single function without a catalog)."""
    try:
        from .action_registry import registered_ids

        return tuple(registered_ids())
    except Exception:  # pragma: no cover - a deliberately quiet fallback
        return ()


def scan(files: Optional[Iterable[Path]] = None,
         known_actions: Optional[Iterable[str]] = None,
         ) -> List[Dict[str, Any]]:
    """Every action a screen binds, with where it is and what it points at.

    Three kinds of hit are reported. ``kind="attribute"`` is a control that
    names its registry action in markup and carries (or builds) the address it
    goes to - this is the kind that can point at a route nobody served.
    ``kind="literal"`` is an action named as a value: a macro argument, or a
    string in the page script that drives the control. ``kind="prepared"`` is
    an action named by the Python file a screen declared as its binding source.
    The latter two have no address of their own, so they are reported but never
    called dangling. Generic dispatch keys such as ``data-action="edit"`` are
    not registry action ids and are deliberately not counted.
    """
    known = set(known_actions) if known_actions is not None else set(_known_ids())
    prepared_files = set(_binding_sources())
    sources = list(files) if files is not None else (_source_files() + sorted(prepared_files))
    found: List[Dict[str, Any]] = []
    for path in sources:
        path = Path(path)
        text = path.read_text(errors="ignore")
        if not any(attribute in text for attribute in ACTION_ATTRIBUTES) \
                and not any(f'"{action_id}"' in text or f"'{action_id}'" in text
                            for action_id in known):
            continue
        relative = _relative(path)
        for match in _ACTION_ATTR.finditer(text):
            line_index = text[:match.start()].count("\n")
            element = _element(text, match.start(), match.end())
            href = _HREF_LITERAL.search(element)
            dynamic = _VARIABLE_HREF.search(element)
            endpoint = _URL_FOR.search(element)
            record = _RECORD_ENDPOINT.search(element)
            found.append({
                "action_id": match.group("id"),
                "kind": "attribute",
                "file": relative,
                "line": line_index + 1,
                "href": href.group("url") if href else None,
                "dynamic_href": (dynamic.group("url") if dynamic and not href
                                 else None),
                "endpoint": endpoint.group("endpoint") if endpoint else None,
                "record_endpoint": record.group("url") if record else None,
                "context": _context(text, match.start()),
            })
        named = list(_NAMED_VALUE.finditer(text))
        if path in prepared_files:
            # The screen declared this file as where its bindings are prepared.
            # An action id in it is a binding with no address of its own: the
            # operation it names is the prepared surface's business.
            prepared = _QUOTED_ID.finditer(text)
            for match in prepared:
                action_id = match.group("id")
                if action_id not in known:
                    continue
                found.append({
                    "action_id": action_id,
                    "kind": "prepared",
                    "file": relative,
                    "line": text[:match.start()].count("\n") + 1,
                    "href": None, "dynamic_href": None, "endpoint": None,
                    "record_endpoint": None,
                    "context": _context(text, match.start()),
                })
        if path.suffix == ".js":
            named += [m for m in _QUOTED_ID.finditer(text)
                      if not _ATTRIBUTE_VALUE.search(text[:m.start()])]
        for match in named:
            action_id = match.group("id")
            if action_id not in known:
                continue
            # A name inside an attribute value has already been reported by the
            # attribute scan; reporting it twice would double every count.
            if _ATTRIBUTE_VALUE.search(text[:match.start()]):
                continue
            found.append({
                "action_id": action_id,
                "kind": "literal",
                "file": relative,
                "line": text[:match.start()].count("\n") + 1,
                "href": None,
                "dynamic_href": None,
                "endpoint": None,
                "record_endpoint": None,
                "context": _context(text, match.start()),
            })
    return found


def by_action(bindings: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, List[Dict[str, Any]]]:
    """The same scan, grouped by action id."""
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for item in (bindings if bindings is not None else scan()):
        grouped.setdefault(item["action_id"], []).append(item)
    return grouped


class RouteMatcher:
    """Whether an address is one the application actually serves.

    Built either from the application (``from_rules``, which is what the test
    suite uses so the answer is Flask's own) or from an arbitrary rule list, so
    the audit document can be generated where no application is running. Both
    turn a rule into a pattern and a template variable into a path segment:
    ``/file/{{ file[0] }}/reprocess`` is compared as a real path, which is the
    only way a link to a route nobody built can be caught before a reader
    follows it.
    """

    def __init__(self, patterns: Sequence[re.Pattern]) -> None:
        self._patterns = tuple(patterns)

    @staticmethod
    def _compile(rule: str) -> re.Pattern:
        parts = []
        for chunk in re.split(r"(<[^>]+>)", rule.rstrip("/") or "/"):
            if not chunk:
                continue
            parts.append("[^/]+" if chunk.startswith("<") else re.escape(chunk))
        return re.compile("^" + "".join(parts) + "/?$")

    @classmethod
    def from_rules(cls, rules: Iterable[Any]) -> "RouteMatcher":
        return cls([cls._compile(rule.rule) for rule in rules])

    def match(self, url: str) -> bool:
        if not url:
            return False
        concrete = _PLACEHOLDER.sub("1", url).split("?")[0].rstrip("/") or "/"
        return any(pattern.match(concrete) for pattern in self._patterns)


def _matcher(rules: Any) -> Any:
    """Accept either a matcher or the application's own rule list."""
    if rules is None:
        return RouteMatcher(())
    if hasattr(rules, "match") and not hasattr(rules, "rule"):
        return rules
    return RouteMatcher.from_rules(rules)


def check(rules: Iterable[Any],
          bindings: Optional[Sequence[Dict[str, Any]]] = None,
          known_actions: Optional[Iterable[str]] = None,
          ) -> Dict[str, List[Dict[str, Any]]]:
    """Compare what the screens bind with what the application serves.

    ``rules`` is ``app.url_map.iter_rules()``. Two questions are asked, and both
    of them are about the reader rather than about authorisation:

    * **Does the control lead anywhere?** Every literal href, every
      ``url_for`` endpoint and every prepared ``data-record-endpoint`` is
      resolved against the application's own rules. A binding that resolves to
      nothing is reported with its file and line.
    * **Is the action still an action?** An id in markup that the registry does
      not know is a rename somebody did not finish.
    """
    items = list(bindings if bindings is not None else scan())
    serve = _matcher(rules)
    # A caller may hand over the application's rule list or an already-built
    # matcher; both are supported, so the endpoints are collected only when the
    # rules are the real thing.
    endpoints = ({rule.endpoint for rule in rules}
                 if rules is not None and not hasattr(rules, "match")
                 else set())
    known = set(known_actions or ())

    dangling: List[Dict[str, Any]] = []
    unknown: List[Dict[str, Any]] = []

    for item in items:
        if item.get("kind") in ("literal", "prepared"):
            # A name carries no address of its own: the page script or the
            # Python that prepares the surface holds it. It can still be a name
            # the registry has forgotten, which is the second question below.
            if known and item["action_id"] not in known:
                unknown.append(item)
            continue
        address = (item.get("href") or item.get("dynamic_href")
                   or item.get("record_endpoint"))
        if address and not address.startswith("{{") and not serve.match(address):
            dangling.append(item)
        if item.get("endpoint") and item["endpoint"] not in endpoints:
            dangling.append(item)
        if known and item["action_id"] not in known:
            unknown.append(item)

    return {
        "bindings": items,
        "dangling": dangling,
        "unknown_actions": unknown,
    }


def unattributed(bindings: Sequence[Dict[str, Any]],
                 templates_by_interface: Dict[str, Sequence[str]],
                 interfaces_by_action: Dict[str, Sequence[str]],
                 ) -> List[Dict[str, Any]]:
    """Bindings on a screen that the registry does not attribute the action to.

    ``templates_by_interface`` is the mapping of interface -> its template
    files (the audit owns it, because the audit is what knows which screen a
    file belongs to). A hit means one of the two is out of date: either the
    screen declaration lists an action for another interface, or the registry's
    ``interfaces`` tuple is missing this one.
    """
    owner_of_file: Dict[str, List[str]] = {}
    for interface_id, files in templates_by_interface.items():
        for relative in files:
            owner_of_file.setdefault(relative, []).append(interface_id)

    reported: List[Dict[str, Any]] = []
    for item in bindings:
        for interface_id in owner_of_file.get(item["file"], ()):
            if item["action_id"] not in interfaces_by_action.get(item["action_id"], ()):
                reported.append({**item, "interface_id": interface_id})
    return reported


def counts(bindings: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, int]:
    """Machine-generated totals for the audit document."""
    items = list(bindings if bindings is not None else scan())
    return {
        "bindings": len(items),
        "attributes": sum(1 for item in items
                          if item.get("kind", "attribute") == "attribute"),
        "literals": sum(1 for item in items if item.get("kind") == "literal"),
        "prepared": sum(1 for item in items if item.get("kind") == "prepared"),
        "actions_bound": len({item["action_id"] for item in items}),
        "files": len({item["file"] for item in items}),
        "with_href": sum(1 for item in items if item.get("href")),
        "with_endpoint": sum(1 for item in items
                            if item.get("endpoint") or item.get("record_endpoint")),
        "dynamic": sum(1 for item in items if item.get("dynamic_href")),
    }


__all__ = [
    "ACTION_ATTRIBUTES",
    "by_action",
    "RouteMatcher",
    "check",
    "counts",
    "scan",
    "unattributed",
]
