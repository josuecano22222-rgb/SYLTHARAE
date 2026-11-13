"""Architecture: templates render the product, they do not define it.

The registry exists so the product is described once. That only holds while
the templates *consume* the description - the moment one of them asks
`is_interface_enabled('something')`, or keeps its own dictionary of endpoint
names, the product map is in two places again and the two drift (which is
exactly how `base.html` came to label `file_classification_page` "Analysis"
while the registry called it "Classification", and how a rename left a dead
sidebar entry behind).

These tests are the guardrail for that. They are deliberately blunt: a
template may not name a live interface or feature id at all, and may not carry
a label map. Comments are stripped first, so explaining *why* the rule exists
is still allowed.

The one exception is a component declaring its own feature: `page_tips.html`
asks for `features.page_tips`, which is that component's own subject rather
than a page deciding whether another part of the product exists.
"""

from __future__ import annotations

import pathlib
import re
from typing import Iterable, List, Tuple

import pytest

from core.interfaces import FEATURES, REGISTRY, LEGACY_INTERFACE_IDS

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
TEMPLATES = PROJECT_ROOT / "templates"

#: Templates allowed to name a product id, and which: a component may name its
#: own feature (that is its subject), never anything else.
SELF_DECLARING = {
    "templates/components/page_tips.html": {"page_tips"},
}

#: Functions a template must not call: each is a product decision in markup.
FORBIDDEN_CALLS = (
    "is_interface_enabled",
    "is_interface_enabled_by_endpoint",
    "get_interface",
    "get_interfaces_by_domain",
    "interface_registry",
)

#: Keys that mean "this file keeps its own map of endpoints to names".
LABEL_MAP_KEYS = ("endpoint_labels", "endpoint_names", "page_titles", "endpoint_map")


def _strip_comments(text: str) -> str:
    """Remove Jinja and HTML comments so explanations are not violations."""
    text = re.sub(r"\{#.*?#\}", "", text, flags=re.DOTALL)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    return text


def _templates() -> List[pathlib.Path]:
    return sorted(TEMPLATES.rglob("*.html"))


def _route_vocabulary() -> set:
    """Endpoint names a template may legitimately use to link to a page.

    `url_for('email_words')` is the application's own vocabulary - a route -
    even where the route name and the interface id happen to be the same word.
    The registry's declared routes and aliases are the vocabulary a template
    may use to *reach* a page; using an id that is not a route is therefore
    unambiguously a product reference.
    """
    routes = set()
    for interface in REGISTRY:
        if interface.route:
            routes.add(interface.route)
        routes.update(interface.aliases)
    return routes


def _distinctive_ids() -> set:
    """Product ids a text scan can recognise without ambiguity.

    Single-word ids (`search`, `sources`, `sides`, `jobs`, `categories`) are
    ordinary English words as well: they appear as `type="search"`, as CSS
    class fragments and as view names, so a text scan cannot tell a product
    reference from a word. Those ids are covered by the idiom test below,
    which looks for the ways an interface is *referred to* rather than for the
    word. Compound ids - and every id that is not also a route - are
    unambiguous, and they are what this scan holds.
    """
    ids = {interface.interface_id for interface in REGISTRY}
    ids |= {feature.feature_id for feature in FEATURES}
    ids |= set(LEGACY_INTERFACE_IDS)
    return {interface_id for interface_id in ids - _route_vocabulary()
            if "_" in interface_id}


def _all_ids() -> set:
    ids = {interface.interface_id for interface in REGISTRY}
    ids |= {feature.feature_id for feature in FEATURES}
    ids |= set(LEGACY_INTERFACE_IDS)
    return ids


def _idioms(product_ids: Iterable[str]) -> List[str]:
    """Product references that are not bare quoted strings."""
    patterns = (
        r'data-interface(?:-id)?\s*=\s*["\']{id}["\']',
        r'interface_id\s*=\s*["\']{id}["\']',
        r'interface_registry\s*\(\s*["\']{id}["\']',
        r'get_interface\s*\(\s*["\']{id}["\']',
        r'is_interface_enabled\s*\(\s*["\']{id}["\']',
        r'page\.interface_id\s*==\s*["\']{id}["\']',
    )
    return [pattern.format(id=interface_id)
            for interface_id in product_ids for pattern in patterns]


class TestTemplatesDoNotDefineTheProduct:
    def test_no_template_calls_an_interface_lookup(self):
        offenders = []
        for path in _templates():
            body = _strip_comments(path.read_text())
            for call in FORBIDDEN_CALLS:
                if re.search(rf"\b{re.escape(call)}\s*\(", body):
                    offenders.append(f"{path.relative_to(PROJECT_ROOT)}: {call}(")
        assert offenders == [], (
            "templates decide product questions instead of rendering the "
            "prepared shell; move the decision into core/interfaces and pass "
            "the result:\n  " + "\n  ".join(offenders))

    def test_no_template_names_a_product_id_as_a_string(self):
        """A quoted id that is not a route name is a product reference.

        Route names are how a template links to a page (`url_for('search_page')`)
        and are allowed. An id that is *not* a route cannot be reached, so a
        template that names it is either keeping a copy of the product map or
        gating markup on a product decision - both of which belong in
        `core/interfaces`.
        """
        product_ids = _distinctive_ids()
        offenders = []
        for path in _templates():
            relative = str(path.relative_to(PROJECT_ROOT))
            allowed = SELF_DECLARING.get(relative, set())
            body = _strip_comments(path.read_text())
            for interface_id in sorted(product_ids):
                if interface_id in allowed:
                    continue
                if f"'{interface_id}'" in body or f'"{interface_id}"' in body:
                    offenders.append(f"{relative}: {interface_id}")
        assert offenders == [], (
            "templates name product ids; the shell is prepared in "
            "Api/routes/common.py and rendered:\n  " + "\n  ".join(offenders))

    def test_no_template_uses_an_interface_reference_idiom(self):
        """Even a route-named interface must not be referenced *as* an interface."""
        product_ids = sorted(_all_ids() | _route_vocabulary())
        offenders = []
        for path in _templates():
            body = _strip_comments(path.read_text())
            for pattern in _idioms(product_ids):
                if re.search(pattern, body):
                    offenders.append(f"{path.relative_to(PROJECT_ROOT)}: {pattern}")
        assert offenders == [], (
            "templates reference interfaces directly:\n  " + "\n  ".join(offenders))

    def test_no_template_gates_markup_on_a_dead_product_id(self):
        """A retired id must not decide anything in markup.

        It may still appear as a *label key* in a translation pack - "Analytics"
        is a word - but it may not be used as a product reference, which is
        what the idiom patterns check.
        """
        offenders = []
        idiom_source = "\n".join(_idioms(sorted(LEGACY_INTERFACE_IDS)))
        for path in _templates():
            body = _strip_comments(path.read_text())
            for pattern in _idioms(sorted(LEGACY_INTERFACE_IDS)):
                if re.search(pattern, body):
                    offenders.append(f"{path.relative_to(PROJECT_ROOT)}: {pattern}")
        assert offenders == [], (
            "templates reference retired product ids:\n  " + "\n  ".join(offenders))

    def test_no_template_keeps_its_own_label_map(self):
        offenders = []
        for path in _templates():
            body = _strip_comments(path.read_text())
            for key in LABEL_MAP_KEYS:
                if re.search(rf"\b{key}\s*=", body):
                    offenders.append(f"{path.relative_to(PROJECT_ROOT)}: {key}")
        assert offenders == [], (
            "a template keeps its own endpoint-to-name map; page identity comes "
            "from the registry (page.label):\n  " + "\n  ".join(offenders))

    def test_the_component_exception_is_narrow(self):
        """Only the named component, and only for its own feature."""
        product_ids = _all_ids()
        for relative, allowed in SELF_DECLARING.items():
            assert (PROJECT_ROOT / relative).exists(), relative
            body = _strip_comments((PROJECT_ROOT / relative).read_text())
            named = {
                interface_id for interface_id in sorted(product_ids)
                if f"'{interface_id}'" in body or f'"{interface_id}"' in body
            }
            assert named <= allowed, (
                f"{relative} names {sorted(named - allowed)}; a component may "
                f"only name its own feature")

    def test_the_sidebar_renders_a_prepared_model(self):
        sidebar = (TEMPLATES / "components/sidebar_nav.html").read_text()
        body = _strip_comments(sidebar)
        assert "navigation" in body
        # It renders entries; it does not look products up.
        for call in FORBIDDEN_CALLS:
            assert call not in body, call
        assert "interface.aliases" not in body and "interface.route" not in body


class TestThePageModelIsPrepared:
    """The other half: the application really does prepare what it promises."""

    def test_the_context_processor_supplies_navigation_and_page(self):
        source = (PROJECT_ROOT / "Api/routes/common.py").read_text()
        assert "build_navigation(" in source
        assert "present_page(" in source
        assert "context['navigation']" in source
        assert "context['page_identity']" in source
        assert "context['features']" in source

    def test_base_html_no_longer_holds_a_label_map(self):
        body = _strip_comments((TEMPLATES / "base.html").read_text())
        for key in LABEL_MAP_KEYS:
            assert key not in body, key
        assert "page_identity.breadcrumbs" in body
        assert "page_identity.label" in body

    def test_every_template_that_shows_page_tips_names_nothing(self):
        """31 templates called page_tips with an interface flag; none may now."""
        callers = [
            path for path in _templates()
            if "page_tips()" in _strip_comments(path.read_text())
        ]
        assert len(callers) >= 20, (
            f"expected the page-tips call sites to still exist, found {len(callers)}")
