"""Catalog-backed translation management and runtime overrides.

The gettext catalogs are the reviewed defaults and define the strings that
exist in the product. Administrator edits are stored as sparse database
overrides, so they survive deploys without mutating PO/MO files. The same
sparse values are merged into Flask-Babel and the public client catalog, which
keeps server-rendered and JavaScript-rendered screens in sync.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from babel.messages.pofile import read_po

from settings.languages import SUPPORTED_LANGUAGES

logger = logging.getLogger(__name__)

UNASSIGNED_SCREEN = "__unassigned__"
_CACHE_TTL_SECONDS = 3.0
_PLACEHOLDER_RE = re.compile(
    r"%\([A-Za-z_][A-Za-z0-9_]*\)[#0 +\-]?[0-9]*(?:\.[0-9]+)?[diouxXeEfFgGcrsa]"
    r"|(?<!\{)\{[A-Za-z_][A-Za-z0-9_]*\}(?!\})"
)

_catalog_lock = threading.RLock()
_catalog_signature: Optional[Tuple[Tuple[str, int, int], ...]] = None
_catalog_snapshot: Optional[dict] = None
_override_lock = threading.RLock()
_override_cache: Dict[str, Tuple[float, Dict[str, dict]]] = {}


def supported_translation_languages() -> Dict[str, str]:
    """Languages admins can translate into (English is the source catalog)."""
    return {code: name for code, name in SUPPORTED_LANGUAGES.items() if code != "en"}


def validate_translation_locale(locale: str) -> bool:
    return locale in supported_translation_languages()


def _project_root() -> Path:
    """Find the repository/application root without depending on cwd."""
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "translations" / "messages.pot").is_file():
            return candidate
    raise FileNotFoundError("The gettext source catalog could not be located")


def _catalog_paths(root: Path) -> Dict[str, Path]:
    paths = {"__pot__": root / "translations" / "messages.pot"}
    for locale in SUPPORTED_LANGUAGES:
        paths[locale] = root / "translations" / locale / "LC_MESSAGES" / "messages.po"
        paths[f"__js_{locale}__"] = root / "static" / "js" / "i18n" / "locales" / f"{locale}.js"
    return paths


def _read_javascript_pack(path: Path, locale: str) -> Dict[str, str]:
    """Read the JSON-shaped dictionary registered by a static locale pack."""
    if not path.is_file():
        return {}
    source = path.read_text(encoding="utf-8")
    marker = f"window.I18N_UI_PACKS['{locale}']"
    assignment = source.find(marker)
    if assignment < 0:
        marker = f'window.I18N_UI_PACKS["{locale}"]'
        assignment = source.find(marker)
    if assignment < 0:
        return {}
    equals = source.find("=", assignment + len(marker))
    start = source.find("{", equals + 1)
    end = source.rfind("};")
    if equals < 0 or start < 0 or end < start:
        logger.warning("Could not parse the %s JavaScript locale pack", locale)
        return {}
    try:
        parsed = json.loads(source[start:end + 1])
    except (json.JSONDecodeError, TypeError):
        logger.warning("Could not parse the %s JavaScript locale pack", locale)
        return {}
    return {
        key: value for key, value in parsed.items()
        if isinstance(key, str) and key and isinstance(value, str)
    }


def _javascript_source_locations(root: Path, msgids: Iterable[str]) -> Dict[str, set]:
    """Locate static-pack keys in current JS modules for screen filtering."""
    candidates = [message for message in msgids if message]
    locations: Dict[str, set] = {message: set() for message in candidates}
    javascript_root = root / "static" / "js"
    if not javascript_root.is_dir() or not candidates:
        return locations

    needles = {}
    for msgid in candidates:
        single_quoted = "'" + msgid.replace("\\", "\\\\").replace("'", "\\'") \
            .replace("\n", "\\n").replace("\r", "\\r") + "'"
        backtick_quoted = "`" + msgid.replace("\\", "\\\\").replace("`", "\\`") \
            .replace("\n", "\\n").replace("\r", "\\r") + "`"
        needles[msgid] = (json.dumps(msgid, ensure_ascii=False), single_quoted, backtick_quoted)

    locale_pack_dir = (javascript_root / "i18n" / "locales").resolve()
    for path in javascript_root.rglob("*.js"):
        resolved = path.resolve()
        if resolved == locale_pack_dir or locale_pack_dir in resolved.parents:
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        matched = [
            msgid for msgid, variants in needles.items()
            if any(needle in source for needle in variants)
        ]
        if not matched:
            continue
        relative = path.relative_to(root).as_posix()
        for line_number, line in enumerate(source.splitlines(), start=1):
            for msgid in matched:
                if any(needle in line for needle in needles[msgid]):
                    locations[msgid].add((relative, line_number))
    return locations


def _normalize_source_path(raw_path: str) -> str:
    path = str(raw_path or "").replace("\\", "/").strip().rstrip(",")
    while path.startswith("./"):
        path = path[2:]
    if path.lower().startswith((
        "templates/", "static/", "api/", "core/", "settings/", "database/", "auth/",
    )):
        return path

    # Older catalogs were extracted on Windows and contain an absolute path.
    # Keep the repository-relative suffix rather than exposing that machine's
    # username and directory structure in the management UI.
    lowered = path.lower()
    for marker in ("/templates/", "/static/", "/api/", "/core/", "/settings/",
                   "/database/", "/auth/"):
        index = lowered.rfind(marker)
        if index >= 0:
            path = path[index + 1:]
            break
    return path.lstrip("/")


def _screen_label(path: str) -> str:
    """Turn a source reference into a readable, stable screen-filter label."""
    parts = path.split("/")
    filename = parts[-1]
    stem = filename.rsplit(".", 1)[0].replace("_", " ").replace("-", " ").strip().title()

    if parts[0] == "templates":
        if len(parts) == 2 and stem.lower() == "base":
            return "Shared shell"
        if len(parts) >= 3 and parts[1] == "components":
            return f"Shared components / {stem}"
        if len(parts) >= 3:
            group = parts[1].replace("_", " ").replace("-", " ").title()
            return f"{group} / {stem}"
        return stem or path

    if parts[0] == "static":
        if len(parts) >= 4 and parts[1:3] == ["js", "pages"]:
            return f"Frontend screen / {stem}"
        return f"Frontend / {stem}"

    if parts[0] == "Api":
        return f"Server messages / {stem}"
    return f"Shared messages / {stem}"


def _message_key(msgid: str) -> str:
    return hashlib.sha256(msgid.encode("utf-8")).hexdigest()


def _read_catalog_file(path: Path, locale: Optional[str] = None) -> Iterable:
    if not path.is_file():
        return ()
    with path.open("rb") as catalog_file:
        catalog = read_po(catalog_file, locale=locale)
    return tuple(catalog)


def _load_catalog_snapshot() -> dict:
    """Read source references and locale defaults, cached by catalog mtimes."""
    root = _project_root()
    paths = _catalog_paths(root)
    signature_items = []
    for name, path in sorted(paths.items()):
        try:
            stat = path.stat()
            signature_items.append((name, stat.st_mtime_ns, stat.st_size))
        except FileNotFoundError:
            signature_items.append((name, -1, -1))
    signature = tuple(signature_items)

    global _catalog_signature, _catalog_snapshot
    with _catalog_lock:
        if _catalog_snapshot is not None and _catalog_signature == signature:
            return _catalog_snapshot

        messages: Dict[str, dict] = {}
        translations: Dict[str, Dict[str, str]] = {
            locale: {} for locale in SUPPORTED_LANGUAGES
        }

        def record_for(msgid: str) -> dict:
            return messages.setdefault(msgid, {
                "msgid": msgid,
                "key": _message_key(msgid),
                "pot_locations": set(),
                "po_locations": set(),
                "js_locations": set(),
            })

        def consume(message, catalog_name: str) -> None:
            msgid = message.id
            # The current catalogs contain only singular, context-free UI
            # strings. Keep this contract explicit rather than silently
            # misrepresenting plural/context entries as editable strings.
            if not isinstance(msgid, str) or not msgid or message.context:
                return
            record = record_for(msgid)
            target = record["pot_locations"] if catalog_name == "__pot__" else record["po_locations"]
            for raw_path, line in (message.locations or ()):
                normalized = _normalize_source_path(raw_path)
                if normalized:
                    try:
                        line_number = int(line or 0)
                    except (TypeError, ValueError):
                        line_number = 0
                    target.add((normalized, line_number))
            # PO strings take precedence over the static JavaScript pack, just
            # as the browser runtime does. Empty PO entries leave a pack value
            # available instead of hiding a valid client-side translation.
            if (catalog_name in translations and isinstance(message.string, str)
                    and message.string):
                translations[catalog_name][msgid] = message.string

        # The POT defines server-rendered text and its current references.
        for message in _read_catalog_file(paths["__pot__"]):
            consume(message, "__pot__")

        # The static UI packs are the source of truth for JavaScript-only copy.
        # Import their defaults before PO catalogs so an explicit PO entry wins.
        javascript_pack_ids = set()
        for locale in SUPPORTED_LANGUAGES:
            pack = _read_javascript_pack(paths[f"__js_{locale}__"], locale)
            for msgid, translation in pack.items():
                record_for(msgid)
                javascript_pack_ids.add(msgid)
                if locale != "en" and translation:
                    translations[locale][msgid] = translation

        # PO catalogs contribute PO-only entries and keep older client strings
        # discoverable. Their POT references are refreshed above when present.
        for locale in SUPPORTED_LANGUAGES:
            for message in _read_catalog_file(paths[locale], locale=locale):
                consume(message, locale)

        # Give static-pack strings a real frontend screen filter by finding
        # their use in the current JavaScript modules (not just the locale
        # pack that defines their translation).
        javascript_locations = _javascript_source_locations(root, javascript_pack_ids)
        for msgid, locations in javascript_locations.items():
            if msgid in messages:
                messages[msgid]["js_locations"].update(locations)

        entries = []
        for msgid, record in messages.items():
            locations = record["pot_locations"] | record["js_locations"]
            if not record["pot_locations"]:
                locations |= record["po_locations"]
            ordered_locations = sorted(locations, key=lambda item: (item[0].casefold(), item[1]))
            source_locations = [
                {
                    "path": path,
                    "line": line,
                    "screen_id": path,
                    "screen_label": _screen_label(path),
                }
                for path, line in ordered_locations
            ]
            entries.append({
                "id": record["key"],
                "msgid": msgid,
                "locations": source_locations,
                "screen_ids": sorted({path for path, _line in ordered_locations}),
            })

        entries.sort(key=lambda row: (row["msgid"].casefold(), row["msgid"]))
        snapshot = {
            "entries": entries,
            "translations": translations,
            "by_id": {row["id"]: row for row in entries},
        }
        _catalog_signature = signature
        _catalog_snapshot = snapshot
        return snapshot


def get_source_catalog_entries() -> List[dict]:
    """Catalog entries for validation and manager reads, without DB overrides."""
    return list(_load_catalog_snapshot()["entries"])


def source_entry_for_id(entry_id: str) -> Optional[dict]:
    return _load_catalog_snapshot()["by_id"].get(entry_id)


def _query_overrides(locale: str) -> Dict[str, dict]:
    from Api.utils import execute_query

    rows = execute_query(
        """
        SELECT message_key, msgid, translation
          FROM translation_overrides
         WHERE locale = %s
        """,
        (locale,),
        fetch="all",
        use_cache=False,
    )
    return {
        str(key).strip(): {"msgid": msgid, "translation": translation}
        for key, msgid, translation in rows
    }


def get_translation_overrides(locale: str, use_cache: bool = True) -> Dict[str, dict]:
    """Return the sparse database overrides, with a short process-local cache."""
    now = time.monotonic()
    if use_cache:
        with _override_lock:
            cached = _override_cache.get(locale)
            if cached and cached[0] > now:
                return {key: value.copy() for key, value in cached[1].items()}

    overrides = _query_overrides(locale)
    if use_cache:
        with _override_lock:
            _override_cache[locale] = (now + _CACHE_TTL_SECONDS, overrides)
    return {key: value.copy() for key, value in overrides.items()}


def invalidate_translation_override_cache(locale: Optional[str] = None) -> None:
    with _override_lock:
        if locale is None:
            _override_cache.clear()
        else:
            _override_cache.pop(locale, None)


#: The unmodified catalog of each locale, ``{locale: dict}``.
#:
#: The object ``flask_babel.get_translations()`` returns is cached for the
#: process, keyed by locale (``Domain.cache[str(locale), domain]``), and shared
#: by every request in that locale. Writing saved edits into it made an override
#: permanent: deleting the row (the editor's "reset") left the text on every
#: page until the service restarted. The overlay therefore merges into a copy of
#: the catalog as it was before any overlay, remembered here.
#:
#: Keyed by locale, not by ``id()``: an id is only unique among live objects, so
#: the locale is what identifies which catalog a copy belongs to - and it is the
#: same key Babel's own cache uses, so the two cannot disagree. The number of
#: supported locales is fixed (``settings.languages``), so the dict is bounded by
#: construction; anything outside that set is not an overlay target.
_PRISTINE_CATALOGS: Dict[str, Dict[Any, Any]] = {}
_PRISTINE_LOCK = threading.Lock()


def _pristine_catalog(locale: str, catalog: dict) -> Dict[Any, Any]:
    """The catalog as it was before any overlay, for ``locale``."""
    with _PRISTINE_LOCK:
        known = _PRISTINE_CATALOGS.get(locale)
        if known is None:
            known = dict(catalog)
            _PRISTINE_CATALOGS[locale] = known
        return known


def apply_runtime_translation_overrides(locale: str) -> None:
    """Overlay the selected locale's saved edits into this request's Babel catalog.

    The overlay is applied to a **copy** and assigned back, so an override that
    is deleted stops rendering on the next request instead of at the next
    restart, and a locale without overrides renders exactly the checked-in
    catalog. ``_PRISTINE_CATALOGS`` is keyed by locale; the copy is per request,
    so two requests in the same locale never share a half-applied catalog.
    """
    from flask_babel import get_translations

    translations = get_translations()
    catalog = getattr(translations, "_catalog", None)
    if not isinstance(catalog, dict):
        return
    merged = dict(_pristine_catalog(str(locale), catalog))
    for item in get_translation_overrides(locale).values():
        # The key is the source msgid, exactly what gettext() looks up.
        merged[item["msgid"]] = item["translation"]
    translations._catalog = merged


def _entry_translation(entry: dict, locale: str, locale_defaults: dict,
                       overrides: dict) -> dict:
    msgid = entry["msgid"]
    base_translation = msgid if locale == "en" else locale_defaults.get(msgid, "")
    override = overrides.get(entry["id"])
    translation = override["translation"] if override else base_translation
    return {
        **entry,
        "source": msgid,
        "base_translation": base_translation,
        "translation": translation,
        "overridden": bool(override),
        "status": "translated" if translation else "missing",
    }


def translation_management_catalog(
    locale: str,
    query: str = "",
    screen: str = "",
    status: str = "all",
    page: int = 1,
    per_page: int = 50,
) -> dict:
    """Filtered, paginated translation data for the administrator interface."""
    snapshot = _load_catalog_snapshot()
    locale_defaults = snapshot["translations"].get(locale, {})
    # Management reads are uncached so an admin sees a change made by any
    # worker immediately; end-user request-time overlays keep the short cache.
    overrides = get_translation_overrides(locale, use_cache=False)
    all_rows = [
        _entry_translation(entry, locale, locale_defaults, overrides)
        for entry in snapshot["entries"]
    ]

    screen_counts: Dict[str, int] = {}
    screen_labels: Dict[str, str] = {}
    for entry in all_rows:
        if entry["screen_ids"]:
            label_by_screen = {
                location["screen_id"]: location["screen_label"]
                for location in entry["locations"]
            }
            for screen_id in entry["screen_ids"]:
                screen_counts[screen_id] = screen_counts.get(screen_id, 0) + 1
                screen_labels.setdefault(screen_id, label_by_screen[screen_id])
        else:
            screen_counts[UNASSIGNED_SCREEN] = screen_counts.get(UNASSIGNED_SCREEN, 0) + 1
            screen_labels[UNASSIGNED_SCREEN] = "Unassigned source"

    stats = {
        "total": len(all_rows),
        "translated": sum(1 for row in all_rows if row["status"] == "translated"),
        "missing": sum(1 for row in all_rows if row["status"] == "missing"),
        "overridden": sum(1 for row in all_rows if row["overridden"]),
    }

    filtered = all_rows
    if screen:
        if screen == UNASSIGNED_SCREEN:
            filtered = [row for row in filtered if not row["screen_ids"]]
        else:
            filtered = [row for row in filtered if screen in row["screen_ids"]]
    if status == "translated":
        filtered = [row for row in filtered if row["status"] == "translated"]
    elif status == "missing":
        filtered = [row for row in filtered if row["status"] == "missing"]
    elif status == "overridden":
        filtered = [row for row in filtered if row["overridden"]]

    normalized_query = (query or "").casefold().strip()
    if normalized_query:
        filtered = [
            row for row in filtered
            if normalized_query in row["source"].casefold()
            or normalized_query in row["translation"].casefold()
            or any(normalized_query in loc["path"].casefold() for loc in row["locations"])
        ]

    total = len(filtered)
    page_count = max(1, math.ceil(total / per_page))
    page = min(max(1, page), page_count)
    start = (page - 1) * per_page
    selected_rows = filtered[start:start + per_page]
    screens = [
        {"id": key, "label": screen_labels[key], "count": screen_counts[key]}
        for key in sorted(screen_counts, key=lambda item: screen_labels[item].casefold())
    ]
    return {
        "locale": locale,
        "items": selected_rows,
        "screens": screens,
        "stats": stats,
        "query": query,
        "screen": screen,
        "status": status,
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": page_count,
    }


def placeholder_signature(text: str) -> List[str]:
    """Named gettext/JS placeholders which a translator must preserve."""
    return sorted(_PLACEHOLDER_RE.findall(text or ""))


def save_translation_changes(locale: str, changes: List[dict], updated_by: Optional[int]) -> int:
    """Atomically save or reset a validated batch of locale overrides."""
    from Api.utils import get_connection, return_connection

    connection = get_connection()
    cursor = None
    try:
        cursor = connection.cursor()
        for change in changes:
            if change["reset"]:
                cursor.execute(
                    """
                    DELETE FROM translation_overrides
                     WHERE locale = %s AND message_key = %s AND msgid = %s
                    """,
                    (locale, change["id"], change["msgid"]),
                )
                continue

            cursor.execute(
                """
                INSERT INTO translation_overrides
                    (locale, message_key, msgid, translation, updated_by, updated_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT (locale, message_key) DO UPDATE SET
                    msgid = EXCLUDED.msgid,
                    translation = EXCLUDED.translation,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = NOW()
                WHERE translation_overrides.msgid = EXCLUDED.msgid
                """,
                (locale, change["id"], change["msgid"], change["translation"], updated_by),
            )
            if cursor.rowcount != 1:
                raise ValueError("A translation key could not be updated safely")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        if cursor is not None:
            cursor.close()
        return_connection(connection)

    invalidate_translation_override_cache(locale)
    return len(changes)
