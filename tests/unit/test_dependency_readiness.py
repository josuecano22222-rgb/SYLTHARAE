"""Python dependency readiness: declarations, real checks, and trusted installs.

The readiness report must be computed from what the environment actually
provides. These tests pin the declaration sources, each state the checker can
produce, the ready/blocking rule, and the install guard. pip is always replaced
with a fake, so no test installs anything into the active environment.
"""

from __future__ import annotations

import sys
import threading
import time
import tomllib
from pathlib import Path

import pytest
from importlib import metadata as importlib_metadata

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core import dependency_readiness as dr  # noqa: E402

#: Captured before any test patches the module, so wrappers can call the real checker.
_REAL_CHECK = dr.check_declaration


def _decl(name="fakepkg", requirement="fakepkg>=1.0,<2", required=True, group="required"):
    return dr.Declaration(name=dr.canonicalize_name(name), display=name,
                          requirement=requirement, required=required, group=group,
                          source="test")


# ---------------------------------------------------------------------------
# Declarations
# ---------------------------------------------------------------------------

def test_required_declarations_are_exactly_the_pyproject_dependencies():
    with (ROOT / "pyproject.toml").open("rb") as fh:
        declared = tomllib.load(fh)["project"]["dependencies"]
    expected = {dr.canonicalize_name(dr.Requirement(text).name) for text in declared}

    required = {d.name for d in dr.load_declarations() if d.required}
    assert required == expected


def test_optional_groups_and_manifest_are_included_once_each():
    decls = dr.load_declarations()
    names = [d.name for d in decls]
    assert len(names) == len(set(names)), "a distribution is listed twice"
    groups = {d.group for d in decls}
    assert {"required", "pdf", "office", "ocr", "manifest"} <= groups
    assert any(d.name == "pillow" for d in decls)


def test_import_name_uses_the_fixed_table_then_the_installed_metadata(monkeypatch):
    assert dr.Declaration("pillow", "Pillow", "Pillow>=1", True, "required", "t").import_name == "PIL"
    assert dr.Declaration("ruff", "ruff", "ruff>=1", False, "dev", "t").import_name is None
    # The installed distribution declares its own module name (top_level.txt).
    monkeypatch.setattr(dr, "_module_from_metadata", lambda name: "rtfde_impl")
    assert dr.Declaration("rtfde", "RTFDE", "RTFDE>=0.1", False, "manifest", "t").import_name == "rtfde_impl"
    # No metadata: fall back to the normalised distribution name.
    monkeypatch.setattr(dr, "_module_from_metadata", lambda name: None)
    assert dr.Declaration("some-tool", "some-tool", "some-tool>=1", False, "manifest", "t").import_name == "some_tool"


# ---------------------------------------------------------------------------
# Checks: one test per state
# ---------------------------------------------------------------------------

def _missing(_name):
    raise importlib_metadata.PackageNotFoundError


def test_a_missing_required_package_is_missing_and_blocking():
    result = dr.check_declaration(_decl(), version_of=_missing, probe=lambda m: None)
    item = result.as_dict()
    assert item["state"] == dr.STATE_MISSING
    assert item["blocking"] is True
    assert item["detail_key"] == "Not installed"


def test_a_missing_optional_package_is_missing_but_not_blocking():
    decl = _decl(required=False, group="pdf")
    item = dr.check_declaration(decl, version_of=_missing, probe=lambda m: None).as_dict()
    assert item["state"] == dr.STATE_MISSING
    assert item["blocking"] is False


def test_an_out_of_range_version_is_incompatible():
    result = dr.check_declaration(_decl(), version_of=lambda n: "2.5", probe=lambda m: None)
    assert result.state == dr.STATE_INCOMPATIBLE
    assert result.detail_vars == {"installed": "2.5", "requires": "<2,>=1.0"}


def test_an_in_range_package_whose_import_fails_is_import_failed():
    result = dr.check_declaration(
        _decl(), version_of=lambda n: "1.4",
        probe=lambda m: "ImportError: libcudart.so.12: cannot open shared object file")
    assert result.state == dr.STATE_IMPORT_FAILED
    assert "libcudart" in result.detail
    assert result.installed_version == "1.4"


def test_an_import_that_never_finishes_is_unknown_not_compatible():
    result = dr.check_declaration(_decl(), version_of=lambda n: "1.4",
                                  probe=lambda m: "__timeout__")
    assert result.state == dr.STATE_UNKNOWN
    assert result.detail_key == "Import did not finish within %(seconds)s seconds"


def test_a_healthy_package_is_compatible():
    result = dr.check_declaration(_decl(), version_of=lambda n: "1.4", probe=lambda m: None)
    assert result.state == dr.STATE_COMPATIBLE
    assert result.installed_version == "1.4"


def test_a_command_line_tool_is_checked_by_metadata_only():
    calls = []
    decl = _decl(name="ruff", requirement="ruff>=0.4", required=False, group="dev")
    result = dr.check_declaration(decl, version_of=lambda n: "0.9", probe=lambda m: calls.append(m))
    assert result.state == dr.STATE_COMPATIBLE
    assert calls == []  # no import attempted


def test_probe_runs_the_real_import_in_a_subprocess():
    # The real probe: a standard-library module imports, a missing one fails.
    assert dr._probe_import("json") is None
    error = dr._probe_import("definitely_not_a_module_xyz_123")
    assert error and "definitely_not_a_module_xyz_123" in error


# ---------------------------------------------------------------------------
# Report: ready only when every required item is usable
# ---------------------------------------------------------------------------

@pytest.fixture()
def fake_manifest(monkeypatch):
    holder = {"decls": []}
    monkeypatch.setattr(dr, "load_declarations", lambda *a, **k: list(holder["decls"]))
    monkeypatch.setattr(dr, "_REPORT_CACHE", {"at": 0.0, "report": None})
    def fake_check(decl):
        if decl.name == "absent":
            return _REAL_CHECK(decl, version_of=_missing, probe=lambda m: None)
        return _REAL_CHECK(decl, version_of=lambda n: "1.0", probe=lambda m: None)

    monkeypatch.setattr(dr, "check_declaration", fake_check)
    return holder


def test_a_missing_required_item_makes_the_report_not_ready(fake_manifest):
    fake_manifest["decls"] = [_decl("ok"), _decl("absent")]
    report = dr.build_report(refresh=True)
    assert report["ready"] is False
    assert report["blocking"] == ["absent"]
    assert report["counts"]["required_ready"] == 1


def test_only_optional_gaps_still_leave_the_report_ready(fake_manifest):
    fake_manifest["decls"] = [_decl("ok"), _decl("absent", required=False, group="pdf")]
    report = dr.build_report(refresh=True)
    assert report["ready"] is True
    assert report["counts"]["optional_gaps"] == 1


# ---------------------------------------------------------------------------
# Install: trusted names only, no upgrades, one job at a time
# ---------------------------------------------------------------------------

class _FakePopen:
    """Stands in for subprocess.Popen; records argv and emits scripted output."""

    calls: list = []
    gate: threading.Event | None = None
    returncode = 0

    def __init__(self, argv, **kwargs):
        type(self).calls.append(argv)
        self.stdout = iter(["Collecting packages\n", "Successfully installed\n"])
        self._code = type(self).returncode
        self._gate = type(self).gate

    def wait(self, timeout=None):
        if self._gate is not None:
            self._gate.wait(5)
        return self._code


@pytest.fixture()
def fake_pip(monkeypatch):
    _FakePopen.calls = []
    _FakePopen.gate = None
    _FakePopen.returncode = 0
    monkeypatch.setattr(dr.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(dr, "_ACTIVE", {})
    monkeypatch.setattr(dr, "_FAILED", {})
    monkeypatch.setattr(dr, "_FAILED_KEYS", {})
    monkeypatch.setattr(dr, "_REPORT_CACHE", {"at": 0.0, "report": None})
    # The post-install check runs subprocess.run (which also uses Popen): keep it healthy.
    monkeypatch.setattr(dr, "_probe_import", lambda module: None)
    return _FakePopen


def _wait_for(job_id, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = dr.get_job(job_id)
        if job and job["status"] != "running":
            return job
        time.sleep(0.02)
    raise AssertionError("install job did not finish")


def test_install_accepts_only_declared_names(fake_pip, monkeypatch):
    monkeypatch.setattr(dr, "trusted_declarations",
                        lambda: {"flask": _decl("Flask", "Flask>=3.0,<4")})
    with pytest.raises(dr.DependencyRequestError) as exc:
        dr.start_install(["requests"])
    assert exc.value.code == "not_declared" and exc.value.name == "requests"
    with pytest.raises(dr.DependencyRequestError) as exc:
        dr.start_install([])
    assert exc.value.code == "none_chosen"
    with pytest.raises(dr.DependencyRequestError) as exc:
        dr.start_install(["flask; pip install evil"])
    assert exc.value.code == "not_declared"
    assert fake_pip.calls == []


def test_install_uses_declared_specifiers_and_never_upgrades(fake_pip, monkeypatch):
    monkeypatch.setattr(dr, "trusted_declarations",
                        lambda: {"flask": _decl("Flask", "Flask>=3.0,<4")})
    job = dr.start_install(["Flask"])
    done = _wait_for(job["job_id"])

    assert done["status"] == "succeeded"
    argv = fake_pip.calls[-1]
    assert argv[1:4] == ["-m", "pip", "install"]
    assert argv[-1] == "Flask>=3.0,<4"
    for flag in ("-U", "--upgrade", "--upgrade-strategy"):
        assert flag not in argv


def test_a_failed_install_is_reported_as_failed_for_that_item(fake_pip, monkeypatch):
    fake_pip.returncode = 1
    monkeypatch.setattr(dr, "trusted_declarations",
                        lambda: {"absent": _decl("absent", "absent>=1")})
    monkeypatch.setattr(dr, "check_declaration",
                        lambda decl: _REAL_CHECK(decl, version_of=_missing))
    job = dr.start_install(["absent"])
    done = _wait_for(job["job_id"])
    assert done["status"] == "failed"
    assert done["returncode"] == 1
    assert dr._FAILED.get("absent")

    monkeypatch.setattr(dr, "load_declarations", lambda *a, **k: [_decl("absent", "absent>=1")])
    report = dr.build_report(refresh=True)
    item = report["items"][0]
    assert item["state"] == dr.STATE_FAILED
    assert item["detail_key"] == "pip exited with code %(code)s"
    assert item["detail_vars"] == {"code": 1}


def test_a_crash_after_pip_still_releases_the_install_lock(fake_pip, monkeypatch):
    def boom(decl):
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(dr, "trusted_declarations",
                        lambda: {"flask": _decl("Flask", "Flask>=3.0,<4")})
    monkeypatch.setattr(dr, "check_declaration", boom)
    job = dr.start_install(["Flask"])
    done = _wait_for(job["job_id"])
    assert done["status"] == "failed"
    assert dr._ACTIVE == {}
    # The next request is accepted rather than reported as busy.
    monkeypatch.setattr(dr, "check_declaration", _REAL_CHECK)
    _wait_for(dr.start_install(["Flask"])["job_id"])


def test_only_one_install_runs_at_a_time(fake_pip, monkeypatch):
    gate = threading.Event()
    fake_pip.gate = gate
    monkeypatch.setattr(dr, "trusted_declarations",
                        lambda: {"flask": _decl("Flask", "Flask>=3.0,<4")})
    first = dr.start_install(["Flask"])
    with pytest.raises(dr.DependencyBusyError):
        dr.start_install(["Flask"])
    gate.set()
    _wait_for(first["job_id"])


# ---------------------------------------------------------------------------
# HTTP surface: gated before installation, admin afterwards
# ---------------------------------------------------------------------------

@pytest.fixture()
def setup_app(monkeypatch):
    from flask import Flask

    import Api.routes.setup as setup_routes

    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="dependency-test")
    from flask_babel import Babel
    Babel(app)
    app.register_blueprint(setup_routes.setup_bp)
    app.add_url_rule("/", endpoint="index", view_func=lambda: "home")
    monkeypatch.setattr(setup_routes, "_is_initialized", lambda: False)
    return app


def test_the_report_endpoint_returns_the_readiness_contract(setup_app, monkeypatch):
    monkeypatch.setattr(dr, "build_report", lambda refresh=False: {
        "ready": True, "counts": {}, "blocking": [], "items": [], "checked_at": "now",
        "active_job": None,
    })
    body = setup_app.test_client().get("/api/setup/dependencies").get_json()
    assert body["ok"] is True and body["ready"] is True


def test_the_install_endpoint_rejects_undeclared_names_with_a_translated_message(setup_app, monkeypatch):
    monkeypatch.setattr(dr, "trusted_declarations", lambda: {})
    resp = setup_app.test_client().post("/api/setup/dependencies/install",
                                        json={"names": ["requests"]})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "requests is not a declared dependency."


def test_the_install_endpoint_rejects_non_object_bodies(setup_app):
    resp = setup_app.test_client().post("/api/setup/dependencies/install", json=["Flask"])
    assert resp.status_code == 400


def test_the_job_endpoint_rejects_malformed_ids(setup_app):
    resp = setup_app.test_client().get("/api/setup/dependencies/jobs/../../etc")
    assert resp.status_code == 404


def test_install_is_closed_to_anonymous_users_once_initialized(setup_app, monkeypatch):
    import Api.routes.setup as setup_routes

    monkeypatch.setattr(setup_routes, "_is_initialized", lambda: True)
    started = []
    monkeypatch.setattr(dr, "start_install", lambda names: started.append(names))
    resp = setup_app.test_client().post("/api/setup/dependencies/install",
                                        json={"names": ["Flask"]})
    assert resp.status_code == 401
    assert started == []


def test_system_check_packages_follow_the_readiness_report(monkeypatch):
    from core import installer

    monkeypatch.setattr(dr, "build_report", lambda refresh=False: {
        "ready": False, "counts": {"required": 2},
        "items": [{"name": "Flask", "blocking": True, "state": "missing"},
                  {"name": "Pillow", "blocking": False, "state": "missing"}],
    })
    packages = installer.check_system()["packages"]
    assert packages["ok"] is False
    assert packages["missing"] == ["Flask"]
