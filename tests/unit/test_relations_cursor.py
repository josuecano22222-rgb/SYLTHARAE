"""Unit: the Relations keyset cursor format.

``/api/archives/hashs`` pages with a forward keyset cursor that carries the
sort key it was built from, so a page is always a coherent continuation of the
page the client came from. The cursor is opaque to the client and survives a
round trip through a URL, which is what these tests pin:

* the historical shape (a bare integer) still decodes as "continue after this
  id", so links and cached cursors from an older build keep working;
* sort-key cursors round-trip their value (including a value containing a
  colon, which is why the value is percent-encoded);
* anything unparsable decodes as "no cursor" - a stale link shows page 1
  instead of raising.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from Api.routes.archives_api import (
    _decode_relation_cursor,
    _encode_relation_cursor,
    _relation_keyset,
)


def test_legacy_integer_cursor_still_means_continue_after_id():
    assert _encode_relation_cursor('id', None, 42) == '42'
    assert _decode_relation_cursor('42') == ('id', None, 42)
    assert _decode_relation_cursor('i:42') == ('id', None, 42)


def test_sort_key_cursors_round_trip():
    for sort_by, value in (('file_count', 3), ('name', 'a' * 64)):
        cursor = _encode_relation_cursor(sort_by, value, 17)
        assert _decode_relation_cursor(cursor) == (sort_by, value, 17)


def test_cursor_value_may_contain_the_separator():
    cursor = _encode_relation_cursor('name', 'ab:cd:ef', 9)
    assert _decode_relation_cursor(cursor) == ('name', 'ab:cd:ef', 9)


@pytest.mark.parametrize('raw', [None, '', '   ', 'not-a-cursor', 'f:x:3',
                                 'n:abc', 'z:1:2', 'f:1:notanint'])
def test_unparsable_cursors_are_ignored_rather_than_raising(raw):
    assert _decode_relation_cursor(raw) is None


def test_keyset_resumes_after_the_sort_key_and_tie_break_id():
    # Descending file count: smaller counts, or the same count with a larger
    # id (ties are broken by id ascending).
    where, params = _relation_keyset(('file_count', 5, 12), 'DESC')
    assert where == ('WHERE (r.file_count < %s OR '
                     '(r.file_count = %s AND r.id > %s))')
    assert params == [5, 5, 12]

    # Ascending file count: larger counts, or the same count with a larger id.
    where, params = _relation_keyset(('file_count', 5, 12), 'ASC')
    assert where == ('WHERE (r.file_count > %s OR '
                     '(r.file_count = %s AND r.id > %s))')
    assert params == [5, 5, 12]

    # Plain id continuation honours the requested direction.
    assert _relation_keyset(('id', None, 12), 'ASC') == (
        'WHERE r.id > %s', [12])
    assert _relation_keyset(('id', None, 12), 'DESC') == (
        'WHERE r.id < %s', [12])


def test_no_cursor_selects_everything():
    assert _relation_keyset(None, 'DESC') == ('', [])
