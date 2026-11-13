"""Shared search routing enumerations."""

from __future__ import annotations

from enum import Enum


class SearchComplexity(str, Enum):
    TRIVIAL = "TRIVIAL"
    FAST = "FAST"
    NORMAL = "NORMAL"
    EXPENSIVE = "EXPENSIVE"
    VERY_EXPENSIVE = "VERY_EXPENSIVE"


class CountStrategy(str, Enum):
    EXACT = "EXACT"
    TIMED = "TIMED"
    SKIP = "SKIP"
    APPROXIMATE = "APPROXIMATE"


class RankStrategy(str, Enum):
    SQL_RELEVANCE = "SQL_RELEVANCE"
    BM25_PYTHON = "BM25_PYTHON"
    NONE = "NONE"


class PaginationStrategy(str, Enum):
    KEYSET = "KEYSET"
    OFFSET = "OFFSET"


class SearchEngineRoute(str, Enum):
    PG_METADATA = "PG_METADATA"
    PG_INVERTED = "PG_INVERTED"
    PG_LITERAL = "PG_LITERAL"
    RG_EXTRACTED = "RG_EXTRACTED"
    HYBRID_PG_THEN_RG = "HYBRID_PG_THEN_RG"
    HYBRID_RG_THEN_PG = "HYBRID_RG_THEN_PG"


class SearchClass(str, Enum):
    METADATA = "METADATA"
    INDEXED_TERM = "INDEXED_TERM"
    RAW_FILESYSTEM = "RAW_FILESYSTEM"
    REGEX_PCRE = "REGEX_PCRE"
    HYBRID = "HYBRID"
