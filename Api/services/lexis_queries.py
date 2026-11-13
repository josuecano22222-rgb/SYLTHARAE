"""The LEXIS Cosmos query catalogue.

Every statement the LEXIS Cosmos interface (``/lexis``) runs is declared here,
once, as a named constant, and nothing else in the application writes LEXIS SQL
inline. The module exists for three reasons:

* **One place to read the SQL.** The interface is a read-heavy browser over the
  taxonomy (``categorys`` -> ``words_categorys`` -> ``words_hashs``) and the
  keyword index (``keywords`` -> ``keywords_hashs``), plus the management
  mutations from the Manage modal. Keeping the statements together means the
  taxonomy model can be reviewed without reading route code.
* **Parameters are named, never interpolated.** Every statement uses psycopg2
  named placeholders (``%(q)s``) and a literal percent is written ``%%``. A
  statement therefore never concatenates a user value into SQL; the service
  layer (``Api/services/lexis_cosmos.py``) binds them.
* **Testability.** ``tests/unit/test_lexis_queries.py`` walks ``ALL_QUERIES`` and
  asserts every placeholder is supplied by a declared parameter set, so a typo
  in a name is a failing test rather than a 500 at runtime.

Naming: ``Q<chapter>_<number><suffix>`` mirrors the chapter numbering of the
interface specification (1 health, 2 taxonomy tree, 3 term finder, 4 sidebar
search/facets, 5 viewer, 6 actions/exports, 7 dashboard, 8 taxonomy CRUD,
9 file manager, 10 saved views, 11 activity log). A suffix (``_a``, ``_b``, ...)
marks a statement that must run separately: psycopg2 cannot send several
parameterised statements in one ``execute`` call, and the specification's
multi-statement blocks are split here rather than in the callers.
"""

from __future__ import annotations

from typing import Dict, Tuple

# ============================================================================
# 1. HEALTH & SYSTEM METADATA (/api/health, Root Node, Status Bar)
# ============================================================================

#: Q1.1 - Server health and global counts for the root node ("LEXIS").
Q1_1_HEALTH = """
SELECT
    (SELECT COUNT(*) FROM paths) AS total_files,
    (SELECT COUNT(*) FROM hashs) AS total_unique_contents,
    (SELECT COUNT(*) FROM categorys WHERE parent_id IS NULL) AS total_main_categories,
    (SELECT COUNT(*) FROM categorys WHERE parent_id IS NOT NULL) AS total_subcategories,
    (SELECT COUNT(*) FROM words_categorys) AS total_categorized_words,
    (SELECT COUNT(*) FROM keywords) AS total_keywords,
    (SELECT pg_database_size(current_database())) AS database_size_bytes
"""


# ============================================================================
# 2. TAXONOMY TREE & GALAXY HIERARCHY
#    (root -> lens -> category -> subcategory -> term -> file)
# ============================================================================

#: Q2.1 - Level 1: lens ("Word galaxy" / "Keyword galaxy") summary counts.
Q2_1_LENS_SUMMARY = """
WITH filtered_paths AS (
    SELECT p.id AS path_id, hc.hash_id
    FROM paths p
    JOIN hash_contexts hc ON hc.id = p.context_id
    JOIN sources s ON s.id = hc.source_id
    JOIN sides si ON si.id = hc.side_id
    WHERE (%(file_types)s::text[] IS NULL OR p.file_type = ANY(%(file_types)s::text[]))
      AND (%(source_ids)s::int[] IS NULL OR hc.source_id = ANY(%(source_ids)s::int[]))
      AND (%(side_ids)s::int[] IS NULL OR hc.side_id = ANY(%(side_ids)s::int[]))
      AND (%(date_from)s::date IS NULL OR p.file_date >= %(date_from)s::date)
      AND (%(date_to)s::date IS NULL OR p.file_date <= %(date_to)s::date)
      AND (%(q)s::text IS NULL OR p.file_name ILIKE '%%' || %(q)s::text || '%%')
)
SELECT
    'words' AS lens,
    (SELECT COUNT(*) FROM words_categorys) AS term_count,
    COUNT(DISTINCT fp.path_id) AS file_count
FROM words_categorys wc
JOIN words_hashs wh ON wh.word_id = wc.word_id
JOIN filtered_paths fp ON fp.hash_id = wh.hash_id
UNION ALL
SELECT
    'keywords' AS lens,
    (SELECT COUNT(*) FROM keywords) AS term_count,
    COUNT(DISTINCT fp.path_id) AS file_count
FROM keywords k
JOIN keywords_hashs kh ON kh.keyword_id = k.id
JOIN filtered_paths fp ON fp.hash_id = kh.hash_id
"""

#: Q2.2 - Level 2: main categories under "Word galaxy".
Q2_2_WORD_CATEGORIES = """
WITH filtered_paths AS (
    SELECT p.id AS path_id, hc.hash_id
    FROM paths p
    JOIN hash_contexts hc ON hc.id = p.context_id
    WHERE (%(file_types)s::text[] IS NULL OR p.file_type = ANY(%(file_types)s::text[]))
      AND (%(source_ids)s::int[] IS NULL OR hc.source_id = ANY(%(source_ids)s::int[]))
      AND (%(side_ids)s::int[] IS NULL OR hc.side_id = ANY(%(side_ids)s::int[]))
      AND (%(date_from)s::date IS NULL OR p.file_date >= %(date_from)s::date)
      AND (%(date_to)s::date IS NULL OR p.file_date <= %(date_to)s::date)
      AND (%(q)s::text IS NULL OR p.file_name ILIKE '%%' || %(q)s::text || '%%')
),
cat_tree AS (
    -- One row for the main category itself and one per subcategory. A plain
    -- LEFT JOIN produced a row per (main, sub) pair only, so the terms hung
    -- directly off a main category became invisible as soon as it had any
    -- subcategory - the category showed terms in the chart but a file count of
    -- zero, and disappeared entirely under "hide empty items".
    SELECT
        main_c.id AS main_category_id,
        w_main.word AS main_category_name,
        NULL::integer AS subcategory_id,
        main_c.id AS target_category_id
    FROM categorys main_c
    JOIN words w_main ON w_main.id = main_c.word_id
    WHERE main_c.parent_id IS NULL
    UNION ALL
    SELECT
        main_c.id AS main_category_id,
        w_main.word AS main_category_name,
        sub_c.id AS subcategory_id,
        sub_c.id AS target_category_id
    FROM categorys main_c
    JOIN words w_main ON w_main.id = main_c.word_id
    JOIN categorys sub_c ON sub_c.parent_id = main_c.id
    WHERE main_c.parent_id IS NULL
)
SELECT
    ct.main_category_id AS id,
    ct.main_category_name AS name,
    ac.color AS color,
    COUNT(DISTINCT ct.subcategory_id) AS subcategory_count,
    COUNT(DISTINCT wc.word_id) AS term_count,
    COUNT(DISTINCT fp.path_id) AS file_count
FROM cat_tree ct
LEFT JOIN analyst_categories ac ON LOWER(ac.name) = LOWER(ct.main_category_name)
LEFT JOIN words_categorys wc ON wc.category_id = ct.target_category_id
LEFT JOIN words_hashs wh ON wh.word_id = wc.word_id
LEFT JOIN filtered_paths fp ON fp.hash_id = wh.hash_id
GROUP BY ct.main_category_id, ct.main_category_name, ac.color
HAVING (NOT %(hide_empty)s::boolean OR COUNT(DISTINCT fp.path_id) > 0)
ORDER BY ct.main_category_name ASC
"""

#: Q2.3 - Level 2: main categories under "Keyword galaxy".
Q2_3_KEYWORD_CATEGORIES = """
WITH filtered_paths AS (
    SELECT p.id AS path_id, hc.hash_id
    FROM paths p
    JOIN hash_contexts hc ON hc.id = p.context_id
    WHERE (%(file_types)s::text[] IS NULL OR p.file_type = ANY(%(file_types)s::text[]))
      AND (%(source_ids)s::int[] IS NULL OR hc.source_id = ANY(%(source_ids)s::int[]))
      AND (%(side_ids)s::int[] IS NULL OR hc.side_id = ANY(%(side_ids)s::int[]))
      AND (%(date_from)s::date IS NULL OR p.file_date >= %(date_from)s::date)
      AND (%(date_to)s::date IS NULL OR p.file_date <= %(date_to)s::date)
      AND (%(q)s::text IS NULL OR p.file_name ILIKE '%%' || %(q)s::text || '%%')
),
cat_tree AS (
    -- One row for the main category itself and one per subcategory. A plain
    -- LEFT JOIN produced a row per (main, sub) pair only, so the terms hung
    -- directly off a main category became invisible as soon as it had any
    -- subcategory - the category showed terms in the chart but a file count of
    -- zero, and disappeared entirely under "hide empty items".
    SELECT
        main_c.id AS main_category_id,
        w_main.word AS main_category_name,
        NULL::integer AS subcategory_id,
        main_c.id AS target_category_id
    FROM categorys main_c
    JOIN words w_main ON w_main.id = main_c.word_id
    WHERE main_c.parent_id IS NULL
    UNION ALL
    SELECT
        main_c.id AS main_category_id,
        w_main.word AS main_category_name,
        sub_c.id AS subcategory_id,
        sub_c.id AS target_category_id
    FROM categorys main_c
    JOIN words w_main ON w_main.id = main_c.word_id
    JOIN categorys sub_c ON sub_c.parent_id = main_c.id
    WHERE main_c.parent_id IS NULL
)
SELECT
    ct.main_category_id AS id,
    ct.main_category_name AS name,
    ac.color AS color,
    COUNT(DISTINCT ct.subcategory_id) AS subcategory_count,
    COUNT(DISTINCT k.id) AS term_count,
    COUNT(DISTINCT fp.path_id) AS file_count
FROM cat_tree ct
LEFT JOIN analyst_categories ac ON LOWER(ac.name) = LOWER(ct.main_category_name)
LEFT JOIN keywords k ON k.category_id = ct.target_category_id
LEFT JOIN keywords_hashs kh ON kh.keyword_id = k.id
LEFT JOIN filtered_paths fp ON fp.hash_id = kh.hash_id
GROUP BY ct.main_category_id, ct.main_category_name, ac.color
HAVING (NOT %(hide_empty)s::boolean OR COUNT(DISTINCT fp.path_id) > 0)
ORDER BY ct.main_category_name ASC
"""

#: Q2.4 - Level 3: subcategories of a main category under "Word galaxy".
Q2_4_WORD_SUBCATEGORIES = """
WITH filtered_paths AS (
    SELECT p.id AS path_id, hc.hash_id
    FROM paths p
    JOIN hash_contexts hc ON hc.id = p.context_id
    WHERE (%(file_types)s::text[] IS NULL OR p.file_type = ANY(%(file_types)s::text[]))
      AND (%(source_ids)s::int[] IS NULL OR hc.source_id = ANY(%(source_ids)s::int[]))
      AND (%(side_ids)s::int[] IS NULL OR hc.side_id = ANY(%(side_ids)s::int[]))
      AND (%(date_from)s::date IS NULL OR p.file_date >= %(date_from)s::date)
      AND (%(date_to)s::date IS NULL OR p.file_date <= %(date_to)s::date)
      AND (%(q)s::text IS NULL OR p.file_name ILIKE '%%' || %(q)s::text || '%%')
)
SELECT
    sub_c.id AS id,
    w_sub.word AS name,
    sub_c.parent_id AS parent_id,
    COUNT(DISTINCT wc.word_id) AS term_count,
    COUNT(DISTINCT fp.path_id) AS file_count
FROM categorys sub_c
JOIN words w_sub ON w_sub.id = sub_c.word_id
LEFT JOIN words_categorys wc ON wc.category_id = sub_c.id
LEFT JOIN words_hashs wh ON wh.word_id = wc.word_id
LEFT JOIN filtered_paths fp ON fp.hash_id = wh.hash_id
WHERE sub_c.parent_id = %(main_category_id)s
GROUP BY sub_c.id, w_sub.word, sub_c.parent_id
HAVING (NOT %(hide_empty)s::boolean OR COUNT(DISTINCT fp.path_id) > 0)
ORDER BY w_sub.word ASC
"""

#: Q2.5 - Level 3: subcategories of a main category under "Keyword galaxy".
Q2_5_KEYWORD_SUBCATEGORIES = """
WITH filtered_paths AS (
    SELECT p.id AS path_id, hc.hash_id
    FROM paths p
    JOIN hash_contexts hc ON hc.id = p.context_id
    WHERE (%(file_types)s::text[] IS NULL OR p.file_type = ANY(%(file_types)s::text[]))
      AND (%(source_ids)s::int[] IS NULL OR hc.source_id = ANY(%(source_ids)s::int[]))
      AND (%(side_ids)s::int[] IS NULL OR hc.side_id = ANY(%(side_ids)s::int[]))
      AND (%(date_from)s::date IS NULL OR p.file_date >= %(date_from)s::date)
      AND (%(date_to)s::date IS NULL OR p.file_date <= %(date_to)s::date)
      AND (%(q)s::text IS NULL OR p.file_name ILIKE '%%' || %(q)s::text || '%%')
)
SELECT
    sub_c.id AS id,
    w_sub.word AS name,
    sub_c.parent_id AS parent_id,
    COUNT(DISTINCT k.id) AS term_count,
    COUNT(DISTINCT fp.path_id) AS file_count
FROM categorys sub_c
JOIN words w_sub ON w_sub.id = sub_c.word_id
LEFT JOIN keywords k ON k.category_id = sub_c.id
LEFT JOIN keywords_hashs kh ON kh.keyword_id = k.id
LEFT JOIN filtered_paths fp ON fp.hash_id = kh.hash_id
WHERE sub_c.parent_id = %(main_category_id)s
GROUP BY sub_c.id, w_sub.word, sub_c.parent_id
HAVING (NOT %(hide_empty)s::boolean OR COUNT(DISTINCT fp.path_id) > 0)
ORDER BY w_sub.word ASC
"""

#: Q2.6 - Level 4: word terms in a subcategory/category (``term:w_<word_id>``).
Q2_6_WORD_TERMS = """
WITH filtered_paths AS (
    SELECT p.id AS path_id, hc.hash_id
    FROM paths p
    JOIN hash_contexts hc ON hc.id = p.context_id
    WHERE (%(file_types)s::text[] IS NULL OR p.file_type = ANY(%(file_types)s::text[]))
      AND (%(source_ids)s::int[] IS NULL OR hc.source_id = ANY(%(source_ids)s::int[]))
      AND (%(side_ids)s::int[] IS NULL OR hc.side_id = ANY(%(side_ids)s::int[]))
      AND (%(date_from)s::date IS NULL OR p.file_date >= %(date_from)s::date)
      AND (%(date_to)s::date IS NULL OR p.file_date <= %(date_to)s::date)
      AND (%(q)s::text IS NULL OR p.file_name ILIKE '%%' || %(q)s::text || '%%')
)
SELECT
    w.id AS word_id,
    w.word AS label,
    wc.category_id AS subcategory_id,
    c.parent_id AS main_category_id,
    COUNT(DISTINCT fp.path_id) AS file_count,
    COALESCE(SUM(wh.word_count) FILTER (WHERE fp.path_id IS NOT NULL), 0) AS total_hits
FROM words_categorys wc
JOIN words w ON w.id = wc.word_id
JOIN categorys c ON c.id = wc.category_id
LEFT JOIN words_hashs wh ON wh.word_id = w.id
LEFT JOIN filtered_paths fp ON fp.hash_id = wh.hash_id
WHERE wc.category_id = %(category_id)s
GROUP BY w.id, w.word, wc.category_id, c.parent_id
HAVING (NOT %(hide_empty)s::boolean OR COUNT(DISTINCT fp.path_id) > 0)
ORDER BY w.word ASC
"""

#: Q2.7 - Level 4: keyword terms in a subcategory/category (``term:k_<keyword_id>``).
Q2_7_KEYWORD_TERMS = """
WITH filtered_paths AS (
    SELECT p.id AS path_id, hc.hash_id
    FROM paths p
    JOIN hash_contexts hc ON hc.id = p.context_id
    WHERE (%(file_types)s::text[] IS NULL OR p.file_type = ANY(%(file_types)s::text[]))
      AND (%(source_ids)s::int[] IS NULL OR hc.source_id = ANY(%(source_ids)s::int[]))
      AND (%(side_ids)s::int[] IS NULL OR hc.side_id = ANY(%(side_ids)s::int[]))
      AND (%(date_from)s::date IS NULL OR p.file_date >= %(date_from)s::date)
      AND (%(date_to)s::date IS NULL OR p.file_date <= %(date_to)s::date)
      AND (%(q)s::text IS NULL OR p.file_name ILIKE '%%' || %(q)s::text || '%%')
)
SELECT
    k.id AS keyword_id,
    k.keyword AS keyword_blob,
    k.category_id AS subcategory_id,
    c.parent_id AS main_category_id,
    COUNT(DISTINCT fp.path_id) AS file_count,
    COALESCE(SUM(kh.word_count) FILTER (WHERE fp.path_id IS NOT NULL), 0) AS total_hits
FROM keywords k
JOIN categorys c ON c.id = k.category_id
LEFT JOIN keywords_hashs kh ON kh.keyword_id = k.id
LEFT JOIN filtered_paths fp ON fp.hash_id = kh.hash_id
WHERE k.category_id = %(category_id)s
GROUP BY k.id, k.keyword, k.category_id, c.parent_id
HAVING (NOT %(hide_empty)s::boolean OR COUNT(DISTINCT fp.path_id) > 0)
ORDER BY k.id ASC
"""

#: Q2.8 - Resolve unpacked keyword ``word_ids`` to ordered keyword phrase text.
Q2_8_RESOLVE_KEYWORD_WORDS = """
SELECT
    u.ord,
    u.word_id,
    w.word
FROM unnest(%(word_ids)s::int[]) WITH ORDINALITY AS u(word_id, ord)
JOIN words w ON w.id = u.word_id
ORDER BY u.ord ASC
"""

#: Q2.9 - Level 5: file stack under a specific word term.
Q2_9_WORD_TERM_FILES = """
SELECT
    p.id AS file_id,
    p.file_name AS name,
    p.file_type AS type,
    p.file_date AS date,
    s.name AS source,
    si.name AS side,
    wh.word_count AS hits
FROM paths p
JOIN hash_contexts hc ON hc.id = p.context_id
JOIN words_hashs wh ON wh.hash_id = hc.hash_id
LEFT JOIN sources s ON s.id = hc.source_id
LEFT JOIN sides si ON si.id = hc.side_id
WHERE wh.word_id = %(word_id)s
  AND (%(file_types)s::text[] IS NULL OR p.file_type = ANY(%(file_types)s::text[]))
  AND (%(source_ids)s::int[] IS NULL OR hc.source_id = ANY(%(source_ids)s::int[]))
  AND (%(side_ids)s::int[] IS NULL OR hc.side_id = ANY(%(side_ids)s::int[]))
  AND (%(date_from)s::date IS NULL OR p.file_date >= %(date_from)s::date)
  AND (%(date_to)s::date IS NULL OR p.file_date <= %(date_to)s::date)
  AND (%(q)s::text IS NULL OR p.file_name ILIKE '%%' || %(q)s::text || '%%')
ORDER BY wh.word_count DESC, p.file_date DESC, p.id DESC
"""

#: Q2.10 - Level 5: file stack under a specific keyword term.
Q2_10_KEYWORD_TERM_FILES = """
SELECT
    p.id AS file_id,
    p.file_name AS name,
    p.file_type AS type,
    p.file_date AS date,
    s.name AS source,
    si.name AS side,
    kh.word_count AS hits
FROM paths p
JOIN hash_contexts hc ON hc.id = p.context_id
JOIN keywords_hashs kh ON kh.hash_id = hc.hash_id
LEFT JOIN sources s ON s.id = hc.source_id
LEFT JOIN sides si ON si.id = hc.side_id
WHERE kh.keyword_id = %(keyword_id)s
  AND (%(file_types)s::text[] IS NULL OR p.file_type = ANY(%(file_types)s::text[]))
  AND (%(source_ids)s::int[] IS NULL OR hc.source_id = ANY(%(source_ids)s::int[]))
  AND (%(side_ids)s::int[] IS NULL OR hc.side_id = ANY(%(side_ids)s::int[]))
  AND (%(date_from)s::date IS NULL OR p.file_date >= %(date_from)s::date)
  AND (%(date_to)s::date IS NULL OR p.file_date <= %(date_to)s::date)
  AND (%(q)s::text IS NULL OR p.file_name ILIKE '%%' || %(q)s::text || '%%')
ORDER BY kh.word_count DESC, p.file_date DESC, p.id DESC
"""

#: Q2.11 (words half) - full taxonomy & outline export, word lens.
Q2_11_OUTLINE_WORDS = """
SELECT
    main_c.id AS main_category_id,
    w_main.word AS main_category_name,
    sub_c.id AS subcategory_id,
    w_sub.word AS subcategory_name,
    'words' AS lens,
    w_term.id AS term_id,
    w_term.word AS term_label,
    NULL::bytea AS keyword_blob,
    COUNT(DISTINCT p.id) AS file_count
FROM categorys main_c
JOIN words w_main ON w_main.id = main_c.word_id
LEFT JOIN categorys sub_c ON sub_c.parent_id = main_c.id
LEFT JOIN words w_sub ON w_sub.id = sub_c.word_id
LEFT JOIN words_categorys wc ON wc.category_id = COALESCE(sub_c.id, main_c.id)
LEFT JOIN words w_term ON w_term.id = wc.word_id
LEFT JOIN words_hashs wh ON wh.word_id = w_term.id
LEFT JOIN hash_contexts hc ON hc.hash_id = wh.hash_id
LEFT JOIN paths p ON p.context_id = hc.id
WHERE main_c.parent_id IS NULL
GROUP BY main_c.id, w_main.word, sub_c.id, w_sub.word, w_term.id, w_term.word
ORDER BY main_category_name ASC, subcategory_name ASC NULLS FIRST, term_id ASC
"""

#: Q2.11 (keywords half) - full taxonomy & outline export, keyword lens.
Q2_11_OUTLINE_KEYWORDS = """
SELECT
    main_c.id AS main_category_id,
    w_main.word AS main_category_name,
    sub_c.id AS subcategory_id,
    w_sub.word AS subcategory_name,
    'keywords' AS lens,
    k.id AS term_id,
    NULL::text AS term_label,
    k.keyword AS keyword_blob,
    COUNT(DISTINCT p.id) AS file_count
FROM categorys main_c
JOIN words w_main ON w_main.id = main_c.word_id
LEFT JOIN categorys sub_c ON sub_c.parent_id = main_c.id
LEFT JOIN words w_sub ON w_sub.id = sub_c.word_id
LEFT JOIN keywords k ON k.category_id = COALESCE(sub_c.id, main_c.id)
LEFT JOIN keywords_hashs kh ON kh.keyword_id = k.id
LEFT JOIN hash_contexts hc ON hc.hash_id = kh.hash_id
LEFT JOIN paths p ON p.context_id = hc.id
WHERE main_c.parent_id IS NULL
GROUP BY main_c.id, w_main.word, sub_c.id, w_sub.word, k.id, k.keyword
ORDER BY main_category_name ASC, subcategory_name ASC NULLS FIRST, term_id ASC
"""


# ============================================================================
# 3. HEADER TERM FINDER (#find typeahead search)
# ============================================================================

#: Q3.1 - Find word terms matching a query with path and file count.
Q3_1_FIND_WORDS = """
SELECT
    w.id AS term_id,
    w.word AS label,
    'words' AS lens,
    COALESCE(main_c.id, sub_c.id) AS cat_id,
    COALESCE(w_main.word, w_sub.word) AS cat_name,
    sub_c.id AS sub_id,
    w_sub.word AS sub_name,
    COUNT(DISTINCT p.id) AS file_count
FROM words_categorys wc
JOIN words w ON w.id = wc.word_id
JOIN categorys sub_c ON sub_c.id = wc.category_id
JOIN words w_sub ON w_sub.id = sub_c.word_id
LEFT JOIN categorys main_c ON main_c.id = sub_c.parent_id
LEFT JOIN words w_main ON w_main.id = main_c.word_id
LEFT JOIN words_hashs wh ON wh.word_id = w.id
LEFT JOIN hash_contexts hc ON hc.hash_id = wh.hash_id
LEFT JOIN paths p ON p.context_id = hc.id
WHERE w.word ILIKE '%%' || %(q)s || '%%'
GROUP BY w.id, w.word, main_c.id, w_main.word, sub_c.id, w_sub.word
ORDER BY file_count DESC, w.word ASC
LIMIT 9
"""

#: Q3.2 - Keyword terms with path and file count for the term finder.
Q3_2_FIND_KEYWORDS = """
SELECT
    k.id AS term_id,
    k.keyword AS keyword_blob,
    'keywords' AS lens,
    COALESCE(main_c.id, sub_c.id) AS cat_id,
    COALESCE(w_main.word, w_sub.word) AS cat_name,
    sub_c.id AS sub_id,
    w_sub.word AS sub_name,
    COUNT(DISTINCT p.id) AS file_count
FROM keywords k
JOIN categorys sub_c ON sub_c.id = k.category_id
JOIN words w_sub ON w_sub.id = sub_c.word_id
LEFT JOIN categorys main_c ON main_c.id = sub_c.parent_id
LEFT JOIN words w_main ON w_main.id = main_c.word_id
LEFT JOIN keywords_hashs kh ON kh.keyword_id = k.id
LEFT JOIN hash_contexts hc ON hc.hash_id = kh.hash_id
LEFT JOIN paths p ON p.context_id = hc.id
GROUP BY k.id, k.keyword, main_c.id, w_main.word, sub_c.id, w_sub.word
ORDER BY file_count DESC, k.id ASC
"""


# ============================================================================
# 4. SIDEBAR SEARCH, FACETS & PAGINATION (/api/search)
# ============================================================================

#: Q4.1 (words) - scope resolution: expand a focused node into word ids.
#: When focus is ``cat:<lens>:<cat_id>`` this includes the main category and
#: all of its subcategories.
Q4_1_SCOPE_WORD_IDS = """
SELECT wc.word_id
FROM words_categorys wc
WHERE wc.category_id = %(category_id)s
   OR wc.category_id IN (SELECT id FROM categorys WHERE parent_id = %(category_id)s)
"""

#: Q4.1 (keywords) - scope resolution: expand a focused node into keyword ids.
Q4_1_SCOPE_KEYWORD_IDS = """
SELECT k.id AS keyword_id
FROM keywords k
WHERE k.category_id = %(category_id)s
   OR k.category_id IN (SELECT id FROM categorys WHERE parent_id = %(category_id)s)
"""

#: Q4.2 - scoped file search, offset pagination (page jumps, First/Last).
Q4_2_SEARCH_OFFSET = """
WITH scoped_hits AS (
    SELECT
        wh.hash_id,
        'w:' || wh.word_id::text AS term_key,
        w.word AS term_label,
        wh.word_count AS hits
    FROM words_hashs wh
    JOIN words w ON w.id = wh.word_id
    WHERE %(word_ids)s::int[] IS NOT NULL
      AND wh.word_id = ANY(%(word_ids)s::int[])
    UNION ALL
    SELECT
        kh.hash_id,
        'k:' || kh.keyword_id::text AS term_key,
        'k:' || kh.keyword_id::text AS term_label,
        kh.word_count AS hits
    FROM keywords_hashs kh
    WHERE %(keyword_ids)s::int[] IS NOT NULL
      AND kh.keyword_id = ANY(%(keyword_ids)s::int[])
),
agg_hashes AS (
    SELECT
        sh.hash_id,
        SUM(sh.hits)::int AS total_hits,
        ARRAY_AGG(DISTINCT sh.term_key ORDER BY sh.term_key) AS matched_keys,
        ARRAY_AGG(DISTINCT sh.term_label ORDER BY sh.term_label) AS matched_labels
    FROM scoped_hits sh
    GROUP BY sh.hash_id
)
SELECT
    p.id AS id,
    p.file_name AS name,
    p.file_type AS type,
    s.name AS source,
    p.file_date AS date,
    si.name AS side,
    p.file_status AS status,
    p.file_size AS size,
    ah.total_hits AS hits,
    ah.matched_keys AS keys,
    ah.matched_labels AS matched,
    COUNT(*) OVER() AS total_count
FROM paths p
JOIN hash_contexts hc ON hc.id = p.context_id
JOIN agg_hashes ah ON ah.hash_id = hc.hash_id
LEFT JOIN sources s ON s.id = hc.source_id
LEFT JOIN sides si ON si.id = hc.side_id
WHERE (%(file_types)s::text[] IS NULL OR p.file_type = ANY(%(file_types)s::text[]))
  AND (%(sources)s::text[] IS NULL OR s.name = ANY(%(sources)s::text[]))
  AND (%(sides)s::text[] IS NULL OR si.name = ANY(%(sides)s::text[]))
  AND (%(date_from)s::date IS NULL OR p.file_date >= %(date_from)s::date)
  AND (%(date_to)s::date IS NULL OR p.file_date <= %(date_to)s::date)
  AND (%(q)s::text IS NULL OR p.file_name ILIKE '%%' || %(q)s::text || '%%')
ORDER BY
    CASE WHEN %(sort_key)s = 'hits' AND %(sort_dir)s = 'desc' THEN ah.total_hits END DESC,
    CASE WHEN %(sort_key)s = 'hits' AND %(sort_dir)s = 'asc'  THEN ah.total_hits END ASC,
    CASE WHEN %(sort_key)s = 'date' AND %(sort_dir)s = 'desc' THEN p.file_date END DESC,
    CASE WHEN %(sort_key)s = 'date' AND %(sort_dir)s = 'asc'  THEN p.file_date END ASC,
    CASE WHEN %(sort_key)s = 'name' AND %(sort_dir)s = 'desc' THEN p.file_name END DESC,
    CASE WHEN %(sort_key)s = 'name' AND %(sort_dir)s = 'asc'  THEN p.file_name END ASC,
    CASE WHEN %(sort_key)s = 'type' AND %(sort_dir)s = 'desc' THEN p.file_type END DESC,
    CASE WHEN %(sort_key)s = 'type' AND %(sort_dir)s = 'asc'  THEN p.file_type END ASC,
    CASE WHEN %(sort_dir)s = 'desc' THEN p.id END DESC,
    CASE WHEN %(sort_dir)s = 'asc'  THEN p.id END ASC
LIMIT %(size)s OFFSET %(offset)s
"""

#: Q4.3 - scoped file search, keyset/cursor pagination (Next page, Show more).
#: Cursor tuple: (after_sort_val, after_id).
Q4_3_SEARCH_KEYSET = """
WITH scoped_hits AS (
    SELECT
        wh.hash_id,
        'w:' || wh.word_id::text AS term_key,
        w.word AS term_label,
        wh.word_count AS hits
    FROM words_hashs wh
    JOIN words w ON w.id = wh.word_id
    WHERE %(word_ids)s::int[] IS NOT NULL
      AND wh.word_id = ANY(%(word_ids)s::int[])
    UNION ALL
    SELECT
        kh.hash_id,
        'k:' || kh.keyword_id::text AS term_key,
        'k:' || kh.keyword_id::text AS term_label,
        kh.word_count AS hits
    FROM keywords_hashs kh
    WHERE %(keyword_ids)s::int[] IS NOT NULL
      AND kh.keyword_id = ANY(%(keyword_ids)s::int[])
),
agg_hashes AS (
    SELECT
        sh.hash_id,
        SUM(sh.hits)::int AS total_hits,
        ARRAY_AGG(DISTINCT sh.term_key ORDER BY sh.term_key) AS matched_keys,
        ARRAY_AGG(DISTINCT sh.term_label ORDER BY sh.term_label) AS matched_labels
    FROM scoped_hits sh
    GROUP BY sh.hash_id
)
SELECT
    p.id AS id,
    p.file_name AS name,
    p.file_type AS type,
    s.name AS source,
    p.file_date AS date,
    si.name AS side,
    p.file_status AS status,
    p.file_size AS size,
    ah.total_hits AS hits,
    ah.matched_keys AS keys,
    ah.matched_labels AS matched
FROM paths p
JOIN hash_contexts hc ON hc.id = p.context_id
JOIN agg_hashes ah ON ah.hash_id = hc.hash_id
LEFT JOIN sources s ON s.id = hc.source_id
LEFT JOIN sides si ON si.id = hc.side_id
WHERE (%(file_types)s::text[] IS NULL OR p.file_type = ANY(%(file_types)s::text[]))
  AND (%(sources)s::text[] IS NULL OR s.name = ANY(%(sources)s::text[]))
  AND (%(sides)s::text[] IS NULL OR si.name = ANY(%(sides)s::text[]))
  AND (%(date_from)s::date IS NULL OR p.file_date >= %(date_from)s::date)
  AND (%(date_to)s::date IS NULL OR p.file_date <= %(date_to)s::date)
  AND (%(q)s::text IS NULL OR p.file_name ILIKE '%%' || %(q)s::text || '%%')
  AND (
      %(after_id)s::int IS NULL
      OR (%(sort_key)s = 'hits' AND %(sort_dir)s = 'desc' AND (ah.total_hits, p.id) < (%(after_hits)s::int, %(after_id)s::int))
      OR (%(sort_key)s = 'hits' AND %(sort_dir)s = 'asc'  AND (ah.total_hits, p.id) > (%(after_hits)s::int, %(after_id)s::int))
      OR (%(sort_key)s = 'date' AND %(sort_dir)s = 'desc' AND (p.file_date, p.id) < (%(after_date)s::date, %(after_id)s::int))
      OR (%(sort_key)s = 'date' AND %(sort_dir)s = 'asc'  AND (p.file_date, p.id) > (%(after_date)s::date, %(after_id)s::int))
      OR (%(sort_key)s = 'name' AND %(sort_dir)s = 'desc' AND (p.file_name, p.id) < (%(after_str)s::text, %(after_id)s::int))
      OR (%(sort_key)s = 'name' AND %(sort_dir)s = 'asc'  AND (p.file_name, p.id) > (%(after_str)s::text, %(after_id)s::int))
      OR (%(sort_key)s = 'type' AND %(sort_dir)s = 'desc' AND (p.file_type, p.id) < (%(after_str)s::text, %(after_id)s::int))
      OR (%(sort_key)s = 'type' AND %(sort_dir)s = 'asc'  AND (p.file_type, p.id) > (%(after_str)s::text, %(after_id)s::int))
  )
ORDER BY
    CASE WHEN %(sort_key)s = 'hits' AND %(sort_dir)s = 'desc' THEN ah.total_hits END DESC,
    CASE WHEN %(sort_key)s = 'hits' AND %(sort_dir)s = 'asc'  THEN ah.total_hits END ASC,
    CASE WHEN %(sort_key)s = 'date' AND %(sort_dir)s = 'desc' THEN p.file_date END DESC,
    CASE WHEN %(sort_key)s = 'date' AND %(sort_dir)s = 'asc'  THEN p.file_date END ASC,
    CASE WHEN %(sort_key)s = 'name' AND %(sort_dir)s = 'desc' THEN p.file_name END DESC,
    CASE WHEN %(sort_key)s = 'name' AND %(sort_dir)s = 'asc'  THEN p.file_name END ASC,
    CASE WHEN %(sort_key)s = 'type' AND %(sort_dir)s = 'desc' THEN p.file_type END DESC,
    CASE WHEN %(sort_key)s = 'type' AND %(sort_dir)s = 'asc'  THEN p.file_type END ASC,
    CASE WHEN %(sort_dir)s = 'desc' THEN p.id END DESC,
    CASE WHEN %(sort_dir)s = 'asc'  THEN p.id END ASC
LIMIT %(size)s
"""

#: Q4.4 - sidebar facet counts (file type, source, side) within the scope.
Q4_4_FACETS = """
WITH scoped_hashes AS (
    SELECT DISTINCT wh.hash_id
    FROM words_hashs wh
    WHERE %(word_ids)s::int[] IS NOT NULL AND wh.word_id = ANY(%(word_ids)s::int[])
    UNION
    SELECT DISTINCT kh.hash_id
    FROM keywords_hashs kh
    WHERE %(keyword_ids)s::int[] IS NOT NULL AND kh.keyword_id = ANY(%(keyword_ids)s::int[])
),
scoped_files AS (
    SELECT
        p.id,
        p.file_type,
        s.name AS source_name,
        si.name AS side_name
    FROM paths p
    JOIN hash_contexts hc ON hc.id = p.context_id
    JOIN scoped_hashes sh ON sh.hash_id = hc.hash_id
    LEFT JOIN sources s ON s.id = hc.source_id
    LEFT JOIN sides si ON si.id = hc.side_id
)
SELECT 'type' AS facet, file_type AS value, COUNT(*) AS file_count
FROM scoped_files
GROUP BY file_type
UNION ALL
SELECT 'source' AS facet, COALESCE(source_name, 'Unknown') AS value, COUNT(*) AS file_count
FROM scoped_files
GROUP BY COALESCE(source_name, 'Unknown')
UNION ALL
SELECT 'dim' AS facet, COALESCE(side_name, 'Unknown') AS value, COUNT(*) AS file_count
FROM scoped_files
GROUP BY COALESCE(side_name, 'Unknown')
ORDER BY facet, file_count DESC, value ASC
"""

#: Q4.5 - distinct filter options for the sidebar dropdowns.
Q4_5_FILE_TYPES = """
SELECT DISTINCT file_type FROM paths
WHERE file_type IS NOT NULL AND file_type <> ''
ORDER BY file_type ASC
"""

Q4_5_SOURCES = "SELECT id, name FROM sources ORDER BY name ASC"

Q4_5_SIDES = "SELECT id, name, importance FROM sides ORDER BY name ASC"


# ============================================================================
# 5. DOCUMENT VIEWER MODAL (openViewer, highlighting, prev/next)
# ============================================================================

#: Q5.1 - single file metadata and canonical content identity.
Q5_1_FILE_METADATA = """
SELECT
    po.path_id AS id,
    po.file_name AS name,
    po.file_path AS path,
    po.file_size AS size,
    po.file_type AS type,
    po.file_status AS status,
    po.file_date AS date,
    po.date_creation,
    po.hash_id,
    po.content_hash AS hash,
    po.source_id,
    po.source_name AS source,
    po.side_id,
    po.side_name AS side,
    po.processing_status
FROM path_overview po
WHERE po.path_id = %(path_id)s
"""

#: Q5.2 - display text for the viewer (ordered raw chunks).
Q5_2_FILE_TEXT = """
SELECT content
FROM contents_raw
WHERE hash_id = (
    SELECT hc.hash_id
    FROM paths p
    JOIN hash_contexts hc ON hc.id = p.context_id
    WHERE p.id = %(path_id)s
)
ORDER BY chunk_seq ASC
"""

#: Q5.3 - fallback compressed symbol-pair content chunks.
Q5_3_FILE_TEXT_COMPRESSED = """
SELECT id, content_data
FROM contents
WHERE hash_id = (
    SELECT hc.hash_id
    FROM paths p
    JOIN hash_contexts hc ON hc.id = p.context_id
    WHERE p.id = %(path_id)s
)
ORDER BY id ASC
"""

#: Q5.4 (words half) - every matched word term for a file, with taxonomy path.
Q5_4_FILE_MATCHES_WORDS = """
WITH target_hash AS (
    SELECT hc.hash_id
    FROM paths p
    JOIN hash_contexts hc ON hc.id = p.context_id
    WHERE p.id = %(path_id)s
)
SELECT
    'words' AS lens,
    w.id AS term_id,
    w.word AS label,
    NULL::bytea AS keyword_blob,
    COALESCE(main_c.id, sub_c.id) AS cat_id,
    COALESCE(w_main.word, w_sub.word) AS cat_name,
    sub_c.id AS sub_id,
    w_sub.word AS sub_name,
    wh.word_count AS hits,
    wh.position_indexer AS position_indexer
FROM target_hash th
JOIN words_hashs wh ON wh.hash_id = th.hash_id
JOIN words_categorys wc ON wc.word_id = wh.word_id
JOIN words w ON w.id = wc.word_id
JOIN categorys sub_c ON sub_c.id = wc.category_id
JOIN words w_sub ON w_sub.id = sub_c.word_id
LEFT JOIN categorys main_c ON main_c.id = sub_c.parent_id
LEFT JOIN words w_main ON w_main.id = main_c.word_id
"""

#: Q5.4 (keywords half) - every matched keyword expression for a file.
Q5_4_FILE_MATCHES_KEYWORDS = """
SELECT
    'keywords' AS lens,
    k.id AS term_id,
    NULL::text AS label,
    k.keyword AS keyword_blob,
    COALESCE(main_c.id, sub_c.id) AS cat_id,
    COALESCE(w_main.word, w_sub.word) AS cat_name,
    sub_c.id AS sub_id,
    w_sub.word AS sub_name,
    kh.word_count AS hits,
    NULL::bytea AS position_indexer
FROM target_hash th
JOIN keywords_hashs kh ON kh.hash_id = th.hash_id
JOIN keywords k ON k.id = kh.keyword_id
JOIN categorys sub_c ON sub_c.id = k.category_id
JOIN words w_sub ON w_sub.id = sub_c.word_id
LEFT JOIN categorys main_c ON main_c.id = sub_c.parent_id
LEFT JOIN words w_main ON w_main.id = main_c.word_id
"""

#: Q5.4 - "why it matched": every matched word term and keyword expression for
#: a file with taxonomy path and hit counts (the two halves above, unioned).
Q5_4_FILE_MATCHES = "\nUNION ALL\n".join([
    Q5_4_FILE_MATCHES_WORDS,
    Q5_4_FILE_MATCHES_KEYWORDS.strip(),
]) + "\nORDER BY hits DESC, lens ASC, term_id ASC\n"


# ============================================================================
# 6. ACTIONS MENU & EXPORTS (Actions 2-12 + standard exporter)
# ============================================================================

#: Q6.1 (words half) - Action 2 (xCats): category/term review rows.
#: A selection overrides the filters when ``selected_path_ids`` is non-null.
Q6_1_REVIEW_WORDS = """
WITH target_files AS (
    SELECT p.id AS path_id, p.file_name, hc.hash_id
    FROM paths p
    JOIN hash_contexts hc ON hc.id = p.context_id
    LEFT JOIN sources s ON s.id = hc.source_id
    LEFT JOIN sides si ON si.id = hc.side_id
    WHERE (
        (%(selected_path_ids)s::int[] IS NOT NULL AND p.id = ANY(%(selected_path_ids)s::int[]))
        OR (
            %(selected_path_ids)s::int[] IS NULL
            AND (%(file_types)s::text[] IS NULL OR p.file_type = ANY(%(file_types)s::text[]))
            AND (%(sources)s::text[] IS NULL OR s.name = ANY(%(sources)s::text[]))
            AND (%(sides)s::text[] IS NULL OR si.name = ANY(%(sides)s::text[]))
            AND (%(date_from)s::date IS NULL OR p.file_date >= %(date_from)s::date)
            AND (%(date_to)s::date IS NULL OR p.file_date <= %(date_to)s::date)
            AND (%(q)s::text IS NULL OR p.file_name ILIKE '%%' || %(q)s::text || '%%')
        )
    )
)
SELECT
    tf.path_id AS file_id,
    tf.file_name AS file,
    'words' AS lens,
    COALESCE(w_main.word, w_sub.word) AS category,
    w_sub.word AS subcategory,
    w.id AS term_id,
    w.word AS term,
    NULL::bytea AS keyword_blob,
    wh.word_count AS hits
FROM target_files tf
JOIN words_hashs wh ON wh.hash_id = tf.hash_id
JOIN words_categorys wc ON wc.word_id = wh.word_id
JOIN words w ON w.id = wc.word_id
JOIN categorys sub_c ON sub_c.id = wc.category_id
JOIN words w_sub ON w_sub.id = sub_c.word_id
LEFT JOIN categorys main_c ON main_c.id = sub_c.parent_id
LEFT JOIN words w_main ON w_main.id = main_c.word_id
WHERE (%(word_ids)s::int[] IS NULL OR w.id = ANY(%(word_ids)s::int[]))
"""

#: Q6.1 (keywords half) - Action 2 (xCats): keyword rows.
Q6_1_REVIEW_KEYWORDS = """
WITH target_files AS (
    SELECT p.id AS path_id, p.file_name, hc.hash_id
    FROM paths p
    JOIN hash_contexts hc ON hc.id = p.context_id
    LEFT JOIN sources s ON s.id = hc.source_id
    LEFT JOIN sides si ON si.id = hc.side_id
    WHERE (
        (%(selected_path_ids)s::int[] IS NOT NULL AND p.id = ANY(%(selected_path_ids)s::int[]))
        OR (
            %(selected_path_ids)s::int[] IS NULL
            AND (%(file_types)s::text[] IS NULL OR p.file_type = ANY(%(file_types)s::text[]))
            AND (%(sources)s::text[] IS NULL OR s.name = ANY(%(sources)s::text[]))
            AND (%(sides)s::text[] IS NULL OR si.name = ANY(%(sides)s::text[]))
            AND (%(date_from)s::date IS NULL OR p.file_date >= %(date_from)s::date)
            AND (%(date_to)s::date IS NULL OR p.file_date <= %(date_to)s::date)
            AND (%(q)s::text IS NULL OR p.file_name ILIKE '%%' || %(q)s::text || '%%')
        )
    )
)
SELECT
    tf.path_id AS file_id,
    tf.file_name AS file,
    'keywords' AS lens,
    COALESCE(w_main.word, w_sub.word) AS category,
    w_sub.word AS subcategory,
    k.id AS term_id,
    NULL::text AS term,
    k.keyword AS keyword_blob,
    kh.word_count AS hits
FROM target_files tf
JOIN keywords_hashs kh ON kh.hash_id = tf.hash_id
JOIN keywords k ON k.id = kh.keyword_id
JOIN categorys sub_c ON sub_c.id = k.category_id
JOIN words w_sub ON w_sub.id = sub_c.word_id
LEFT JOIN categorys main_c ON main_c.id = sub_c.parent_id
LEFT JOIN words w_main ON w_main.id = main_c.word_id
WHERE (%(keyword_ids)s::int[] IS NULL OR k.id = ANY(%(keyword_ids)s::int[]))
"""

#: Q6.1 - combined review export (ordered for CSV/table display).
#:
#: The two halves need the *same* ``target_files`` CTE, and a CTE cannot be
#: declared twice in one statement: the words half therefore supplies the
#: ``WITH`` clause and the keyword half only its ``SELECT``. Concatenating the
#: two halves as written produced ``... UNION ALL WITH target_files AS ...``,
#: which PostgreSQL rejects - the export returned 500 while every unit test on
#: the halves passed.
def _split_leading_cte(statement: str) -> tuple[str, str]:
    """Split ``WITH ... SELECT ...`` into the CTE part and the SELECT body."""
    marker = "\nSELECT\n"
    index = statement.index(marker)
    return statement[: index + 1], statement[index + 1:]


_Q6_1_CTE, _Q6_1_WORDS_BODY = _split_leading_cte(Q6_1_REVIEW_WORDS.strip())
_, _Q6_1_KEYWORDS_BODY = _split_leading_cte(Q6_1_REVIEW_KEYWORDS.strip())

Q6_1_REVIEW = (
    "SELECT * FROM (\n"
    + _Q6_1_CTE
    + _Q6_1_WORDS_BODY
    + "UNION ALL\n"
    + _Q6_1_KEYWORDS_BODY
    + ") review_rows\n"
    + "ORDER BY file ASC, lens ASC, category ASC, subcategory ASC, hits DESC\n"
)

#: Q6.2 - Actions 3 & 5 (xZipText, xDirText): extracted text bundle.
Q6_2_EXTRACTED_TEXT = """
SELECT
    p.id AS file_id,
    p.file_name AS name,
    p.file_type AS type,
    s.name AS source,
    p.file_date AS date,
    si.name AS side,
    p.file_status AS status,
    COALESCE(
        (SELECT STRING_AGG(cr.content, '' ORDER BY cr.chunk_seq)
         FROM contents_raw cr
         WHERE cr.hash_id = hc.hash_id),
        ''
    ) AS extracted_text
FROM paths p
JOIN hash_contexts hc ON hc.id = p.context_id
LEFT JOIN sources s ON s.id = hc.source_id
LEFT JOIN sides si ON si.id = hc.side_id
WHERE p.id = ANY(%(target_path_ids)s::int[])
ORDER BY p.id ASC
"""

#: Q6.3 - Actions 4, 6, 9, 10 (xZipOrig, xDirOrig, xOpen, xLocate).
Q6_3_ORIGINAL_PATHS = """
SELECT
    p.id AS file_id,
    p.file_name AS name,
    p.file_path AS path,
    p.file_size AS size,
    p.file_type AS type,
    p.file_date AS date,
    s.name AS source,
    si.name AS side
FROM paths p
JOIN hash_contexts hc ON hc.id = p.context_id
LEFT JOIN sources s ON s.id = hc.source_id
LEFT JOIN sides si ON si.id = hc.side_id
WHERE p.id = ANY(%(target_path_ids)s::int[])
ORDER BY p.id ASC
"""

#: Q6.4 - Action 7 (xStruct): structured export with strongest term taxonomy.
Q6_4_STRUCTURED = """
WITH term_matches AS (
    SELECT
        wh.hash_id,
        COALESCE(w_main.word, w_sub.word) AS category_name,
        w_sub.word AS subcategory_name,
        wh.word_count AS hits
    FROM words_hashs wh
    JOIN words_categorys wc ON wc.word_id = wh.word_id
    JOIN categorys sub_c ON sub_c.id = wc.category_id
    JOIN words w_sub ON w_sub.id = sub_c.word_id
    LEFT JOIN categorys main_c ON main_c.id = sub_c.parent_id
    LEFT JOIN words w_main ON w_main.id = main_c.word_id
    WHERE (%(word_ids)s::int[] IS NULL OR wh.word_id = ANY(%(word_ids)s::int[]))
    UNION ALL
    SELECT
        kh.hash_id,
        COALESCE(w_main.word, w_sub.word) AS category_name,
        w_sub.word AS subcategory_name,
        kh.word_count AS hits
    FROM keywords_hashs kh
    JOIN keywords k ON k.id = kh.keyword_id
    JOIN categorys sub_c ON sub_c.id = k.category_id
    JOIN words w_sub ON w_sub.id = sub_c.word_id
    LEFT JOIN categorys main_c ON main_c.id = sub_c.parent_id
    LEFT JOIN words w_main ON w_main.id = main_c.word_id
    WHERE (%(keyword_ids)s::int[] IS NULL OR kh.keyword_id = ANY(%(keyword_ids)s::int[]))
),
strongest_term AS (
    SELECT DISTINCT ON (tm.hash_id)
        tm.hash_id,
        tm.category_name,
        tm.subcategory_name,
        tm.hits
    FROM term_matches tm
    ORDER BY tm.hash_id, tm.hits DESC, tm.category_name ASC, tm.subcategory_name ASC
)
SELECT
    p.id AS file_id,
    p.file_name AS name,
    p.file_path AS path,
    p.file_type AS type,
    p.file_date AS date,
    TO_CHAR(p.file_date, 'YYYY') AS year_seg,
    TO_CHAR(p.file_date, 'YYYY-MM') AS year_month_seg,
    COALESCE(s.name, 'Unknown') AS source,
    COALESCE(si.name, 'Unknown') AS side,
    COALESCE(st.category_name, 'Uncategorised') AS primary_category,
    COALESCE(st.subcategory_name, 'Uncategorised') AS primary_subcategory
FROM paths p
JOIN hash_contexts hc ON hc.id = p.context_id
LEFT JOIN sources s ON s.id = hc.source_id
LEFT JOIN sides si ON si.id = hc.side_id
LEFT JOIN strongest_term st ON st.hash_id = hc.hash_id
WHERE p.id = ANY(%(target_path_ids)s::int[])
ORDER BY p.id ASC
"""

#: Q6.5 (a) - Action 8 (xRename): persist the rename.
Q6_5_RENAME_UPDATE = """
UPDATE paths
SET file_name = %(new_file_name)s,
    file_path = %(new_file_path)s,
    status_updated_at = NOW()
WHERE id = %(path_id)s
RETURNING id, file_name, file_path
"""

#: Q6.5 (b) - Action 8: record the path revision beside the rename.
Q6_5_RENAME_REVISION = """
INSERT INTO path_revisions (path_id, changed_by, change_kind, old_values, new_values)
VALUES (
    %(path_id)s,
    %(user_id)s,
    'modified',
    jsonb_build_object('file_name', %(old_file_name)s, 'file_path', %(old_file_path)s),
    jsonb_build_object('file_name', %(new_file_name)s, 'file_path', %(new_file_path)s)
)
"""

#: Q6.6 - Action 11 & standard exporter (xColumns/buildExport): all columns.
Q6_6_COLUMNS = """
WITH file_term_matches AS (
    SELECT
        wh.hash_id,
        wh.word_count AS hits,
        w.word AS term_label,
        '[Word] ' || COALESCE(w_main.word || ' › ', '') || w_sub.word || ' › ' || w.word AS taxonomy_path
    FROM words_hashs wh
    JOIN words_categorys wc ON wc.word_id = wh.word_id
    JOIN words w ON w.id = wc.word_id
    JOIN categorys sub_c ON sub_c.id = wc.category_id
    JOIN words w_sub ON w_sub.id = sub_c.word_id
    LEFT JOIN categorys main_c ON main_c.id = sub_c.parent_id
    LEFT JOIN words w_main ON w_main.id = main_c.word_id
    WHERE (%(word_ids)s::int[] IS NULL OR wh.word_id = ANY(%(word_ids)s::int[]))
),
file_match_rollup AS (
    SELECT
        ftm.hash_id,
        SUM(ftm.hits)::int AS hits,
        STRING_AGG(DISTINCT ftm.term_label, '; ' ORDER BY ftm.term_label) AS matched,
        STRING_AGG(DISTINCT ftm.taxonomy_path, ' | ' ORDER BY ftm.taxonomy_path) AS paths
    FROM file_term_matches ftm
    GROUP BY ftm.hash_id
)
SELECT
    p.id AS id,
    p.file_name AS name,
    p.file_type AS type,
    p.file_size AS size,
    COALESCE(s.name, 'Unknown') AS source,
    CASE
        WHEN EXISTS (SELECT 1 FROM contents_raw cr WHERE cr.hash_id = hc.hash_id)
          OR EXISTS (SELECT 1 FROM contents c WHERE c.hash_id = hc.hash_id)
        THEN 'Indexed'
        ELSE 'No text'
    END AS status,
    p.processing_status AS processing_status,
    COALESCE(fmr.paths, '') AS path,
    p.file_date AS date,
    h.hash AS hash,
    COALESCE(si.name, 'Unknown') AS side,
    COALESCE(fmr.hits, 0) AS hits,
    COALESCE(fmr.matched, '') AS matched
FROM paths p
JOIN hash_contexts hc ON hc.id = p.context_id
JOIN hashs h ON h.id = hc.hash_id
LEFT JOIN sources s ON s.id = hc.source_id
LEFT JOIN sides si ON si.id = hc.side_id
LEFT JOIN file_match_rollup fmr ON fmr.hash_id = hc.hash_id
WHERE p.id = ANY(%(target_path_ids)s::int[])
ORDER BY COALESCE(fmr.hits, 0) DESC, p.file_date DESC, p.id DESC
"""


# ============================================================================
# 7. MANAGE MODAL - TAB 1: DASHBOARD & DATA HEALTH (tabOverview)
# ============================================================================

#: Q7.1 - dashboard KPIs (categories, words, keywords, files, coverage, views).
Q7_1_DASHBOARD_KPIS = """
WITH term_usage AS (
    SELECT wc.word_id AS id, 'word' AS kind,
           EXISTS (
               SELECT 1
               FROM words_hashs wh
               JOIN hash_contexts hc ON hc.hash_id = wh.hash_id
               JOIN paths p ON p.context_id = hc.id
               WHERE wh.word_id = wc.word_id
           ) AS has_files
    FROM (SELECT DISTINCT word_id FROM words_categorys) wc
    UNION ALL
    SELECT k.id AS id, 'keyword' AS kind,
           EXISTS (
               SELECT 1
               FROM keywords_hashs kh
               JOIN hash_contexts hc ON hc.hash_id = kh.hash_id
               JOIN paths p ON p.context_id = hc.id
               WHERE kh.keyword_id = k.id
           ) AS has_files
    FROM keywords k
)
SELECT
    (SELECT COUNT(*) FROM categorys WHERE parent_id IS NULL) AS categories_count,
    (SELECT COUNT(*) FROM categorys WHERE parent_id IS NOT NULL) AS subcategories_count,
    (SELECT COUNT(DISTINCT word_id) FROM words_categorys) AS words_count,
    (SELECT COUNT(*) FROM keywords) AS keywords_count,
    (SELECT COUNT(*) FROM paths) AS files_count,
    CASE
        WHEN COUNT(*) > 0
        THEN ROUND(100.0 * COUNT(*) FILTER (WHERE has_files) / COUNT(*))::int
        ELSE 0
    END AS coverage_pct,
    (SELECT COUNT(*) FROM saved_searches WHERE owner_user_id = %(user_id)s OR owner_user_id IS NULL) AS saved_views_count
FROM term_usage
"""

#: Q7.2 - files by type bar chart.
Q7_2_FILES_BY_TYPE = """
SELECT
    COALESCE(NULLIF(BTRIM(p.file_type), ''), 'Unknown') AS file_type,
    COUNT(*) AS file_count
FROM paths p
GROUP BY COALESCE(NULLIF(BTRIM(p.file_type), ''), 'Unknown')
ORDER BY file_count DESC, file_type ASC
"""

#: Q7.3 - files by side bar chart.
Q7_3_FILES_BY_SIDE = """
SELECT
    si.name AS side_name,
    COUNT(DISTINCT p.id) AS file_count
FROM sides si
JOIN hash_contexts hc ON hc.side_id = si.id
JOIN paths p ON p.context_id = hc.id
GROUP BY si.id, si.name
HAVING COUNT(DISTINCT p.id) > 0
ORDER BY file_count DESC, si.name ASC
"""

#: Q7.4 - data health: terms with zero matching files.
Q7_4_ORPHAN_TERMS = """
SELECT
    'words' AS lens,
    w.id AS term_id,
    w.word AS label,
    NULL::bytea AS keyword_blob,
    wc.category_id
FROM words_categorys wc
JOIN words w ON w.id = wc.word_id
WHERE NOT EXISTS (
    SELECT 1
    FROM words_hashs wh
    JOIN hash_contexts hc ON hc.hash_id = wh.hash_id
    JOIN paths p ON p.context_id = hc.id
    WHERE wh.word_id = wc.word_id
)
UNION ALL
SELECT
    'keywords' AS lens,
    k.id AS term_id,
    NULL::text AS label,
    k.keyword AS keyword_blob,
    k.category_id
FROM keywords k
WHERE NOT EXISTS (
    SELECT 1
    FROM keywords_hashs kh
    JOIN hash_contexts hc ON hc.hash_id = kh.hash_id
    JOIN paths p ON p.context_id = hc.id
    WHERE kh.keyword_id = k.id
)
ORDER BY lens ASC, term_id ASC
LIMIT 30
"""

#: Q7.5 - data health: files no taxonomy term matches.
Q7_5_UNCATEGORISED_FILES = """
SELECT
    p.id,
    p.file_name,
    p.file_type,
    p.file_date,
    COUNT(*) OVER() AS total_uncategorised
FROM paths p
JOIN hash_contexts hc ON hc.id = p.context_id
WHERE NOT EXISTS (
    SELECT 1
    FROM words_hashs wh
    JOIN words_categorys wc ON wc.word_id = wh.word_id
    WHERE wh.hash_id = hc.hash_id
)
AND NOT EXISTS (
    SELECT 1
    FROM keywords_hashs kh
    WHERE kh.hash_id = hc.hash_id
)
ORDER BY p.date_creation DESC, p.id DESC
LIMIT 8
"""


# ============================================================================
# 8. MANAGE MODAL - TAB 2: TAXONOMY CRUD
# ============================================================================

#: Q8.1 - create or get a word for a category/subcategory/term name.
Q8_1_UPSERT_WORD = """
INSERT INTO words (word)
VALUES (%(word)s)
ON CONFLICT (word) DO UPDATE SET word = EXCLUDED.word
RETURNING id
"""

#: Q8.1b - read a word id without creating one (used by rename previews).
Q8_1_FIND_WORD = "SELECT id FROM words WHERE word = %(word)s"

#: Q8.2 - create a main category (parent_id IS NULL).
Q8_2_CREATE_MAIN_CATEGORY = """
INSERT INTO categorys (word_id, parent_id)
VALUES (%(word_id)s, NULL)
RETURNING id
"""

#: Q8.3 - create a subcategory (enforced by trg_categorys_hierarchy_check).
Q8_3_CREATE_SUBCATEGORY = """
INSERT INTO categorys (word_id, parent_id)
VALUES (%(word_id)s, %(parent_category_id)s)
RETURNING id
"""

#: Q8.4 - rename a category or subcategory (point categorys.word_id at a word).
Q8_4_RENAME_CATEGORY = """
UPDATE categorys
SET word_id = %(new_word_id)s
WHERE id = %(category_id)s
RETURNING id
"""

#: Q8.5 - upsert a category colour in analyst_categories.
Q8_5_UPSERT_CATEGORY_COLOR = """
INSERT INTO analyst_categories (name, color, created_by)
VALUES (%(name)s, %(color)s, %(user_id)s)
ON CONFLICT (LOWER(name)) DO UPDATE SET color = EXCLUDED.color
RETURNING id
"""

#: Q8.6 (a) - delete a main category: subcategories first (ON DELETE RESTRICT).
Q8_6_DELETE_SUBCATEGORIES_OF = "DELETE FROM categorys WHERE parent_id = %(main_category_id)s"

#: Q8.6 (b) - delete the main category itself.
Q8_6_DELETE_MAIN_CATEGORY = "DELETE FROM categorys WHERE id = %(main_category_id)s"

#: Q8.7 - delete a subcategory.
Q8_7_DELETE_SUBCATEGORY = "DELETE FROM categorys WHERE id = %(subcategory_id)s"

#: Q8.8 - check for a duplicate word term across the taxonomy.
Q8_8_FIND_DUPLICATE_TERM = """
SELECT
    wc.word_id,
    wc.category_id AS sub_id,
    w_sub.word AS sub_name,
    main_c.id AS cat_id,
    w_main.word AS cat_name
FROM words_categorys wc
JOIN words w ON w.id = wc.word_id
JOIN categorys sub_c ON sub_c.id = wc.category_id
JOIN words w_sub ON w_sub.id = sub_c.word_id
LEFT JOIN categorys main_c ON main_c.id = sub_c.parent_id
LEFT JOIN words w_main ON w_main.id = main_c.word_id
WHERE LOWER(w.word) = LOWER(%(label)s)
LIMIT 1
"""

#: Q8.9 - add a word term to a subcategory.
Q8_9_ADD_WORD_TERM = """
INSERT INTO words_categorys (word_id, category_id)
VALUES (%(word_id)s, %(subcategory_id)s)
ON CONFLICT (word_id, category_id) DO NOTHING
RETURNING word_id, category_id
"""

#: Q8.10 (a) - edit a word term: remove the old link.
Q8_10_REMOVE_OLD_WORD_TERM = """
DELETE FROM words_categorys
WHERE word_id = %(old_word_id)s AND category_id = %(subcategory_id)s
"""

#: Q8.10 (b) - edit a word term: create the new link.
Q8_10_ADD_NEW_WORD_TERM = """
INSERT INTO words_categorys (word_id, category_id)
VALUES (%(new_word_id)s, %(subcategory_id)s)
ON CONFLICT (word_id, category_id) DO NOTHING
"""

#: Q8.11 - remove a word term from a subcategory.
Q8_11_REMOVE_WORD_TERM = """
DELETE FROM words_categorys
WHERE word_id = %(word_id)s AND category_id = %(subcategory_id)s
"""

#: Q8.12 - add a keyword expression to a subcategory.
Q8_12_ADD_KEYWORD = """
INSERT INTO keywords (keyword, category_id)
VALUES (%(keyword_blob)s, %(subcategory_id)s)
RETURNING id
"""

#: Q8.13 (a) - edit a keyword: clear stale matches.
Q8_13_CLEAR_KEYWORD_MATCHES = "DELETE FROM keywords_hashs WHERE keyword_id = %(keyword_id)s"

#: Q8.13 (b) - edit a keyword: update the expression and its category.
Q8_13_UPDATE_KEYWORD = """
UPDATE keywords
SET keyword = %(keyword_blob)s,
    category_id = %(subcategory_id)s
WHERE id = %(keyword_id)s
RETURNING id
"""

#: Q8.14 - delete a keyword expression (cascades to keywords_hashs).
Q8_14_DELETE_KEYWORD = "DELETE FROM keywords WHERE id = %(keyword_id)s"


# ============================================================================
# 9. MANAGE MODAL - TAB 3: FILE MANAGER & BULK OPERATIONS (tabFiles)
# ============================================================================

#: Q9.1 - list & search files in the file manager (snippet, matched terms).
Q9_1_FILE_MANAGER = """
WITH file_term_counts AS (
    SELECT
        hc.id AS context_id,
        (
            COUNT(DISTINCT wc.word_id) +
            COUNT(DISTINCT kh.keyword_id)
        )::int AS term_count
    FROM hash_contexts hc
    LEFT JOIN words_hashs wh ON wh.hash_id = hc.hash_id
    LEFT JOIN words_categorys wc ON wc.word_id = wh.word_id
    LEFT JOIN keywords_hashs kh ON kh.hash_id = hc.hash_id
    GROUP BY hc.id
)
SELECT
    p.id AS id,
    p.file_name AS name,
    p.file_type AS type,
    s.name AS source,
    p.file_date AS date,
    si.name AS side,
    p.file_status AS status,
    LEFT(COALESCE(cr.content, ''), 70) AS snippet,
    COALESCE(ftc.term_count, 0) AS matched_terms_count,
    COUNT(*) OVER() AS total_count
FROM paths p
JOIN hash_contexts hc ON hc.id = p.context_id
LEFT JOIN sources s ON s.id = hc.source_id
LEFT JOIN sides si ON si.id = hc.side_id
LEFT JOIN contents_raw cr ON cr.hash_id = hc.hash_id AND cr.chunk_seq = 0
LEFT JOIN file_term_counts ftc ON ftc.context_id = hc.id
WHERE (%(file_type)s::text IS NULL OR %(file_type)s = '' OR p.file_type = %(file_type)s)
  AND (
      %(mfq)s::text IS NULL
      OR %(mfq)s = ''
      OR p.file_name ILIKE '%%' || %(mfq)s || '%%'
      OR s.name ILIKE '%%' || %(mfq)s || '%%'
      OR si.name ILIKE '%%' || %(mfq)s || '%%'
      OR EXISTS (
          SELECT 1
          FROM contents_raw cr_search
          WHERE cr_search.hash_id = hc.hash_id
            AND cr_search.content ILIKE '%%' || %(mfq)s || '%%'
      )
  )
ORDER BY p.date_creation DESC, p.id DESC
LIMIT %(limit)s OFFSET %(offset)s
"""

#: Q9.2 (a) - add/ingest a file: content identity (one content).
Q9_2_UPSERT_HASH = """
INSERT INTO hashs (hash)
VALUES (%(sha256_hex)s)
ON CONFLICT (hash) DO UPDATE SET hash = EXCLUDED.hash
RETURNING id
"""

#: Q9.2 (b) - add/ingest a file: source.
Q9_2_UPSERT_SOURCE = """
INSERT INTO sources (name, job, importance, country, date_creation)
VALUES (%(source_name)s, 'Lexicon', 0.5000, 'Unknown', CURRENT_DATE)
ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
RETURNING id
"""

#: Q9.2 (c) - add/ingest a file: side.
Q9_2_UPSERT_SIDE = """
INSERT INTO sides (name, importance, date_creation)
VALUES (%(side_name)s, 0.5000, CURRENT_DATE)
ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
RETURNING id
"""

#: Q9.2 (d) - add/ingest a file: context (many contexts).
Q9_2_UPSERT_CONTEXT = """
INSERT INTO hash_contexts (hash_id, source_id, side_id, date_creation)
VALUES (%(hash_id)s, %(source_id)s, %(side_id)s, CURRENT_DATE)
ON CONFLICT (hash_id, source_id, side_id) DO UPDATE SET hash_id = EXCLUDED.hash_id
RETURNING id
"""

#: Q9.2 (e) - add/ingest a file: the occurrence path.
Q9_2_INSERT_PATH = """
INSERT INTO paths (
    file_name, file_path, file_size, file_type, file_status,
    file_date, date_creation, context_id, processing_status, status_updated_at
)
VALUES (
    %(file_name)s, %(file_path)s, %(file_size)s, %(file_type)s, 'Read',
    %(file_date)s, CURRENT_DATE, %(context_id)s, 'processed', NOW()
)
RETURNING id
"""

#: Q9.2 (f) - add/ingest a file: raw content chunk.
Q9_2_UPSERT_CONTENT = """
INSERT INTO contents_raw (hash_id, chunk_seq, content, char_count)
VALUES (%(hash_id)s, 0, %(content_text)s, LENGTH(%(content_text)s))
ON CONFLICT (hash_id, chunk_seq) DO UPDATE
SET content = EXCLUDED.content,
    char_count = EXCLUDED.char_count
"""

#: Q9.2 (g) - add/ingest a file: word term occurrences.
Q9_2_UPSERT_WORD_HITS = """
INSERT INTO words_hashs (hash_id, word_id, word_count, position_indexer)
VALUES (%(hash_id)s, %(word_id)s, %(word_count)s, %(position_indexer)s)
ON CONFLICT (hash_id, word_id) DO UPDATE
SET word_count = EXCLUDED.word_count,
    position_indexer = EXCLUDED.position_indexer
"""

#: Q9.2 (h) - add/ingest a file: keyword expression occurrences.
Q9_2_UPSERT_KEYWORD_HITS = """
INSERT INTO keywords_hashs (hash_id, keyword_id, word_count)
VALUES (%(hash_id)s, %(keyword_id)s, %(word_count)s)
ON CONFLICT (hash_id, keyword_id) DO UPDATE
SET word_count = EXCLUDED.word_count
"""

#: Q9.3 - duplicate a file record (shares context / canonical content).
Q9_3_DUPLICATE_PATH = """
INSERT INTO paths (
    file_name, file_path, file_size, file_type, file_status,
    file_date, date_creation, context_id, coordinates, extraction_provenance,
    processing_status, status_detail, attempts, parent_path_id, hierarchy_path,
    status_updated_at
)
SELECT
    %(copy_file_name)s, file_path, file_size, file_type, file_status,
    file_date, CURRENT_DATE, context_id, coordinates, extraction_provenance,
    processing_status, status_detail, attempts, parent_path_id, hierarchy_path,
    NOW()
FROM paths
WHERE id = %(source_path_id)s
RETURNING id
"""

#: Q9.4 - bulk update side on selected files (create/reuse the hash_context).
Q9_4_BULK_SET_SIDE = """
WITH target_paths AS (
    SELECT p.id AS path_id, hc.hash_id, hc.source_id
    FROM paths p
    JOIN hash_contexts hc ON hc.id = p.context_id
    WHERE p.id = ANY(%(path_ids)s::int[])
),
upserted_contexts AS (
    INSERT INTO hash_contexts (hash_id, source_id, side_id, date_creation)
    SELECT DISTINCT tp.hash_id, tp.source_id, %(new_side_id)s, CURRENT_DATE
    FROM target_paths tp
    ON CONFLICT (hash_id, source_id, side_id) DO UPDATE
    SET side_id = EXCLUDED.side_id
    RETURNING id AS context_id, hash_id, source_id
)
UPDATE paths p
SET context_id = uc.context_id,
    status_updated_at = NOW()
FROM target_paths tp
JOIN upserted_contexts uc
  ON uc.hash_id = tp.hash_id AND uc.source_id = tp.source_id
WHERE p.id = tp.path_id
RETURNING p.id
"""

#: Q9.5 - bulk delete selected files.
Q9_5_BULK_DELETE = """
DELETE FROM paths
WHERE id = ANY(%(path_ids)s::int[])
RETURNING id
"""


# ============================================================================
# 10. MANAGE MODAL - TAB 4: SAVED VIEWS (saved_searches)
# ============================================================================

#: Q10.1 - list saved views for the current user.
Q10_1_LIST_VIEWS = """
SELECT
    id,
    owner_user_id,
    name,
    query,
    filters,
    criteria,
    criteria_fingerprint,
    criteria_schema_version,
    created_at,
    updated_at,
    last_used_at
FROM saved_searches
WHERE owner_user_id = %(user_id)s OR owner_user_id IS NULL
ORDER BY created_at ASC, id ASC
"""

#: Q10.2 - save the current view (focus, basket, filters, side in filters/criteria).
Q10_2_SAVE_VIEW = """
INSERT INTO saved_searches (
    owner_user_id,
    name,
    query,
    filters,
    criteria,
    criteria_fingerprint,
    criteria_schema_version
)
VALUES (
    %(user_id)s,
    %(name)s,
    %(query)s,
    %(filters_json)s::jsonb,
    %(criteria_json)s::jsonb,
    %(criteria_fingerprint)s,
    %(criteria_schema_version)s
)
RETURNING id, name, filters, criteria, created_at
"""

#: Q10.3 - rename a saved view.
Q10_3_RENAME_VIEW = """
UPDATE saved_searches
SET name = %(new_name)s,
    updated_at = NOW()
WHERE id = %(view_id)s
  AND (owner_user_id = %(user_id)s OR owner_user_id IS NULL)
RETURNING id, name, updated_at
"""

#: Q10.4 - record that a view was applied (touch last_used_at).
Q10_4_TOUCH_VIEW = """
UPDATE saved_searches
SET last_used_at = NOW()
WHERE id = %(view_id)s
"""

#: Q10.5 - delete a saved view.
Q10_5_DELETE_VIEW = """
DELETE FROM saved_searches
WHERE id = %(view_id)s
  AND (owner_user_id = %(user_id)s OR owner_user_id IS NULL)
RETURNING id
"""


# ============================================================================
# 11. MANAGE MODAL - TAB 5: ACTIVITY LOG (audit_log)
# ============================================================================

#: Q11.1 - list activity log entries (most recent 300).
Q11_1_ACTIVITY = """
SELECT
    id,
    TO_CHAR(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI') AS t,
    username,
    action,
    resource,
    detail,
    COALESCE(detail->>'message', action || COALESCE(' · ' || resource, '')) AS msg
FROM audit_log
WHERE (%(user_id)s::int IS NULL OR user_id = %(user_id)s::int)
ORDER BY created_at DESC, id DESC
LIMIT 300
"""

#: Q11.2 - record an activity log entry (mutations, undo, redo, backup/restore).
Q11_2_RECORD_ACTIVITY = """
INSERT INTO audit_log (user_id, username, action, resource, detail, ip_address)
VALUES (
    %(user_id)s,
    %(username)s,
    %(action)s,
    %(resource)s,
    jsonb_build_object('message', %(message)s),
    %(ip_address)s
)
RETURNING id, created_at
"""


# ============================================================================
# The catalogue
# ============================================================================

#: key -> statement. Keys are the names the service layer uses; the docstrings
#: above carry the specification number.
ALL_QUERIES: Dict[str, str] = {
    name: value
    for name, value in sorted(globals().items())
    if name.startswith("Q") and isinstance(value, str)
}

#: Parameters every scoped statement shares (the sidebar filter envelope).
FILTER_PARAMS: Tuple[str, ...] = (
    "file_types", "source_ids", "side_ids", "date_from", "date_to", "q",
)

#: Parameters every scoped statement shares in the search surface, where the
#: source/side filters are given by name rather than by id.
SEARCH_FILTER_PARAMS: Tuple[str, ...] = (
    "file_types", "sources", "sides", "date_from", "date_to", "q",
)

__all__ = sorted(ALL_QUERIES) + ["ALL_QUERIES", "FILTER_PARAMS", "SEARCH_FILTER_PARAMS"]


# ---------------------------------------------------------------------------
# Supporting statements
# ---------------------------------------------------------------------------
# These exist so the statements above can be driven by the interface rather
# than by hand-written SQL in a route. Each is a projection of a statement in
# the specification and is named after it.

#: Q4.1 support - every word id, for the root and "Word galaxy" scopes.
Q4_1_SCOPE_ALL_WORD_IDS = "SELECT DISTINCT word_id FROM words_categorys"

#: Q4.1 support - every keyword id, for the root and "Keyword galaxy" scopes.
Q4_1_SCOPE_ALL_KEYWORD_IDS = "SELECT id FROM keywords"

#: Q2.8 support - the packed blobs whose phrase text Q2.8 rebuilds.
Q2_8_FETCH_KEYWORDS = "SELECT id, keyword FROM keywords WHERE id = ANY(%(keyword_ids)s::int[])"

#: Q9.2 support - taxonomy words present in an ingested text (lower-cased set).
Q9_2_LOOKUP_WORDS = """
SELECT id, word FROM words WHERE LOWER(word) = ANY(%(tokens)s::text[])
"""

#: Q9.2 support - every keyword expression, unpacked by the service so a
#: phrase is counted as a phrase (the blob itself carries no text).
Q9_2_LOOKUP_KEYWORDS = "SELECT id, keyword FROM keywords"


ALL_QUERIES.update({
    name: value
    for name, value in globals().items()
    if name.startswith("Q") and isinstance(value, str)
})
__all__ = sorted(ALL_QUERIES) + ["ALL_QUERIES", "FILTER_PARAMS", "SEARCH_FILTER_PARAMS"]
