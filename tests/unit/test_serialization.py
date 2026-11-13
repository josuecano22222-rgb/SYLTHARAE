"""Unit + security tests: safe serialization (DB-08)."""

import pickle
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.serialization import (
    pack_int_list,
    unpack_int_list,
    pack_mapping,
    unpack_mapping,
    RestrictedDeserializationError,
)


class TestIntLists:
    def test_roundtrip(self):
        ids = [1, 2, 3, 999999]
        assert unpack_int_list(pack_int_list(ids)) == ids

    def test_empty(self):
        assert unpack_int_list(pack_int_list([])) == []
        assert unpack_int_list(None) == []

    def test_legacy_pickle_readable(self):
        legacy = pickle.dumps([4, 5, 6])
        assert unpack_int_list(legacy) == [4, 5, 6]

    def test_malicious_pickle_rejected(self):
        class Evil:
            def __reduce__(self):
                return (eval, ("1+1",))

        payload = pickle.dumps(Evil())
        with pytest.raises(RestrictedDeserializationError):
            unpack_int_list(payload)

    def test_garbage_rejected(self):
        with pytest.raises(RestrictedDeserializationError):
            unpack_int_list(b"not json or pickle \xff\xfe")


class TestMapping:
    def test_roundtrip(self):
        obj = {"a": [1, 2, 3], "b": {"c": "x"}}
        assert unpack_mapping(pack_mapping(obj)) == obj

    def test_legacy_pickle_readable(self):
        legacy = pickle.dumps({"k": [1, 2]})
        assert unpack_mapping(legacy) == {"k": [1, 2]}

    def test_global_opcode_rejected(self):
        class Evil:
            def __reduce__(self):
                return (eval, ("__import__('os').system('echo pwned')",))

        payload = pickle.dumps(Evil())
        with pytest.raises(RestrictedDeserializationError):
            unpack_mapping(payload)
