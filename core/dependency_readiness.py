"""Python dependency readiness, computed from the declared manifest and real checks.

The first-run wizard and the administrator diagnostics need one answer to
"can this installation run?". The answer comes from two sources:

* **Declarations.** ``pyproject.toml`` is authoritative for the required
  dependencies (``[project].dependencies``) and the optional extras
  (``[project.optional-dependencies]``). ``requirements.txt`` is the complete
  Windows manifest; any distribution it lists that ``pyproject.toml`` does not
  declare is included as an optional item. Platform markers are evaluated.
* **Checks.** For every declared distribution the installed version is read
  from package metadata and compared with the declared specifier. Then the
  module is imported in a short-lived subprocess, so a native extension that
  crashes on import cannot take the web server down.

Each item ends in exactly one state:

``compatible``     installed, version in range, import succeeds
``missing``        not installed
``incompatible``   installed, but the version is outside the declared range
``import_failed``  installed and in range, but importing it fails
``unknown``        the check could not be completed (e.g. probe timed out)

Install and repair only accept names declared above. They run one pip command
per request with each declared specifier and never add ``--upgrade``, so
unrelated packages are not touched. States ``installing`` and ``failed`` are
overlays for the install job and are reported alongside the check state.
"""

from __future__ import annotations

import importlib.metadata as importlib_metadata
import logging
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
REQUIREMENTS = PROJECT_ROOT / "requirements.txt"

#: Distribution name -> importable module, where they differ.
IMPORT_NAMES: Dict[str, str] = {
    "pillow": "PIL",
    "pymupdf": "fitz",
    "opencv-python-headless": "cv2",
    "python-docx": "docx",
    "python-pptx": "pptx",
    "python-dateutil": "dateutil",
    "python-dotenv": "dotenv",
    "psycopg2-binary": "psycopg2",
    "pyyaml": "yaml",
    "odfpy": "odf",
    "beautifulsoup4": "bs4",
    "libpff-python": "pypff",
    "sentry-sdk": "sentry_sdk",
    "pytest-cov": "pytest_cov",
    "extract-msg": "extract_msg",
    "compressed-rtf": "compressed_rtf",
    "rapidocr-onnxruntime": "rapidocr_onnxruntime",
}

#: Distributions with no importable module (command-line tools only).
METADATA_ONLY = frozenset({"ruff"})

PROBE_TIMEOUT_SECONDS = 25
PROBE_WORKERS = 6
REPORT_TTL_SECONDS = 20.0

STATE_COMPATIBLE = "compatible"
STATE_MISSING = "missing"
STATE_INCOMPATIBLE = "incompatible"
STATE_IMPORT_FAILED = "import_failed"
STATE_UNKNOWN = "unknown"
STATE_INSTALLING = "installing"
STATE_FAILED = "failed"

#: States that stop the application from running when the item is required.
BLOCKING_STATES = frozenset({STATE_MISSING, STATE_INCOMPATIBLE, STATE_IMPORT_FAILED,
                             STATE_UNKNOWN, STATE_FAILED})


@dataclass(frozen=True)
class Declaration:
    name: str            # canonical distribution name
    display: str         # name as written in the manifest
    requirement: str     # declared requirement string, e.g. "Flask>=3.0,<4"
    required: bool
    group: str           # "required", an extra name, or "manifest"
    source: str          # where it is declared, for the inspect view

    @property
    def import_name(self) -> Optional[str]:
        """Module to import: the fixed table, else the installed package's own
        top-level module name, else the normalised distribution name."""
        if self.name in METADATA_ONLY:
            return None
        if self.name in IMPORT_NAMES:
            return IMPORT_NAMES[self.name]
        return _module_from_metadata(self.display) or self.name.replace("-", "_")


# ─────────────────────────────────────────────────────────────────────────
# Declarations
# ─────────────────────────────────────────────────────────────────────────

def _load_pyproject(path: Path) -> Optional[Dict[str, Any]]:
    try:
        import tomllib  # Python 3.11+
    except ImportError:  # pragma: no cover - Python 3.10 fallback
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            return None
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (OSError, ValueError):
        return None


def _parse_requirement(text: str) -> Optional[Requirement]:
    try:
        return Requirement(text)
    except InvalidRequirement:
        return None


def _marker_applies(req: Requirement) -> bool:
    if req.marker is None:
        return True
    try:
        return bool(req.marker.evaluate())
    except Exception:
        return True


def load_declarations(pyproject: Path = PYPROJECT,
                      requirements: Path = REQUIREMENTS) -> List[Declaration]:
    """Every declared distribution, once each, required items first."""
    items: Dict[str, Declaration] = {}

    data = _load_pyproject(pyproject) or {}
    project = data.get("project", {}) if isinstance(data, dict) else {}
    for text in project.get("dependencies", []) or []:
        req = _parse_requirement(text)
        if req is None or not _marker_applies(req):
            continue
        name = canonicalize_name(req.name)
        items.setdefault(name, Declaration(
            name=name, display=req.name, requirement=str(req), required=True,
            group="required", source="pyproject.toml [project].dependencies"))

    for group, deps in (project.get("optional-dependencies", {}) or {}).items():
        for text in deps or []:
            req = _parse_requirement(text)
            if req is None or not _marker_applies(req):
                continue
            name = canonicalize_name(req.name)
            items.setdefault(name, Declaration(
                name=name, display=req.name, requirement=str(req), required=False,
                group=group,
                source=f"pyproject.toml [project.optional-dependencies].{group}"))

    try:
        lines = requirements.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for raw in lines:
        line = re.split(r"\s+#", raw, maxsplit=1)[0].strip()
        if not line or line.startswith("-"):
            continue
        req = _parse_requirement(line)
        if req is None or not _marker_applies(req):
            continue
        name = canonicalize_name(req.name)
        items.setdefault(name, Declaration(
            name=name, display=req.name, requirement=str(req), required=False,
            group="manifest", source="requirements.txt"))

    return sorted(items.values(), key=lambda d: (not d.required, d.group, d.name))


def trusted_declarations() -> Dict[str, Declaration]:
    return {d.name: d for d in load_declarations()}


# ─────────────────────────────────────────────────────────────────────────
# Checks
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class ItemResult:
    declaration: Declaration
    state: str
    installed_version: Optional[str] = None
    detail: str = ""
    #: Message key (gettext msgid) and its variables, so the UI can translate
    #: the explanation. ``detail`` stays as English text for logs and the CLI.
    detail_key: str = ""
    detail_vars: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        d = self.declaration
        return {
            "name": d.display,
            "canonical_name": d.name,
            "requirement": d.requirement,
            "required": d.required,
            "group": d.group,
            "source": d.source,
            "import_name": d.import_name,
            "state": self.state,
            "installed_version": self.installed_version,
            "blocking": d.required and self.state in BLOCKING_STATES,
            "detail": self.detail,
            "detail_key": self.detail_key,
            "detail_vars": self.detail_vars,
        }


def _probe_import(module: str) -> Optional[str]:
    """Import ``module`` in a subprocess. Returns None on success, else the error line."""
    code = f"import importlib; importlib.import_module({module!r})"
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True,
            timeout=PROBE_TIMEOUT_SECONDS, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
    except subprocess.TimeoutExpired:
        return "__timeout__"
    except OSError as exc:
        return f"__unknown__:{exc}"
    if proc.returncode == 0:
        return None
    lines = [ln for ln in (proc.stderr or "").strip().splitlines() if ln.strip()]
    return lines[-1][:300] if lines else f"exit code {proc.returncode}"


def _module_from_metadata(dist_name: str) -> Optional[str]:
    """The importable module an installed distribution declares (top_level.txt)."""
    try:
        text = importlib_metadata.distribution(dist_name).read_text("top_level.txt") or ""
    except Exception:
        return None
    modules = [line.strip() for line in text.splitlines() if line.strip()]
    return modules[0] if modules else None


def check_declaration(decl: Declaration,
                      version_of: Optional[Callable[[str], str]] = None,
                      probe: Optional[Callable[[str], Optional[str]]] = None) -> ItemResult:
    """Evaluate one declaration against the live environment.

    ``version_of`` and ``probe`` default to the live implementations, looked up
    at call time so they can be replaced (tests, alternative runners).
    """
    version_of = version_of or importlib_metadata.version
    probe = probe or _probe_import
    try:
        installed = version_of(decl.display)
    except importlib_metadata.PackageNotFoundError:
        return ItemResult(decl, STATE_MISSING, None, "Not installed",
                          "Not installed")
    except Exception as exc:  # unreadable metadata
        return ItemResult(decl, STATE_UNKNOWN, None,
                          f"Could not read package metadata: {exc}",
                          "Could not read package metadata: %(error)s", {"error": str(exc)})

    req = Requirement(decl.requirement)
    if req.specifier and not req.specifier.contains(installed, prereleases=True):
        text = f"Installed {installed}; requires {req.specifier}"
        return ItemResult(decl, STATE_INCOMPATIBLE, installed, text,
                          "Installed %(installed)s; requires %(requires)s",
                          {"installed": installed, "requires": str(req.specifier)})

    module = decl.import_name
    if module is None:
        return ItemResult(decl, STATE_COMPATIBLE, installed,
                          "Installed (command-line tool)", "Installed (command-line tool)")

    error = probe(module)
    if error is None:
        return ItemResult(decl, STATE_COMPATIBLE, installed, "")
    if error == "__timeout__":
        return ItemResult(decl, STATE_UNKNOWN, installed,
                          f"Import did not finish within {PROBE_TIMEOUT_SECONDS} seconds",
                          "Import did not finish within %(seconds)s seconds",
                          {"seconds": PROBE_TIMEOUT_SECONDS})
    if error.startswith("__unknown__:"):
        return ItemResult(decl, STATE_UNKNOWN, installed, error.split(":", 1)[1])
    return ItemResult(decl, STATE_IMPORT_FAILED, installed, error)


# ─────────────────────────────────────────────────────────────────────────
# Install jobs (trusted names only)
# ─────────────────────────────────────────────────────────────────────────

_STATE_LOCK = threading.Lock()
_JOBS: Dict[str, Dict[str, Any]] = {}
_ACTIVE: Dict[str, str] = {}       # canonical name -> job id, while installing
_FAILED: Dict[str, str] = {}       # canonical name -> last failure message
_FAILED_KEYS: Dict[str, Any] = {}  # canonical name -> (message key, variables) for _FAILED
_REPORT_CACHE: Dict[str, Any] = {"at": 0.0, "report": None}
_JOB_LOG_LINES = 200


class DependencyRequestError(ValueError):
    """Rejected install request. ``code`` is stable so the API can translate it."""

    def __init__(self, code: str, name: str = ""):
        super().__init__(code)
        self.code = code
        self.name = name


class DependencyBusyError(RuntimeError):
    """Raised when an install is already running."""


def _invalidate_cache() -> None:
    _REPORT_CACHE["report"] = None
    _REPORT_CACHE["at"] = 0.0


def build_report(refresh: bool = False) -> Dict[str, Any]:
    """Readiness for the whole declared manifest, computed now (or from a fresh cache)."""
    now = time.monotonic()
    with _STATE_LOCK:
        cached = _REPORT_CACHE["report"]
        if cached is not None and not refresh and now - _REPORT_CACHE["at"] < REPORT_TTL_SECONDS:
            return cached

    declarations = load_declarations()
    with ThreadPoolExecutor(max_workers=PROBE_WORKERS) as pool:
        results = list(pool.map(check_declaration, declarations))

    with _STATE_LOCK:
        active = dict(_ACTIVE)
        failed = dict(_FAILED)
    items = []
    for result in results:
        item = result.as_dict()
        if result.declaration.name in active:
            item["state"] = STATE_INSTALLING
            item["job_id"] = active[result.declaration.name]
        elif result.declaration.name in failed and result.state in BLOCKING_STATES:
            item["state"] = STATE_FAILED
            item["blocking"] = item["required"]
            item["detail"] = failed[result.declaration.name]
            key, variables = _FAILED_KEYS.get(result.declaration.name, ("", {}))
            item["detail_key"] = key
            item["detail_vars"] = dict(variables)
        items.append(item)

    required_items = [i for i in items if i["required"]]
    optional_gaps = [i for i in items if not i["required"] and i["state"] != STATE_COMPATIBLE]
    blocking = [i for i in required_items if i["blocking"]]
    report = {
        "ready": not blocking,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "counts": {
            "declared": len(items),
            "required": len(required_items),
            "required_ready": len(required_items) - len(blocking),
            "optional_gaps": len(optional_gaps),
        },
        "blocking": [i["name"] for i in blocking],
        "items": items,
        "active_job": next(iter(active.values()), None),
    }
    with _STATE_LOCK:
        _REPORT_CACHE["report"] = report
        _REPORT_CACHE["at"] = time.monotonic()
    return report


def _pip_command(specs: List[str]) -> List[str]:
    # No --upgrade, no -U, no --upgrade-strategy: only the declared packages
    # are installed, and already-satisfied packages are left alone.
    return [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
            "--no-input", "--progress-bar", "off", *specs]


def _run_job(job_id: str, names: List[str], specs: List[str]) -> None:
    """Run pip for ``specs`` and record the outcome. Always releases the job.

    The ``finally`` clause matters: if anything here raised before releasing
    ``_ACTIVE``, every later installation would report "busy" until restart.
    """
    job = _JOBS[job_id]
    log: List[str] = []
    code = -1
    outcome: Dict[str, Any] = {}
    try:
        try:
            proc = subprocess.Popen(
                _pip_command(specs), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, env={**os.environ, "PIP_NO_INPUT": "1"},
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                log.append(line.rstrip())
                del log[:-_JOB_LOG_LINES]
                job["log"] = list(log)
            code = proc.wait(timeout=1800)
        except Exception as exc:  # pip could not start, or timed out
            code = -1
            log.append(f"pip did not complete: {exc}")
            job["log"] = list(log)

        declared = trusted_declarations()
        for name in names:
            decl = declared.get(name)
            if decl is None:
                continue
            try:
                result = check_declaration(decl)
                outcome[decl.display] = {"ok": result.state == STATE_COMPATIBLE,
                                         "state": result.state, "detail": result.detail,
                                         "detail_key": result.detail_key,
                                         "detail_vars": dict(result.detail_vars)}
            except Exception as exc:
                outcome[decl.display] = {"ok": False, "state": STATE_UNKNOWN,
                                         "detail": f"Check after install failed: {exc}",
                                         "detail_key": "", "detail_vars": {}}
    except Exception:
        logger.exception("Dependency install job %s failed unexpectedly", job_id)
    finally:
        with _STATE_LOCK:
            declared = trusted_declarations()
            for name in names:
                _ACTIVE.pop(name, None)
                decl = declared.get(name)
                if decl is None:
                    continue
                entry = outcome.get(decl.display)
                if entry is not None and entry["ok"]:
                    _FAILED.pop(name, None)
                    _FAILED_KEYS.pop(name, None)
                    continue
                if code != 0:
                    # The install itself failed: the exit code is the actionable fact.
                    _FAILED[name] = f"pip exited with code {code}"
                    _FAILED_KEYS[name] = ("pip exited with code %(code)s", {"code": code})
                elif entry is not None:
                    _FAILED[name] = entry["detail"] or entry["state"]
                    _FAILED_KEYS[name] = (entry["detail_key"], dict(entry["detail_vars"]))
                else:
                    _FAILED[name] = f"pip exited with code {code}"
                    _FAILED_KEYS[name] = ("pip exited with code %(code)s", {"code": code})
            succeeded = code == 0 and bool(outcome) and all(o["ok"] for o in outcome.values())
            job.update(status="succeeded" if succeeded else "failed", returncode=code,
                       results=outcome, log=list(log),
                       finished_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        _invalidate_cache()


def start_install(names: List[str]) -> Dict[str, Any]:
    """Start one pip job for declared distributions. Returns the job record."""
    if not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names):
        raise DependencyRequestError("none_chosen")
    if len(names) > 80:
        raise DependencyRequestError("too_many")

    declared = trusted_declarations()
    chosen: List[Declaration] = []
    seen = set()
    for raw in names:
        canonical = canonicalize_name(raw)
        decl = declared.get(canonical)
        if decl is None:
            raise DependencyRequestError("not_declared", raw)
        if canonical not in seen:
            seen.add(canonical)
            chosen.append(decl)

    with _STATE_LOCK:
        if _ACTIVE:
            raise DependencyBusyError("An installation is already running.")
        job_id = uuid.uuid4().hex
        names_canonical = [d.name for d in chosen]
        for name in names_canonical:
            _ACTIVE[name] = job_id
        _JOBS[job_id] = {
            "job_id": job_id, "status": "running", "names": [d.display for d in chosen],
            "log": [], "results": {}, "returncode": None,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "finished_at": None,
        }
    specs = [d.requirement for d in chosen]
    thread = threading.Thread(target=_run_job, args=(job_id, names_canonical, specs),
                              name=f"dependency-install-{job_id[:8]}", daemon=True)
    thread.start()
    return dict(_JOBS[job_id])


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    with _STATE_LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job is not None else None
