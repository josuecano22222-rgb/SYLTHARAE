"""
Ripgrep integration for fast line-number discovery in stored text.

PostgreSQL still selects candidate files; ripgrep accelerates scanning extracted
text when building line-level context for Advanced Search results.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from core.search.session import SearchSession

logger = logging.getLogger(__name__)

_CACHED_BINARY: Optional[str] = None
_CACHED_VERSION: Optional[str] = None


def _normalize_binary(path: Optional[str]) -> Optional[str]:
    if path:
        candidate = Path(path).expanduser()
        if candidate.is_file():
            return str(candidate)
    found = shutil.which("rg")
    if found:
        return found
    for name in ("rg.exe", "rg"):
        found = shutil.which(name)
        if found:
            return found
    return None


def resolve_ripgrep_binary(configured_path: Optional[str] = None) -> Optional[str]:
    """Locate ripgrep on PATH or from explicit settings."""
    global _CACHED_BINARY
    if configured_path:
        resolved = _normalize_binary(configured_path)
        if resolved:
            _CACHED_BINARY = resolved
            return resolved
    if _CACHED_BINARY and Path(_CACHED_BINARY).is_file():
        return _CACHED_BINARY
    _CACHED_BINARY = _normalize_binary(None)
    return _CACHED_BINARY


def ripgrep_version(binary: Optional[str] = None) -> Optional[str]:
    global _CACHED_VERSION
    exe = binary or resolve_ripgrep_binary()
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    first = (proc.stdout or proc.stderr or "").strip().splitlines()
    version = first[0] if first else None
    _CACHED_VERSION = version
    return version


def build_ripgrep_pattern(
    terms: Sequence[str],
    *,
    whole_word: bool = False,
) -> str:
    """Build a single regex alternation for positive query terms."""
    parts: List[str] = []
    for term in terms:
        term = str(term or "").strip()
        if not term:
            continue
        escaped = re.escape(term)
        if " " in term:
            escaped = r"\s+".join(re.escape(p) for p in term.split())
        if whole_word:
            escaped = rf"(?:^|[^\w]){escaped}(?:[^\w]|$)"
        parts.append(escaped)
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return "|".join(f"(?:{part})" for part in parts)


@dataclass(frozen=True)
class RipgrepConfig:
    enabled: bool = True
    binary_path: Optional[str] = None
    config_path: Optional[str] = None
    min_content_bytes: int = 8192
    max_matches_per_file: int = 10
    context_lines: int = 0
    use_pcre2: bool = False


@dataclass
class RipgrepMatch:
    line_number: int
    line_text: str
    matched_text: str = ""

    def to_line_match_entry(
        self,
        *,
        context_before: Optional[str] = None,
        context_after: Optional[str] = None,
        highlighted_line: Optional[str] = None,
    ) -> Dict[str, Any]:
        return {
            "line_number": self.line_number,
            "line_text": self.line_text,
            "highlighted_line": highlighted_line or self.line_text,
            "context_before": context_before,
            "context_after": context_after,
        }


class RipgrepScanner:
    """Run ripgrep against in-memory corpus files for line numbers."""

    def __init__(self, config: RipgrepConfig):
        self.config = config
        self._binary = resolve_ripgrep_binary(config.binary_path)

    @property
    def available(self) -> bool:
        return bool(self.config.enabled and self._binary and Path(self._binary).is_file())

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": self.config.enabled,
            "available": self.available,
            "binary": self._binary if self.available else None,
            "version": ripgrep_version(self._binary) if self.available else None,
            "min_content_bytes": self.config.min_content_bytes,
        }

    def should_scan(self, content: str) -> bool:
        if not self.available:
            return False
        encoded = content.encode("utf-8", errors="surrogatepass")
        return len(encoded) >= max(0, self.config.min_content_bytes)

    def find_line_numbers(
        self,
        content: str,
        pattern: str,
        *,
        case_sensitive: bool = False,
        whole_word: bool = False,
        max_matches: Optional[int] = None,
    ) -> List[int]:
        hits = self.scan_batch(
            {0: content},
            pattern,
            case_sensitive=case_sensitive,
            whole_word=whole_word,
            max_matches_per_file=max_matches,
        )
        return hits.get(0, [])

    def scan_batch_detailed(
        self,
        documents: Mapping[int, str],
        pattern: str,
        *,
        case_sensitive: bool = False,
        whole_word: bool = False,
        max_matches_per_file: Optional[int] = None,
        session: Optional[SearchSession] = None,
    ) -> Dict[int, List[RipgrepMatch]]:
        """Structured matches — avoids re-scanning document text in Python."""
        parsed = self._run_batch_json(
            documents,
            pattern,
            case_sensitive=case_sensitive,
            whole_word=whole_word,
            max_matches_per_file=max_matches_per_file,
            session=session,
        )
        out: Dict[int, List[RipgrepMatch]] = {}
        for pid, entries in parsed.items():
            out[pid] = [
                RipgrepMatch(
                    line_number=e["line_number"],
                    line_text=e["line_text"],
                    matched_text=e.get("matched_text") or "",
                )
                for e in entries
            ]
        return out

    def scan_batch(
        self,
        documents: Mapping[int, str],
        pattern: str,
        *,
        case_sensitive: bool = False,
        whole_word: bool = False,
        max_matches_per_file: Optional[int] = None,
        session: Optional[SearchSession] = None,
    ) -> Dict[int, List[int]]:
        """Return {path_id: [line_number, ...]} using one ripgrep invocation."""
        detailed = self.scan_batch_detailed(
            documents,
            pattern,
            case_sensitive=case_sensitive,
            whole_word=whole_word,
            max_matches_per_file=max_matches_per_file,
            session=session,
        )
        return {pid: [m.line_number for m in matches] for pid, matches in detailed.items()}

    def _run_batch_json(
        self,
        documents: Mapping[int, str],
        pattern: str,
        *,
        case_sensitive: bool = False,
        whole_word: bool = False,
        max_matches_per_file: Optional[int] = None,
        session: Optional[SearchSession] = None,
    ) -> Dict[int, List[Dict[str, Any]]]:
        """Return {path_id: [match dicts...]} using one ripgrep invocation."""
        if not self.available or not pattern or not pattern.strip():
            return {}
        eligible = {
            pid: text
            for pid, text in documents.items()
            if text and self.should_scan(text)
        }
        if not eligible:
            return {}

        limit = max_matches_per_file or self.config.max_matches_per_file
        env = os.environ.copy()
        if self.config.config_path:
            env["RIPGREP_CONFIG_PATH"] = self.config.config_path

        with tempfile.TemporaryDirectory(prefix="syltharae-rg-") as tmp:
            root = Path(tmp)
            for pid, text in eligible.items():
                path = root / f"{pid}.txt"
                path.write_text(text, encoding="utf-8", errors="surrogatepass")

            args = [
                self._binary,
                "--json",
                "--line-number",
                "--no-heading",
                "--sort",
                "path",
                "--max-count",
                str(max(1, limit)),
            ]
            if not case_sensitive:
                args.append("-i")
            else:
                args.append("-s")
            if whole_word:
                args.append("-w")
            if self.config.context_lines:
                args.extend(["-C", str(self.config.context_lines)])
            if self.config.use_pcre2:
                args.append("-P")
            args.extend(["-e", pattern, str(root)])

            stdout = ""
            try:
                if session is not None:
                    proc = subprocess.Popen(
                        args,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        env=env,
                    )
                    session.register_rg(proc)
                    stdout, stderr = proc.communicate(timeout=120)
                    if session.is_cancelled:
                        return {}
                else:
                    completed = subprocess.run(
                        args,
                        capture_output=True,
                        text=True,
                        timeout=120,
                        env=env,
                        check=False,
                    )
                    stdout = completed.stdout or ""
                    if completed.returncode not in (0, 1):
                        logger.debug(
                            "ripgrep exited %s: %s",
                            completed.returncode,
                            (completed.stderr or stdout)[:500],
                        )
                        return {}
            except (OSError, subprocess.TimeoutExpired) as exc:
                logger.warning("ripgrep batch scan failed: %s", exc)
                return {}

            results: Dict[int, List[Dict[str, Any]]] = {pid: [] for pid in eligible}
            for line in stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if payload.get("type") != "match":
                    continue
                data = payload.get("data") or {}
                path_text = ((data.get("path") or {}).get("text")) or ""
                stem = Path(path_text).stem
                if not stem.isdigit():
                    continue
                pid = int(stem)
                line_number = int(data.get("line_number") or 0)
                if pid not in results or line_number <= 0:
                    continue
                lines_obj = data.get("lines") or {}
                line_text = str(lines_obj.get("text") or "").rstrip("\n")
                submatches = data.get("submatches") or []
                matched = ""
                if submatches and isinstance(submatches[0], dict):
                    match_obj = submatches[0].get("match") or {}
                    matched = str(match_obj.get("text") or "")
                bucket = results[pid]
                if len(bucket) >= limit:
                    continue
                bucket.append(
                    {
                        "line_number": line_number,
                        "line_text": line_text,
                        "matched_text": matched,
                    }
                )

        for pid in list(results.keys()):
            results[pid].sort(key=lambda item: item["line_number"])
        return results


def _resolve_config_path(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    path = Path(raw).expanduser()
    if path.is_file():
        return str(path.resolve())
    root = os.getenv("PROJECT_ROOT")
    if root:
        joined = Path(root) / raw
        if joined.is_file():
            return str(joined.resolve())
    return str(path) if path.is_file() else raw


def get_scanner_from_settings(settings: Optional[Any] = None) -> RipgrepScanner:
    if settings is None:
        from settings import get_settings

        settings = get_settings()
    cfg = settings.get_search_config() if hasattr(settings, "get_search_config") else {}
    if not isinstance(cfg, dict):
        cfg = {}
    config_path = _resolve_config_path(cfg.get("ripgrep_config_path") or None)
    return RipgrepScanner(
        RipgrepConfig(
            enabled=bool(cfg.get("ripgrep_enabled", True)),
            binary_path=cfg.get("ripgrep_path") or None,
            config_path=config_path,
            min_content_bytes=int(cfg.get("ripgrep_min_content_bytes", 8192)),
            max_matches_per_file=int(cfg.get("ripgrep_max_matches_per_file", 10)),
            context_lines=int(cfg.get("ripgrep_context_lines", 0)),
            use_pcre2=bool(cfg.get("ripgrep_use_pcre2", False)),
        )
    )


def positive_terms_from_patterns(
    patterns: Iterable[Tuple[str, re.Pattern]],
) -> List[str]:
    return [value for value, _pattern in patterns if value]
