"""The Action Registry: every action the product offers, in one catalog.

The registry answers one question well - *what can a person do here?* - and it
answers it in one place, so the same action cannot mean two things on two
screens. For every action it says what it is, where it can appear, what it acts
on, which permission domain it belongs to, whether it is destructive and must
be confirmed, and which operation owns it.

Five rules hold here, and the tests enforce them:

* **The id is a resource and a name** (``files.delete_selected``), never a page
  and never a button. An action that appears on a second screen keeps its id.
* **It describes; it never executes.** ``execution`` is an opaque operation
  reference such as ``download_original``. It is not a route, not a URL, not a
  Flask endpoint and not a Python callable, and nothing in this package can
  resolve it. The registry is not a second router, and there is deliberately no
  ``run()`` here for anyone to be tempted by.
* **An absent operation is stated, not hidden.** An action whose operation does
  not exist yet carries ``execution=None`` and is counted, by name, as declared
  work that no service owns. That is a finding for the audit, not a defect to
  paper over.
* **Confirmation is required of anything destructive**, and a bulk action acts
  on the whole selection: an action that acts on one member of a selection is a
  selection action with ``selection_rule="one"``.
* **A permission is a name.** It says which domain the action belongs to. The
  server authorises the request on its own, exactly as before this module
  existed, and a hidden action is not a protected one.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .model import ACTION_SCOPES, ActionDefinition

#: The catalog. One entry per action, ordered so the file reads like the
#: product: files, then the indexes they produce, then sources and sides, then
#: operations. Every option is declared here and nowhere else - a page declares
#: which screen it is, not what its buttons mean.
ACTION_REGISTRY: Tuple[ActionDefinition, ...] = (
    # ------------------------------------------------------------------ files
    ActionDefinition(
        action_id="files.select_all", source="Select All",
        label_key="action.files.select_all.label",
        interfaces=("file_library",), scope="page",
        permission="files.view", execution="select_all",
        icon="bi-check-square"),
    ActionDefinition(
        action_id="files.select_none", source="Select None",
        label_key="action.files.select_none.label",
        interfaces=("file_library",), scope="page",
        permission="files.view", execution="select_none",
        icon="bi-square"),
    ActionDefinition(
        action_id="files.upload", source="Upload Files",
        label_key="action.files.upload.label",
        interfaces=("file_library",), scope="page",
        permission="files.upload", execution="upload_files",
        icon="bi-upload"),
    ActionDefinition(
        action_id="files.open_record", source="View Details",
        label_key="action.files.open_record.label",
        interfaces=("file_library",), scope="record",
        permission="files.view", execution="view_details",
        icon="bi-eye"),
    ActionDefinition(
        action_id="files.analyze_selected", source="Analyze",
        label_key="action.files.analyze_selected.label",
        interfaces=("file_library",), scope="bulk",
        permission="files.analyze", execution="analyze_selected",
        requires_selection=True, selection_rule="all", loading="toast",
        icon="bi-lightning-charge"),
    ActionDefinition(
        action_id="files.export_selected", source="Export",
        label_key="action.files.export_selected.label",
        interfaces=("file_library",), scope="bulk",
        permission="files.export", execution="export_selected",
        requires_selection=True, selection_rule="all", loading="toast",
        icon="bi-download"),
    ActionDefinition(
        action_id="files.delete_selected", source="Delete",
        label_key="action.files.delete_selected.label",
        interfaces=("file_library",), scope="bulk",
        permission="files.delete", destructive=True,
        confirmation="action.files.delete_selected.confirm",
        requires_selection=True, selection_rule="all",
        execution="delete_selected", icon="bi-trash"),
    ActionDefinition(
        action_id="files.delete", source="Delete",
        label_key="action.files.delete.label",
        interfaces=("file_library",), scope="record",
        permission="files.delete", destructive=True,
        confirmation="action.files.delete.confirm",
        execution="delete_file", icon="bi-trash"),
    ActionDefinition(
        action_id="files.reprocess", source="Reprocess",
        label_key="action.files.reprocess.label", interfaces=("file_library",),
        scope="record", permission="files.reprocess", loading="toast",
        icon="bi-arrow-clockwise",
        # NOT BUILT, and declared as such rather than dressed up. The page
        # carried a link to /file/<id>/reprocess; no route ever served it, and
        # re-ingesting the stored path is refused as a duplicate by
        # contents_db_service (a hash with a stored path returns the existing
        # record), so there is no operation to reference. See the gaps table in
        # docs/ACTION_SURFACE_AUDIT.md.
        visibility="not_built"),
    ActionDefinition(
        action_id="files.view_original", source="View Original",
        label_key="action.files.view_original.label",
        interfaces=("file_library",), scope="record",
        permission="files.view_original", execution="view_original",
        icon="bi-file-earmark-image"),
    ActionDefinition(
        action_id="files.export_content", source="Export Extracted Text",
        label_key="action.files.export_content.label",
        interfaces=("file_library",), scope="record",
        permission="files.export", execution="export_file_content",
        icon="bi-file-earmark-arrow-down"),
    ActionDefinition(
        action_id="files.download_original", source="Download Original",
        label_key="action.files.download_original.label",
        interfaces=("file_library",), scope="record",
        permission="files.download_original", execution="download_original",
        icon="bi-download"),

    # --------------------------------------------------------------- keywords
    ActionDefinition(
        action_id="keywords.select_all", source="Select All",
        label_key="action.keywords.select_all.label",
        interfaces=("keywords",), scope="page",
        permission="keywords.view", execution="select_all",
        icon="bi-check-square"),
    ActionDefinition(
        action_id="keywords.select_none", source="Select None",
        label_key="action.keywords.select_none.label",
        interfaces=("keywords",), scope="page",
        permission="keywords.view", execution="select_none",
        icon="bi-square"),
    ActionDefinition(
        action_id="keywords.update", source="Update Keywords",
        label_key="action.keywords.update.label",
        interfaces=("keywords",), scope="page",
        permission="keywords.edit", execution="update_keywords",
        icon="bi-arrow-repeat"),
    ActionDefinition(
        action_id="keywords.edit_selected", source="Edit Selected",
        label_key="action.keywords.edit_selected.label",
        interfaces=("keywords",), scope="selection",
        permission="keywords.edit",
        requires_selection=True, selection_rule="one",
        execution="edit_keyword", icon="bi-pencil"),
    ActionDefinition(
        action_id="keywords.delete_selected", source="Delete Selected",
        label_key="action.keywords.delete_selected.label",
        interfaces=("keywords",), scope="bulk",
        permission="keywords.delete", destructive=True,
        confirmation="action.keywords.delete_selected.confirm",
        requires_selection=True, selection_rule="all",
        execution="delete_keywords", icon="bi-trash"),
    ActionDefinition(
        action_id="keywords.merge_duplicates", source="Merge Duplicates",
        label_key="action.keywords.merge_duplicates.label",
        interfaces=("keywords",), scope="page",
        permission="keywords.delete", destructive=True,
        confirmation="action.keywords.merge_duplicates.confirm",
        execution="merge_duplicate_keywords", icon="bi-diagram-3"),

    # ------------------------------------------------------------------ words
    ActionDefinition(
        action_id="words.select_all", source="Select All",
        label_key="action.words.select_all.label",
        interfaces=("words",), scope="page",
        permission="words.view", execution="select_all",
        icon="bi-check-square"),
    ActionDefinition(
        action_id="words.select_none", source="Select None",
        label_key="action.words.select_none.label",
        interfaces=("words",), scope="page",
        permission="words.view", execution="select_none",
        icon="bi-square"),
    ActionDefinition(
        action_id="words.edit_selected", source="Edit Selected",
        label_key="action.words.edit_selected.label",
        interfaces=("words",), scope="selection",
        permission="words.edit",
        requires_selection=True, selection_rule="one",
        execution="edit_word", icon="bi-pencil"),
    ActionDefinition(
        action_id="words.delete_selected", source="Delete Selected",
        label_key="action.words.delete_selected.label",
        interfaces=("words",), scope="bulk",
        permission="words.delete", destructive=True,
        confirmation="action.words.delete_selected.confirm",
        requires_selection=True, selection_rule="all",
        execution="delete_words", icon="bi-trash"),
    ActionDefinition(
        action_id="words.open", source="View Details",
        label_key="action.words.open.label",
        interfaces=("words",), scope="record",
        permission="words.view", execution="view_details", icon="bi-eye"),
    ActionDefinition(
        action_id="words.edit", source="Edit",
        label_key="action.words.edit.label",
        interfaces=("words",), scope="record",
        permission="words.edit", execution="edit_word", icon="bi-pencil"),
    ActionDefinition(
        action_id="words.delete", source="Delete",
        label_key="action.words.delete.label",
        interfaces=("words",), scope="record",
        permission="words.delete", destructive=True,
        confirmation="action.words.delete.confirm",
        execution="delete_word", icon="bi-trash"),

    # ---------------------------------------------------------------- sources
    ActionDefinition(
        action_id="sources.select_all", source="Select All",
        label_key="action.sources.select_all.label",
        interfaces=("sources",), scope="page",
        permission="sources.view", execution="select_all",
        icon="bi-check-square"),
    ActionDefinition(
        action_id="sources.select_none", source="Select None",
        label_key="action.sources.select_none.label",
        interfaces=("sources",), scope="page",
        permission="sources.view", execution="select_none",
        icon="bi-square"),
    ActionDefinition(
        action_id="sources.edit_selected", source="Edit Selected",
        label_key="action.sources.edit_selected.label",
        interfaces=("sources",), scope="bulk",
        permission="sources.edit",
        requires_selection=True, selection_rule="all",
        # No service operation owns this yet: the button is on the screen and
        # the audit counts it as declared work nobody has built.
        execution=None, icon="bi-pencil"),
    ActionDefinition(
        action_id="sources.export_selected", source="Export Selected",
        label_key="action.sources.export_selected.label",
        interfaces=("sources",), scope="bulk",
        permission="export.create",
        requires_selection=True, selection_rule="all",
        execution=None, icon="bi-download"),

    # ------------------------------------------------------------------ sides
    ActionDefinition(
        action_id="sides.select_all", source="Select All",
        label_key="action.sides.select_all.label",
        interfaces=("sides",), scope="page",
        permission="sides.view", execution="select_all",
        icon="bi-check-square"),
    ActionDefinition(
        action_id="sides.select_none", source="Select None",
        label_key="action.sides.select_none.label",
        interfaces=("sides",), scope="page",
        permission="sides.view", execution="select_none",
        icon="bi-square"),
    ActionDefinition(
        action_id="sides.edit_selected", source="Edit Selected",
        label_key="action.sides.edit_selected.label",
        interfaces=("sides",), scope="bulk",
        permission="sides.edit",
        requires_selection=True, selection_rule="all",
        execution=None, icon="bi-pencil"),
    ActionDefinition(
        action_id="sides.export_selected", source="Export Selected",
        label_key="action.sides.export_selected.label",
        interfaces=("sides",), scope="bulk",
        permission="export.create",
        requires_selection=True, selection_rule="all",
        execution=None, icon="bi-download"),

    # ------------------------------------------------------------- operations
    ActionDefinition(
        action_id="jobs.cancel", source="Cancel",
        label_key="action.jobs.cancel.label",
        interfaces=("jobs",), scope="record",
        permission="jobs.cancel",
        # Cancelling stops work in flight. It destroys no record, so it is not
        # destructive - and it still asks first, because it cannot be undone.
        confirmation="action.jobs.cancel.confirm",
        execution="cancel_job", loading="toast", icon="bi-x-circle"),
)


def _unique_ids(actions: Tuple[ActionDefinition, ...]) -> None:
    """Two actions with one id is how a registry stops being authoritative."""
    from .model import ContractError

    seen: Dict[str, str] = {}
    for item in actions:
        previous = seen.get(item.action_id)
        if previous is not None:
            raise ContractError(
                f"action id {item.action_id!r} is registered twice "
                f"({previous} and {', '.join(item.interfaces)})")
        seen[item.action_id] = ", ".join(item.interfaces)


_unique_ids(ACTION_REGISTRY)


# ---------------------------------------------------------------------------
# Reading the catalog
# ---------------------------------------------------------------------------
def registered() -> Tuple[ActionDefinition, ...]:
    return ACTION_REGISTRY


def action(action_id: str) -> Optional[ActionDefinition]:
    for item in ACTION_REGISTRY:
        if item.action_id == action_id:
            return item
    return None


def _check_scope(scope: str) -> None:
    from .model import ACTION_SCOPES

    if scope not in ACTION_SCOPES:
        raise ValueError(
            f"unknown scope {scope!r}; an action applies to one of "
            f"{list(ACTION_SCOPES)}")


def for_interface(interface_id: str) -> Tuple[ActionDefinition, ...]:
    """The actions one screen may show, in declaration order."""
    return tuple(item for item in ACTION_REGISTRY
                 if interface_id in item.interfaces)


def all_actions() -> Tuple[ActionDefinition, ...]:
    """Every declared action, in declaration order (the catalog itself)."""
    return ACTION_REGISTRY


def registered_ids() -> Tuple[str, ...]:
    """Every action id, in declaration order."""
    return tuple(item.action_id for item in ACTION_REGISTRY)


def actions_for(interface_id: str) -> Tuple[ActionDefinition, ...]:
    """Every action one screen may show, whatever its scope."""
    return for_interface(interface_id)


def actions_for_scope(interface_id: str, scope: str) -> Tuple[ActionDefinition, ...]:
    """The actions one screen may show for one scope."""
    _check_scope(scope)
    return tuple(item for item in for_interface(interface_id)
                 if item.scope == scope)


def not_built() -> Tuple[ActionDefinition, ...]:
    """Actions the product declares and no operation performs yet."""
    return tuple(item for item in ACTION_REGISTRY if not item.built)


def without_operation() -> Tuple[ActionDefinition, ...]:
    """Declared actions no service operation owns yet, by name."""
    return tuple(item for item in ACTION_REGISTRY if not item.execution)


def by_namespace() -> Dict[str, Tuple[ActionDefinition, ...]]:
    groups: Dict[str, List[ActionDefinition]] = {}
    for item in ACTION_REGISTRY:
        groups.setdefault(item.namespace, []).append(item)
    return {name: tuple(items) for name, items in groups.items()}


def namespaced(interface_id: str) -> Dict[str, str]:
    """``action_id -> execution`` for one screen.

    The one thing a page needs from the registry: which operation each control
    asks for, keyed by the action it belongs to.
    """
    return {item.action_id: item.execution or ""
            for item in for_interface(interface_id)}


def counts() -> Dict[str, object]:
    rows = list(ACTION_REGISTRY)
    return {
        "actions": len(rows),
        "interfaces_with_actions": len({i for item in rows
                                        for i in item.interfaces}),
        "by_scope": {scope: sum(1 for item in rows if item.scope == scope)
                     for scope in ACTION_SCOPES},
        "by_namespace": {name: len(items) for name, items in by_namespace().items()},
        "destructive": sum(1 for item in rows if item.destructive),
        "with_confirmation": sum(1 for item in rows if item.confirmation),
        "with_permission": sum(1 for item in rows if item.permission),
        "with_execution": sum(1 for item in rows if item.execution),
        "without_execution": len(without_operation()),
        # Declared and not built: counted, named by the audit, never drawn.
        "not_built": len(not_built()),
        "not_built_ids": ", ".join(item.action_id for item in not_built()),
        "selection_actions": sum(1 for item in rows
                                 if item.scope == "selection"),
        "kinds": {
            "page": sum(1 for item in rows if item.scope == "page"),
            "record": sum(1 for item in rows if item.scope == "record"),
            "selection": sum(1 for item in rows if item.scope == "selection"),
            "bulk": sum(1 for item in rows if item.scope == "bulk"),
        },
    }


def to_dict() -> Dict[str, object]:
    """The registry as one object: every action, plus what it adds up to."""
    return {
        "actions": [item.to_dict() for item in ACTION_REGISTRY],
        "counts": counts(),
        "registered_ids": list(registered_ids()),
    }


def to_json() -> List[Dict]:
    return [item.to_dict() for item in ACTION_REGISTRY]


__all__ = [
    "ACTION_REGISTRY",
    "action",
    "actions_for",
    "actions_for_scope",
    "all_actions",
    "by_namespace",
    "counts",
    "for_interface",
    "namespaced",
    "not_built",
    "registered",
    "registered_ids",
    "to_dict",
    "to_json",
    "without_operation",
]
