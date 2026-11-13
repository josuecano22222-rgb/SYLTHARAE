"""Unit tests: password hashing + client-safe errors (SEC-01, SEC-08)."""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.security.passwords import hash_password, verify_password
from core.security.service import AuthError, AuthService
from core.errors import new_correlation_id, sanitize_message


class TestPasswords:
    def test_hash_is_salted_and_verifiable(self):
        h1 = hash_password("correct horse battery staple")
        h2 = hash_password("correct horse battery staple")
        assert h1 != h2  # salted
        assert verify_password("correct horse battery staple", h1)
        assert not verify_password("wrong password", h1)

    def test_never_plaintext(self):
        pw = "super-secret-password"
        assert pw not in hash_password(pw)

    def test_empty_rejected(self):
        with pytest.raises(ValueError):
            hash_password("")

    def test_malformed_hash_is_failed_verification(self):
        assert verify_password("x", "not-a-hash") is False
        assert verify_password("x", "") is False
        assert verify_password("", "somesalt$hash") is False

    @pytest.mark.parametrize("configured", ["invalid", "0", "7", "129", "-2"])
    def test_invalid_minimum_uses_the_secure_default(self, monkeypatch, configured):
        monkeypatch.setenv("PASSWORD_MIN_LENGTH", configured)
        with pytest.raises(AuthError, match="12 characters"):
            AuthService._validate_password_strength("x" * 10)
        AuthService._validate_password_strength("x" * 12)

    def test_valid_minimum_is_enforced(self, monkeypatch):
        monkeypatch.setenv("PASSWORD_MIN_LENGTH", "8")
        AuthService._validate_password_strength("x" * 8)
        with pytest.raises(AuthError, match="8 characters"):
            AuthService._validate_password_strength("x" * 7)


class TestCorrelationIds:
    def test_format(self):
        cid = new_correlation_id()
        assert cid.startswith("ERR-")
        parts = cid.split("-")
        assert len(parts) == 3
        assert len(parts[2]) == 6

    def test_unique(self):
        ids = {new_correlation_id() for _ in range(100)}
        assert len(ids) == 100


class TestSanitizeMessage:
    def test_scrubs_connection_strings(self):
        msg = "connect failed: postgresql://user:pass@host/db"
        cleaned = sanitize_message(msg)
        assert "postgresql://" not in cleaned

    def test_scrubs_traceback_marker(self):
        cleaned = sanitize_message("Traceback (most recent call last): ...")
        assert "Traceback (most recent call last)" not in cleaned
