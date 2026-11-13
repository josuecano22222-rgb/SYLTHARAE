"""Canonical search criteria and the single query compiler (Phase 0 spine).

See ``docs/implementation/CRITERIA_SPINE.md`` for the contract.
"""

from core.criteria.model import (  # noqa: F401
    CRITERIA_SCHEMA_VERSION,
    DEFAULT_ANALYST_SCOPE,
    Criteria,
    CriteriaError,
    SearchOptions,
    Sort,
    canonical_json,
    from_dict,
    from_legacy_search,
    legacy_unmapped_keys,
    normalize_analyst_scope,
    sha256_hex,
)
from core.criteria.compiler import (  # noqa: F401
    MAX_PAGE_SIZE,
    AccessScope,
    CompiledQuery,
    compile_criteria,
)
