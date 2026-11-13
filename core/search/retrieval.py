"""Bounded text retrieval levels (never load full documents for list/search)."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Iterable, List, Optional, Tuple

from Api.utils import execute_query
from core.search.governor import snippet_slot_semaphore

logger = logging.getLogger(__name__)

RETRIEVAL_METADATA = 0
RETRIEVAL_PREVIEW = 1
RETRIEVAL_CONTEXT = 2
RETRIEVAL_FULL = 3


def resolve_hash_ids(path_ids: Iterable[int]) -> Dict[int, int]:
    ids = [int(i) for i in path_ids if i is not None]
    if not ids:
        return {}
    rows = execute_query(
        """
        SELECT p.id, hc.hash_id
        FROM paths p
        JOIN hash_contexts hc ON hc.id = p.context_id
        WHERE p.id = ANY(%s)
        """,
        (ids,),
        fetch="all",
    ) or []
    return {int(row[0]): int(row[1]) for row in rows if row[1] is not None}


def load_raw_text_window(hash_id: int, max_bytes: int) -> Tuple[str, int]:
    """Level 1/2: load only the first ``max_bytes`` of stored raw text."""
    if max_bytes <= 0:
        return "", 0
    rows = execute_query(
        """
        SELECT content, char_count
        FROM contents_raw
        WHERE hash_id = %s
        ORDER BY chunk_seq ASC
        """,
        (hash_id,),
        fetch="all",
    ) or []
    parts: List[str] = []
    total = 0
    for row in rows:
        chunk = row[0] if row else ""
        if not chunk:
            continue
        encoded = str(chunk).encode("utf-8", errors="surrogatepass")
        if total + len(encoded) > max_bytes:
            remain = max_bytes - total
            if remain > 0:
                parts.append(encoded[:remain].decode("utf-8", errors="replace"))
                total = max_bytes
            break
        parts.append(str(chunk))
        total += len(encoded)
    text = "".join(parts)
    return text, total


def batch_load_text_windows(
    path_ids: Iterable[int],
    *,
    max_bytes_per_file: int,
    max_workers: int = 4,
) -> Dict[int, str]:
    """Parallel bounded loads keyed by path_id."""
    hash_by_path = resolve_hash_ids(path_ids)
    if not hash_by_path:
        return {}

    result: Dict[int, str] = {}
    workers = max(1, min(max_workers, len(hash_by_path)))

    def _load_one(path_id: int, hash_id: int) -> Tuple[int, str]:
        sem = snippet_slot_semaphore()
        sem.acquire()
        try:
            text, _ = load_raw_text_window(hash_id, max_bytes_per_file)
            return path_id, text
        finally:
            sem.release()

    if workers == 1:
        for path_id, hash_id in hash_by_path.items():
            pid, text = _load_one(path_id, hash_id)
            result[pid] = text
        return result

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_load_one, path_id, hash_id): path_id
            for path_id, hash_id in hash_by_path.items()
        }
        for future in as_completed(futures):
            try:
                path_id, text = future.result()
                result[path_id] = text
            except Exception as exc:
                path_id = futures[future]
                logger.warning("Bounded text load failed for path_id=%s: %s", path_id, exc)
                result[path_id] = ""
    return result
