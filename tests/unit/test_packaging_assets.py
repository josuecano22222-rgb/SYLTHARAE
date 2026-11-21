"""The distribution must contain the web application's assets.

AUDIT-PKG-01: ``pyproject.toml`` excluded ``templates*`` and ``static*`` from
``packages.find`` and shipped no ``package-data``, so ``pip wheel .`` produced a
wheel with 438 files and **zero** templates, zero static files and zero
translations - while ``apps/web/app.py`` resolves them from
``Path(__file__).parents[2]``. Measured on the installed wheel:

    GET /auth/login  ->  500  jinja2.exceptions.TemplateNotFound: auth/login.html

and, once templates were added, a second failure surfaced:
``core/app_version.py`` loads ``<project root>/version.py`` by path and raised
``FileNotFoundError`` for every page that renders the release line.

These tests read the build configuration (cheap and deterministic) instead of
building a wheel; the reporting turn records the executed wheel/install/serve
check separately.
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def pyproject() -> dict:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def test_the_asset_trees_are_packaged(pyproject):
    include = " ".join(pyproject["tool"]["setuptools"]["packages"]["find"]["include"])
    for tree in ("templates", "static", "translations"):
        assert tree in include, (
            f"{tree}/ is not part of the distribution: an installed wheel "
            f"returns 500 TemplateNotFound on every page")


def test_the_directories_are_treated_as_namespace_packages(pyproject):
    """They have no ``__init__.py``; without namespaces they are skipped."""
    assert pyproject["tool"]["setuptools"]["packages"]["find"]["namespaces"] is True


def test_package_data_covers_the_file_types_the_app_loads(pyproject):
    data = pyproject["tool"]["setuptools"]["package-data"]
    assert "html" in " ".join(data["templates"])
    assert "mo" in " ".join(data["translations"]), (
        "without .mo files the interface falls back to English everywhere")


def test_version_py_is_shipped_as_a_module(pyproject):
    """``core/app_version.py`` loads it from the project root."""
    assert "version" in pyproject["tool"]["setuptools"]["py-modules"]


def test_the_version_reader_degrades_when_version_py_is_absent(tmp_path, monkeypatch):
    """A tree without ``version.py`` must render, not raise."""
    import core.app_version as app_version

    monkeypatch.setattr(app_version, "_MODULE", None)
    monkeypatch.setattr(app_version, "get_project_root", lambda: str(tmp_path))
    monkeypatch.setattr(app_version, "_installed_distribution_version", lambda: "9.9.9")

    assert app_version.get_version() == "SYLTHARAE 9.9.9"
    monkeypatch.setattr(app_version, "_MODULE", None)


def test_the_version_reader_reports_a_missing_version_instead_of_crashing(tmp_path, monkeypatch):
    import core.app_version as app_version

    monkeypatch.setattr(app_version, "_MODULE", None)
    monkeypatch.setattr(app_version, "get_project_root", lambda: str(tmp_path))
    monkeypatch.setattr(app_version, "_installed_distribution_version", lambda: None)

    with pytest.raises(ImportError):
        app_version.get_version()
    monkeypatch.setattr(app_version, "_MODULE", None)


def test_the_package_does_not_ship_the_test_corpus(pyproject):
    """``test-doc/`` is 67 MB of third-party documents (LICENSING-02)."""
    exclude = " ".join(pyproject["tool"]["setuptools"]["packages"]["find"]["exclude"])
    assert "test-doc" in exclude
    assert not (PROJECT_ROOT / "test-doc" / "__init__.py").exists()
    assert sys.version_info >= (3, 11)  # tomllib
