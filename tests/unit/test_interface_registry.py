"""Unit: the interface registry describes the product, and is checkable.

The registry replaces an untyped metadata dictionary that lived inside the
settings adapter. That dictionary listed an endpoint that did not exist, a
switch with no page at all, and two entries for the same page - and nothing
could tell, because there was no rule to break.

These tests are the rule. They cover the model and the validation, using
synthetic registries so that each rule can be seen to fire; the real registry
is checked against the live application in
``tests/integration/test_interface_coverage.py``.
"""

from __future__ import annotations

import pathlib

import pytest

from core.interfaces import (
    Domain,
    Feature,
    Interface,
    InterfaceKind,
    InterfaceStatus,
    REGISTRY,
    build_inventory,
    classify_endpoint,
    defaults,
    domain_label,
    get_all_interfaces,
    get_dependencies,
    get_dependents,
    get_interface,
    get_interface_for_endpoint,
    get_interfaces_by_domain,
    is_registered,
    resolve_interface_id,
    summary,
    unowned_user_interfaces,
    validate_registry,
)
from core.interfaces.inventory import EndpointClass


def make(interface_id="thing", **kwargs):
    base = dict(
        interface_id=interface_id,
        name="Thing",
        description="Does a thing.",
        domain=Domain.DISCOVER,
        route="thing_page",
        icon="bi-box",
    )
    base.update(kwargs)
    return Interface(**base)


class TestModel:
    def test_id_is_independent_of_the_label(self):
        """Renaming what a person sees must not touch the stored key."""
        renamed = make(interface_id="batch_analysis", name="Batch Analysis")
        assert renamed.interface_id == "batch_analysis"
        assert renamed.name == "Batch Analysis"

    def test_endpoints_include_aliases(self):
        entry = make(aliases=("thing_old_page", "thing_action"))
        assert entry.endpoints == ("thing_page", "thing_old_page", "thing_action")

    def test_a_section_has_no_page(self):
        section = make(route=None, kind=InterfaceKind.SECTION)
        assert section.endpoints == ()
        assert section.navigable is False

    def test_retired_interfaces_are_not_navigable(self):
        entry = make(status=InterfaceStatus.RETIRED)
        assert entry.live is False
        assert entry.navigable is False

    def test_domain_accepts_an_identifier_string(self):
        entry = make(domain="ANALYZE")
        assert entry.domain is Domain.ANALYZE
        assert domain_label(entry.domain) == "Analyze"


class TestIntegrity:
    """Each rule in ``validate_registry`` fires when it should."""

    def codes(self, interfaces, **kwargs):
        return {issue.code for issue in validate_registry(interfaces, **kwargs)}

    def test_duplicate_ids_are_rejected(self):
        assert "duplicate_id" in self.codes([make(), make()])

    def test_invalid_id_is_rejected(self):
        codes = self.codes([make(interface_id="Not An Id")])
        assert "invalid_id" in codes

    def test_missing_name_or_description_is_rejected(self):
        codes = self.codes([make(name=""), make(interface_id="other", description="")])
        assert {"missing_name", "missing_description"} <= codes

    def test_unknown_role_is_rejected(self):
        assert "invalid_role" in self.codes([make(required_role="superuser")])

    def test_unknown_endpoint_is_rejected(self):
        """A registry entry that names a page the application does not serve."""
        issues = validate_registry([make(route="does_not_exist")],
                                   known_endpoints={"thing_page"})
        assert any(i.code == "unknown_endpoint" and "does_not_exist" in i.message
                   for i in issues)

    def test_unknown_dependency_is_rejected(self):
        codes = self.codes([make(dependencies=("does_not_exist",))])
        assert "unknown_dependency" in codes

    def test_self_dependency_is_rejected(self):
        codes = self.codes([make(dependencies=("thing",))])
        assert "self_dependency" in codes

    def test_dependency_cycle_is_rejected(self):
        a = make("a", route="a_page", dependencies=("b",))
        b = make("b", route="b_page", dependencies=("c",))
        c = make("c", route="c_page", dependencies=("a",))
        issues = validate_registry([a, b, c])
        cycle = [i for i in issues if i.code == "dependency_cycle"]
        assert cycle, "a -> b -> c -> a must be reported"
        assert "a -> b -> c -> a" in cycle[0].message or "->" in cycle[0].message

    def test_two_interfaces_may_not_own_one_endpoint(self):
        codes = self.codes([make("a"), make("b")])
        assert "duplicate_endpoint" in codes

    def test_aliases_are_the_declared_way_to_share_an_endpoint(self):
        """Everything stays in one interface; that is not duplication."""
        entry = make("a", aliases=("a_old_page",))
        assert self.codes([entry]) == set()

    def test_shortcut_collisions_are_rejected(self):
        codes = self.codes([
            make("a", keyboard_shortcut="g s"),
            make("b", route="b_page", keyboard_shortcut="g s"),
        ])
        assert "shortcut_collision" in codes

    def test_page_interfaces_must_name_a_route(self):
        assert "page_without_route" in self.codes([make(route=None)])

    def test_experimental_interfaces_must_name_their_flag(self):
        codes = self.codes([make(status=InterfaceStatus.EXPERIMENTAL)])
        assert "experimental_without_flag" in codes
        ok = self.codes([make(status=InterfaceStatus.EXPERIMENTAL,
                              feature_flag="relationship_graph_v2")])
        assert "experimental_without_flag" not in ok

    def test_unknown_setting_reference_is_rejected(self):
        issues = validate_registry([make(settings=("nope.missing",))],
                                   known_settings={"processing.max_workers"})
        assert any(i.code == "unknown_setting_reference" for i in issues)

    def test_malformed_setting_reference_is_rejected(self):
        assert "invalid_setting_reference" in self.codes([make(settings=("max_workers",))])

    def test_a_clean_registry_reports_nothing(self):
        assert validate_registry([make()]) == []


class TestTheRealRegistry:
    """Facts about the registry this repository actually ships."""

    def test_ids_are_unique_and_machine_safe(self):
        ids = [i.interface_id for i in REGISTRY]
        assert len(ids) == len(set(ids))
        assert validate_registry(REGISTRY) == [] or all(
            i.severity == "warning" for i in validate_registry(REGISTRY))

    def test_every_interface_has_a_domain(self):
        for interface in REGISTRY:
            assert isinstance(interface.domain, Domain), interface.interface_id

    def test_domains_are_from_the_declared_set(self):
        allowed = {str(d) for d in Domain}
        grouped = get_interfaces_by_domain()
        assert set(grouped) <= allowed

    def test_summary_is_generated_not_recorded(self):
        data = summary()
        assert data["interfaces"] == len(REGISTRY)
        assert sum(data["by_domain"].values()) == len(REGISTRY)

    def test_dependencies_resolve_within_the_registry(self):
        for interface in REGISTRY:
            for dep in interface.dependencies:
                assert get_interface(dep) is not None, (interface.interface_id, dep)

    def test_the_known_dependency_pairs(self):
        assert "file_library" in get_dependencies("batch_analysis")
        dependents = {d.interface_id for d in get_dependents("file_library")}
        assert "batch_analysis" in dependents

    def test_exactly_one_owner_per_endpoint(self):
        seen = {}
        for interface in REGISTRY:
            for endpoint in interface.endpoints:
                assert endpoint not in seen, f"{endpoint} owned twice"
                seen[endpoint] = interface.interface_id

    def test_unknown_ids_are_not_registered(self):
        for unknown in ("nonsense", "file_upload", "analytics", "", "file_analysis"):
            assert is_registered(unknown) is False, unknown

    def test_legacy_ids_resolve_to_their_replacement(self):
        assert resolve_interface_id("file_analysis") == "archives"
        assert resolve_interface_id("upload_files") == "input_ingestion"
        assert resolve_interface_id("file_browser") == "file_library"
        assert resolve_interface_id("advanced_search") == "search"
        # A retired id that was merged resolves; one that was simply dropped
        # never switches anything on.
        assert resolve_interface_id("file_upload") == "input_ingestion"

    def test_defaults_come_from_the_registry_for_everything(self):
        decl = defaults()
        assert set(decl) == {i.interface_id for i in REGISTRY if i.live} | {
            f.feature_id for f in _features()}
        for interface in REGISTRY:
            assert decl[interface.interface_id] == interface.default_enabled

    def test_page_tips_is_a_feature_not_an_interface(self):
        assert not any(i.interface_id == "page_tips" for i in REGISTRY)
        from core.interfaces import get_feature

        assert get_feature("page_tips") is not None

    def test_the_retired_duplicate_is_gone(self):
        assert not any(i.interface_id == "file_upload" for i in REGISTRY)

    def test_one_ingestion_interface(self):
        owners = [i.interface_id for i in get_all_interfaces()
                  if "operations_input_page" in i.endpoints]
        assert owners == ["input_ingestion"]

    def test_registry_holds_no_second_copy_of_anything(self):
        """It refers to routes, roles, settings; it does not restate them."""
        for interface in REGISTRY:
            for ref in interface.settings:
                assert "." in ref and "=" not in ref
            if interface.required_role is not None:
                assert interface.required_role in {"viewer", "analyst", "admin"}


def _features():
    from core.interfaces import get_features

    return get_features()


class SyntheticApp:
    """The smallest thing ``build_inventory`` can walk."""

    class _Rule:
        """Mirrors what ``build_inventory`` reads from a real Flask rule."""

        def __init__(self, endpoint, rule, methods):
            self.endpoint = endpoint
            self.rule = rule
            self.methods = set(methods) | {"HEAD", "OPTIONS"}

        def __str__(self):
            return self.rule

    def __init__(self, rules):
        self.url_map = type("Map", (), {"iter_rules": lambda _self: iter(rules)})()
        self.view_functions = {r.endpoint: (lambda: None) for r in rules}


def _rule(endpoint, rule, methods=("GET",)):
    return SyntheticApp._Rule(endpoint, rule, methods)


class TestInventory:
    """The inventory is generated from the application, never typed."""

    def test_classification_of_each_kind(self):
        assert classify_endpoint("health.health", "/health", ["GET"]) is EndpointClass.SYSTEM_ENDPOINT
        assert classify_endpoint("auth.login", "/auth/login", ["POST"]) is EndpointClass.SYSTEM_ENDPOINT
        assert classify_endpoint("some.api", "/api/things", ["GET"]) is EndpointClass.API_ENDPOINT
        assert classify_endpoint("diag.pools", "/concurrency/api/pools", ["GET"]) is EndpointClass.API_ENDPOINT
        assert classify_endpoint("files.get_active_tasks", "/upload/active-tasks", ["GET"]) is EndpointClass.API_ENDPOINT
        assert classify_endpoint("files.upload_progress", "/upload/progress/<task_id>", ["GET"]) is EndpointClass.API_ENDPOINT
        assert classify_endpoint("page", "/page", ["GET"]) is EndpointClass.USER_INTERFACE
        assert classify_endpoint("action", "/page/delete", ["POST"]) is EndpointClass.ACTION

    def test_a_new_page_that_nobody_registered_is_reported(self):
        """The guardrail: add a page, forget the registry, fail the build."""
        app = SyntheticApp([
            _rule("files.files_list", "/files"),
            _rule("brand_new_page", "/brand-new"),
        ])
        unowned = [r.endpoint for r in unowned_user_interfaces(build_inventory(app))]
        assert unowned == ["brand_new_page"]

    def test_infrastructure_is_not_reported_as_unowned(self):
        app = SyntheticApp([
            _rule("health.health", "/health"),
            _rule("auth.login_page", "/auth/login"),
            _rule("thing.api", "/api/thing"),
        ])
        assert unowned_user_interfaces(build_inventory(app)) == []

    def test_the_inventory_records_what_a_reader_needs(self):
        app = SyntheticApp([_rule("files.files_list", "/files")])
        record = build_inventory(app)[0]
        payload = record.to_dict()
        for key in ("endpoint", "rule", "methods", "blueprint", "module",
                    "classification", "owned_by", "internal"):
            assert key in payload, key
        assert payload["owned_by"] == "file_library"

class TestLegacyIdsStayOutOfCode:
    """A renamed id may survive in a *stored setting*; never in code.

    This is the defect that motivated the migration rules: renaming
    ``upload_files`` to ``input_ingestion`` left ``base.html`` gating a
    sidebar entry on the old id, so the navigation silently lost the entry.
    Nothing failed - the switch simply was not there. A stored value has to
    keep working across a rename; a source file naming the old id is a bug.
    """

    PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent

    #: Modules whose whole job is to know about the old ids.
    ALLOWED = {
        "core/interfaces/registry.py",
        "settings/interface_state.py",
        "settings/settings_adapter.py",
        "settings/settings_manager.py",
        "core/interfaces/validation.py",
    }

    #: Directories that are code (as opposed to data or documentation).
    SCANNED = ("templates", "static/js", "Api", "core", "settings", "apps", "services")

    def _legacy_ids(self):
        from core.interfaces import LEGACY_INTERFACE_IDS, RETIRED_INTERFACE_IDS

        return set(LEGACY_INTERFACE_IDS) | set(RETIRED_INTERFACE_IDS)

    @staticmethod
    def _references(text, interface_id):
        """Every way code asks about an interface *by id*.

        A bare quoted string that happens to match an old id is not a
        reference - ``Blueprint("analytics")`` and a display label are not
        interface lookups - so the patterns are the call sites that matter.
        """
        quoted = (f"'{interface_id}'", f'"{interface_id}"')
        forms = (
            "is_interface_enabled({q})",
            "get_interface({q})",
            "interface_id={q}",
            'data-interface-id={q}',
            "interfaces.{id}.enabled",
        )
        found = []
        for form in forms:
            for quote in quoted:
                candidate = form.format(q=quote, id=interface_id)
                if candidate in text:
                    found.append(candidate)
        return found

    def test_no_source_file_gates_on_a_renamed_id(self):
        legacy = self._legacy_ids()
        offenders = []
        for folder in self.SCANNED:
            for path in (self.PROJECT_ROOT / folder).rglob("*"):
                if not path.is_file() or path.suffix not in {".py", ".html", ".js"}:
                    continue
                relative = str(path.relative_to(self.PROJECT_ROOT))
                if relative in self.ALLOWED:
                    continue
                text = path.read_text(errors="ignore")
                for old_id in legacy:
                    for hit in self._references(text, old_id):
                        offenders.append(f"{relative}: {hit}")
        assert offenders == [], (
            "renamed or retired interface ids are referenced in code; only stored "
            "settings may carry them:\n  " + "\n  ".join(sorted(offenders)))

    def test_the_check_itself_catches_the_defect_it_exists_for(self):
        """A regression test for the guardrail: the old markup must fail it."""
        old_markup = "{% if is_interface_enabled('upload_files') %}"
        assert self._references(old_markup, "upload_files")
        unrelated = 'Blueprint("analytics", __name__)'
        assert self._references(unrelated, "analytics") == []

    def test_the_navigation_names_no_interface_id_at_all(self):
        """The sidebar renders a prepared model; it does not look anything up."""
        nav = self.PROJECT_ROOT / "templates/components/sidebar_nav.html"
        assert nav.exists(), "the registry-driven navigation component is missing"
        text = nav.read_text()
        assert "navigation" in text
        assert "entry.url" in text and "entry.label" in text
        for call in ("is_interface_enabled", "get_interface(", "interface_registry("):
            assert call not in text, call
        # No interface may be named by hand in the navigation any more. The
        # stronger version of this rule is tests/unit/test_registry_purity.py.
        from core.interfaces import REGISTRY

        for interface in REGISTRY:
            assert f"'{interface.interface_id}'" not in text, interface.interface_id

class TestDeliberateAbsences:
    """What an interface does *not* declare has to be a decision.

    `help_topic` and `keyboard_shortcut` are optional, and an omitted value is
    indistinguishable from a forgotten one by inspection - which is how a
    product surface ends up unreachable by keyboard and unexplained in help
    without anybody deciding that. These two sets enumerate the interfaces
    that legitimately have no help topic and no shortcut; adding a third means
    editing this test, which means somebody had to decide.
    """

    #: Internal or product-internal surfaces: no help page, no shortcut.
    WITHOUT_HELP = {"concurrency_monitor", "import_export_console"}

    #: Interfaces whose shortcut is not yet declared (§24: the data contract
    #: comes first; the command palette that consumes it is not built).
    WITHOUT_SHORTCUT = {
        "sources", "sides", "keywords", "words", "categories", "email_words",
        "analyst_categorization", "classification", "import_center",
        "comprehensive_dashboard", "charts_dashboard", "import_export_console",
        "concurrency_monitor", "schedules",
        "retention", "reports_dashboard",
    }

    def test_only_the_named_interfaces_lack_a_help_topic(self):
        from core.interfaces import REGISTRY

        without = {i.interface_id for i in REGISTRY if not i.help_topic}
        assert without == self.WITHOUT_HELP, (
            "the set of interfaces with no help topic changed; if that is "
            "deliberate, update WITHOUT_HELP: " + repr(sorted(without)))

    def test_only_the_named_interfaces_lack_a_shortcut(self):
        from core.interfaces import REGISTRY

        without = {i.interface_id for i in REGISTRY if not i.keyboard_shortcut}
        assert without == self.WITHOUT_SHORTCUT, (
            "the set of interfaces with no keyboard shortcut changed; if that "
            "is deliberate, update WITHOUT_SHORTCUT: " + repr(sorted(without)))

    def test_the_named_absences_are_still_real_interfaces(self):
        """A typo in the lists above must not silently pass."""
        from core.interfaces import is_registered

        for interface_id in self.WITHOUT_HELP | self.WITHOUT_SHORTCUT:
            assert is_registered(interface_id), interface_id

    def test_every_navigable_interface_has_a_help_topic(self):
        """Anything an operator can reach from the sidebar is documented."""
        from core.interfaces import REGISTRY

        missing = [i.interface_id for i in REGISTRY
                   if i.route and i.interface_id not in self.WITHOUT_HELP and not i.help_topic]
        assert missing == [], missing


class TestEndpointExceptionsAreDeliberate:
    """The coverage rule tolerates exceptions - a fixed, named list of them.

    §27: "Do not permit silent omissions." A page may be exempted from the
    one-owner rule, but only by appearing here, so the exemption is visible in
    a diff instead of an absence nobody notices.
    """

    def test_the_system_endpoints_are_the_declared_set(self):
        from core.interfaces.inventory import SYSTEM_ENDPOINTS

        assert set(SYSTEM_ENDPOINTS) == {
            "static", "favicon", "get_csrf_token", "set_language",
            "auth.login", "auth.login_page", "auth.logout", "auth.me",
            "auth.change_password", "auth.first_admin_page", "auth.first_admin_create",
            "setup.setup_page", "setup.system_check", "setup.test_database",
            "setup.run_installation", "setup.check_setup_status",
            "health.health",
        }

    def test_the_internal_page_is_the_concurrency_dashboard(self):
        from core.interfaces.inventory import INTERNAL_PAGE_ENDPOINTS

        # The one page that is part of the application but not of the product
        # surface, and it is owned by an INTERNAL interface.
        assert set(INTERNAL_PAGE_ENDPOINTS) == {"concurrency.dashboard"}

    def test_the_redirects_are_the_compatibility_aliases(self):
        from core.interfaces.inventory import REDIRECT_ENDPOINTS

        assert set(REDIRECT_ENDPOINTS) == {"files.upload_page"}

    def test_the_test_scaffolding_prefix_is_isolated(self):
        from core.interfaces.inventory import TEST_ENDPOINT_PREFIXES

        assert tuple(TEST_ENDPOINT_PREFIXES) == ("/_test/",)

    def test_a_test_endpoint_is_not_counted_as_a_product_page(self):
        from core.interfaces.inventory import EndpointClass, classify_endpoint

        assert classify_endpoint(
            "_sec08_boom", "/_test/sec08/boom", ("GET",)) == EndpointClass.TEST_ENDPOINT
