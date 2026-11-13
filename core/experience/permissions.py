"""The permission vocabulary an action may name.

This module is a *vocabulary*, and it is deliberately the whole of it: a tuple
of names, nothing that evaluates one. There is no ``can(user, permission)``
here, no mapping from a permission to a role, and no branch anywhere in
``core/experience`` that turns one of these strings into a decision.

What it is for: an action definition should be able to say *which permission
domain it belongs to* (``files.download_original``) so that the Screen
Inspector can show it, an administrator can see what an action belongs to, and
the interface can decide whether to draw the control. That is visibility, and
visibility is not authorisation.

What it is not for: security. The server authorises every request on its own,
in ``core/security`` and the route guard, exactly as it did before this module
existed. A name in this tuple grants nothing. An action hidden from a reader is
not therefore protected - the endpoint behind it remains the boundary, and
``tests/security`` keeps proving that the boundary is the server's, not the
screen's.

The names are ``<resource>.<operation>`` and match the action namespaces in
``model.ACTION_NAMESPACES``. They are declared here, in one place, so that two
actions cannot invent two spellings of the same permission and so that adding
one is a visible decision rather than a string typed into a template.

Some of these names belong to actions that are not in the registry yet - the
taxonomy, search and export families. They are kept because the vocabulary is
the product's own description of what it will ask permission for, and the read
API reports them as ``reserved`` rather than hiding the difference: a name that
silently runs ahead of the catalog is how one permission comes to mean two
things.
"""

from __future__ import annotations

from typing import Tuple

ACTION_PERMISSIONS: Tuple[str, ...] = (
    # Files: reading a record, seeing its original, taking a copy of it, and
    # the operations that change stored state.
    "files.view",
    "files.view_original",
    "files.download_original",
    "files.upload",
    "files.analyze",
    "files.export",
    "files.reprocess",
    "files.archive",
    "files.delete",
    # The word and keyword indexes, and the sides and sources they came from.
    "keywords.view",
    "keywords.edit",
    "keywords.delete",
    "words.view",
    "words.edit",
    "words.delete",
    "sources.view",
    "sources.edit",
    "sides.view",
    "sides.edit",
    # Categorisation: assigning what an analyst records, and managing the
    # taxonomy itself. Two permissions, because they are two jobs.
    "categories.view",
    "categories.assign",
    "categories.manage",
    # Search and export: running a query, exporting what it selected.
    "search.run",
    "search.export",
    "export.create",
    # Operations.
    "jobs.view",
    "jobs.cancel",
)

#: The operations each permission covers, for the Screen Inspector and the
#: documentation. Descriptive text only.
PERMISSION_NOTES = {
    "files.view": "See a record and its metadata.",
    "files.view_original": "Render the stored original file.",
    "files.download_original": "Take a byte-identical copy of the original file.",
    "files.upload": "Bring new material in.",
    "files.analyze": "Run analysis over a record or a selection.",
    "files.export": "Export records, their metadata, or their originals.",
    "files.reprocess": "Run the pipeline over a record again.",
    "files.archive": "Move a record out of the active set without deleting it.",
    "files.delete": "Remove a record and its derived data.",
    "keywords.view": "See the keyword index.",
    "keywords.edit": "Change keyword records.",
    "keywords.delete": "Remove keyword records.",
    "words.view": "See the word index.",
    "words.edit": "Change word records.",
    "words.delete": "Remove word records.",
    "sources.view": "See sources.",
    "sources.edit": "Change sources.",
    "sides.view": "See sides.",
    "sides.edit": "Change sides.",
    "categories.view": "See the taxonomy and what is assigned to it.",
    "categories.assign": "Assign or remove analyst categories on records.",
    "categories.manage": "Change the taxonomy itself: create, move, merge, archive.",
    "search.run": "Run a search.",
    "search.export": "Export the result set of a search.",
    "export.create": "Create an export or a research package.",
    "jobs.view": "See the job list and a job's progress.",
    "jobs.cancel": "Cancel a running job.",
}

__all__ = ["ACTION_PERMISSIONS", "PERMISSION_NOTES"]
