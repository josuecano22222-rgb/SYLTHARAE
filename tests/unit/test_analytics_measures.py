"""Analytical measures against hand-computed oracles (core.analytics.measures).

Every expected value below is worked out by hand from the published
definition cited in the module, not produced by the code under test.
"""

import math

import pytest

from core.analytics import measures as m
from core.analytics import thresholds as th


# --------------------------------------------------------------- keyness

def test_log_likelihood_oracle():
    # a=100 of c=10,000; b=50 of d=20,000. E1 = 10000*150/30000 = 50,
    # E2 = 100. G2 = 2*(100 ln 2 + 50 ln 0.5) = 2*50 ln 2 = 100 ln 2.
    assert m.log_likelihood(100, 50, 10_000, 20_000) == pytest.approx(100 * math.log(2), rel=1e-12)
    assert m.log_likelihood(100, 50, 10_000, 20_000) == pytest.approx(69.31471805599453)


def test_log_likelihood_is_symmetric_and_zero_for_equal_rates():
    assert m.log_likelihood(10, 20, 1000, 2000) == pytest.approx(0.0, abs=1e-12)
    assert m.log_likelihood(100, 50, 10_000, 20_000) == pytest.approx(
        m.log_likelihood(50, 100, 20_000, 10_000))


def test_log_likelihood_zero_cell_contributes_nothing():
    # a=0: only b's term. E2 = d*b/(c+d) = 20*10/30; G2 = 2*10*ln(10/(200/30)).
    assert m.log_likelihood(0, 10, 10, 20) == pytest.approx(2 * 10 * math.log(10 / (200 / 30)))


@pytest.mark.parametrize("args", [(1, 1, 0, 10), (1, 1, 10, 0), (11, 1, 10, 10), (1, 11, 10, 10)])
def test_log_likelihood_not_measurable(args):
    assert m.log_likelihood(*args) is None


@pytest.mark.parametrize("bad", [-1, float("nan"), float("inf"), None, True, "3"])
def test_counts_are_validated(bad):
    with pytest.raises(ValueError):
        m.log_likelihood(bad, 1, 10, 10)


def test_log_ratio_oracle_and_zero_adjustment():
    # (100/10000)/(50/20000) = 4 -> log2 = 2.
    assert m.log_ratio(100, 50, 10_000, 20_000) == pytest.approx(2.0)
    # b = 0 is replaced by 0.5: (10/100)/(0.5/100) = 20.
    assert m.log_ratio(10, 0, 100, 100) == pytest.approx(math.log2(20))
    assert m.log_ratio(0, 0, 100, 100) is None
    assert m.log_ratio(1, 1, 0, 100) is None


# --------------------------------------------------------------- others

def test_tf_idf_oracle():
    assert m.tf_idf(3, 1, 10) == pytest.approx(3 * math.log(10))
    assert m.tf_idf(3, 10, 10) == 0.0          # in every document: no weight
    assert m.tf_idf(3, 0, 10) is None
    assert m.tf_idf(3, 1, 0) is None


def test_hhi_oracle():
    assert m.hhi([50, 30, 20]) == pytest.approx(3800.0)
    assert m.hhi([1]) == pytest.approx(10_000.0)
    assert m.hhi([1, 1, 1, 1]) == pytest.approx(2500.0)
    assert m.hhi([]) is None and m.hhi([0, 0]) is None


def test_vocabulary_profile():
    p = m.vocabulary_profile({"a": 3, "b": 1, "c": 1, "d": 0})
    assert p == {"tokens": 5, "types": 3, "type_token_ratio": 0.6, "hapax": 2,
                 "hapax_ratio": pytest.approx(2 / 3)}
    empty = m.vocabulary_profile({})
    assert empty["type_token_ratio"] is None and empty["hapax_ratio"] is None


def test_cohen_kappa_oracle():
    # 2x2 table yes/yes 20, yes/no 5, no/yes 10, no/no 15 (n=50):
    # po = 35/50 = 0.7; pe = (25*30 + 25*20)/2500 = 0.5; kappa = 0.4.
    pairs = ([("y", "y")] * 20 + [("y", "n")] * 5 + [("n", "y")] * 10 + [("n", "n")] * 15)
    k = m.cohen_kappa(pairs)
    assert k["observed"] == pytest.approx(0.7) and k["expected"] == pytest.approx(0.5)
    assert k["kappa"] == pytest.approx(0.4) and k["n"] == 50
    assert th.kappa_band(k["kappa"]) == "fair"
    assert m.cohen_kappa([]) is None
    assert m.cohen_kappa([("a", "a")] * 3) is None      # pe = 1: undefined


def test_mann_kendall_oracle():
    # 1..10 strictly increasing: S = C(10,2) = 45; Var = 10*9*25/18 = 125;
    # Z = (45-1)/sqrt(125).
    r = m.mann_kendall(list(range(1, 11)))
    assert r["s"] == 45 and r["var_s"] == pytest.approx(125.0)
    assert r["z"] == pytest.approx(44 / math.sqrt(125))
    assert abs(r["z"]) > th.MK_Z05.value
    assert m.mann_kendall([1, 2]) is None


def test_mann_kendall_tie_correction():
    # [1,1,2]: pairs (1,1)=0, (1,2)=+1, (1,2)=+1 -> S=2. One tie group of
    # size 2: Var = (3*2*11 - 2*1*9)/18 = (66-18)/18.
    r = m.mann_kendall([1, 1, 2])
    assert r["s"] == 2 and r["var_s"] == pytest.approx(48 / 18)
    flat = m.mann_kendall([5, 5, 5])
    assert flat["s"] == 0 and flat["z"] == 0.0


def test_theil_sen_slope():
    assert m.theil_sen_slope(list(range(1, 11))) == 1.0
    assert m.theil_sen_slope([0, 10, 2, 3]) == pytest.approx(1.0)  # robust to one outlier
    assert m.theil_sen_slope([4]) is None


def test_log_dice_oracle():
    assert m.log_dice(10, 20, 20) == pytest.approx(13.0)
    assert m.log_dice(20, 20, 20) == pytest.approx(14.0)    # the maximum
    assert m.log_dice(0, 20, 20) is None
    assert m.log_dice(30, 20, 40) is None                   # impossible counts


def test_jaccard_and_coverage():
    assert m.jaccard({1, 2, 3}, {2, 3, 4}) == 0.5
    assert m.jaccard(set(), set()) is None
    assert m.coverage(3, 4) == {"measured": 3, "unmeasured": 1, "total": 4, "share": 0.75}
    assert m.coverage(0, 0)["share"] is None, "an empty population is not 0 %"
    with pytest.raises(ValueError):
        m.coverage(5, 4)


# --------------------------------------------------------------- thresholds

def test_every_threshold_names_a_source():
    for t in th.ALL:
        assert t.source.strip() and t.meaning.strip(), t.key
    assert th.HHI_HIGH.caveat, "HHI bands are borrowed from market analysis: say so"


def test_ll_levels():
    assert th.ll_level(None) == "not_measurable"
    assert th.ll_level(3.0) == "none"
    assert th.ll_level(3.84) == "ll_p05"
    assert th.ll_level(15.13) == "ll_p0001"
    assert th.ll_level(100 * math.log(2)) == "ll_p0001"
