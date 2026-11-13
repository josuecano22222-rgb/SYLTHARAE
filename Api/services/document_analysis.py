"""Shared document-analysis model for viewer, selection, and export.

Builds one structured payload per file:

* **categories** — smart taxonomy names (from classification)
* **category_terms** — single words linked into those categories
* **keywords** — multi-word phrases (≥2 words) with optional category

Reuses ``select_classification`` / ``compute_percentage`` and keyword
frequency helpers already used by the file chart-data endpoint.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Sequence

from Api.utils import (
    compute_percentage,
    execute_query,
    select_classification,
)
from core.serialization import unpack_int_list

logger = logging.getLogger(__name__)

DEFAULT_TERM_LIMIT = 80
DEFAULT_KEYWORD_LIMIT = 50
BATCH_FILE_CAP = 100
AGGREGATE_TERM_CAP = 120
AGGREGATE_KEYWORD_CAP = 80


def _decode_keyword_phrase(keyword_bytes) -> str:
    """Turn a packed keyword BYTEA into a space-joined phrase."""
    if not keyword_bytes:
        return ''
    try:
        word_ids = unpack_int_list(keyword_bytes) or []
        if not word_ids:
            return ''
        placeholders = ','.join(['%s'] * len(word_ids))
        words_query = f"""
            SELECT word
            FROM words
            WHERE id IN ({placeholders})
            ORDER BY ARRAY_POSITION(ARRAY[{placeholders}]::INTEGER[], id)
        """
        rows = execute_query(words_query, word_ids + word_ids, fetch='all')
        if not rows:
            return ''
        return ' '.join(str(r[0]) for r in rows if r and r[0])
    except Exception as exc:
        logger.warning('Could not decode keyword bytes: %s', exc)
        return ''


def _keyword_rows_with_category(file_id: int, limit: int = DEFAULT_KEYWORD_LIMIT) -> List[Dict[str, Any]]:
    """Keywords for a file with optional parent category name."""
    try:
        query = """
            SELECT k.keyword, kp.word_count, COALESCE(w_cat.word, '') AS category_name
            FROM keywords_hashs kp
            JOIN keywords k ON k.id = kp.keyword_id
            LEFT JOIN categorys c ON c.id = k.category_id
            LEFT JOIN words w_cat ON w_cat.id = c.word_id
            WHERE kp.hash_id = (
                SELECT c2.hash_id
                FROM paths p2
                JOIN hash_contexts c2 ON c2.id = p2.context_id
                WHERE p2.id = %s
            )
            ORDER BY kp.word_count DESC
            LIMIT %s
        """
        rows = execute_query(query, (file_id, limit), fetch='all') or []
        out: List[Dict[str, Any]] = []
        for keyword_bytes, count, category_name in rows:
            phrase = _decode_keyword_phrase(keyword_bytes)
            if not phrase or len(phrase.split()) < 2:
                continue
            out.append({
                'phrase': phrase,
                'category': category_name or None,
                'count': int(count or 0),
            })
        return out
    except Exception as exc:
        logger.error('Keyword analysis failed for file %s: %s', file_id, exc)
        return []


def _file_metadata(file_id: int) -> Dict[str, Any]:
    row = execute_query(
        """
        SELECT
            p.id,
            p.file_name,
            p.file_type,
            p.file_size,
            p.file_status,
            p.file_date,
            p.file_path,
            COALESCE(s.name, '') AS source_name,
            COALESCE(si.name, '') AS side_name
        FROM paths p
        LEFT JOIN hash_contexts hc ON hc.id = p.context_id
        LEFT JOIN sources s ON s.id = hc.source_id
        LEFT JOIN sides si ON si.id = hc.side_id
        WHERE p.id = %s
        """,
        (file_id,),
        fetch='one',
    )
    if not row:
        return {}
    return {
        'id': row[0],
        'name': row[1] or f'file_{file_id}',
        'type': row[2] or '',
        'size': row[3],
        'status': row[4] or '',
        'date': str(row[5]) if row[5] is not None else '',
        'path': row[6] or '',
        'source': row[7] or '',
        'side': row[8] or '',
    }


def build_document_analysis(
    file_id: int,
    *,
    term_limit: int = DEFAULT_TERM_LIMIT,
    keyword_limit: int = DEFAULT_KEYWORD_LIMIT,
) -> Optional[Dict[str, Any]]:
    """Return the DocumentAnalysis payload for one file, or None if missing."""
    meta = _file_metadata(file_id)
    if not meta:
        return None

    classification_rows = select_classification(file_id) or []
    percentages = compute_percentage(classification_rows) or {}

    categories = []
    for name, data in percentages.items():
        categories.append({
            'name': name,
            'count': int(data.get('count') or 0),
            'percentage': round(float(data.get('percentage') or 0), 1),
        })
    categories.sort(key=lambda c: c['count'], reverse=True)

    # Category terms: taxonomy-linked single words (not the full token list).
    term_counts: Dict[tuple, int] = defaultdict(int)
    for category_name, word, count in classification_rows:
        if not word:
            continue
        key = (str(word).strip(), str(category_name or '').strip())
        if not key[0]:
            continue
        term_counts[key] += int(count or 0)

    category_terms = [
        {'term': term, 'category': category or None, 'count': count}
        for (term, category), count in term_counts.items()
    ]
    category_terms.sort(key=lambda t: (-t['count'], t['term'].lower()))
    if term_limit and len(category_terms) > term_limit:
        category_terms = category_terms[:term_limit]

    keywords = _keyword_rows_with_category(file_id, limit=keyword_limit)

    return {
        'file_id': file_id,
        'file_name': meta.get('name') or f'file_{file_id}',
        'categories': categories,
        'category_terms': category_terms,
        'keywords': keywords,
        'metadata': {
            'type': meta.get('type') or '',
            'size': meta.get('size'),
            'source': meta.get('source') or '',
            'side': meta.get('side') or '',
            'date': meta.get('date') or '',
            'path': meta.get('path') or '',
            'status': meta.get('status') or '',
        },
    }


def _merge_named_counts(
    items: Iterable[Dict[str, Any]],
    *,
    name_key: str,
    extra_keys: Sequence[str] = (),
) -> List[Dict[str, Any]]:
    """Union items by name (+ optional extras), summing counts."""
    bucket: Dict[tuple, Dict[str, Any]] = {}
    for item in items:
        name = str(item.get(name_key) or '').strip()
        if not name:
            continue
        extras = tuple(str(item.get(k) or '') for k in extra_keys)
        key = (name.lower(),) + extras
        if key not in bucket:
            entry = {name_key: name, 'count': 0}
            for k, v in zip(extra_keys, extras):
                entry[k] = v or None
            bucket[key] = entry
        bucket[key]['count'] += int(item.get('count') or 0)
    out = list(bucket.values())
    out.sort(key=lambda row: (-row['count'], str(row.get(name_key) or '').lower()))
    return out


def build_selection_analysis(
    file_ids: Sequence[int],
    *,
    max_files: int = BATCH_FILE_CAP,
) -> Dict[str, Any]:
    """Per-file analyses plus an aggregate union across the selection."""
    ids = []
    seen = set()
    for raw in file_ids:
        try:
            fid = int(raw)
        except (TypeError, ValueError):
            continue
        if fid <= 0 or fid in seen:
            continue
        seen.add(fid)
        ids.append(fid)
        if len(ids) >= max_files:
            break

    files: List[Dict[str, Any]] = []
    all_categories: List[Dict[str, Any]] = []
    all_terms: List[Dict[str, Any]] = []
    all_keywords: List[Dict[str, Any]] = []

    for fid in ids:
        analysis = build_document_analysis(fid)
        if not analysis:
            continue
        files.append(analysis)
        all_categories.extend(analysis.get('categories') or [])
        all_terms.extend(analysis.get('category_terms') or [])
        all_keywords.extend(analysis.get('keywords') or [])

    aggregate_categories = _merge_named_counts(all_categories, name_key='name')
    # Drop percentage on aggregates (not meaningful across files).
    for row in aggregate_categories:
        row.pop('percentage', None)

    aggregate_terms = _merge_named_counts(
        all_terms, name_key='term', extra_keys=('category',)
    )[:AGGREGATE_TERM_CAP]
    aggregate_keywords = _merge_named_counts(
        all_keywords, name_key='phrase', extra_keys=('category',)
    )[:AGGREGATE_KEYWORD_CAP]

    return {
        'success': True,
        'files': files,
        'aggregate': {
            'categories': aggregate_categories,
            'category_terms': aggregate_terms,
            'keywords': aggregate_keywords,
            'file_count': len(files),
        },
    }


def analysis_column_values(file_id: int) -> Dict[str, str]:
    """Joined strings for details CSV/XLSX enrichment columns."""
    analysis = build_document_analysis(file_id, term_limit=40, keyword_limit=40)
    if not analysis:
        return {'category': '', 'category_terms': '', 'keywords': ''}
    cats = [c['name'] for c in analysis.get('categories') or [] if c.get('name')]
    terms = [t['term'] for t in analysis.get('category_terms') or [] if t.get('term')]
    phrases = [k['phrase'] for k in analysis.get('keywords') or [] if k.get('phrase')]
    # De-dupe preserving order
    def uniq(values: List[str]) -> List[str]:
        out, seen = [], set()
        for v in values:
            key = v.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(v)
        return out

    return {
        'category': '; '.join(uniq(cats)),
        'category_terms': '; '.join(uniq(terms)),
        'keywords': '; '.join(uniq(phrases)),
    }
