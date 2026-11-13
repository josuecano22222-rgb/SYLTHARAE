"""The Experience Contract: what must be true of every contract.

Three things are protected here.

* **It describes the product that exists.** Every registry interface has a
  contract, a contract's identity is the registry's identity, and a declared
  column, filter, action or state names a string that really appears in that
  interface's template - so a declaration cannot drift away from the screen.
* **It stays declarative.** No SQL, no imports, no calls, no authorisation
  decisions inside a definition, and - just as important - a screen called
  "Import Center" is not flagged for its name.
* **Its measurements are measurements.** Coverage is recomputed here from the
  catalogs themselves and compared with what the module reports, so a figure
  cannot be typed in and cannot silently drift.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

from core.experience import (
    ActionDefinition,
    ColumnDefinition,
    ContractError,
    ScreenConfiguration,
    TranslationDefinition,
    check_declarative,
    check_translation_edit,
    check_translations,
    contract,
    contract_counts,
    contracts,
    coverage_counts,
    declarations,
    language_coverage,
    screen_coverage,
    source_strings,
    translation_index,
)
from core.experience.action_registry import action
from core.experience.coverage import JS_PACKS, TRANSLATIONS, _read_js_pack, _read_po
from core.experience.docgen import BEGIN, END, reference_markdown
from core.interfaces import REGISTRY

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
DOC = PROJECT_ROOT / "docs/EXPERIENCE_CONTRACT.md"

#: The interface whose template is read to verify its declarations.
#: An interface can be served by more than one screen, and a declaration may
#: name a string that appears on any of them: the file library has its list, a
#: record page and the reader. A declaration still has to be somewhere real.
TEMPLATES = {
    "file_library": ("templates/file/files_list.html",
                     "templates/file/file_detail.html",
                     "templates/file/full_content.html",
                     # The library's rows live in the one shared table
                     # component, which the panels embed too.
                     "templates/components/file_library_table.html",
                     "templates/file/file_types.html"),
    "keywords": ("templates/Keyword/keywords_list.html",),
    "words": ("templates/Word/Word_list.html",),
    "sources": ("templates/Sources/sources_list.html",),
    "sides": ("templates/Side/sides_list.html",),
}


class TestTheContractDescribesTheProduct:
    def test_every_registry_interface_has_a_contract(self):
        assert len(contracts()) == len(REGISTRY)

    def test_identity_comes_from_the_registry(self):
        for item in contracts():
            interface = next(i for i in REGISTRY
                             if i.interface_id == item.interface_id)
            assert item.name == interface.name
            assert item.description == interface.description
            assert item.domain == str(interface.domain)
            assert item.route == interface.route
            assert item.endpoints == interface.endpoints
            assert item.required_role == interface.required_role
            assert item.dependencies == interface.dependencies

    def test_every_navigable_interface_has_a_navigation_entry(self):
        for item in contracts():
            if item.navigable:
                assert item.screen.navigation is not None, item.interface_id
                assert item.screen.navigation.visible is True, item.interface_id

    def test_the_immutable_half_is_labelled_as_immutable(self):
        """A configuration screen may not pretend it can move the code."""
        for item in contracts():
            payload = item.to_dict()
            assert payload["implementation"]["editable_from_frontend"] is False
            assert payload["implementation"]["route"] == item.route

    def test_a_declared_screen_says_so(self):
        declared = {item.interface_id for item in contracts()
                    if item.screen.declared}
        assert declared == set(declarations.DECLARED_SCREENS)


class TestDeclarationsAreVerifiedAgainstTheScreens:
    """A declaration that has drifted from the template is a defect."""

    @pytest.mark.parametrize("interface_id", sorted(TEMPLATES))
    def test_declared_columns_filters_actions_and_states_exist_on_screen(
            self, interface_id):
        """Every declared thing is evidenced by the screen it belongs to.

        Templates are the evidence for columns, filters and states. Actions are
        the exception now, and deliberately: a screen's actions are handed to a
        prepared surface, so the words a reader sees come from the registry's
        label and the screen's own binding source - the file it declares beside
        its actions - rather than from a string typed into the template. The
        action ids are checked against the screen's declarations as well, so a
        prepared action cannot be attributed to a screen that does not offer it.
        """
        text = "\n".join((PROJECT_ROOT / relative).read_text()
                         for relative in TEMPLATES[interface_id])
        binding_text = "\n".join(
            (PROJECT_ROOT / relative).read_text()
            for relative in declarations.binding_sources(interface_id))
        missing = []
        for items, attribute in (
            (declarations.columns(interface_id), "column_id"),
            (declarations.filters(interface_id), "filter_id"),
            (declarations.states(interface_id, interface_id), "state"),
        ):
            for item in items:
                if item.source not in text:
                    missing.append(
                        f"{attribute}={getattr(item, attribute)!r} "
                        f"source {item.source!r} is not in {TEMPLATES[interface_id]}")
        for item in declarations.actions(interface_id):
            if item.source in text:
                continue
            if item.source in binding_text:
                continue
            registry_row = action(item.action_id)
            if registry_row is not None and registry_row.source == item.source:
                # The words come from the registry, which is where a label
                # belongs once the surface is prepared rather than hand-written.
                continue
            missing.append(
                f"action_id={item.action_id!r} source {item.source!r} is in "
                f"neither {TEMPLATES[interface_id]} nor the screen's binding "
                f"source {declarations.binding_sources(interface_id)}")
        assert missing == [], missing

    def test_a_declared_screen_has_a_title_and_keys_for_its_strings(self):
        for interface_id in declarations.DECLARED_SCREENS:
            item = contract(interface_id)
            assert item is not None
            assert item.screen.declared is True
            assert item.screen.translations, interface_id
            for entry in item.screen.translations:
                assert entry.screen == interface_id


class TestTheModelRefusesWhatItShould:
    def test_a_key_must_be_a_key_not_a_sentence(self):
        with pytest.raises(ContractError):
            ColumnDefinition(column_id="name", label_key="File Name",
                             source="File Name")

    def test_an_unknown_state_is_refused(self):
        from core.experience.model import StateDefinition

        with pytest.raises(ContractError):
            StateDefinition(state="vibing", title_key="state.x.title",
                            source="Vibing")

    def test_a_destructive_action_must_declare_a_confirmation(self):
        with pytest.raises(ContractError) as error:
            ActionDefinition(action_id="files.purge",
                             label_key="action.files.purge.label",
                             source="Purge", interfaces=("file_library",),
                             destructive=True)
        assert "confirmation" in str(error.value)

    def test_a_bulk_action_must_require_a_selection(self):
        with pytest.raises(ContractError) as error:
            ActionDefinition(action_id="files.do_many",
                             label_key="action.files.do_many.label",
                             source="Do many", interfaces=("file_library",),
                             scope="bulk")
        assert "selection" in str(error.value)

    def test_a_screen_refuses_two_items_with_the_same_id(self):
        with pytest.raises(ContractError):
            ScreenConfiguration(
                interface_id="x", title_key="screen.x.title", source="X",
                columns=(
                    ColumnDefinition(column_id="name",
                                     label_key="screen.x.column.name.label",
                                     source="Name"),
                    ColumnDefinition(column_id="name",
                                     label_key="screen.x.column.other.label",
                                     source="Name again"),
                ))

    def test_a_translation_must_record_the_placeholders_it_uses(self):
        with pytest.raises(ContractError):
            TranslationDefinition(key="screen.x.count", source="Files: {count}",
                                  placeholders=())

    def test_placeholder_edits_are_checked(self):
        problems = check_translation_edit("Processing {count} files",
                                          "جارٍ معالجة ملف")
        assert any("missing placeholder" in p for p in problems)
        assert check_translation_edit("Processing {count} files",
                                      "جارٍ معالجة {count} ملف") == []
        assert any("unexpected placeholder" in p for p in
                   check_translation_edit("Processing files", "معالجة {n} ملف"))
        assert any("unbalanced" in p for p in
                   check_translation_edit("Processing {count} files",
                                          "معالجة {count ملف"))

    def test_two_source_strings_under_one_key_is_a_conflict(self):
        from dataclasses import replace

        item = contract("keywords")
        entry = item.screen.translations[0]
        changed = replace(item, screen=replace(
            item.screen,
            translations=item.screen.translations + (
                replace(entry, source="Something else"),)))
        assert any("two source strings" in problem
                   for problem in check_translations(changed))


class TestItStaysDeclarative:
    def test_no_contract_carries_a_mechanism(self):
        from core.experience import check_all

        assert check_all() == []

    def test_sql_and_calls_are_caught(self):
        assert check_declarative({"a": "SELECT id FROM paths"})
        assert check_declarative({"a": "__import__('os').system('id')"})
        assert check_declarative({"a": "eval(user_input)"})

    def test_an_authorisation_decision_is_caught(self):
        problems = check_declarative({"a": "has_permission('admin')"})
        assert any("core/security" in problem for problem in problems)

    def test_a_name_that_contains_a_mechanism_word_is_not_flagged(self):
        """The check must tell a name from something that would execute."""
        assert check_declarative(contract("import_center").to_dict()) == []
        assert check_declarative({"label": "Update Selected"}) == []


class TestCoverageIsMeasuredNotAsserted:
    def test_every_source_string_is_accounted_for(self):
        total_source = len(source_strings())
        assert total_source > 1000, "the message template did not load"
        for row in language_coverage():
            assert row["translated"] + row["fallback"] + row["missing"] == row["total"]
            assert row["total"] == total_source

    def test_the_language_figures_match_a_fresh_read_of_the_catalogs(self):
        """Recomputed here from the files, not from the module's own numbers."""
        for row in language_coverage():
            locale = row["locale"]
            messages = {}
            messages.update(_read_po(TRANSLATIONS / locale / "LC_MESSAGES" / "messages.po"))
            messages.update(_read_js_pack(JS_PACKS / f"{locale}.js"))
            source = source_strings()
            translated = sum(
                1 for message_id, text in source.items()
                if messages.get(message_id) and
                messages[message_id].strip() != text.strip())
            missing = sum(1 for message_id in source if not messages.get(message_id))
            assert row["translated"] == translated, locale
            assert row["missing"] == missing, locale

    def test_coverage_is_the_arithmetic_it_claims_to_be(self):
        for row in language_coverage():
            expected = round(100 * (row["translated"] + row["fallback"]) /
                             row["total"], 1)
            assert row["coverage"] == expected

    def test_no_language_claims_more_than_it_has(self):
        for row in language_coverage():
            assert row["translated"] <= row["total"]
            if row["missing"]:
                assert row["translated"] < row["total"]

    def test_screen_coverage_counts_the_screen_s_keys(self):
        index = translation_index()
        for screen in screen_coverage():
            item = contract(screen["interface_id"])
            expected = len([key for key in item.screen.translation_keys
                            if key in index])
            assert screen["keys"] == expected, screen["interface_id"]
            for language in screen["languages"]:
                assert (language["translated"] + language["fallback"] +
                        language["missing"]) == language["keys"]

    def test_counts_are_generated_from_the_contracts(self):
        counts = contract_counts()
        assert counts["contracts"] == len(contracts())
        assert counts["translations"] == len(translation_index())
        assert coverage_counts()["source_strings"] == len(source_strings())


class TestTheDocumentCannotDrift:
    def test_the_generated_block_matches(self):
        document = DOC.read_text()
        assert BEGIN in document and END in document
        embedded = document.split(BEGIN, 1)[1].split(END, 1)[0].strip()
        assert embedded == reference_markdown().strip(), (
            "regenerate with "
            "`python3 -m core.experience.docgen docs/EXPERIENCE_CONTRACT.md`")

    def test_the_document_has_no_hand_typed_counts(self):
        """The same rule the registry document already follows."""
        document = DOC.read_text().split(BEGIN, 1)[0]
        # `§63` is a reference to the specification, not a count.
        suspicious = [number for number in re.findall(r"(?<!§)\b\d{2,}\b", document)]
        assert suspicious == [], (
            "counts in the hand-written half of the document will drift; "
            "they belong in the generated block: " + repr(suspicious))


class TestTheApiIsReadOnlyAndHonest:
    """The contract API, against the application the rest of the suite uses.

    These tests used to authenticate against a named demo database on one
    machine. They take the session's authenticated client instead: the API
    reads the contract modules, so the database in front of it should be the
    disposable one the fixtures created, not whatever happened to be running.
    """

    def test_contracts_endpoint_reports_the_same_counts(self, admin_client):
        client = admin_client
        payload = client.get("/api/experience/contracts").get_json()
        assert payload["success"] is True
        assert payload["counts"] == contract_counts()
        assert payload["total"] == len(contracts())

    def test_an_unknown_interface_is_a_clear_404(self, admin_client):
        client = admin_client
        response = client.get("/api/experience/contracts/not_a_screen")
        assert response.status_code == 404
        body = response.get_json()
        assert body["success"] is False
        assert "registered" in body["error"]

    def test_one_contract_carries_its_problems_and_keys(self, admin_client):
        client = admin_client
        payload = client.get("/api/experience/contracts/keywords").get_json()
        assert payload["success"] is True
        assert payload["problems"] == []
        assert payload["contract"]["screen"]["declared"] is True
        assert payload["keys_still_keyed_by_source"]

    def test_the_endpoints_do_not_accept_writes(self, admin_client):
        client = admin_client
        assert client.post("/api/experience/contracts").status_code == 405
        assert client.put("/api/experience/contracts/keywords").status_code == 405
        assert client.delete("/api/experience/contracts/keywords").status_code == 405
        for path in ("/api/experience/actions", "/api/experience/actions/keywords.update",
                     "/api/experience/permissions"):
            assert client.post(path).status_code == 405, path
            assert client.delete(path).status_code == 405, path

    def test_the_action_registry_is_readable_as_data(self, admin_client):
        from core.experience.action_registry import counts, registered

        client = admin_client
        payload = client.get("/api/experience/actions").get_json()
        assert payload["success"] is True
        assert payload["counts"] == counts()
        assert payload["total"] == len(registered())

        one = client.get("/api/experience/actions/keywords.delete_selected").get_json()
        assert one["success"] is True
        assert one["action"]["scope"] == "bulk"
        assert one["action"]["destructive"] is True
        assert one["action"]["execution"] == "delete_keywords"
        assert "permission" in one["action"]

        missing = client.get("/api/experience/actions/files.nope")
        assert missing.status_code == 404
        assert "registered" in missing.get_json()["error"]

    def test_the_permission_vocabulary_reports_that_it_grants_nothing(
            self, admin_client):
        client = admin_client
        payload = client.get("/api/experience/permissions").get_json()
        assert payload["success"] is True
        assert "authoris" in payload["note"]
        names = [item["permission"] for item in payload["permissions"]]
        assert "files.download_original" in names
        for item in payload["permissions"]:
            assert item["description"], item["permission"]
            if item["permission"] in payload["reserved"]:
                assert item["actions"] == [], item["permission"]
            else:
                assert item["actions"], (
                    f"{item['permission']} is neither used nor reported as "
                    "reserved")
        # The vocabulary may run ahead of the catalog - the taxonomy and
        # export families have no actions yet - but never silently: the names
        # that no action uses are reported.
        assert set(payload["reserved"]) <= set(names)
        for name in payload["reserved"]:
            assert name.startswith(("categories.", "search.", "files.", "jobs.")), name

    def test_coverage_endpoint_matches_the_module(self, admin_client):
        client = admin_client
        payload = client.get("/api/experience/coverage").get_json()
        assert payload["success"] is True
        assert payload["languages"] == language_coverage()


class TestNoEndpointReturnsRawExceptionText:
    """The defect that was fixed in /api/translations, guarded product-wide."""

    #: Domain errors whose text is written for the reader: an account lock-out
    #: message, a rejected database configuration. Their `str()` is the message
    #: the product decided to show, and the settings pipeline sanitises it.
    CURATED = ("AccountLocked", "AuthError", "DatabaseConfigRejected",
               "InvalidCredentials", "AccountDisabled")

    @staticmethod
    def _returns_exception_text(node, names) -> list:
        """`{'error': str(x)}` where x came from this handler's own except."""
        found = []
        for inner in ast.walk(node):
            if not (isinstance(inner, ast.Dict) and inner.values):
                continue
            for key, value in zip(inner.keys, inner.values):
                if not (isinstance(key, ast.Constant) and key.value == "error"):
                    continue
                for item in ast.walk(value):
                    if (isinstance(item, ast.Call)
                            and isinstance(item.func, ast.Name)
                            and item.func.id == "str"
                            and item.args
                            and isinstance(item.args[0], ast.Name)
                            and item.args[0].id in names):
                        found.append((inner.lineno, item.args[0].id))
        return found

    def test_no_route_returns_exception_text_from_a_failed_operation(self):
        """`except Exception` → the client must not receive `str(exc)`.

        Deliberate domain errors are a different thing and may carry their
        curated message: they are written for the reader, there is no traceback
        to leak, and the settings pipeline sanitises them. So the check is
        scoped to the body of a broad handler, not to the file.
        """
        offenders = []
        modules = (list((PROJECT_ROOT / "Api/routes").rglob("*.py")) +
                   list((PROJECT_ROOT / "Api/blueprints").rglob("*.py")) +
                   list((PROJECT_ROOT / "settings").rglob("*.py")))
        for path in modules:
            text = path.read_text(errors="ignore")
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if not isinstance(node, ast.ExceptHandler):
                    continue
                caught = node.type
                broad = caught is None or (
                    isinstance(caught, ast.Name) and caught.id == "Exception")
                if not broad or not node.name:
                    continue
                for lineno, name in self._returns_exception_text(node, {node.name}):
                    offenders.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{lineno} returns "
                        f"str({name}) from an `except Exception` handler")
            if "technical_error" in text:
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}: "
                                 "returns a technical_error field")
        assert offenders == [], offenders

    def test_the_translation_api_uses_the_shared_pipeline(self):
        text = (PROJECT_ROOT / "Api/routes/translations.py").read_text()
        assert "client_error" in text
        assert "str(e)" not in text.split("def _translations_failure")[1]
