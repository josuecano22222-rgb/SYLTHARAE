"""The installer receives the gazetteer seeder; it does not import it.

``core.installer`` used to do a function-local ``from services.geo import
gazetteer``, which breaks the pinned rule "core must not import a service".
A deferred import is not an exemption: the rule is checked by walking the AST,
which sees it exactly as it would a top-level one.

The seeding is real domain logic, so it now lives in
``services/geo/gazetteer_seed.py``, and each entry point hands it to the
installer. These tests pin the contract from the installer's side.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import core.installer as installer

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_the_installer_does_not_import_services():
    """The rule that was broken, stated where the fix lives."""
    tree = ast.parse(
        (PROJECT_ROOT / "core" / "installer.py").read_text(encoding="utf-8")
    )
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            imported.append(node.module)
    offenders = [name for name in imported if name.startswith(("services", "Api"))]
    assert offenders == [], (
        f"core/installer.py imports {offenders}; core must not depend on services"
    )


def test_the_seeder_lives_in_the_geo_service():
    """The implementation is where the layering allows it to be."""
    from services.geo import gazetteer_seed

    assert callable(gazetteer_seed.ensure_seed)


def test_an_unregistered_seeder_fails_loudly_rather_than_skipping(monkeypatch):
    """A silently unseeded gazetteer looks, months later, like a broken
    detector, with nothing in the install log to explain it."""
    monkeypatch.setattr(installer, "_gazetteer_seeder", None)
    with pytest.raises(RuntimeError, match="no gazetteer seeder is registered"):
        installer.ensure_gazetteer_seed("h", 5432, "u", "p", "d")


def test_a_registered_seeder_receives_the_connection_arguments(monkeypatch):
    """The installer passes the database coordinates straight through."""
    seen = {}

    def fake_seeder(host, port, user, password, database):
        seen.update(host=host, port=port, user=user,
                    password=password, database=database)
        return {"place_count": 1, "name_count": 2, "changed": True}

    monkeypatch.setattr(installer, "_gazetteer_seeder", fake_seeder)
    result = installer.ensure_gazetteer_seed("db.example", 5433, "u1", "pw", "syl")

    assert seen == {"host": "db.example", "port": 5433, "user": "u1",
                    "password": "pw", "database": "syl"}
    assert result["changed"] is True


def test_set_gazetteer_seeder_installs_the_real_implementation(monkeypatch):
    """The helper the entry points call wires up the real seeder."""
    from services.geo.gazetteer_seed import ensure_seed

    try:
        installer.set_gazetteer_seeder(ensure_seed)
        assert installer._gazetteer_seeder is ensure_seed
    finally:
        monkeypatch.setattr(installer, "_gazetteer_seeder", None)


@pytest.mark.parametrize("path, anchor", [
    ("install.py", "set_gazetteer_seeder"),
    ("Api/routes/setup.py", "set_gazetteer_seeder"),
])
def test_every_entry_point_registers_the_seeder(path, anchor):
    """A caller that forgets would install without place detection."""
    text = (PROJECT_ROOT / path).read_text(encoding="utf-8")
    assert anchor in text, f"{path} never registers the seeder"
