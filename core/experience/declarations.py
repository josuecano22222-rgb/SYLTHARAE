"""Declared experience: the screens somebody has actually described.

Everything in this file is *verified against the product* by
``tests/unit/test_experience_contract.py``: a declared column, filter, action
or state must name a string that really appears in one of that interface's
templates, or the test fails. That is the difference between a contract and a wish - a
declaration that has drifted from the screen is a defect, not documentation.

Screens that are not listed here get a contract derived from the registry with
``declared = False``: identity, navigation, lifecycle and help are known, and
the audit says the experience has not been described yet instead of inventing
columns and actions nobody has built.

The keys are semantic and stable (``screen.files.column.name.label``), never the
English sentence, so the wording can change without changing the key and two
identical English words can hold two different translations.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

from .model import (
    ActionDefinition,
    ContractError,
    ColumnDefinition,
    FieldDefinition,
    FilterDefinition,
    HelpDefinition,
    LayoutDefinition,
    ShortcutDefinition,
    StateDefinition,
)

#: Which of a screen's registered actions that screen actually presents.
#:
#: The Action Registry owns what an action *is* - one catalog, one meaning per
#: id, whichever screen shows it. A screen still has to say which of those
#: actions it puts in front of a reader: the file library is attributed eleven
#: actions, and no single page of it shows all eleven. Without this, "the
#: registry says the interface may show it" quietly becomes "every screen of it
#: shows it", and a not-built action is drawn by a page that never intended to.
#:
#: An id here must be registered and attributed to that interface - the tests
#: check both, so this table cannot invent an action or steal another screen's.
SCREEN_ACTIONS: Dict[str, Tuple[str, ...]] = {
    "file_library": (
        # The library list, which the migration has not reached yet.
        "files.select_all", "files.select_none", "files.upload",
        "files.open_record", "files.analyze_selected", "files.export_selected",
        "files.delete_selected",
        # The record page, on the shared action surface.
        "files.view_original", "files.download_original", "files.export_content",
        "files.delete",
        # files.reprocess belongs here - this is the screen a reprocess action
        # would live on - and is listed even though nothing is built: the
        # registry marks it `visibility="not_built"`, the state derivation
        # refuses it, and the surface therefore draws nothing. A declaration
        # says *where* an action belongs; it does not claim the product can do
        # it yet, which is why leaving it out would hide a gap instead of
        # reporting one.
        "files.reprocess",
    ),
    "keywords": (
        "keywords.select_all", "keywords.select_none", "keywords.update",
        "keywords.edit_selected", "keywords.delete_selected",
        "keywords.merge_duplicates",
    ),
    "words": (
        "words.select_all", "words.select_none", "words.edit_selected",
        "words.delete_selected", "words.open", "words.edit", "words.delete",
    ),
    "sources": (
        "sources.select_all", "sources.select_none", "sources.edit_selected",
        "sources.export_selected",
    ),
    "sides": (
        "sides.select_all", "sides.select_none", "sides.edit_selected",
        "sides.export_selected",
    ),
}

#: Where a screen's *bindings* live - the non-template sources that say what
#: each presented action does on that screen. A declaration's evidence used to
#: be a template string only; once a screen binds its actions through the shared
#: surface, the screen's own source of truth is the code that prepares them, and
#: a verification that only read templates would call a migrated screen
#: undeclared.
BINDING_SOURCES: Dict[str, Tuple[str, ...]] = {
    "file_library": ("Api/blueprints/files.py",),
}


#: Screens whose experience has been described. Everything else is derived.
DECLARED_SCREENS: Dict[str, Dict[str, Any]] = {

    # ---------------------------------------------------------------- files
    "file_library": {
        "title": "File Library",
        "columns": (
            ("name", "File Name", "screen.files.column.name.label",
             {"sortable": True, "filterable": True}),
            ("type", "Type", "screen.files.column.type.label",
             {"filterable": True}),
            ("size", "Size", "screen.files.column.size.label",
             {"render": "bytes", "align": "end", "sortable": True}),
            ("source", "Source", "screen.files.column.source.label",
             {"filterable": True}),
            ("side", "Side", "screen.files.column.side.label",
             {"filterable": True}),
            ("status", "Status", "screen.files.column.status.label",
             {"render": "status", "filterable": True}),
            ("date", "Date", "screen.files.column.date.label",
             {"render": "date", "sortable": True}),
        ),
        "filters": (
            ("file_type", "Type", "screen.files.filter.file_type.label",
             {"control": "select"}),
            ("status", "Status", "screen.files.filter.status.label",
             {"control": "select"}),
            ("source", "Source", "screen.files.filter.source.label",
             {"control": "select"}),
            ("side", "Side", "screen.files.filter.side.label",
             {"control": "select"}),
        ),
        "states": (
            ("empty", "No files found.", "state.files.empty.title"),
        ),
        "help": ("files", "File Library",
                 "help.files.title", "help.files.summary"),
    },

    # ------------------------------------------------------------- keywords
    "keywords": {
        "title": "Keyword Intelligence",
        "columns": (
            ("keyword", "Keyword", "screen.keywords.column.keyword.label",
             {"sortable": True, "filterable": True}),
            ("usage", "Usage", "screen.keywords.column.usage.label",
             {"render": "number", "align": "end", "sortable": True}),
            ("status", "Status", "screen.keywords.column.status.label",
             {"render": "status", "filterable": True}),
            ("category", "Category", "screen.keywords.column.category.label",
             {"filterable": True}),
        ),
        "filters": (
            ("status", "Status", "screen.keywords.filter.status.label",
             {"control": "select"}),
            ("category", "Category", "screen.keywords.filter.category.label",
             {"control": "select"}),
        ),
        "states": (
            ("empty", "No keywords found.", "state.keywords.empty.title"),
        ),
        "help": ("keywords", "Keyword Intelligence",
                 "help.keywords.title", "help.keywords.summary"),
    },

    # -------------------------------------------------------------- sources
    # The reference pair for the action toolbar. These are the actions the bar
    # offers, described and not performed: `select_all`/`select_none` act on the
    # page, the other two act on the selection and therefore may not be usable
    # until a scope exists. No permission is claimed here - SYLTHARAE decides
    # authorisation server-side from the account's role, and a permission
    # vocabulary that does not exist yet would be an invention. The execution
    # reference (handler, endpoint) is the page's, not the definition's.
    "sources": {
        "title": "Sources",
        "columns": (
            ("name", "Name", "screen.sources.column.name.label",
             {"sortable": True}),
            ("job", "Job/Type", "screen.sources.column.job.label", {}),
            ("location", "Location", "screen.sources.column.location.label", {}),
            ("importance", "Importance", "screen.sources.column.importance.label",
             {"sortable": True}),
            ("documents", "Documents", "screen.sources.column.documents.label",
             {"render": "number", "align": "end"}),
            ("category", "Category", "screen.sources.column.category.label", {}),
            ("access_status", "Access Status",
             "screen.sources.column.access_status.label", {}),
            ("discovered", "Discovery Date",
             "screen.sources.column.discovered.label",
             {"render": "date", "sortable": True}),
        ),
        "states": (
            ("empty", "No sources found", "state.sources.empty.title"),
        ),
        "help": ("sources", "Sources", "help.sources.title", "help.sources.summary"),
    },

    # ---------------------------------------------------------------- sides
    "sides": {
        "title": "Sides",
        "columns": (
            ("name", "Name", "screen.sides.column.name.label",
             {"sortable": True}),
            ("importance", "Importance", "screen.sides.column.importance.label",
             {"sortable": True}),
            ("sources", "Sources", "screen.sides.column.sources.label",
             {"render": "number", "align": "end"}),
            ("documents", "Documents", "screen.sides.column.documents.label",
             {"render": "number", "align": "end"}),
            ("created", "Created", "screen.sides.column.created.label",
             {"render": "date", "sortable": True}),
        ),
        "states": (
            ("empty", "No sides found", "state.sides.empty.title"),
        ),
        "help": ("sides", "Sides", "help.sides.title", "help.sides.summary"),
    },

    # ---------------------------------------------------------------- words
    "words": {
        "title": "Word Index",
        "columns": (
            ("word", "Word", "screen.words.column.word.label",
             {"sortable": True, "filterable": True}),
            ("usage", "Usage Count", "screen.words.column.usage.label",
             {"render": "number", "align": "end", "sortable": True}),
            ("status", "Status", "screen.words.column.status.label",
             {"render": "status"}),
        ),
        "states": (
            ("empty", "No words found.", "state.words.empty.title"),
        ),
    },
}


def columns(interface_id: str) -> Tuple[ColumnDefinition, ...]:
    entries = DECLARED_SCREENS.get(interface_id, {}).get("columns", ())
    return tuple(
        ColumnDefinition(column_id=column_id, label_key=key, source=source,
                         **options)
        for column_id, source, key, options in entries
    )


def filters(interface_id: str) -> Tuple[FilterDefinition, ...]:
    entries = DECLARED_SCREENS.get(interface_id, {}).get("filters", ())
    return tuple(
        FilterDefinition(filter_id=filter_id, label_key=key, source=source,
                         **options)
        for filter_id, source, key, options in entries
    )


def actions(interface_id: str) -> Tuple[ActionDefinition, ...]:
    """The actions this screen presents, from the Action Registry.

    Two tables meet here and neither duplicates the other: ``SCREEN_ACTIONS``
    says *which* actions a screen offers, and the Action Registry says what each
    of them is - scope, permission, confirmation, operation reference. Nothing
    restates a definition, so the same action cannot mean two things on two
    screens, and a screen cannot present an action that does not exist.
    """
    from .action_registry import for_interface

    wanted = SCREEN_ACTIONS.get(interface_id)
    if wanted is None:
        # Nobody has declared this screen's actions, so the registry's own
        # account stands: a screen that has not been described yet is not a
        # screen with no actions, and dropping its actions would make the
        # contract quietly smaller than the product.
        return tuple(for_interface(interface_id))
    available = {item.action_id: item for item in for_interface(interface_id)}
    missing = [action_id for action_id in wanted
               if action_id not in available]
    if missing:
        raise ContractError(
            f"{interface_id}: SCREEN_ACTIONS names {missing}, which the Action "
            "Registry does not attribute to this interface")
    return tuple(available[action_id] for action_id in wanted)


def binding_sources(interface_id: str) -> Tuple[str, ...]:
    """The files, besides the templates, where this screen's bindings live."""
    return BINDING_SOURCES.get(interface_id, ())


def states(interface_id: str, prefix: str) -> Tuple[StateDefinition, ...]:
    entries = DECLARED_SCREENS.get(interface_id, {}).get("states", ())
    return tuple(
        StateDefinition(state=state, title_key=key, source=source,
                        message_key=f"{key.rsplit('.', 1)[0]}.message")
        for state, source, key in entries
    )


def help_definition(interface_id: str) -> HelpDefinition | None:
    entry = DECLARED_SCREENS.get(interface_id, {}).get("help")
    if not entry:
        return None
    topic, source, title_key, summary_key = entry
    return HelpDefinition(topic=topic, title_key=title_key, source=source,
                          summary_key=summary_key)


def title(interface_id: str) -> str | None:
    return DECLARED_SCREENS.get(interface_id, {}).get("title")


def fields(interface_id: str) -> Tuple[FieldDefinition, ...]:
    entries = DECLARED_SCREENS.get(interface_id, {}).get("fields", ())
    return tuple(
        FieldDefinition(field_id=field_id, label_key=key, source=source,
                        **options)
        for field_id, source, key, options in entries
    )


def shortcuts(interface_id: str) -> Tuple[ShortcutDefinition, ...]:
    entries = DECLARED_SCREENS.get(interface_id, {}).get("shortcuts", ())
    return tuple(
        ShortcutDefinition(shortcut=shortcut, action_key=key)
        for shortcut, key in entries
    )


def layout(interface_id: str) -> LayoutDefinition:
    return LayoutDefinition()


__all__ = [
    "DECLARED_SCREENS",
    "actions",
    "columns",
    "fields",
    "filters",
    "help_definition",
    "layout",
    "shortcuts",
    "states",
    "title",
]
