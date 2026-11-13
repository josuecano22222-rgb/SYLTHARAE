"""VBA macro extraction: the code, its metadata and its proof.

A macro-enabled document is evidence in two layers. The first layer is the
compressed VBA project itself: its presence, its size, its digest, and the fact
that it is signed or not. The second is the decompressed source of every module,
which is what an examiner reads and searches.

Both layers are extracted here:

* the ``vbaProject.bin`` stream (or the legacy ``_VBA_PROJECT_CUR`` storage
  inside a ``.doc``/``.xls``/``.ppt``) is hashed with MD5 and SHA-256 and its
  size recorded - the digest is the evidence of exactly which macro build was
  present, independent of whether the source could be decompressed;
* the modules are decompressed through ``oletools``/``olevba`` (MS-OVBA
  compression), which is also what produces the auto-execution and suspicious
  keyword analysis.

Failure is a recorded outcome, never an empty result: a project that cannot be
decompressed reports ``extraction_error`` with the reason, and its hashes are
still kept. "No macros" and "macros we could not read" must never look alike in
a report.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import zipfile
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

MACRO_ENTRY_RE = r"(^|/)vbaProject\.bin$"

#: Caps: a hostile or corrupt package must not be able to exhaust memory.
MAX_MODULES = 2000
MAX_SOURCE_CHARS = 8 * 1024 * 1024
MAX_PROJECT_BYTES = 256 * 1024 * 1024

_SIGNATURE_PART_RE = r"vbaProjectSignature\.bin$"


@dataclass(frozen=True)
class MacroProject:
    """One VBA project: where it lives, what it hashes to, what it contains."""

    container: str
    """Where the project was found: a package part name, or the file itself."""

    size: int
    md5: str
    sha256: str
    modules: List[Dict[str, object]]
    metadata: Dict[str, object]
    signed: bool = False
    extraction_error: Optional[str] = None
    truncated: bool = False

    def as_dict(self) -> Dict[str, object]:
        return {
            "container": self.container,
            "size": self.size,
            "md5": self.md5,
            "sha256": self.sha256,
            "signed": self.signed,
            "module_count": len(self.modules),
            "modules": self.modules,
            "metadata": self.metadata,
            "extraction_error": self.extraction_error,
            "truncated": self.truncated,
        }

    @property
    def source_text(self) -> str:
        """Concatenated module source, for indexing and search."""
        return "\n\n".join(str(module.get("source") or "") for module in self.modules)


def _hashes(data: bytes) -> Tuple[str, str]:
    return hashlib.md5(data, usedforsecurity=False).hexdigest(), hashlib.sha256(data).hexdigest()


def _extract_with_olevba(path: str) -> Tuple[List[Dict[str, object]], Dict[str, object], Optional[str]]:
    """Decompress modules with oletools; returns ``(modules, metadata, error)``."""
    try:
        from oletools.olevba import VBA_Parser  # type: ignore
    except Exception as exc:  # pragma: no cover - environment dependent
        return [], {}, f"oletools/olevba is not available: {exc}"

    modules: List[Dict[str, object]] = []
    metadata: Dict[str, object] = {}
    try:
        parser = VBA_Parser(path)
    except Exception as exc:
        return [], {}, f"olevba could not open the macro project: {exc}"

    truncated = False
    try:
        if not parser.detect_vba_macros():
            return [], {"autoexec": [], "suspicious": [],
                        "vba_stomping": False}, None
        try:
            metadata["autoexec"] = [
                {"keyword": keyword, "description": description}
                for keyword, description in parser.detect_autoexec()
            ]
        except Exception as exc:
            metadata["autoexec_error"] = str(exc)
        try:
            metadata["suspicious"] = [
                {"keyword": keyword, "description": description}
                for keyword, description in parser.detect_suspicious_keywords()
            ]
        except Exception as exc:
            metadata["suspicious_error"] = str(exc)
        try:
            metadata["vba_stomping"] = bool(parser.detect_vba_stomping())
        except Exception as exc:
            metadata["vba_stomping_error"] = str(exc)

        total_chars = 0
        for (_filename, stream_path, vba_filename, vba_code) in parser.extract_macros():
            if len(modules) >= MAX_MODULES or total_chars >= MAX_SOURCE_CHARS:
                truncated = True
                break
            code = vba_code or ""
            if total_chars + len(code) > MAX_SOURCE_CHARS:
                code = code[: max(0, MAX_SOURCE_CHARS - total_chars)]
                truncated = True
            total_chars += len(code)
            modules.append({
                "stream": stream_path,
                "name": vba_filename,
                "line_count": code.count("\n") + 1 if code else 0,
                "source": code,
                "md5": hashlib.md5(code.encode("utf-8", "replace"), usedforsecurity=False).hexdigest(),
            })
        metadata["truncated"] = truncated
    except Exception as exc:
        return modules, metadata, f"macro decompression failed: {exc}"
    finally:
        try:
            parser.close()
        except Exception:
            pass
    return modules, metadata, None


def _project_from_bytes(data: bytes, container: str, signed: bool = False,
                        source_path: Optional[str] = None) -> MacroProject:
    """Hash a VBA project and decompress it (via a temporary file when needed)."""
    md5, sha256 = _hashes(data)
    path = source_path
    cleanup = None
    if path is None:
        handle = tempfile.NamedTemporaryFile(prefix="vbaProject_", suffix=".bin", delete=False)
        try:
            handle.write(data)
            handle.close()
            path = handle.name
            cleanup = handle.name
        except Exception:
            handle.close()
            return MacroProject(container=container, size=len(data), md5=md5, sha256=sha256,
                                modules=[], metadata={}, signed=signed,
                                extraction_error="could not materialise the VBA project for parsing")
    try:
        modules, metadata, error = _extract_with_olevba(path)
    finally:
        if cleanup:
            try:
                os.unlink(cleanup)
            except OSError:
                pass
    return MacroProject(container=container, size=len(data), md5=md5, sha256=sha256,
                        modules=modules, metadata=metadata, signed=signed,
                        extraction_error=error,
                        truncated=bool(metadata.get("truncated")))


def extract_vba(path: str, *, declared_format: Optional[str] = None) -> Dict[str, object]:
    """Extract every VBA project reachable from an Office artifact.

    Works for both container kinds:

    * OOXML/macro-enabled packages (``.docm``/``.xlsm``/``.pptm``/``.dotm`` and
      friends) - the ``vbaProject.bin`` parts are pulled out of the ZIP and each
      one is hashed and decompressed;
    * legacy OLE documents (``.doc``/``.xls``/``.ppt``) - the file itself is the
      compound document holding ``_VBA_PROJECT_CUR``/``Macros``.

    Args:
        path: Path to the artifact.
        declared_format: Detected format id, when the caller has one (recorded
            in the result for provenance).

    Returns:
        ``{"macro_present": bool, "projects": [...], "extraction_errors": [...],
        "signed": bool, "source_text": str}``. ``macro_present`` is True when a
        project exists even if it could not be decompressed, so a failure is
        never reported as "no macros".
    """
    result: Dict[str, object] = {
        "macro_present": False,
        "projects": [],
        "signed": False,
        "extraction_errors": [],
        "declared_format": declared_format,
    }
    projects: List[Dict[str, object]] = result["projects"]  # type: ignore[assignment]
    errors: List[Dict[str, str]] = result["extraction_errors"]  # type: ignore[assignment]

    if not path or not os.path.exists(path):
        errors.append({"scope": "macro", "error": f"file not readable: {path!r}"})
        return result

    # --- OOXML package: one project per vbaProject.bin part -----------------
    package_projects = 0
    try:
        with zipfile.ZipFile(path) as package:
            names = package.namelist()
            signature_present = any(_re_search(_SIGNATURE_PART_RE, name) for name in names)
            for name in names:
                if not _re_search(MACRO_ENTRY_RE, name):
                    continue
                package_projects += 1
                result["macro_present"] = True
                try:
                    info = package.getinfo(name)
                    if info.file_size > MAX_PROJECT_BYTES:
                        errors.append({"scope": name,
                                       "error": f"VBA project larger than {MAX_PROJECT_BYTES} bytes"})
                        continue
                    data = package.read(name)
                except Exception as exc:
                    errors.append({"scope": name, "error": str(exc)})
                    continue
                project = _project_from_bytes(data, container=name,
                                             signed=signature_present)
                projects.append(project.as_dict())
            if signature_present:
                result["signed"] = True
    except zipfile.BadZipFile:
        pass  # not a package: fall through to the OLE path
    except Exception as exc:
        errors.append({"scope": "package", "error": str(exc)})

    # --- Legacy OLE document: the file is the compound document -------------
    if package_projects == 0:
        try:
            import olefile  # type: ignore
        except Exception as exc:  # pragma: no cover - environment dependent
            errors.append({"scope": "ole", "error": f"olefile is not available: {exc}"})
            return result
        try:
            if olefile.isOleFile(path):
                with olefile.OleFileIO(path) as ole:
                    entries = ["/".join(entry) for entry in ole.listdir()]
                    lowered = [entry.lower() for entry in entries]
                    macro_storages = [entry for entry, low in zip(entries, lowered)
                                      if "vba_project_cur" in low or low.startswith("macros")
                                      or "vba" in low.split("/")[0]]
                    if macro_storages:
                        result["macro_present"] = True
                        signed = any("signature" in low for low in lowered)
                        result["signed"] = signed
                        metadata = {"streams": macro_storages[:256],
                                    "storage": macro_storages[0].split("/")[0] if macro_storages else ""}
                        modules, analysis, error = _extract_with_olevba(path)
                        project = {
                            "container": metadata["storage"] or "<legacy storage>",
                            "size": _file_size(path),
                            "md5": _file_digest(path, "md5"),
                            "sha256": _file_digest(path, "sha256"),
                            "signed": signed,
                            "module_count": len(modules),
                            "modules": modules,
                            "metadata": {**metadata, **analysis},
                            "extraction_error": error,
                            "truncated": bool(analysis.get("truncated")),
                            "note": ("Legacy OLE macro storage: the digests are of the "
                                     "whole document, which is the container of the project."),
                        }
                        projects.append(project)
        except Exception as exc:
            errors.append({"scope": "ole", "error": str(exc)})

    source_parts = []
    for project in projects:
        for module in project.get("modules", []) or []:  # type: ignore[union-attr]
            source = str(module.get("source") or "").strip()
            if source:
                source_parts.append(f"' --- {project.get('container')} :: {module.get('name')}\n{source}")
    result["source_text"] = "\n\n".join(source_parts)
    result["module_count"] = sum(len(p.get("modules") or []) for p in projects)  # type: ignore[arg-type]
    result["project_count"] = len(projects)
    return result


def macro_summary(macros: Dict[str, object]) -> Dict[str, object]:
    """Compact, report-friendly summary of an :func:`extract_vba` result.

    Keeps the identity of every project (container, sizes, digests) without the
    source text, for records that should not carry megabytes of code.
    """
    if not macros:
        return {}
    projects = macros.get("projects") or []
    return {
        "macro_present": bool(macros.get("macro_present")),
        "project_count": macros.get("project_count", len(projects)),  # type: ignore[arg-type]
        "module_count": macros.get("module_count", 0),
        "signed": bool(macros.get("signed")),
        "projects": [
            {
                "container": project.get("container"),
                "size": project.get("size"),
                "md5": project.get("md5"),
                "sha256": project.get("sha256"),
                "module_count": project.get("module_count"),
                "module_names": [m.get("name") for m in (project.get("modules") or [])],
                "signed": project.get("signed"),
                "extraction_error": project.get("extraction_error"),
                "autoexec": [a.get("keyword") for a in (project.get("metadata") or {}).get("autoexec", [])],
                "suspicious": [s.get("keyword") for s in (project.get("metadata") or {}).get("suspicious", [])],
            }
            for project in projects  # type: ignore[union-attr]
        ],
        "extraction_errors": macros.get("extraction_errors", []),
    }


# ---------------------------------------------------------------------------
def _re_search(pattern: str, value: str) -> bool:
    import re
    return re.search(pattern, value, re.IGNORECASE) is not None


def _file_digest(path: str, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def _file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0
