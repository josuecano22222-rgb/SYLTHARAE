"""docs/ARCHITECTURE.md and docs/DOMAIN_MODEL.md, derived from the code.

The two documents a reviewer reads first were, until this test, checked only
where a diagram happened to name a constant: the job states. Everything else
was prose that could drift - and two of its sentences had. This module takes
every checkable sentence in both documents and derives it from the code:

* every path a diagram names exists;
* every import edge the layer table states is measured, and its exceptions
  are pinned by name so a new one fails here;
* every method named on the ingestion sequence exists on the class it is
  attributed to;
* every concept in the domain model has the table and columns it claims;
* every claim that is *not* checkable is listed as unverified, in the test, so
  a sentence cannot be quietly promoted to "checked" by moving it.

When a sentence here fails, the document is wrong or the code changed; the
fix is to correct the document, not this list.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ARCHITECTURE = (PROJECT_ROOT / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
DOMAIN = (PROJECT_ROOT / "docs" / "DOMAIN_MODEL.md").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Reading the code
# --------------------------------------------------------------------------- #
def _imports(path: Path) -> set:
    """Module names imported by one file, whatever the nesting."""
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module)
    return found


def _import_statements(package: str, prefixes: tuple) -> list:
    """One entry per import statement, so a file importing twice counts twice."""
    statements = []
    for path in sorted((PROJECT_ROOT / package).rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                modules = [node.module]
            for module in modules:
                if module.startswith(prefixes):
                    statements.append((str(path.relative_to(PROJECT_ROOT)), module))
    return statements


def _import_edges(package: str, prefixes: tuple) -> set:
    """(importer, imported) pairs where a module in `package` imports `prefixes`."""
    return set(_import_statements(package, prefixes))


#: The layer rows in docs/ARCHITECTURE.md that say "may not", and what a breach
#: of each would look like.
FORBIDDEN_EDGES = {
    "routes must not import a reader directly": ("Api", ("reader_file",)),
    "core must not import a route or a service": ("core", ("Api", "services")),
    "readers must not import the database": ("reader_file/readers", ("database",)),
}

#: The breaches that exist, by name and with their count. Pinned: a new import
#: in any of these files - or a new file at all - fails
#: `test_the_pinned_layer_violations_are_the_only_ones`.
KNOWN_LAYER_VIOLATIONS = {
    ("core/monitoring/notification_display.py", "Api"): 1,
    ("core/monitoring/notification_service.py", "Api"): 6,
    ("core/search/retrieval.py", "Api"): 1,
    ("core/search/session.py", "Api"): 1,
    ("core/search/words.py", "Api"): 1,
    ("Api/routes/analysis.py", "pipeline"): 2,
}


# --------------------------------------------------------------------------- #
# Architecture: the paths a diagram names
# --------------------------------------------------------------------------- #
#: Every repository path the document names, and what has to exist there.
NAMED_PATHS = {
    "apps/web/app.py": "file",
    "core/security": "package",
    "Api/routes": "package",
    "Api/blueprints": "package",
    "Api/services": "package",
    "services": "package",
    "templates": "package",
    "reader_file/readers": "package",
    "core/ocr": "package",
    "pipeline": "package",
    "apps/cli/main.py": "file",
    "Api/routes/analysis.py": "file",
    "Api/utils": "package",
    "core/monitoring": "package",
    "core/search": "package",
}


def test_every_path_the_document_names_exists():
    missing = [path for path in NAMED_PATHS
               if not (PROJECT_ROOT / path).exists()]
    assert not missing, f"named in docs/ARCHITECTURE.md, not in the tree: {missing}"


def test_the_paths_a_diagram_names_are_in_the_diagram_table():
    """A path added to a diagram has to be added to `NAMED_PATHS` too."""
    diagrams = "\n".join(re.findall(r"```mermaid\n(.*?)```", ARCHITECTURE, re.S))
    in_diagram = set(re.findall(r"[`(]([A-Za-z_][\w/]*\.py|[A-Za-z_]+/[A-Za-z_/]+)[`)]",
                                diagrams))
    documented = {p for p in in_diagram if (PROJECT_ROOT / p).exists()}
    unlisted = {p for p in documented if p not in NAMED_PATHS}
    assert not unlisted, (
        "these paths are named in the document but not derived by this test, "
        f"so nothing checks them: {sorted(unlisted)}")


def test_the_layer_rules_that_hold_are_measured_and_hold():
    edges = {name: _import_edges(*spec) for name, spec in FORBIDDEN_EDGES.items()}
    assert edges["readers must not import the database"] == set()
    assert edges["routes must not import a reader directly"] == set(), (
        sorted(edges["routes must not import a reader directly"]))


def test_the_pinned_layer_violations_are_the_only_ones():
    """`core -> Api` and one route -> pipeline, counted, not excused."""
    measured = {}
    for importer, module in _import_statements("core", ("Api", "services")):
        key = (importer, module.split(".")[0])
        measured[key] = measured.get(key, 0) + 1
    for importer, module in _import_statements("Api/routes", ("pipeline",)):
        key = (importer, module.split(".")[0])
        measured[key] = measured.get(key, 0) + 1
    assert measured == KNOWN_LAYER_VIOLATIONS, (
        "the layer table in docs/ARCHITECTURE.md lists exactly these "
        f"exceptions; the code has {sorted(measured)}")


def test_the_document_lists_every_exception_it_has():
    """The exceptions table, row for row, against the pinned counts."""
    rows = dict(re.findall(r"^\| `([\w/.]+)` \| `[\w.]+` \| (\d+) \|$",
                           ARCHITECTURE, re.M))
    documented = {path: int(count) for path, count in rows.items()}
    pinned = {importer: count for (importer, _module), count
              in KNOWN_LAYER_VIOLATIONS.items()}
    assert documented == pinned, (
        "docs/ARCHITECTURE.md's exceptions table and the pinned violations "
        f"disagree: document {documented}, code {pinned}")


# --------------------------------------------------------------------------- #
# Architecture: the ingestion sequence
# --------------------------------------------------------------------------- #
def _block(kind: str) -> str:
    return next(b for b in re.findall(r"```mermaid\n(.*?)```", ARCHITECTURE, re.S)
                if b.startswith(kind))


def _sequence_messages():
    """(sender, receiver, label) for every arrow in the ingestion sequence."""
    block = _block("sequenceDiagram")
    names = dict(re.findall(r"participant (\w+) as ([^\n]+)", block))
    messages = []
    for sender, arrow, receiver, label in re.findall(
            r"^\s*(\w+)\s*(->>|-->>)\s*(\w+):\s*(.+)$", block, re.M):
        messages.append((sender, receiver, arrow, label))
    return names, messages


def test_the_ingestion_sequence_names_its_participants_from_the_tree():
    names, _ = _sequence_messages()
    assert set(names) == {"C", "P", "D", "O", "S", "DB"}
    # Every participant is either a class that exists or a named path.
    from pipeline.integrated_reader import IntegratedFileReader
    from pipeline.storage_pipeline import StoragePipeline
    from reader_file.readers.read_video import VideoFileReader

    import core.ocr

    resolvable = {
        "IntegratedFileReader": IntegratedFileReader,
        "StoragePipeline": StoragePipeline,
        "Reader": VideoFileReader,  # one representative; the sequence says "Reader"
        "OCR engine": core.ocr,
    }
    for participant, described in names.items():
        assert described.split(" (")[0].strip() in resolvable or participant in {
            "C", "DB"}, (f"{participant} describes {described!r}, which is "
                         "nothing this test can resolve")
        path = re.search(r"\(([\w/.]+\.py)\)", described)
        if path:
            assert (PROJECT_ROOT / path.group(1)).exists(), path.group(1)


def test_every_method_the_sequence_names_exists_on_its_class():
    from pipeline.integrated_reader import IntegratedFileReader
    from pipeline.storage_pipeline import StoragePipeline

    _, messages = _sequence_messages()
    text = " ".join(label for _s, _r, _a, label in messages)
    for pattern in (r"main_specify_method_of_reading_the_file\(", r"file_info"):
        assert re.search(pattern, text), pattern

    # The document used to name `StoragePipeline.extract`, which never existed.
    assert not hasattr(StoragePipeline, "extract")
    _, messages = _sequence_messages()
    labels = " ".join(label for _s, _r, _a, label in messages)
    assert "extract(" not in labels, (
        "the diagram again calls a method called extract(")
    assert hasattr(StoragePipeline, "_ocr_provenance")
    assert hasattr(IntegratedFileReader, "process_single_file"), (
        "the CLI participant's entry point is gone")


def test_the_storage_call_the_sequence_names_exists():
    from pipeline.storage_pipeline import StoragePipeline

    _, messages = _sequence_messages()
    called = [label for sender, receiver, _arrow, label in messages
              if sender == "P" and receiver == "S"]
    assert called, "the sequence shows no call from the reader to the pipeline"
    for label in called:
        method = re.match(r"(\w+)\(", label.strip())
        assert method, label
        assert hasattr(StoragePipeline, method.group(1)), (
            f"the sequence calls StoragePipeline.{method.group(1)}, which does "
            "not exist")


def test_ocr_is_attributed_to_the_readers_that_call_it():
    """The sequence draws OCR under the reader, and only the readers import it."""
    callers = {path for path, module in _import_edges("reader_file", ("core.ocr",))
               if "readers" in path}
    assert callers == {"reader_file/readers/read_img_fast.py",
                       "reader_file/readers/read_pdf.py"}, sorted(callers)
    assert _import_edges("pipeline", ("core.ocr",)) == set(), (
        "the pipeline calls OCR itself; the sequence says the reader does")


def test_the_read_path_names_no_reader():
    """The request flowchart has no reader in it, because no request reads a file."""
    flowchart = _block("flowchart")
    assert "reader_file" not in flowchart
    assert "core/ocr" not in flowchart


# --------------------------------------------------------------------------- #
# Domain model: the concepts, the table, the columns
# --------------------------------------------------------------------------- #
BASELINE = (PROJECT_ROOT / "database" / "migrations" / "m0001_baseline.py").read_text(
    encoding="utf-8")

#: concept in docs/DOMAIN_MODEL.md -> the table that implements it.
CONCEPT_TABLES = {
    "file": "paths",
    "logical file": "hashs",
    "occurrence": "paths",
    "source / side": "sources",
    "keyword": "keywords",
    "category": "categorys",
}


def test_every_concept_has_the_table_it_claims():
    for concept, table in CONCEPT_TABLES.items():
        assert concept in DOMAIN, f"{concept} is no longer a row in DOMAIN_MODEL.md"
        assert re.search(rf"CREATE TABLE {table} \(", BASELINE), table


def test_a_file_is_identified_by_its_path_and_hash():
    """The sentence in the document, as the foreign keys that make it true."""
    paths = BASELINE[BASELINE.index("CREATE TABLE paths ("):]
    paths = paths[:paths.index("\n    \"\"\"")]
    assert "file_path TEXT NOT NULL" in paths
    assert "context_id INTEGER NOT NULL" in paths
    assert "REFERENCES hash_contexts(id)" in paths
    assert "hash_id INTEGER NOT NULL" in BASELINE, "hash_contexts lost its hash"
    assert "hash CHAR(64) NOT NULL UNIQUE" in BASELINE


def test_an_archive_is_lineage_not_a_table():
    """The document says "container read as a tree, with lineage" - and there
    is no archive table; the lineage is a path's parent."""
    assert "CREATE TABLE archives" not in BASELINE
    paths = BASELINE[BASELINE.index("CREATE TABLE paths ("):]
    paths = paths[:paths.index("\n    \"\"\"")]
    assert "parent_path_id INTEGER NULL" in paths
    assert "hierarchy_path TEXT NULL" in paths
    assert "archive" in DOMAIN


def test_every_ocr_field_the_document_lists_is_a_key_the_pipeline_writes():
    """The list is checked bidirectionally in test_ocr_provenance_docs.py;
    this asserts the paragraph is still where that test reads it."""
    assert "For OCR, `extraction_provenance.ocr` holds" in DOMAIN


def test_the_provenance_column_exists_on_the_occurrence():
    assert "extraction_provenance JSONB NULL" in BASELINE


def test_the_document_does_not_claim_provenance_of_rows_it_cannot_reach():
    """The column is nullable; "every stored item" was a claim it cannot keep."""
    assert "Every stored item carries" not in DOMAIN
    assert "Every item the pipeline stores carries `extraction_provenance`" in DOMAIN


def test_a_media_read_records_that_speech_was_not_extracted():
    """The document's media paragraph, as the code that decides it."""
    from reader_file.readers.read_video import VideoFileReader

    assert "not extracted" in DOMAIN or "was **not** extracted" in DOMAIN
    source = (PROJECT_ROOT / "reader_file" / "readers" / "read_video.py").read_text(
        encoding="utf-8")
    assert "speech" in source.lower(), (
        "the media paragraph says speech extraction is recorded as not done; "
        "the reader no longer mentions speech")
    assert hasattr(VideoFileReader, "read_file")
    assert "speech_extracted" in source, (
        "the media paragraph says a read records that speech was not "
        "extracted; the reader no longer writes that field")


# --------------------------------------------------------------------------- #
# The honest part: what neither document's test can check
# --------------------------------------------------------------------------- #
#: Sentences kept in the documents that no test derives. Each is named here so
#: that "unchecked" is a recorded state, not an oversight. A sentence moves out
#: of this list only when a test derives it.
UNVERIFIED_CLAIMS = [
    # ARCHITECTURE.md
    "the web application answers a request from the database - measured by "
    "reading the file-content endpoints, not by a test",
    "readers write only what a container has to be unpacked into - the email "
    "reader is inspected, not exercised",
    "templates decide no access - asserted by absence of a macro, not by "
    "reviewing every template",
    # DOMAIN_MODEL.md
    "a field that is absent means 'not measured', never 'measured as zero' - "
    "a convention, checked field by field only where the OCR test reaches",
    "the record states speech was not extracted - asserted from the reader's "
    "source writing speech_extracted, not from a media fixture",
    "duration, geometry, codec, stream counts and tags are all stored - the "
    "field names exist, no fixture asserts the five together",
    "every item the pipeline stores carries extraction_provenance - measured "
    "only as the column existing and the pipeline writing it",
    "an attempt that failed is stored as a failed attempt - the storage is "
    "tested elsewhere (test_ocr_provenance_docs); the wording is not",
]


def test_the_unverified_list_is_still_honest():
    """Every entry still corresponds to a sentence the documents keep."""
    for entry in UNVERIFIED_CLAIMS:
        head = entry.split(" - ")[0]
        keyword = head.split()[0].lower()
        assert keyword in (ARCHITECTURE + DOMAIN).lower(), (
            f"'{head[:40]}' is listed as unverified but no longer in the documents")


def test_each_document_names_its_reviewer():
    for document in (ARCHITECTURE, DOMAIN):
        assert "Reviewed by:" in document, (
            "the document does not say who reviewed it")
        assert "repository owner" in document
