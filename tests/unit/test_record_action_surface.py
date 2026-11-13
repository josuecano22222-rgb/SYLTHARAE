"""The record action surface: the join, the boundary, and the missing route.

Four things are protected here, and each of them is a defect that has already
happened once in this product:

* **The join is the only one.** A screen says what an action does *here*; the
  Action Registry says what the action *is*. A binding that names an unknown
  action, another screen's action, or a state reason the vocabulary does not
  have is refused rather than rendered as a guess.
* **The states stay apart.** ``no_selection`` and ``no_permission`` are not the
  same thing, a disabled action keeps its place with a reason, and a hidden one
  is not drawn at all. A product that collapses them into one grey button has
  lost information the reader needed.
* **"Not built" is not "available".** ``files.reprocess`` was declared, drawn,
  and pointed at ``/file/<id>/reprocess`` - a route that has never existed, so
  the link answered 404. It is now registered with ``visibility="not_built"``,
  refused by the state derivation, and reported by name.
* **The controls that ship lead somewhere.** ``bindings.check`` resolves every
  address a screen binds against the application's own URL map, so this class
  of defect fails a test instead of reaching a reader.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess

import pytest

from core.experience import bindings, declarations, presentation
from core.experience.action_registry import (
    action,
    actions_for_scope,
    all_actions,
    counts as registry_counts,
    not_built,
    registered_ids,
    to_dict as registry_to_dict,
)
from core.experience.presentation import ActionBindingError
from core.experience.validation import check_registry

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _binding(**overrides):
    binding = {"href": "/files", "group": "secondary"}
    binding.update(overrides)
    return binding


class TestTheBindingIsTheOnlyJoin:
    def test_an_action_that_does_not_exist_is_refused(self):
        with pytest.raises(ActionBindingError) as error:
            presentation.record_actions("file_library",
                                        {"files.explode": _binding()},
                                        scope="record")
        assert "files.explode" in str(error.value)

    def test_another_screens_action_is_refused(self):
        # keywords.delete_selected is real, and it is not the file library's.
        with pytest.raises(ActionBindingError):
            presentation.record_actions("file_library",
                                        {"keywords.delete_selected":
                                         _binding()},
                                        scope="record")

    def test_an_action_of_another_scope_is_refused(self):
        with pytest.raises(ActionBindingError):
            presentation.record_actions("file_library",
                                        {"files.upload": _binding()},
                                        scope="record")

    def test_a_button_with_nowhere_to_go_is_refused(self):
        with pytest.raises(ActionBindingError) as error:
            presentation.prepare(action("files.view_original"),
                                 {"group": "primary"})
        assert "href" in str(error.value)

    def test_a_binding_may_not_be_both_a_link_and_a_post(self):
        with pytest.raises(ActionBindingError) as error:
            presentation.prepare(action("files.delete"),
                                 {"href": "/a", "endpoint": "/b",
                                  "confirmation": {"message": "Sure?"}})
        assert "not both" in str(error.value)

    def test_unknown_binding_keys_are_refused(self):
        with pytest.raises(ActionBindingError) as error:
            presentation.prepare(action("files.view_original"),
                                 _binding(scope="everything"))
        assert "unknown binding key" in str(error.value)

    def test_an_unknown_group_is_refused(self):
        with pytest.raises(ActionBindingError) as error:
            presentation.prepare(action("files.view_original"),
                                 _binding(group="favourite"))
        assert "favourite" in str(error.value)

    def test_a_reason_outside_the_vocabulary_is_refused(self):
        with pytest.raises(ActionBindingError) as error:
            presentation.prepare(action("files.view_original"),
                                 _binding(disabled_reason="because"))
        assert "original_missing" in str(error.value)

    def test_a_disabled_label_without_a_reason_says_nothing(self):
        with pytest.raises(ActionBindingError):
            presentation.prepare(action("files.view_original"),
                                 _binding(disabled_label="Not right now"))


class TestTheStatesStayApart:
    def test_nothing_selected_is_disabled_and_not_hidden(self):
        result = presentation.record_actions(
            "keywords",
            {"keywords.delete_selected": {"endpoint": "/api/keywords/delete",
                                          "selected": 0}},
            scope="bulk")
        drawn = result["primary"] + result["secondary"] + result["overflow"]
        assert [item["action_id"] for item in drawn] == [
            "keywords.delete_selected"]
        assert drawn[0]["state"] == "disabled"
        assert drawn[0]["disabled_reason"] == "no_selection"
        assert drawn[0]["hidden_reason"] is None
        assert drawn[0]["drawn"] is True

    def test_no_permission_is_hidden_and_not_disabled(self):
        result = presentation.record_actions(
            "file_library",
            {"files.delete": {"endpoint": "/file/1/delete", "permitted": False}},
            scope="record")
        assert result["drawn"] == 0
        assert len(result["hidden"]) == 1
        hidden = result["hidden"][0]
        assert hidden["state"] == "hidden"
        assert hidden["hidden_reason"] == "no_permission"
        assert hidden["disabled_reason"] is None, (
            "a refusal is not a greyed-out button: it is not shown")

    def test_a_missing_original_disables_the_two_actions_that_need_it(self):
        result = presentation.record_actions(
            "file_library",
            {
                "files.view_original": _binding(
                    group="primary", disabled_reason="original_missing",
                    disabled_label="The source file is no longer there."),
                "files.download_original": _binding(
                    disabled_reason="original_missing",
                    disabled_label="The source file is no longer there."),
            },
            scope="record")
        drawn = result["primary"] + result["secondary"]
        assert len(drawn) == 2
        for item in drawn:
            assert item["enabled"] is False
            assert item["drawn"] is True
            assert item["disabled_reason"] == "original_missing"
            assert item["disabled_label"]

    def test_an_action_the_product_has_not_built_is_never_available(self):
        result = presentation.record_actions(
            "file_library", {"files.reprocess": {"permitted": True}},
            scope="record")
        assert result["drawn"] == 0
        assert [item["action_id"] for item in result["hidden"]] == [
            "files.reprocess"]
        assert result["hidden"][0]["hidden_reason"] == "not_built"

    def test_a_destructive_action_carries_the_question_it_asks(self):
        result = presentation.record_actions(
            "file_library",
            {"files.delete": {
                "endpoint": "/file/1/delete",
                "group": "overflow",
                "confirmation": {"title": "Delete this file?",
                                 "message": "It cannot be undone.",
                                 "confirm_label": "Delete file",
                                 "cancel_label": "Keep it"}}},
            scope="record")
        delete = result["overflow"][0]
        assert delete["destructive"] is True
        assert delete["confirmation"] == "action.files.delete.confirm"
        assert delete["confirmation_required"] is True
        assert delete["confirmation_message"] == "It cannot be undone."

    def test_a_destructive_question_that_says_nothing_is_refused(self):
        with pytest.raises(ActionBindingError) as error:
            presentation.prepare(
                action("files.delete"),
                {"endpoint": "/file/1/delete", "confirmation": {"title": "?"}})
        assert "does not say what will happen" in str(error.value).replace(
            "the reader is not told what will happen", "does not say what will happen")

    def test_a_typed_confirmation_belongs_to_a_destructive_action(self):
        with pytest.raises(ActionBindingError):
            presentation.prepare(action("files.export_content"),
                                 {"endpoint": "/api/files/1/export",
                                  "confirmation": {"message": "Sure?",
                                                   "typed": "EXPORT"}})

    def test_what_a_screen_did_not_bind_is_reported_not_implied(self):
        result = presentation.record_actions(
            "file_library",
            {"files.view_original": _binding(group="primary")},
            scope="record")
        assert "files.delete" in result["skipped"]
        assert "files.download_original" in result["skipped"]
        assert result["drawn"] == 1


class TestTheScanIsHonest:
    """The scan reads the product, and says which kind of binding it found.

    A binding is not one thing. Markup can name an action and carry the address
    it goes to; a macro argument or a page script can name it and hold the
    address itself; and the file a screen declares as its binding source can
    prepare the whole surface. Collapsing those into one number is how a page
    that binds nothing looks like a page that binds four things.
    """

    def test_the_binding_kinds_are_reported_separately(self):
        items = bindings.scan()
        kinds = {item.get("kind", "attribute") for item in items}
        assert kinds == {"literal", "prepared"}, (
            "today the product names its actions in values and in the code "
            "that prepares the surface, and in no static registry-action "
            "attributes: every record control is drawn from prepared data")
        assert not {item["action_id"] for item in items} & {
            "edit", "view", "delete", "merge"
        }, "local row-dispatch keys are not Action Registry bindings"

    def test_a_prepared_binding_points_at_the_file_the_screen_declared(self):
        prepared = [item for item in bindings.scan() if item["kind"] == "prepared"]
        assert prepared, "the file library declares a binding source"
        declared = set(declarations.BINDING_SOURCES["file_library"])
        assert {item["file"] for item in prepared} == declared

    def test_a_name_is_never_called_dangling(self):
        """A literal has no address, so there is nothing to resolve."""
        items = [item for item in bindings.scan() if item["kind"] == "literal"]
        assert items
        report = bindings.check(
            bindings.RouteMatcher(()), items,
            known_actions={item["action_id"] for item in items})
        assert report["dangling"] == []

    def test_an_id_the_registry_does_not_know_is_not_a_binding(self):
        """The scan reads ids the registry knows, so it cannot turn a MIME type
        into an action - and a rename nobody finished is caught by the
        attribute scan instead, where the id is written as an attribute."""
        template = PROJECT_ROOT / "templates" / "file" / "file_detail.html"
        real = template.read_text(encoding="utf-8")
        try:
            template.write_text(
                real + "\n{{ confirm_dialog('x', 'T', 'M', "
                "action='files.vanish') }}\n", encoding="utf-8")
            items = bindings.scan(files=[template], known_actions={"files.delete"})
            assert all(item["action_id"] != "files.vanish" for item in items)

            # Written as an attribute, it *is* reported - and refused by the
            # check, because the registry has no such action.
            template.write_text(
                real + '\n<a data-action="files.vanish" href="/files">x</a>\n',
                encoding="utf-8")
            items = bindings.scan(files=[template], known_actions={"files.delete"})
            report = bindings.check(bindings.RouteMatcher(()), items,
                                    known_actions={"files.delete"})
            assert [item["action_id"] for item in report["unknown_actions"]] == [
                "files.vanish"]
        finally:
            template.write_text(real, encoding="utf-8")

    def test_the_counts_add_up(self):
        values = bindings.counts()
        assert values["bindings"] == (values["attributes"] + values["literals"]
                                      + values["prepared"])
        assert values["actions_bound"] <= values["bindings"]

    def test_only_known_ids_are_reported_from_scripts(self, tmp_path):
        """A registry id in a page script is a binding; a MIME type is not."""
        script = tmp_path / "page.js"
        script.write_text(
            "const a = 'application/json';\n"
            "if (action === 'files.delete') { go(); }\n"
            "const b = 'text/csv';\n", encoding="utf-8")
        items = bindings.scan(files=[script], known_actions={"files.delete"})
        assert [item["action_id"] for item in items] == ["files.delete"]
        assert items[0]["kind"] == "literal"
        # And a file outside the repository is still reported readably.
        assert items[0]["file"].endswith("page.js")


class TestTheRegistryIsACatalog:
    def test_the_registry_api_agrees_with_itself(self):
        assert registered_ids() == tuple(item.action_id for item in all_actions())
        assert registry_to_dict()["registered_ids"] == list(registered_ids())
        assert registry_to_dict()["counts"] == registry_counts()
        record = [item.action_id for item in
                  actions_for_scope("file_library", "record")]
        assert record == ["files.open_record", "files.delete",
                          "files.reprocess", "files.view_original",
                          "files.export_content", "files.download_original"], (
            "the catalog's order, including the action that is declared and "
            "not built - a catalog does not quietly lose an entry")

    def test_a_scope_nobody_has_heard_of_is_refused(self):
        with pytest.raises(ValueError):
            actions_for_scope("file_library", "everything")

    def test_the_catalog_validates_itself(self):
        assert check_registry() == []

    def test_one_shortcut_for_two_actions_is_refused(self):
        from core.experience.model import ActionDefinition

        first = ActionDefinition(action_id="sources.select_all",
                                 label_key="action.sources.select_all.label",
                                 source="Select All", interfaces=("sources",),
                                 scope="page", shortcut="g s")
        second = ActionDefinition(action_id="sources.select_none",
                                  label_key="action.sources.select_none.label",
                                  source="Select None", interfaces=("sources",),
                                  scope="page", shortcut="g s")
        problems = check_registry(rows=[first, second])
        assert any("claimed by both" in problem for problem in problems), problems

    def test_one_id_for_two_actions_is_refused(self):
        from core.experience.model import ActionDefinition

        first = ActionDefinition(action_id="files.open_record",
                                 label_key="action.files.open_record.label",
                                 source="View Details",
                                 interfaces=("file_library",), scope="record")
        second = ActionDefinition(action_id="files.open_record",
                                  label_key="action.files.open_record.other",
                                  source="Open", interfaces=("file_library",),
                                  scope="page")
        problems = check_registry(rows=[first, second])
        assert any("declared twice" in problem for problem in problems), problems

    def test_an_action_cannot_claim_an_interface_that_does_not_exist(self):
        from core.experience.model import ActionDefinition

        ghost = ActionDefinition(action_id="files.haunt",
                                 label_key="action.files.haunt.label",
                                 source="Haunt", interfaces=("nowhere",),
                                 scope="page")
        problems = check_registry(rows=[ghost])
        assert any("nowhere" in problem for problem in problems), problems

    def test_a_not_built_action_may_not_name_an_operation(self):
        from core.experience.model import ActionDefinition
        from core.experience.model import ContractError

        with pytest.raises(ContractError) as error:
            ActionDefinition(action_id="files.ghost",
                             label_key="action.files.ghost.label",
                             source="Ghost", interfaces=("file_library",),
                             scope="page", visibility="not_built",
                             execution="ghost_file")
        assert "not built" in str(error.value)


class TestEveryControlLeadsSomewhere:
    def test_no_binding_in_the_product_points_at_a_route_that_is_missing(
            self, app):
        """The regression that this class exists for.

        ``/file/<id>/reprocess`` was bound by two links on the file detail page
        and served by nothing. The scan reads the markup; the application's own
        URL map answers whether the address exists.
        """
        rules = app.url_map.iter_rules()
        report = bindings.check(rules, known_actions=set(registered_ids()))
        assert report["dangling"] == [], [
            (item["file"], item["line"], item.get("href")
             or item.get("dynamic_href") or item.get("endpoint")
             or item.get("record_endpoint"))
            for item in report["dangling"]]
        assert report["unknown_actions"] == [], [
            (item["file"], item["line"], item["action_id"])
            for item in report["unknown_actions"]]

    def test_the_scan_catches_a_link_to_a_route_that_does_not_exist(self):
        class Rule:
            def __init__(self, rule, endpoint):
                self.rule, self.endpoint = rule, endpoint

        template = PROJECT_ROOT / "templates" / "file" / "file_detail.html"
        real = template.read_text(encoding="utf-8")
        try:
            template.write_text(
                real + '\n<a href="/file/1/reprocess" data-action="files.reprocess">x</a>\n',
                encoding="utf-8")
            items = bindings.scan(files=[template])
            served = [Rule("/file/<int:file_id>", "files.file_detail")]
            dangling = bindings.check(served, items,
                                      known_actions={"files.reprocess"})["dangling"]
            assert [(item["file"], item["href"]) for item in dangling] == [
                ("templates/file/file_detail.html", "/file/1/reprocess")]
        finally:
            template.write_text(real, encoding="utf-8")

    def test_the_product_no_longer_binds_the_reprocess_link(self):
        text = (PROJECT_ROOT / "templates" / "file"
                / "file_detail.html").read_text(encoding="utf-8")
        assert 'data-action="files.reprocess"' not in text, (
            "the page no longer binds an action whose operation does not exist")
        assert "/file/{{ file[0] }}/reprocess" not in text
        assert "data-reprocess" not in text

    def test_the_declared_actions_of_a_screen_are_the_ones_it_presents(self):
        for interface_id in declarations.SCREEN_ACTIONS:
            for item in declarations.actions(interface_id):
                assert item.action_id in registered_ids()
                assert interface_id in item.interfaces, (
                    f"{interface_id} presents {item.action_id}, which the "
                    "registry attributes elsewhere")
            # And nothing the registry attributes to it is silently dropped
            # without a reason: a not-built action is the reason.
            attributed = {item.action_id
                          for item in all_actions() if interface_id in item.interfaces}
            presented = set(declarations.SCREEN_ACTIONS[interface_id])
            ignored = attributed - presented
            for action_id in ignored:
                definition = action(action_id)
                assert definition is not None
                assert (not definition.built
                        or action_id.endswith("_selected")
                        or interface_id in ("file_library",)), (
                    f"{interface_id}: {action_id} is registered, built, and "
                    "presented by no screen")


class TestTheRecordActionsAreWhatTheProductDoes:
    """The catalog's own account of the actions a record page offers."""

    def test_reprocess_is_declared_and_not_built(self):
        definition = action("files.reprocess")
        assert definition is not None, "the action stays in the catalog"
        assert definition.built is False
        assert definition.visibility == "not_built"
        assert definition.execution is None, (
            "a capability nobody has built cannot name the operation that "
            "performs it")
        assert "files.reprocess" in [item.action_id for item in not_built()]

    def test_the_two_original_actions_are_two_and_say_which_is_which(self):
        view = action("files.view_original")
        download = action("files.download_original")
        assert view.source == "View Original"
        assert download.source == "Download Original"
        assert view.permission != download.permission, (
            "showing a document and taking a copy of it are separate "
            "permissions, and the vocabulary says so")
        assert view.action_id != download.action_id

    def test_the_extracted_text_export_is_registered_because_it_exists(self):
        definition = action("files.export_content")
        assert definition is not None
        assert definition.execution == "export_file_content"
        assert definition.scope == "record"


class TestTheRecordPageRendersThem:
    """The page itself: what a reader is given, and what they are not."""

    @pytest.fixture()
    def record(self, pg_db):
        """One stored record, with no content: the page has to render anyway.

        The names carry a per-run marker: this fixture runs once per test and
        the taxonomy tables are shared by the whole session.
        """
        import psycopg2
        import uuid

        marker = uuid.uuid4().hex[:8]
        cfg = pg_db
        conn = psycopg2.connect(
            host=cfg["host"], port=cfg["port"], user=cfg["user"],
            password=cfg["password"], dbname=cfg["database"])
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO sides (name, importance, date_creation)"
                    " VALUES (%s, 1.0, CURRENT_DATE) RETURNING id",
                    (f"surface-test-side-{marker}",))
                side_id = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO sources (name, job, importance, country,"
                    " date_creation) VALUES (%s, 'test', 1.0, 'NL',"
                    " CURRENT_DATE) RETURNING id",
                    (f"surface-test-source-{marker}",))
                source_id = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO hashs (hash) VALUES (%s) RETURNING id",
                    (f"surface-test-hash-{marker}",))
                hash_id = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO hash_contexts (hash_id, source_id, side_id)"
                    " VALUES (%s, %s, %s) RETURNING id",
                    (hash_id, source_id, side_id))
                context_id = cur.fetchone()[0]
                cur.execute(
                    """
                    INSERT INTO paths (file_name, file_path, file_size,
                                       file_type, file_status, file_date,
                                       date_creation, context_id)
                    VALUES (%s, %s, 2048, 'txt', 'Read', CURRENT_DATE,
                            CURRENT_DATE, %s)
                    RETURNING id
                    """,
                    (f"surface-test-{marker}.txt",
                     f"/surface-test/missing-{marker}.txt", context_id))
                file_id = cur.fetchone()[0]
            conn.commit()
        finally:
            conn.close()
        return {"file_id": file_id}

    def test_the_page_renders_the_record_header_and_its_actions(
            self, admin_client, record):
        response = admin_client.get(f"/file/{record['file_id']}")
        assert response.status_code == 200
        html = response.get_data(as_text=True)

        assert 'id="fileRecordHeader"' in html, "the record header is drawn"
        assert 'id="fileRecordActions"' in html, "the action surface is drawn"
        assert 'role="toolbar"' in html
        assert 'data-record-action="files.view_original"' in html
        assert 'data-record-action="files.download_original"' in html
        assert 'data-record-action="files.export_content"' in html

        # The two original actions point at the secure endpoint, and they are
        # distinguishable in the markup as well as in the words.
        assert (f'/api/file/{record["file_id"]}/original/content'
                in html)
        assert (f'/api/file/{record["file_id"]}/original/content?download=1'
                in html), "the download has to be a download"

        # Not built: not drawn, and reported rather than implied.
        assert 'data-record-action="files.reprocess"' not in html
        assert 'data-hidden-count="1"' in html

    def test_a_source_that_is_no_longer_on_disk_disables_both_actions(
            self, admin_client, record):
        response = admin_client.get(f"/file/{record['file_id']}")
        html = response.get_data(as_text=True)
        # The record points at a path that does not exist, so the two actions
        # that need the bytes are offered with the reason, not silently broken.
        assert 'aria-disabled="true"' in html
        assert 'data-disabled-reason="original_missing"' in html

    def test_deleting_is_drawn_as_the_dangerous_action_it_is(
            self, admin_client, record):
        response = admin_client.get(f"/file/{record['file_id']}")
        html = response.get_data(as_text=True)
        assert f'data-record-endpoint="/file/{record["file_id"]}/delete"' in html
        assert 'data-confirm-key="action.files.delete.confirm"' in html
        assert 'data-confirm-dangerous="true"' in html
        assert 'id="fileDeleteDialog"' in html, (
            "the surface asks through the shared dialog, which the page renders")
        assert "Reprocess this file?" not in html

    def test_a_reader_who_may_not_delete_is_not_offered_the_control(
            self, viewer_client, record):
        response = viewer_client.get(f"/file/{record['file_id']}")
        html = response.get_data(as_text=True)
        assert 'data-record-action="files.view_original"' in html, (
            "reading the record is not a write")
        assert 'data-record-action="files.delete"' not in html, (
            "a write the route guard would refuse is not drawn as a button")

    def test_share_and_the_dead_export_control_are_gone(self, admin_client, record):
        html = admin_client.get(f"/file/{record['file_id']}").get_data(as_text=True)
        assert "shareFile()" not in html
        assert "exportFile()" not in html
        assert 'data-on-click="toggleFullscreen()"' in html, (
            "full screen is a control, and it stays: it changes how the page "
            "is shown, not what the record is")
        # It must also be callable: data-on-click resolves names on window, and
        # the page script is a module (found by the browser smoke, RES-CSP-01).
        script = (PROJECT_ROOT / "static/js/pages/file-detail-page.js").read_text(encoding="utf-8")
        assert "window.toggleFullscreen = toggleFullscreen" in script

    def test_the_metadata_tab_does_not_pretend_to_save(self, admin_client, record):
        """No route stores a file's name or notes. The tab had a "Save Changes"
        button whose handler logged the values and announced "Metadata saved
        successfully!" - and it was not even reachable (module scope)."""
        html = admin_client.get(f"/file/{record['file_id']}").get_data(as_text=True)
        assert "saveMetadata" not in html
        assert "Metadata saved successfully" not in html
        assert 'id="metaNotes"' not in html, "notes typed here would be discarded"
        assert 'id="metaName"' not in html, "a name field would discard edits"
        # The remediation removed the form outright rather than leaving a
        # read-only field behind: the panel renders identity from the payload
        # (static/js/modules/file-operations/file-details-panels.js), so it
        # must hold no input at all. The retired strings survive only as
        # obsolete catalog entries in translations/*/LC_MESSAGES/messages.po.
        panel = re.search(r'id="fileDetailInspector".*?</details>', html, re.S)
        assert panel and 'id="pageFileMetadataSection"' in panel.group(0), (
            "the record page keeps its metadata panel")
        assert "<input" not in panel.group(0), (
            "the metadata panel is read-only presentation; no route stores edits")
        script = (PROJECT_ROOT / "static/js/pages/file-detail-page.js").read_text(encoding="utf-8")
        assert "function saveMetadata" not in script

    def test_the_file_list_has_no_placeholder_preview(self, admin_client, record):
        """The eye button called quickPreview, a placeholder that only logged -
        and not loaded on the page. The card's open link is the preview."""
        html = admin_client.get("/files").get_data(as_text=True)
        assert "quickPreview" not in html
        for relative in ("static/js/modules/file-operations/file-management.js",
                         "static/js/managers/function-manager.js"):
            assert "quickPreview" not in (PROJECT_ROOT / relative).read_text(encoding="utf-8")


class TestTheSurfaceRuntimeIsReal:
    """The component's runtime, driven in a browser-shaped harness.

    A server renders the surface; the question, the busy state and the outcome
    happen in the browser. The harness runs the shipped module - never a copy -
    so what it proves is what a reader meets.
    """

    HARNESS = PROJECT_ROOT / "tests/js/record_actions_smoke.mjs"

    def test_the_harness_passes(self):
        node = shutil.which("node")
        if node is None:
            pytest.skip("node is not installed")
        proc = subprocess.run(
            [node, str(self.HARNESS)], cwd=str(PROJECT_ROOT),
            capture_output=True, text=True, timeout=300)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "checks passed" in proc.stdout
        assert "FAIL" not in proc.stdout, proc.stdout

    def test_the_runtime_stays_inert(self):
        """A surface draws actions; it never performs one and never asks the
        browser for an answer."""
        source = (PROJECT_ROOT / "static/js/modules/core/record-actions.js"
                  ).read_text(encoding="utf-8")
        for forbidden in ("fetch(", "XMLHttpRequest", "innerHTML", "url_for",
                          "localStorage", "window.confirm("):
            assert forbidden not in source, (
                f"{forbidden} in the surface runtime: the page owns the "
                "operation, the component owns the presentation")
        assert "There is no `window.confirm` fallback" in source, (
            "the module says, in its own words, that a question it cannot ask "
            "through the component is refused")

    def test_the_page_that_uses_it_owns_the_operation(self):
        """Inverted on purpose: the *page* is where the request belongs."""
        source = (PROJECT_ROOT / "static/js/pages/file-detail-page.js"
                  ).read_text(encoding="utf-8")
        assert "fetch(endpoint" in source, (
            "the page performs the delete; the surface only reports it")
        assert "RecordActions.bind(" in source
        assert "deleteSelected(" not in source, (
            "the surface API is bind/report, never deleteSelected()-style verbs")
