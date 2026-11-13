"""Unit: every repository method the pipeline calls must actually exist.

This closes a whole defect class, not just the instances found. Calls into
``db_service.<repo>.<method>`` named methods that do not exist (the
error-recovery fallback in storage_pipeline called ``insert_path``/``insert_hash``;
the phantom names raised AttributeError inside an ``except`` handler and the
recovery silently did nothing).

Static verification is the right tool here: the fallback only runs after a
storage failure, and when it is reached the failure is swallowed. Resolving
attribute names against the real classes catches every instance, including
ones not yet written.

After the content-identity evolution (HASH = content identity, HASH+SOURCE+SIDE
= context identity, PATH = occurrence) the authoritative surface is:

    ContentDBService.resolve_hash_id / resolve_context_id /
    register_occurrence / hash_exists / check_duplicate
    PathsRepository.insert_info_paths(context_id=...)
    WordsHashsRepository / KeywordsHashsRepository   (content-keyed index)

The retired surface (competing identity implementations) is pinned as ABSENT
below: hashs_repo / words_paths_repo / keywords_paths_repo / create_hash /
create_path / link_words_to_path / process_keywords_for_path /
get_path_id_by_hash_id must never reappear.
"""

import ast
import inspect
import sys
import textwrap
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from database.services.contents_db_service import ContentDBService  # noqa: E402

#: Files whose calls into ``self.db_service.<repo>.<method>`` are checked.
SCANNED_FILES = [
    PROJECT_ROOT / "pipeline" / "storage_pipeline.py",
    PROJECT_ROOT / "database" / "services" / "contents_db_service.py",
]


def _repo_attributes():
    """Map ``db_service`` attribute name -> repository class, from the source.

    Derived from the class rather than hardcoded, so a repository added later is
    covered automatically instead of silently skipped. The whole class is
    scanned, not one method: the assignments live in _init_repositories, and
    naming a specific method made this return an empty map - which is exactly
    what test_repo_map_was_actually_discovered exists to catch.
    """
    # getsource returns the class with its module-level indentation intact,
    # which ast.parse rejects; dedent first.
    source = textwrap.dedent(inspect.getsource(ContentDBService))
    tree = ast.parse(source)
    mapping = {}
    for node in ast.walk(tree):
        # self.<attr> = <ClassName>(self.db)
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not (isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)
                and target.value.id == "self"):
            continue
        call = node.value
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)):
            continue
        mapping[target.attr] = call.func.id
    return mapping


REPO_ATTRS = _repo_attributes()


def _repository_class(class_name):
    import importlib
    import pkgutil

    package = importlib.import_module("database.database.repository")
    for _, module_name, _ in pkgutil.iter_modules(package.__path__):
        module = importlib.import_module(f"database.database.repository.{module_name}")
        candidate = getattr(module, class_name, None)
        if isinstance(candidate, type):
            return candidate
    return None


def _calls_into_repos(path):
    """Yield (lineno, attr, method) for every self.db_service.<attr>.<method>(...)."""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        method = node.func.attr
        receiver = node.func.value
        if not (isinstance(receiver, ast.Attribute) and isinstance(receiver.value, ast.Attribute)):
            continue
        if receiver.value.attr != "db_service":
            continue
        yield node.lineno, receiver.attr, method


def test_repo_map_was_actually_discovered():
    """Guard the guard: if the map came back empty the test proves nothing."""
    assert REPO_ATTRS, "no repository attributes discovered from __init__"
    assert "paths_repo" in REPO_ATTRS and "words_hashs_repo" in REPO_ATTRS, sorted(REPO_ATTRS)
    assert "keywords_hashs_repo" in REPO_ATTRS, sorted(REPO_ATTRS)


def test_every_repository_class_resolves():
    for attr, class_name in sorted(REPO_ATTRS.items()):
        assert _repository_class(class_name) is not None, (attr, class_name)


@pytest.mark.parametrize("path", SCANNED_FILES, ids=[p.name for p in SCANNED_FILES])
def test_no_phantom_repository_methods(path):
    """Every ``db_service.<repo>.<method>()`` must name a real method."""
    assert path.exists(), path
    offenders = []
    for lineno, attr, method in _calls_into_repos(path):
        class_name = REPO_ATTRS.get(attr)
        if class_name is None:
            # Not a repository attribute (e.g. db_service.db, .transaction);
            # only repository dispatch is under test here.
            continue
        klass = _repository_class(class_name)
        if klass is None:
            offenders.append((lineno, attr, method, "class not found"))
            continue
        if not hasattr(klass, method):
            offenders.append((lineno, attr, method, class_name))
    assert not offenders, (
        f"{path.name} calls repository methods that do not exist: {offenders}. "
        "These raise AttributeError at runtime, and when the call sits inside an "
        "error handler the failure is swallowed and the operation silently "
        "does nothing."
    )


def test_the_retired_identity_surface_is_gone():
    """The old per-path identity modules were competing implementations.

    One authoritative implementation per concern (dedup = DeduplicationService;
    registration = ContentDBService.register_occurrence). The retired names
    must never reappear in the service or the repositories.
    """
    import importlib

    from database.database.repository.paths_repo import PathsRepository

    assert hasattr(PathsRepository, "insert_info_paths")
    assert not hasattr(PathsRepository, "insert_path"), (
        "insert_path has appeared; callers must use insert_info_paths"
    )

    for retired in (
        "database.database.repository.hashs_repo",
        "database.database.repository.words_paths_repo",
        "database.database.repository.keywords_paths_repo",
        "database.database.queries.hash_queries",
        "database.database.queries.word_path_queries",
        "database.database.queries.keyword_path_queries",
    ):
        try:
            importlib.import_module(retired)
        except ModuleNotFoundError:
            pass
        else:
            raise AssertionError(f"{retired} has reappeared; it is a competing identity implementation")

    service = ContentDBService.__new__(ContentDBService)  # no connection needed
    for retired in ("create_hash", "create_path", "get_path_id_by_hash_id",
                    "link_words_to_path", "process_keywords_for_path",
                    "hashs_repo", "words_paths_repo", "keywords_paths_repo"):
        assert not hasattr(service, retired), retired
    for live in ("resolve_hash_id", "resolve_context_id", "register_occurrence",
                 "hash_exists", "check_duplicate"):
        assert hasattr(ContentDBService, live), live


def test_no_source_file_calls_the_phantom_names():
    """Textual sweep, because an AST walk alone cannot prove absence in strings."""
    for path in SCANNED_FILES:
        text = path.read_text()
        for phantom in ("paths_repo.insert_path(", "hashs_repo.insert_hash(",
                        ".hashs_repo.", "words_paths_repo", "keywords_paths_repo",
                        "create_hash(", "link_words_to_path",
                        "process_keywords_for_path", "get_path_id_by_hash_id"):
            occurrences = [
                i + 1 for i, line in enumerate(text.splitlines())
                if phantom in line and not line.strip().startswith("#")
            ]
            assert not occurrences, f"{path.name}: {phantom} at lines {occurrences}"


def test_insert_info_paths_accepts_the_status_columns():
    """The fallback now records a truthful failed status; the signature must allow it."""
    from database.database.repository.paths_repo import PathsRepository

    params = inspect.signature(PathsRepository.insert_info_paths).parameters
    for name in ("processing_status", "status_detail", "attempts",
                 "file_name", "file_path", "file_size", "file_type",
                 "file_status", "file_date", "context_id", "coordinates",
                 "parent_path_id", "hierarchy_path"):
        assert name in params, (name, list(params))
    assert "hash_id" not in params, (
        "paths rows identify their CONTEXT (context_id); content identity lives "
        "on hash_contexts -> hashs"
    )


def test_register_occurrence_signature_matches_the_call():
    """The single registration entry point: content + context + occurrence."""
    params = inspect.signature(ContentDBService.register_occurrence).parameters
    for name in ("hash_value", "source_id", "side_id", "path_row", "commit"):
        assert name in params, (name, list(params))
