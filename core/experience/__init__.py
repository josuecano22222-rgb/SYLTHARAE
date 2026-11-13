"""The SYLTHARAE Experience Contract.

The registry says what the product offers. ``templates/components`` says how a
repeated piece of interface is drawn. This package is the layer between them:
the declarative description of how one screen behaves and presents itself -
its navigation entry, actions, columns, filters, states, help, shortcuts,
layout and the translation keys it needs.

It is description, not behaviour. Nothing here queries a database, decides
access, or executes anything a configuration file asked for; the checks in
``validation`` fail the build if a definition ever tries.
"""

from . import action_registry, declarations
from .contract import (
    build_contract,
    contract,
    contracts,
    contracts_json,
    counts as contract_counts,
    screens_without_experience,
    translation_index,
)
from .coverage import (
    available_locales,
    catalog,
    counts as coverage_counts,
    hardcoded_source_strings,
    language_coverage,
    screen_coverage,
    source_strings,
)
from .permissions import ACTION_PERMISSIONS, PERMISSION_NOTES
from .model import (
    ACTION_NAMESPACES,
    ACTION_SCOPES,
    ACTION_STATES,
    ActionDefinition,
    ActionState,
    DISABLED_REASONS,
    HIDDEN_REASONS,
    SELECTION_RULES,
    ColumnDefinition,
    ContractError,
    ExperienceContract,
    FieldDefinition,
    FilterDefinition,
    HelpDefinition,
    LayoutDefinition,
    NavigationDefinition,
    SCREEN_STATES,
    ScreenConfiguration,
    ShortcutDefinition,
    StateDefinition,
    TranslationDefinition,
    check_placeholders,
    placeholders,
)
from .validation import (
    check_all,
    check_contract,
    check_declarative,
    check_translation_edit,
    check_translations,
)

__all__ = [
    "ACTION_NAMESPACES",
    "ACTION_PERMISSIONS",
    "ACTION_SCOPES",
    "ACTION_STATES",
    "ActionDefinition",
    "ActionState",
    "DISABLED_REASONS",
    "HIDDEN_REASONS",
    "PERMISSION_NOTES",
    "SELECTION_RULES",
    "ColumnDefinition",
    "ContractError",
    "ExperienceContract",
    "FieldDefinition",
    "FilterDefinition",
    "HelpDefinition",
    "LayoutDefinition",
    "NavigationDefinition",
    "SCREEN_STATES",
    "ScreenConfiguration",
    "ShortcutDefinition",
    "StateDefinition",
    "TranslationDefinition",
    "available_locales",
    "action_registry",
    "declarations",
    "build_contract",
    "catalog",
    "check_all",
    "check_contract",
    "check_declarative",
    "check_placeholders",
    "check_translation_edit",
    "check_translations",
    "contract",
    "contract_counts",
    "contracts",
    "contracts_json",
    "coverage_counts",
    "hardcoded_source_strings",
    "language_coverage",
    "placeholders",
    "screen_coverage",
    "screens_without_experience",
    "source_strings",
    "translation_index",
]
