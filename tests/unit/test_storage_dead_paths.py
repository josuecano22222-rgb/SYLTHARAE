"""Locks in the DatabaseHub call-site investigation as executable evidence.

These tests encode the conclusions of PHASE2_DBHUB_INVESTIGATION.md so that a
future change which reintroduces the dead code, or which makes a fall-through
look like a successful store, fails loudly instead of silently changing
behaviour.

They were written while the legacy storage implementation was still present as a
>5 000-character string literal inside ``_store_file_sync`` (inert, because an
unconditional ``return None`` preceded it). That block has since been deleted
outright - dead code that cannot run, cannot be reviewed and cannot be
maintained is worse than no code - so the assertions below now guard the
*stronger* invariant: the legacy implementation is gone, no live code anywhere
uses the DatabaseHub attributes it called, and the single remaining
"should never happen" branch is loud and accounted.

They analyse the module with ``ast`` rather than reading it, because the whole
point is that some occurrences of ``db_hub.<x>_operations`` are text inside a
string literal or a comment, not code. A grep cannot tell the difference; this
can.
"""

import ast
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

PIPELINE = PROJECT_ROOT / "pipeline" / "storage_pipeline.py"
DB_SERVICE = PROJECT_ROOT / "database" / "services" / "contents_db_service.py"

OPERATION_ATTRIBUTES = (
    "word_operations",
    "path_operations",
    "title_operations",
    "punctuation_operations",
    "content_operations",
    "hash_operations",
)


def parse(path):
    return ast.parse(path.read_text(encoding="utf-8"))


def string_literal_lines(tree):
    """Every line occupied by a multi-line string constant (i.e. not code)."""
    lines = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "\n" in node.value:
                lines.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return lines


def is_comment(source_lines, lineno):
    return source_lines[lineno - 1].lstrip().startswith("#")


def operation_sites(path):
    """Yield (lineno, attribute, reachable_syntax) for each occurrence."""
    source = path.read_text(encoding="utf-8")
    tree = parse(path)
    in_string = string_literal_lines(tree)
    source_lines = source.splitlines()
    sites = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr in OPERATION_ATTRIBUTES
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "db_hub"
        ):
            sites.append((node.lineno, node.attr, node.lineno not in in_string))
    # grep-style sweep to also catch occurrences inside strings and comments,
    # which the AST walk deliberately does not return.
    textual = {
        i
        for i, line in enumerate(source_lines, 1)
        if any(f"db_hub.{attr}" in line for attr in OPERATION_ATTRIBUTES)
    }
    ast_lines = {lineno for lineno, _, _ in sites}
    return sites, textual - ast_lines, in_string, source_lines


class TestOperationsAttributesDoNotExist:
    """The premise. Asserted rather than assumed."""

    @pytest.mark.parametrize("attribute", OPERATION_ATTRIBUTES)
    def test_database_hub_lacks(self, attribute):
        from database import DatabaseHub

        assert not hasattr(DatabaseHub, attribute), attribute

    def test_no_dynamic_attribute_access_to_hide_it(self):
        from database import DatabaseHub

        assert not hasattr(DatabaseHub, "__getattr__")


class TestLegacyBlockIsGone:
    """The inert legacy implementation was deleted, not kept for reference."""

    def test_no_large_string_literal_remains_in_the_storage_helper(self):
        tree = parse(PIPELINE)
        blocks = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and len(node.value) > 5000
        ]
        assert blocks == [], (
            "a large string literal reappeared in storage_pipeline.py at lines "
            f"{[b.lineno for b in blocks]}; a disabled copy of the storage path "
            "must not be re-added - delete dead code instead of commenting it out"
        )

    def test_no_path_operations_site_remains_anywhere(self):
        sites, textual, in_string, source_lines = operation_sites(PIPELINE)
        path_sites = [ln for ln, attr, _ in sites if attr == "path_operations"]
        assert path_sites == [], (
            f"db_hub.path_operations is used as live code at {path_sites}, but "
            "DatabaseHub does not define it"
        )
        textual_sites = [
            i for i, line in enumerate(source_lines, 1)
            if "db_hub.path_operations" in line and not line.lstrip().startswith("#")
        ]
        assert textual_sites == [], (
            f"db_hub.path_operations still appears at {textual_sites}; the "
            "historical block that used it has been removed"
        )

    def test_fall_through_is_loud_and_accounted(self):
        """The guard that replaced the disabled block.

        Every supported path returns before it; if control somehow falls through
        anyway, the branch must count the file as failed and say so - a
        fall-through reported as a successful store is the failure mode that
        matters here.
        """
        source = PIPELINE.read_text(encoding="utf-8")
        assert "Storage fell through every return path" in source, (
            "the fail-safe for a storage fall-through was removed"
        )
        fail_safe = source.index("Storage fell through every return path")
        tail = source[fail_safe:fail_safe + 600]
        assert "files_failed" in tail, "the fall-through no longer counts a failure"
        assert "return None" in tail, "the fall-through no longer returns"


class TestStoreContentPipelineRemoved:
    """ITEM-11.1. The method was dead AND broken, so it was deleted.

    Dead: its only textual call site sits inside the disabled string literal, so
    it had zero live callers. Broken: it called six ``db_hub.*_operations``
    attributes that DatabaseHub does not define (see
    TestOperationsAttributesDoNotExist), so had it ever been reached it would
    have raised AttributeError rather than storing anything.

    These tests replace the earlier "it exists but is unreachable" assertions.
    They now guard the stronger invariant: the broken method is gone, and no live
    code anywhere in the module uses those nonexistent attributes.
    """

    def test_the_method_is_gone_from_the_class(self):
        from pipeline.storage_pipeline import StoragePipeline

        assert not hasattr(StoragePipeline, "_store_content_pipeline"), (
            "_store_content_pipeline was reintroduced; it calls DatabaseHub "
            "attributes that do not exist and would raise AttributeError"
        )

    def test_no_live_definition_or_call_remains_in_the_module(self):
        tree = parse(PIPELINE)
        definitions = [
            n.lineno
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_store_content_pipeline"
        ]
        calls = [
            n.lineno
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and getattr(n.func, "attr", None) == "_store_content_pipeline"
        ]
        assert definitions == [], f"live definition reappeared at {definitions}"
        assert calls == [], f"live call reappeared at {calls}"

    def test_no_reference_remains_at_all(self):
        """The historical block that mentioned it was deleted with it."""
        source_lines = PIPELINE.read_text(encoding="utf-8").splitlines()
        occurrences = [
            i
            for i, line in enumerate(source_lines, 1)
            if "_store_content_pipeline" in line
        ]
        assert occurrences == [], (
            f"_store_content_pipeline still referenced at {occurrences}; it called "
            "DatabaseHub attributes that do not exist"
        )

    @pytest.mark.parametrize("attribute", OPERATION_ATTRIBUTES)
    def test_no_live_code_uses_the_missing_dbhub_attribute(self, attribute):
        """The reason for deletion must not creep back in elsewhere."""
        sites, _textual, _in_string, _lines = operation_sites(PIPELINE)
        live = [ln for ln, attr, ok in sites if ok and attr == attribute]
        assert live == [], (
            f"db_hub.{attribute} is used as live code at {live}, but "
            "DatabaseHub does not define it"
        )


class TestPunctuationGap:
    """The one genuine gap: formatting metadata is implemented but never wired."""

    def test_create_content_from_symbols_has_no_callers(self):
        source = DB_SERVICE.read_text(encoding="utf-8")
        occurrences = [
            i
            for i, line in enumerate(source.splitlines(), 1)
            if "create_content_from_symbols" in line
        ]
        # Only the definition. If this fails, the method was wired in - which
        # is the desired end state, and this test should then assert that the
        # punctuation table is populated.
        assert len(occurrences) == 1, (
            f"create_content_from_symbols is now referenced at {occurrences}; "
            "verify the punctuation table is populated and update this test"
        )

    def test_create_content_from_symbols_is_functional_so_it_must_not_be_deleted(self):
        """ITEM-11.2. Uncalled is not the same as dead-and-broken.

        Unlike _store_content_pipeline (deleted: it called DatabaseHub
        attributes that do not exist), every dependency of this method resolves.
        It is the only implementation of punctuation- and position-preserving
        content storage in the codebase, so deleting it would destroy a
        capability rather than remove one. Asserted here so that a future
        dead-code sweep cannot treat "zero callers" as sufficient grounds.
        """
        import inspect

        from database.database.repository.contents_repo import ContentsRepository
        from database.database.repository.punctuation_repo import PunctuationRepository
        from database.database.repository.words_repo import WordsRepository
        from database.services.contents_db_service import ContentDBService

        assert hasattr(ContentDBService, "create_content_from_symbols")
        assert hasattr(ContentDBService, "get_spacing_id")
        assert hasattr(ContentDBService, "calculate_char_position")
        assert hasattr(WordsRepository, "resolve_word_ids_batch")
        assert hasattr(PunctuationRepository, "resolve_punctuation_ids_batch")
        assert hasattr(ContentsRepository, "store_symbol_pairs")

        # Its producer exists too and yields the exact tuple shape it consumes.
        assert hasattr(ContentDBService, "extract_symbols_from_text")
        sig = inspect.signature(ContentDBService.create_content_from_symbols)
        assert list(sig.parameters)[:3] == ["self", "symbols", "hash_id"], list(sig.parameters)

    def test_its_reader_counterpart_is_also_unwired(self):
        """The symbol-pair read path is dead too - the gap is symmetric."""
        source = DB_SERVICE.read_text(encoding="utf-8")
        for name in ("reconstruct_text_from_symbols", "get_content_symbol_pairs"):
            occurrences = [
                i
                for i, line in enumerate(source.splitlines(), 1)
                if name in line
            ]
            assert occurrences, f"{name} vanished; the audit result is stale"

    def test_live_path_stores_degenerate_symbol_pairs(self):
        """ROOT CAUSE of the empty punctuation table, pinned as a regression.

        The live path is create_content -> ContentsRepository.store_text_content,
        which does NOT store text: it synthesises symbol pairs with punctuation
        forced to None, spacing hard-coded to 1 (space) and positions purely
        sequential. So every document is stored as if it were a flat,
        unpunctuated, evenly spaced word list. That - not a missing caller
        alone - is why punctuation is never populated.
        """
        import inspect

        from database.database.repository.contents_repo import ContentsRepository

        source = inspect.getsource(ContentsRepository.store_text_content)
        assert "store_symbol_pairs" in source, (
            "store_text_content no longer delegates to store_symbol_pairs; "
            "re-run the item-11 audit, this conclusion is stale"
        )
        # The synthetic pair shape: (word_id, None, None, 1, position)
        assert "None,         # punct_before_id" in source, source
        assert "None,         # punct_after_id" in source, source
        assert "1,            # spacing_id" in source, source

    def test_punctuation_table_is_empty_after_a_real_ingest(self, pg_db):
        """Runtime proof of the gap, not just a static one."""
        import datetime

        import psycopg2

        conn = psycopg2.connect(
            host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
            password=pg_db["password"], dbname=pg_db["database"],
        )
        today = datetime.date.today()
        tag = f"_punct_{today}"
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO sides (name, importance, date_creation)"
                    " VALUES (%s, 0.5, %s) ON CONFLICT (name)"
                    " DO UPDATE SET name = EXCLUDED.name",
                    (f"{tag}_side", today),
                )
                cur.execute(
                    "INSERT INTO sources (name, job, importance, country,"
                    " date_creation) VALUES (%s, 't', 0.5, 't', %s)"
                    " ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name",
                    (f"{tag}_src", today),
                )
            conn.commit()

            pymupdf = pytest.importorskip("pymupdf")
            import tempfile

            doc = pymupdf.open()
            doc.new_page().insert_text(
                (72, 72),
                "Quarterly reconciliation, dated 14 March; a variance of 4.7 "
                "percent was noted by the auditor.",
                fontsize=11,
            )
            tmp = Path(tempfile.mkdtemp())
            path = tmp / "punct.pdf"
            path.write_bytes(doc.tobytes())
            doc.close()

            from pipeline.integrated_reader import IntegratedFileReader

            IntegratedFileReader(
                max_workers=1, enable_storage=True,
                storage_source=f"{tag}_src", storage_side=f"{tag}_side",
            ).process_folder(str(tmp))

            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM words")
                assert cur.fetchone()[0] > 0, "sanity: words were stored"
                cur.execute("SELECT COUNT(*) FROM punctuation")
                assert cur.fetchone()[0] == 0, (
                    "punctuation is now populated - the gap is closed; update "
                    "this test to assert the correct behaviour"
                )
        finally:
            conn.close()
