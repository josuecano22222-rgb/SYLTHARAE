"""Interpretation thresholds, each with its source.

A number becomes a finding only by comparison with a threshold, and a
threshold nobody can trace is an opinion. Every constant here names where it
comes from and what it does and does not mean. Narrative templates cite the
``source`` of the threshold they apply, so a reader can check it.

Changing a value or adding one changes the meaning of the analyses that use
it: the value is part of each analysis fingerprint (``core.analytics.kinds``),
so the report lock refuses the change under an existing version.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple


@dataclass(frozen=True)
class Threshold:
    key: str
    value: float
    meaning: str        # what crossing it means, in reviewer prose
    source: str         # citation shown to readers
    caveat: str = ""    # where applying it here departs from its origin


#: Chi-square critical values, 1 degree of freedom, used for log-likelihood
#: G2 in corpus comparison (UCREL log-likelihood calculator; Rayson, Berridge
#: & Francis 2004, who recommend the 0.01 % value 15.13 as reliable for the
#: frequencies typical of word comparisons).
LL_P05 = Threshold("ll_p05", 3.84, "p < 0.05 for one comparison",
                   "Rayson & Garside (2000); UCREL log-likelihood calculator")
LL_P01 = Threshold("ll_p01", 6.63, "p < 0.01 for one comparison",
                   "Rayson & Garside (2000); UCREL log-likelihood calculator")
LL_P001 = Threshold("ll_p001", 10.83, "p < 0.001 for one comparison",
                    "Rayson & Garside (2000); UCREL log-likelihood calculator")
LL_P0001 = Threshold(
    "ll_p0001", 15.13, "p < 0.0001; the level recommended for word-frequency comparison",
    "Rayson, Berridge & Francis (2004), JADT 2004, pp. 926-936",
    caveat="Every term is tested, so many terms cross the lower levels by chance; "
           "only this level is treated as a finding.")

#: Log Ratio of 1 = the term is twice as frequent (relative) in one corpus.
LOG_RATIO_DOUBLE = Threshold(
    "log_ratio_double", 1.0, "at least twice as frequent, relative to corpus size",
    "Hardie (2014), 'Log Ratio: an informal introduction', CASS, Lancaster University",
    caveat="An effect-size convention, not a significance test.")

#: HHI bands of the 2023 U.S. Merger Guidelines (DOJ/FTC), section 2.1.
HHI_HIGH = Threshold(
    "hhi_high", 1800.0, "highly concentrated (HHI above 1,800)",
    "U.S. Department of Justice & Federal Trade Commission, Merger Guidelines (2023), 2.1",
    caveat="Defined for market shares; applied here to shares of documents or "
           "occurrences by analogy only.")

#: Landis & Koch (1977) agreement bands for kappa (the authors call them
#: arbitrary; they are reported as a convention).
KAPPA_BANDS: Tuple[Tuple[float, str], ...] = (
    (0.81, "almost perfect"), (0.61, "substantial"), (0.41, "moderate"),
    (0.21, "fair"), (0.0, "slight"),
)
KAPPA_SOURCE = "Landis & Koch (1977), Biometrics 33(1), 159-174"
KAPPA_CAVEAT = "The authors describe these divisions as arbitrary."

#: |Z| above 1.96: two-sided p < 0.05 under the normal approximation of the
#: Mann-Kendall statistic, which Gilbert (1987) advises only from n >= 10.
MK_Z05 = Threshold("mk_z05", 1.96, "two-sided p < 0.05",
                   "Mann (1945); Kendall (1975); normal approximation per Gilbert (1987)")
MK_MIN_N = Threshold("mk_min_n", 10, "smallest series for the normal approximation",
                     "Gilbert (1987), Statistical Methods for Environmental "
                     "Pollution Monitoring, 17.3")

ALL: Tuple[Threshold, ...] = (LL_P05, LL_P01, LL_P001, LL_P0001, LOG_RATIO_DOUBLE,
                              HHI_HIGH, MK_Z05, MK_MIN_N)
BY_KEY: Dict[str, Threshold] = {t.key: t for t in ALL}

#: The G2 levels, strongest first, as (threshold, label) for classification.
LL_LEVELS: Tuple[Threshold, ...] = (LL_P0001, LL_P001, LL_P01, LL_P05)


def ll_level(g2) -> str:
    """The strongest level ``g2`` reaches: a threshold key, or ``"none"``."""
    if g2 is None:
        return "not_measurable"
    for t in LL_LEVELS:
        if g2 >= t.value:
            return t.key
    return "none"


def kappa_band(kappa) -> str:
    if kappa is None:
        return "not_measurable"
    if kappa < 0:
        return "poor"
    for floor, label in KAPPA_BANDS:
        if kappa >= floor:
            return label
    return "slight"
