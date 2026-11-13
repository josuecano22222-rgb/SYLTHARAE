"""Unit: keyword-association inserts (KEYWORD-01).

The update-associations route handed its ``{keyword_id: count}`` matches to
``ContentDBService.process_keywords_for_content(hash_id, keyword_counts)``.
That method expects the document's word-id SEQUENCE and re-matches it, so
``_keyword_occurrence_counts`` sliced the dict and every file with matches
failed with ``Failed to insert keyword associations for hash_id N:
unhashable type: 'slice'``.  The outer handler additionally referenced an
undefined ``path_id`` (NameError inside the except block).

These tests pin the contract: ``KeywordOperations`` bulk-inserts
``{keyword_id: count}`` through the keywords_hashs repository, reports how
many rows it wrote, and the route counts failures exactly once.
"""

import ast
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from database.operations import KeywordOperations  # noqa: E402

KEYWORDS_ROUTE = PROJECT_ROOT / 'Api' / 'routes' / 'keywords.py'


class _RepoStub:
    def __init__(self):
        self.calls = []

    def bulk_insert_keywords_hashs(self, hash_id, keyword_counts):
        self.calls.append((hash_id, dict(keyword_counts)))


class _ServiceStub:
    def __init__(self):
        self.keywords_hashs_repo = _RepoStub()
        self.keywords_repo = None  # touched by KeywordOperations.__init__


@pytest.fixture()
def ops():
    return KeywordOperations(db_service=_ServiceStub())


class TestInsertKeywordPathRelationships:
    def test_method_exists(self):
        assert hasattr(KeywordOperations, 'insert_keyword_path_relationships')
        assert hasattr(KeywordOperations, 'insert_keyword_hash_relationships')

    def test_bulk_inserts_the_counts_and_returns_written_rows(self, ops):
        written = ops.insert_keyword_path_relationships(7, {3: 2, 9: 5})
        assert written == 2
        assert ops.db_service.keywords_hashs_repo.calls == [(7, {3: 2, 9: 5})]

    def test_empty_mapping_is_a_no_op(self, ops):
        written = ops.insert_keyword_path_relationships(7, {})
        assert written == 0
        assert ops.db_service.keywords_hashs_repo.calls == []

    def test_repository_errors_propagate_for_logging(self, ops):
        def boom(hash_id, counts):
            raise RuntimeError('connection lost')

        ops.db_service.keywords_hashs_repo.bulk_insert_keywords_hashs = boom
        with pytest.raises(RuntimeError, match='connection lost'):
            ops.insert_keyword_path_relationships(7, {1: 1})


class TestRouteCallSite:
    def test_route_calls_the_bulk_insert_inside_its_own_try(self):
        source = KEYWORDS_ROUTE.read_text(encoding='utf-8')
        assert 'insert_keyword_path_relationships(hash_id, keyword_counts)' in source
        # the counts dict must never reach the sequence matcher again
        assert 'process_keywords_for_content(hash_id, keyword_counts)' not in source
        # failures are logged with the real cause, and counted exactly once
        assert (
            'Failed to insert keyword associations for hash_id {hash_id}: {insert_err}'
            in source
        )
        assert 'except Exception as insert_err' in source

    def test_update_associations_handler_has_no_unbound_path_id(self):
        """The outer except referenced ``path_id`` - a NameError in the loop."""
        tree = ast.parse(KEYWORDS_ROUTE.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body_src = ast.dump(node)
            if 'get_word_ids_from_content' not in body_src:
                continue
            bound = {
                n.id for n in ast.walk(node)
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
            }
            bound |= {a.arg for a in ast.walk(node) if isinstance(a, ast.arg)}
            used = {
                n.id for n in ast.walk(node)
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
            }
            assert 'path_id' not in used or 'path_id' in bound
            return
        pytest.fail('update-associations route not found')
