"""Translation is manual: no ingestion, indexing or detection path reaches it.

The feature exists because a human asked for a translation of a selection. It
must never run as part of ingestion, indexing, classification or a detector:
those run unattended over the whole corpus, and a machine translation written
into the stored material would be indistinguishable from source text later.

This test walks the import graph instead of trusting a code review. It fails
when a module in the ingestion, detection, indexing or storage path imports
``services.translation`` - directly, or through a ``from services import
translation`` form - and it fails in the other direction too: the translation
package must not import those modules, so nothing in it can be tempted to run
during a pipeline pass.
"""

import ast
import pathlib

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: Modules that run without a human in front of them.
AUTOMATIC_ROOTS = (
    "pipeline",
    "services/ingesting",
    "services/detection",
    "services/processing",
    "services/storage",
    "core/detection",
    "apps/importing",
)

TRANSLATION_PACKAGE = "services/translation"


def _python_files(relative_root):
    base = PROJECT_ROOT / relative_root
    if not base.exists():
        return []
    return [path for path in base.rglob("*.py")
            if "__pycache__" not in path.parts]


def _imports(path):
    """Every module name *path* imports, as written."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:                     # relative import: resolve it
                continue
            module = node.module or ""
            names.add(module)
            names.update(f"{module}.{alias.name}" for alias in node.names)
    return names


def test_no_automatic_path_imports_the_translation_package():
    offenders = []
    for root in AUTOMATIC_ROOTS:
        for path in _python_files(root):
            for name in _imports(path):
                if name == TRANSLATION_PACKAGE or name.startswith(TRANSLATION_PACKAGE + "."):
                    offenders.append(f"{path.relative_to(PROJECT_ROOT)} -> {name}")
    assert not offenders, (
        "translation must stay manual, but these automatic-path modules import it: "
        + "; ".join(sorted(offenders)))


def test_the_translation_package_does_not_import_a_pipeline_module():
    offenders = []
    forbidden = tuple(root.replace("/", ".") for root in AUTOMATIC_ROOTS)
    for path in _python_files(TRANSLATION_PACKAGE):
        for name in _imports(path):
            if name.startswith(forbidden):
                offenders.append(f"{path.relative_to(PROJECT_ROOT)} -> {name}")
    assert not offenders, (
        "the translation package imported a pipeline module: "
        + "; ".join(sorted(offenders)))


def test_the_translation_package_does_not_write_into_stored_content():
    """No INSERT/UPDATE/DELETE against the tables that hold the corpus.

    Reading a stored document to translate it is fine (that is a SELECT);
    writing the translation back is not, and the check is textual on purpose:
    it also catches a write hidden in a helper.
    """
    forbidden = ("content_signals", "hash_contexts", "titles_content",
                 "words_hashs", "INSERT INTO paths", "UPDATE paths",
                 "INSERT INTO contents", "UPDATE contents")
    offenders = []
    for path in _python_files(TRANSLATION_PACKAGE):
        source = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in source:
                offenders.append(f"{path.relative_to(PROJECT_ROOT)} mentions {token}")
    assert not offenders, (
        "translation wrote into the stored corpus: " + "; ".join(sorted(offenders)))
