"""Analytical measures: pure, deterministic functions over counts.

Nothing here reads the database, the clock or the environment. Every function
takes numbers and returns numbers (or ``None`` where the measure is not
defined for the input, which callers must report as *not measurable*, never
as zero). Each measure names its published definition; the thresholds used to
*interpret* the numbers live in ``core.analytics.thresholds`` with their
sources.

Measures
--------
* ``log_likelihood`` - G2 for one term in two corpora, the Read & Cressie form
  cited in Rayson & Garside (2000), as used by the UCREL log-likelihood
  calculator: ``G2 = 2 * (a ln(a/E1) + b ln(b/E2))`` with
  ``E1 = c(a+b)/(c+d)``, ``E2 = d(a+b)/(c+d)``; a zero cell contributes 0.
* ``log_ratio`` - Hardie (2014): ``log2((a/c) / (b/d))``; a zero frequency is
  replaced by 0.5 before normalising (the UCREL calculator's adjustment).
* ``tf_idf`` - term frequency times ``ln(N / df)`` (Sparck Jones 1972).
* ``hhi`` - Herfindahl-Hirschman index, sum of squared percentage shares
  (0-10,000).
* ``vocabulary_profile`` - tokens, types, type/token ratio, hapax legomena.
* ``cohen_kappa`` - Cohen (1960), agreement of two raters beyond chance.
* ``mann_kendall`` - Mann (1945) / Kendall (1975) trend statistic S, its
  variance with tie correction, and Z with continuity correction.
* ``theil_sen_slope`` - Sen (1968): median of pairwise slopes.
* ``log_dice`` - Rychly (2008): ``14 + log2(2 f_xy / (f_x + f_y))``.
* ``jaccard`` - |A intersect B| / |A union B|.
* ``coverage`` - measured units over all units, with the unmeasured counted.
"""

from __future__ import annotations

import math
from collections import Counter
from itertools import combinations
from statistics import median
from typing import Dict, Iterable, Mapping, Optional, Sequence, Tuple

__all__ = [
    "log_likelihood", "log_ratio", "tf_idf", "hhi", "vocabulary_profile",
    "cohen_kappa", "mann_kendall", "theil_sen_slope", "log_dice", "jaccard",
    "coverage",
]


def _nonneg(*values) -> None:
    for v in values:
        if v is None or isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 \
                or (isinstance(v, float) and not math.isfinite(v)):
            raise ValueError(f"counts must be finite non-negative numbers, got {v!r}")


def log_likelihood(a: float, b: float, c: float, d: float) -> Optional[float]:
    """G2 of one term: ``a``/``b`` its frequency in corpus 1/2, ``c``/``d``
    the corpus sizes (tokens). ``None`` when a corpus is empty (no expected
    value exists) or the term frequency exceeds its corpus size."""
    _nonneg(a, b, c, d)
    if c == 0 or d == 0 or a > c or b > d:
        return None
    total = c + d
    e1 = c * (a + b) / total
    e2 = d * (a + b) / total
    g2 = 0.0
    if a > 0:
        g2 += a * math.log(a / e1)
    if b > 0:
        g2 += b * math.log(b / e2)
    return 2.0 * g2


def log_ratio(a: float, b: float, c: float, d: float) -> Optional[float]:
    """Hardie's Log Ratio (binary log of the ratio of relative frequencies).
    ``None`` when a corpus is empty or the term occurs in neither."""
    _nonneg(a, b, c, d)
    if c == 0 or d == 0 or (a == 0 and b == 0):
        return None
    a_adj = a if a > 0 else 0.5
    b_adj = b if b > 0 else 0.5
    return math.log2((a_adj / c) / (b_adj / d))


def tf_idf(tf: float, df: int, n_documents: int) -> Optional[float]:
    """``tf * ln(N/df)``. ``None`` when the term occurs in no document or
    there are no documents (idf undefined)."""
    _nonneg(tf, df, n_documents)
    if n_documents == 0 or df == 0 or df > n_documents:
        return None
    return tf * math.log(n_documents / df)


def hhi(counts: Iterable[float]) -> Optional[float]:
    """Herfindahl-Hirschman index of the shares of ``counts`` on the
    0-10,000 scale (shares in percent, squared, summed). ``None`` for an
    empty total."""
    values = list(counts)
    _nonneg(*values)
    total = sum(values)
    if total == 0:
        return None
    return sum((100.0 * v / total) ** 2 for v in values)


def vocabulary_profile(frequencies: Mapping[str, int]) -> Dict[str, Optional[float]]:
    """Tokens, types, type/token ratio and hapax legomena of a frequency
    table. The ratios are ``None`` when there are no tokens/types."""
    values = list(frequencies.values())
    _nonneg(*values)
    tokens = sum(values)
    types = sum(1 for v in values if v > 0)
    hapax = sum(1 for v in values if v == 1)
    return {
        "tokens": tokens,
        "types": types,
        "type_token_ratio": (types / tokens) if tokens else None,
        "hapax": hapax,
        "hapax_ratio": (hapax / types) if types else None,
    }


def cohen_kappa(pairs: Iterable[Tuple[str, str]]) -> Optional[Dict[str, float]]:
    """Cohen's kappa for pairs ``(label_by_rater_1, label_by_rater_2)``.
    Returns ``{"kappa", "observed", "expected", "n"}``; ``None`` when there
    are no pairs or chance agreement is 1 (kappa undefined)."""
    pairs = list(pairs)
    n = len(pairs)
    if n == 0:
        return None
    observed = sum(1 for x, y in pairs if x == y) / n
    first = Counter(x for x, _ in pairs)
    second = Counter(y for _, y in pairs)
    expected = sum(first[k] * second.get(k, 0) for k in first) / (n * n)
    if expected == 1:
        return None
    return {"kappa": (observed - expected) / (1 - expected), "observed": observed,
            "expected": expected, "n": n}


def mann_kendall(values: Sequence[float]) -> Optional[Dict[str, float]]:
    """Mann-Kendall S, Var(S) with tie correction, and Z with continuity
    correction. ``None`` for fewer than 3 values (no test possible)."""
    xs = list(values)
    _nonneg(*[abs(v) for v in xs])
    n = len(xs)
    if n < 3:
        return None
    s = 0
    for i, j in combinations(range(n), 2):
        diff = xs[j] - xs[i]
        s += (diff > 0) - (diff < 0)
    ties = Counter(xs).values()
    var = (n * (n - 1) * (2 * n + 5) - sum(t * (t - 1) * (2 * t + 5) for t in ties)) / 18.0
    if var == 0:
        z = 0.0
    elif s > 0:
        z = (s - 1) / math.sqrt(var)
    elif s < 0:
        z = (s + 1) / math.sqrt(var)
    else:
        z = 0.0
    return {"s": s, "var_s": var, "z": z, "n": n}


def theil_sen_slope(values: Sequence[float]) -> Optional[float]:
    """Median of the slopes between every pair of equally spaced points.
    ``None`` for fewer than 2 values."""
    xs = list(values)
    if len(xs) < 2:
        return None
    return float(median((xs[j] - xs[i]) / (j - i) for i, j in combinations(range(len(xs)), 2)))


def log_dice(f_xy: float, f_x: float, f_y: float) -> Optional[float]:
    """logDice association (maximum 14). ``None`` when the pair never
    co-occurs (log of zero) or a frequency is zero."""
    _nonneg(f_xy, f_x, f_y)
    if f_xy == 0 or f_x == 0 or f_y == 0 or f_xy > min(f_x, f_y):
        return None
    return 14.0 + math.log2(2.0 * f_xy / (f_x + f_y))


def jaccard(left: Iterable, right: Iterable) -> Optional[float]:
    """Set overlap. ``None`` when both sets are empty."""
    a, b = set(left), set(right)
    union = a | b
    if not union:
        return None
    return len(a & b) / len(union)


def coverage(measured: int, total: int) -> Dict[str, Optional[float]]:
    """How much of a population a measure actually covers. ``share`` is
    ``None`` for an empty population, so an empty population is not 0 %."""
    _nonneg(measured, total)
    if measured > total:
        raise ValueError("measured units cannot exceed the population")
    return {"measured": measured, "unmeasured": total - measured, "total": total,
            "share": (measured / total) if total else None}
