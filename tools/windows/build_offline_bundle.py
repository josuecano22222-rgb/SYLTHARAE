"""Build the fully offline Windows distribution bundle for SYLTHARAE.

The bundle is assembled on a CONNECTED machine and installed on a machine
that never touches the Internet. Every runtime resource is staged locally:

    runtime     the pinned CPython 3.11 Windows installer
    wheels      the complete win_amd64 wheelhouse (base + runtime extras),
                including the pgserver wheel that carries the bundled
                PostgreSQL 16 server binaries
    tesseract   the pinned Tesseract portable build (and optional FFmpeg)
    tessdata    eng / ara / heb language data from offline-bundle/windows/tessdata
    app         `git archive` of the release commit as a zip

Everything lands in an output directory next to ``manifest.json``, which
records a SHA-256 for every file. The offline target verifies the manifest
before installing (``--verify``), so a truncated or tampered bundle cannot
half-install.

Usage (build machine, online):
    python tools/windows/build_offline_bundle.py --output /path/bundle
    python tools/windows/build_offline_bundle.py --output /path/bundle \\
        --stage wheels --stage app --stage tessdata --stage tesseract --stage runtime

Offline integrity check (any machine):
    python tools/windows/build_offline_bundle.py --output /path/bundle --verify

Plan only (no network): add ``--plan``; the manifest then records every
required entry with ``present: false`` for components not yet fetched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import urllib.request
from urllib.parse import urlsplit
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

BUNDLE_VERSION = 1

#: Pinned upstream artifacts. A release pins the SHA-256 here once the artifact
#: has been reviewed; the builder refuses to fetch an artifact whose recorded
#: hash does not match (a recorded ``None`` means "pin on first fetch and
#: update this table before releasing").
PINNED = {
    "python": {
        "version": "3.11.9",
        "url": "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe",
        "file": "python-3.11.9-amd64.exe",
        "sha256": None,
    },
    "tesseract": {
        # UB-Mannheim Tesseract portable build for Windows x64.
        "url": "https://digi.bib.uni-mannheim.de/tesseract/tesseract-ocr-w64-setup-5.5.0.20241111.exe",
        "file": "tesseract-ocr-w64-setup-5.5.0.20241111.exe",
        "sha256": None,
    },
    "ffmpeg": {
        # Optional: media formats. gyan.dev "essentials" build.
        "url": "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
        "file": "ffmpeg-release-essentials.zip",
        "sha256": None,
        "optional": True,
    },
}

RUNTIME_EXTRAS = ("pdf", "office", "ocr", "ebook", "audio", "media", "server")

#: Dependencies that publish NO wheel at all (sdist only, on every platform)
#: but are pure Python. The offline target installs wheels only - an sdist
#: would need a toolchain it must not require - so the CONNECTED build
#: machine builds their universal wheel at staging time (``pip wheel``). The
#: build refuses any wheel that is not ``none-any``: the moment one of these
#: starts shipping compiled content, it leaves this list and needs a pinned
#: upstream artifact instead. The values are the package's runtime
#: dependencies that the platform download must carry (they are not pulled
#: in transitively once the sdist-only line is removed from it).
SDIST_ONLY_PURE = {
    "odfpy": ["defusedxml"],   # [WIN-NATIVE] OpenDocument extraction
    # transitive (extract-msg): ships two pure modules, no wheel anywhere
    "red-black-tree-mod": [],
}


def _split_sdist_only(lines: list[str]) -> tuple[list[str], list[str]]:
    """Split requirement lines into (platform-download, build-here).

    The platform download must not contain the sdist-only names (pip cannot
    resolve them under ``--only-binary``) but must carry their runtime
    dependencies; the build-here list keeps the original requirement lines
    for ``pip wheel`` on the connected machine.
    """
    from packaging.utils import canonicalize_name

    platform_lines: list[str] = []
    build_lines: list[str] = []
    for line in lines:
        name = _req_name(line)
        if canonicalize_name(name) in {canonicalize_name(n) for n in SDIST_ONLY_PURE}:
            build_lines.append(line)
            platform_lines.extend(SDIST_ONLY_PURE[name])
        else:
            platform_lines.append(line)
    return platform_lines, build_lines

TESSDATA_DIR = ROOT / "offline-bundle" / "windows" / "tessdata"
TESSDATA_LANGUAGES = ("eng", "ara", "heb")


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# Development-only packages: never downloaded into the shipped wheelhouse
# (the "Development-only" class of the dependency inventory). The list mirrors
# tools/windows/inventory_dependencies.py's dev-only set: pyzipper is there
# because tests/unit/test_archive_formats.py imports it to *build* an
# AES-encrypted ZIP fixture - no product module imports it - so it belongs in
# requirements.txt's test section but never in the offline bundle.
DEV_ONLY_PACKAGES = {"pytest", "pytest-cov", "ruff", "bandit", "pyzipper"}


def _req_name(requirement: str) -> str:
    """The package name of a pip requirement line ('Flask>=3.0,<4' -> flask)."""
    return re.split(r"[<>=!~\[]", requirement, maxsplit=1)[0].strip().lower().replace("_", "-")


def _runtime_requirement_lines() -> list[str]:
    """Base requirements plus the runtime extras, as pip requirement lines."""
    lines = []
    for raw in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            lines.append(line)
    import tomllib

    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = data["project"]["optional-dependencies"]
    for extra in RUNTIME_EXTRAS:
        lines.extend(extras[extra])
    # Build tooling for the offline venv.
    lines.extend(["pip", "setuptools", "wheel"])
    # Development-only packages are not shipped.
    lines = [line for line in lines if _req_name(line) not in DEV_ONLY_PACKAGES]
    return sorted(set(lines))


def stage_wheels(output: Path, manifest: dict, python_version: str) -> None:
    """Download the complete win_amd64 wheelhouse."""
    wheels_dir = output / "wheels"
    wheels_dir.mkdir(parents=True, exist_ok=True)
    platform_lines, build_lines = _split_sdist_only(_runtime_requirement_lines())
    # Every registered sdist-only package gets a wheel, whether it is a
    # direct requirement or only a transitive one (the split above can only
    # see direct lines): the platform download below resolves through
    # --find-links and must find them already built.
    build_reqs = list(build_lines)
    covered = {_req_name(line).replace("-", "_").lower() for line in build_lines}
    for name in SDIST_ONLY_PURE:
        if name.replace("-", "_").lower() not in covered:
            build_reqs.append(name)
    # Build the sdist-only pure-python wheels locally (connected machine);
    # a non-universal wheel here means the package is no longer pure and
    # must move to a pinned upstream artifact, not be shipped as a build.
    for line in build_reqs:
        print(f"building the universal wheel for the sdist-only dependency: {line}", flush=True)
        subprocess.run([sys.executable, "-m", "pip", "wheel", line,
                        "--no-deps", "-w", str(wheels_dir)], check=True)
    built_names = {_req_name(line).replace("-", "_").lower() for line in build_reqs}
    for path in wheels_dir.iterdir():
        if path.suffix == ".whl" and "none-any" not in path.name \
                and path.name.split("-")[0].lower() in built_names:
            raise SystemExit(
                f"{path.name} is not a universal (none-any) wheel - the "
                "package is not pure Python and cannot be built here")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
        handle.write("\n".join(platform_lines) + "\n")
        req_file = handle.name
    command = [
        sys.executable, "-m", "pip", "download",
        "--platform", "win_amd64",
        "--python-version", python_version.replace(".", "")[:3],
        "--implementation", "cp",
        "--abi", "cp311" if python_version.startswith("3.11") else "cp" + python_version.replace(".", "")[:3],
        "--only-binary", ":all:",
        "--find-links", str(wheels_dir),
        "-d", str(wheels_dir),
        "-r", req_file,
    ]
    print("resolving the win_amd64 wheelhouse ...", flush=True)
    subprocess.run(command, check=True)
    files = sorted(path for path in wheels_dir.iterdir() if path.suffix == ".whl")
    manifest["wheels"] = {
        "directory": "wheels",
        "platform": "win_amd64",
        "python": python_version,
        "requirements": _runtime_requirement_lines(),
        "files": [{"file": path.name, "sha256": sha256_of(path)} for path in files],
    }
    print(f"wheels: {len(files)} files")


def stage_runtime(output: Path, manifest: dict) -> None:
    pin = PINNED["python"]
    target = output / pin["file"]
    _fetch(pin, target, manifest_entry=manifest.setdefault("python", {}))
    manifest["python"].update({
        "version": pin["version"],
        "installer": pin["file"],
        "url": pin["url"],
    })


def stage_tesseract(output: Path, manifest: dict) -> None:
    pin = PINNED["tesseract"]
    target = output / pin["file"]
    _fetch(pin, target, manifest_entry=manifest.setdefault("tesseract", {}))
    manifest["tesseract"].update({"installer": pin["file"], "url": pin["url"]})
    ff = PINNED["ffmpeg"]
    ff_target = output / ff["file"]
    entry = manifest.setdefault("ffmpeg", {"optional": True})
    if ff_target.exists():
        entry.update({"archive": ff["file"], "sha256": sha256_of(ff_target), "present": True})
    else:
        try:
            _fetch(ff, ff_target, manifest_entry=entry)
        except Exception as error:  # optional component
            entry.update({"archive": ff["file"], "url": ff["url"],
                          "sha256": None, "present": False, "error": str(error)})
            print(f"ffmpeg unavailable ({error}); bundle stays valid, media "
                  "formats will be recorded as unsupported")


def stage_tessdata(output: Path, manifest: dict) -> None:
    tess_dir = output / "tessdata"
    tess_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for language in TESSDATA_LANGUAGES:
        source = TESSDATA_DIR / f"{language}.traineddata"
        target = tess_dir / source.name
        target.write_bytes(source.read_bytes())
        entries.append({"language": language, "file": f"tessdata/{source.name}",
                        "sha256": sha256_of(target)})
    manifest["tessdata"] = entries


def stage_app(output: Path, manifest: dict) -> None:
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT),
                            check=True, capture_output=True, text=True).stdout.strip()
    archive = output / "app-source.zip"
    with open(archive, "wb") as handle:
        subprocess.run(["git", "archive", "--format=zip", commit], cwd=str(ROOT),
                       check=True, stdout=handle)
    manifest["app"] = {"archive": archive.name, "commit": commit,
                       "sha256": sha256_of(archive)}


def _fetch(pin: dict, target: Path, manifest_entry: dict) -> None:
    if target.exists() and pin.get("sha256"):
        actual = sha256_of(target)
        if actual != pin["sha256"]:
            raise SystemExit(f"{target.name}: sha256 {actual} does not match the "
                             f"pinned {pin['sha256']}; refusing to bundle it")
        manifest_entry.update({"archive": target.name, "url": pin["url"],
                               "sha256": actual, "present": True})
        return
    print(f"fetching {pin['url']} ...", flush=True)
    if urlsplit(pin["url"]).scheme != "https":
        raise SystemExit(f"refusing to fetch a non-https pin URL: {pin['url']}")
    tmp = target.with_suffix(target.suffix + ".part")
    # https-only by the guard above; the artifact is refused below unless
    # its sha256 matches the pin.
    urllib.request.urlretrieve(pin["url"], tmp)  # nosec B310 - https-only, hash-verified
    actual = sha256_of(tmp)
    if pin.get("sha256") and actual != pin["sha256"]:
        tmp.unlink()
        raise SystemExit(f"{target.name}: sha256 {actual} does not match the "
                         f"pinned {pin['sha256']}; refusing to bundle it")
    tmp.rename(target)
    if not pin.get("sha256"):
        print(f"PIN THIS HASH before releasing: {target.name} sha256={actual}")
    manifest_entry.update({"archive": target.name, "url": pin["url"],
                           "sha256": actual, "present": True})


def plan(output: Path, python_version: str) -> dict:
    """The manifest without any network use: everything required, none fetched."""
    manifest: dict = {"bundle_version": BUNDLE_VERSION}
    pin = PINNED["python"]
    manifest["python"] = {"version": pin["version"], "installer": pin["file"],
                          "url": pin["url"], "sha256": pin["sha256"],
                          "present": False}
    manifest["wheels"] = {"directory": "wheels", "platform": "win_amd64",
                          "python": python_version,
                          "requirements": _runtime_requirement_lines(),
                          "files": []}
    tp = PINNED["tesseract"]
    manifest["tesseract"] = {"installer": tp["file"], "url": tp["url"],
                             "sha256": tp["sha256"], "present": False}
    ff = PINNED["ffmpeg"]
    manifest["ffmpeg"] = {"archive": ff["file"], "url": ff["url"],
                          "sha256": ff["sha256"], "present": False,
                          "optional": True}
    stage_tessdata(output, manifest)
    manifest["postgres"] = {
        "mechanism": "pgserver wheel (bundled PostgreSQL 16 binaries)",
        "note": "the wheels stage ships it; tools/windows/local_postgres.py "
                "creates the private cluster under the data directory",
    }
    manifest["app"] = {"archive": "app-source.zip", "commit": None,
                       "sha256": None, "present": False}
    return manifest


def verify(output: Path) -> int:
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    problems: list[str] = []
    checked = 0

    def check_file(relative: str, expected: str | None, required: bool) -> None:
        nonlocal checked
        path = output / relative
        if not path.exists():
            if required:
                problems.append(f"missing required file: {relative}")
            return
        if not expected:
            if required:
                problems.append(f"{relative}: no sha256 recorded in the manifest")
            return
        actual = sha256_of(path)
        if actual != expected:
            problems.append(f"{relative}: sha256 {actual} != manifest {expected}")
        checked += 1

    py = manifest.get("python", {})
    if py.get("installer"):
        check_file(py["installer"], py.get("sha256"), required=True)
    wheels = manifest.get("wheels", {})
    for entry in wheels.get("files", []):
        check_file(f"{wheels.get('directory', 'wheels')}/{entry['file']}",
                   entry.get("sha256"), required=True)
    if not wheels.get("files"):
        problems.append("manifest records no wheelhouse files")
    tess = manifest.get("tesseract", {})
    if tess.get("installer"):
        check_file(tess["installer"], tess.get("sha256"), required=True)
    for entry in manifest.get("tessdata", []):
        check_file(entry["file"], entry.get("sha256"), required=True)
    app = manifest.get("app", {})
    if app.get("archive"):
        check_file(app["archive"], app.get("sha256"), required=app.get("present") is True)
    ff = manifest.get("ffmpeg", {})
    if ff.get("archive") and ff.get("sha256"):
        check_file(ff["archive"], ff.get("sha256"), required=False)

    if problems:
        print("BUNDLE VERIFY: FAIL")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"BUNDLE VERIFY: PASS ({checked} files checked against manifest.json)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--stage", action="append",
                        choices=["runtime", "wheels", "tesseract", "tessdata", "app"],
                        help="stage to build (repeatable; default: all)")
    parser.add_argument("--python-version", default="3.11.9")
    parser.add_argument("--plan", action="store_true",
                        help="write manifest.json without network use")
    parser.add_argument("--verify", action="store_true",
                        help="verify an existing bundle against its manifest")
    args = parser.parse_args()

    if args.verify:
        return verify(args.output)

    args.output.mkdir(parents=True, exist_ok=True)
    if args.plan:
        manifest = plan(args.output, args.python_version)
    else:
        manifest_path = args.output / "manifest.json"
        manifest = (json.loads(manifest_path.read_text(encoding="utf-8"))
                    if manifest_path.exists() else {"bundle_version": BUNDLE_VERSION})
        stages = args.stage or ["runtime", "wheels", "tesseract", "tessdata", "app"]
        if "wheels" in stages:
            stage_wheels(args.output, manifest, args.python_version)
        if "runtime" in stages:
            stage_runtime(args.output, manifest)
        if "tesseract" in stages:
            stage_tesseract(args.output, manifest)
        if "tessdata" in stages:
            stage_tessdata(args.output, manifest)
        if "app" in stages:
            stage_app(args.output, manifest)
    manifest_path = args.output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n",
                             encoding="utf-8")
    print(f"manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
