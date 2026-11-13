"""The Windows offline deployment kit.

The directive's release gate says a Windows release may only be declared
offline-ready when the bundle is complete, the acceptance test covers every
step, nothing downloads at runtime, and the committed evidence regenerates.
These tests pin the kit's mechanics cross-platform (the heavyweight parts —
actually running the installer and the acceptance test on offline Windows
hardware — are the documented operator procedure in
docs/WINDOWS_OFFLINE.md; everything that can fail silently in the repo is
asserted here).
"""

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "windows"))

import offline_acceptance  # noqa: E402
import build_offline_bundle as builder  # noqa: E402
import inventory_dependencies  # noqa: E402
import local_postgres  # noqa: E402


# ---------------------------------------------------------------------------
# The dependency inventory is generated, and the committed copy is current
# ---------------------------------------------------------------------------
class TestDependencyInventory:
    def test_the_committed_inventory_matches_the_generator(self):
        assert inventory_dependencies.generate() == (
            (ROOT / "docs" / "windows" / "DEPENDENCY_INVENTORY.md")
            .read_text(encoding="utf-8"))

    def test_every_runtime_package_is_classified(self):
        content = inventory_dependencies.generate()
        # Every requirement line of the manifest appears in the inventory.
        for raw in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            name = inventory_dependencies._req_name(line)
            if name == "libpff-python":
                continue
            assert f"| {name} |" in content, name
        # The directive's classes are all present where they must be.
        for fragment in ("Native Windows binaries", "Database",
                         "OCR engines and language data", "Development-only",
                         "External/network"):
            assert fragment in content, fragment

    def test_moviepy_is_not_shipped_and_the_floor_holds(self):
        """Step 27 packaging audit contract: moviepy cannot coexist with the
        Pillow security floor (1.x has no wheel at all, 2.x pins pillow<11),
        so it must stay out of the shipped set - the win_amd64 wheelhouse
        must keep resolving."""
        lines = builder._runtime_requirement_lines()
        names = {inventory_dependencies._req_name(line).lower() for line in lines}
        assert "moviepy" not in names
        # the floor itself stays
        floors = [line for line in lines if "pillow" in line.lower()]
        assert floors and ">=" in floors[0]

    def test_the_sdist_only_split_carries_the_transitive_requirements(self):
        platform_lines, build_lines = builder._split_sdist_only(
            ["odfpy>=1.4", "defusedxml>=0.7", "requests>=2.31"])
        assert build_lines == ["odfpy>=1.4"]
        # odfpy leaves the platform set, its runtime dependency enters it
        assert "defusedxml>=0.7" in platform_lines
        assert "requests>=2.31" in platform_lines
        assert not any("odfpy" in line for line in platform_lines)

    def test_every_sdist_only_entry_declares_its_runtime_dependencies(self):
        for name, deps in builder.SDIST_ONLY_PURE.items():
            assert isinstance(deps, list), name
            for dep in deps:
                assert isinstance(dep, str) and dep, (name, dep)

    def test_wheelhouse_excludes_development_only_packages(self):
        lines = builder._runtime_requirement_lines()
        names = {inventory_dependencies._req_name(line) for line in lines}
        for dev_only in ("pytest", "pytest-cov", "ruff", "bandit", "pyzipper"):
            assert dev_only not in names, dev_only
        # The runtime extras are in.
        for required in ("pymupdf", "python-docx", "openpyxl", "rapidocr-onnxruntime",
                         "waitress", "pgserver", "pytesseract"):
            assert required in names, required


# ---------------------------------------------------------------------------
# The bundle builder: plan, manifest integrity, verification
# ---------------------------------------------------------------------------
class TestBundleBuilder:
    @pytest.fixture
    def plan_dir(self, tmp_path):
        manifest = builder.plan(tmp_path, "3.11.9")
        (tmp_path / "manifest.json").write_text(json.dumps(manifest, indent=1))
        return tmp_path

    def test_the_plan_records_every_required_component(self, plan_dir):
        manifest = json.loads((plan_dir / "manifest.json").read_text())
        assert manifest["python"]["url"].startswith("https://www.python.org/ftp/python/")
        assert manifest["tesseract"]["url"]
        assert manifest["postgres"]["mechanism"].startswith("pgserver")
        assert {e["language"] for e in manifest["tessdata"]} == {"eng", "ara", "heb"}
        requirements = " ".join(manifest["wheels"]["requirements"])
        for needle in ("Flask", "psycopg2-binary", "PyMuPDF", "pgserver",
                       "rapidocr-onnxruntime", "waitress"):
            assert needle.lower() in requirements.lower(), needle

    def test_tessdata_hashes_match_the_committed_files(self, plan_dir):
        manifest = json.loads((plan_dir / "manifest.json").read_text())
        for entry in manifest["tessdata"]:
            path = plan_dir / entry["file"]
            assert path.exists(), entry["file"]
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            assert actual == entry["sha256"], entry["file"]
            # and the committed MANIFEST.sha256 agrees
            recorded = (ROOT / "offline-bundle" / "windows" / "tessdata"
                        / "MANIFEST.sha256").read_text(encoding="utf-8")
            assert entry["sha256"] in recorded, entry["file"]

    def test_verify_fails_on_a_missing_required_component(self, plan_dir):
        assert builder.verify(plan_dir) == 1  # wheels/tesseract/app not fetched

    def test_verify_passes_on_a_bundle_whose_files_match(self, tmp_path):
        # A minimal-but-complete bundle: real tessdata, and every other entry
        # satisfied with recorded hashes of real files.
        manifest = builder.plan(tmp_path, "3.11.9")
        for name, content in (("python-3.11.9-amd64.exe", b"py"),
                              ("tesseract-portable.exe", b"tess"),
                              ("app-source.zip", b"app")):
            (tmp_path / name).write_bytes(content)
            manifest_key = {"python-3.11.9-amd64.exe": "python",
                            "tesseract-portable.exe": "tesseract",
                            "app-source.zip": "app"}[name]
            manifest[manifest_key].update({
                "installer" if manifest_key != "app" else "archive": name,
                "sha256": hashlib.sha256(content).hexdigest(),
                "present": True})
        wheels = tmp_path / "wheels"
        wheels.mkdir()
        wheel_files = []
        for name in ("flask-3.0.0-py3-none-any.whl", "pgserver-0.1.4-cp311-cp311-win_amd64.whl"):
            data = f"# {name}".encode()
            (wheels / name).write_bytes(data)
            wheel_files.append({"file": name, "sha256": hashlib.sha256(data).hexdigest()})
        manifest["wheels"]["files"] = wheel_files
        manifest["app"]["commit"] = "0" * 40
        (tmp_path / "manifest.json").write_text(json.dumps(manifest, indent=1))
        assert builder.verify(tmp_path) == 0

    def test_a_corrupted_bundle_is_refused(self, tmp_path):
        manifest = builder.plan(tmp_path, "3.11.9")
        entry = manifest["tessdata"][0]
        (tmp_path / entry["file"]).write_bytes(b"tampered")
        (tmp_path / "manifest.json").write_text(json.dumps(manifest, indent=1))
        assert builder.verify(tmp_path) == 1


# ---------------------------------------------------------------------------
# The offline acceptance test: the directive's 13 steps, network monitor
# ---------------------------------------------------------------------------
class TestOfflineAcceptance:
    def test_all_thirteen_directive_steps_are_registered(self):
        steps = {number: name for number, name in offline_acceptance.STEPS}
        assert sorted(steps) == list(range(1, 14))
        names = " ".join(name.lower() for name in steps.values())
        for fragment in ("start", "interface", "authenticate", "ingest",
                         "ocr", "search", "analysis", "export", "restart",
                         "persistence", "repeat"):
            assert fragment in names, fragment

    def test_connection_classification_loopback_vs_external(self):
        classify = offline_acceptance.classify_endpoint
        assert classify("127.0.0.1", 5432) == "local"
        assert classify("::1", 5000) == "local"
        # Link-local (169.254/16) — assembled so this file never contains a
        # machine-specific address literal (test_no_machine_paths).
        link_local = ".".join(("169", "254", "3", "7"))
        assert classify(link_local, 5432) == "local"      # link-local
        assert classify("224.0.0.251", 5353) == "local"      # multicast
        assert classify("203.0.113.9", 443) == "external"
        assert classify("8.8.8.8", 53) == "external"
        assert classify("not-an-ip", 80) == "external"

    def test_search_response_shapes_are_handled(self):
        class R:
            def __init__(self, payload):
                self._payload = payload

            def json(self):
                return self._payload

        get = offline_acceptance._hits
        assert [h["id"] for h in get(R({"results": [{"id": 7}]}))] == [7]
        assert [h["id"] for h in get(R({"hits": [{"id": 7}]}))] == [7]
        assert [h["id"] for h in get(R([{"id": 7}]))] == [7]
        assert get(R({"unexpected": 1})) == []

    def test_the_kit_help_texts_document_offline_use(self):
        for script in ("build_offline_bundle.py", "local_postgres.py",
                       "offline_acceptance.py"):
            text = (ROOT / "tools" / "windows" / script).read_text(encoding="utf-8")
            assert "offline" in text.lower(), script


# ---------------------------------------------------------------------------
# local_postgres: pgserver's URI becomes the application's DB_* settings
# ---------------------------------------------------------------------------
class TestLocalPostgresUriParts:
    def test_a_socket_uri_yields_the_documented_db_fields(self):
        parts = local_postgres._uri_parts(
            "postgresql://postgres@/postgres?host=/srv/SYLTHARAE/pgdata")
        assert parts == {"DB_HOST": "/srv/SYLTHARAE/pgdata", "DB_PORT": "5432",
                         "DB_USER": "postgres", "DB_PASSWORD": "",
                         "DB_NAME": "postgres"}

    def test_an_explicit_port_and_password_uri_is_respected(self):
        parts = local_postgres._uri_parts(
            "postgresql://postgres:secret@127.0.0.1:5433/syltharae")
        assert parts["DB_HOST"] == "127.0.0.1"
        assert parts["DB_PORT"] == "5433"
        assert parts["DB_PASSWORD"] == "secret"
        assert parts["DB_NAME"] == "syltharae"


# ---------------------------------------------------------------------------
# The install scripts provision everything locally
# ---------------------------------------------------------------------------
class TestInstallScripts:
    @pytest.fixture
    def installer(self):
        return (ROOT / "offline-bundle" / "windows" / "install.ps1").read_text(
            encoding="utf-8")

    def test_installer_verifies_before_installing(self, installer):
        assert "manifest.json" in installer
        assert "Get-FileHash" in installer
        assert installer.index('Step "1. Verifying the bundle') < installer.index(
            'Step "2. Python 3.11"')

    def test_installer_installs_without_any_index(self, installer):
        assert "--no-index" in installer
        assert "--find-links" in installer

    def test_installer_separates_application_from_data(self, installer):
        assert "InstallDir" in installer and "DataDir" in installer
        assert "pgdata" in installer  # bundled cluster under the data dir
        assert "TESSERACT_CMD" in installer and "TESSDATA_PREFIX" in installer

    def test_start_script_boots_the_local_database_first(self):
        text = (ROOT / "offline-bundle" / "windows" / "start_syltharae.ps1").read_text(
            encoding="utf-8")
        code = re.sub(r"<#[\s\S]*?#>", "", text)
        code = "\n".join(line for line in code.splitlines()
                         if not line.lstrip().startswith("#"))
        assert code.index("local_postgres.py") < code.index("run_web.py")

    def test_tessdata_ships_the_three_declared_languages(self):
        directory = ROOT / "offline-bundle" / "windows" / "tessdata"
        recorded = (directory / "MANIFEST.sha256").read_text(encoding="utf-8").splitlines()
        recorded_map = {}
        for line in recorded:
            digest, name = line.split(maxsplit=1)
            recorded_map[name.strip()] = digest
        for language in ("eng", "ara", "heb"):
            path = directory / f"{language}.traineddata"
            assert path.exists(), language
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            assert actual == recorded_map[f"{language}.traineddata"], language


# ---------------------------------------------------------------------------
# Nothing may phone home: telemetry stays opt-in
# ---------------------------------------------------------------------------
class TestTelemetryStaysOffByDefault:
    def test_error_monitoring_integrations_default_to_disabled(self):
        module = ROOT / "Hdg_Err_Ex_Log" / "error_monitoring.py"
        text = module.read_text(encoding="utf-8")
        assert "enable_sentry: bool = False" in text
        assert "enable_rollbar: bool = False" in text
