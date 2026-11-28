"""A/B statistics: reference values and decision rules."""

from __future__ import annotations

import math

import pytest

from lifecycle_engine.experiments.stats import (
    analyze_experiment,
    chi_square_sf,
    holm,
    normal_ppf,
    probability_to_beat,
    required_sample_size,
    two_proportion_test,
    wilson_interval,
)
from lifecycle_engine.schemas.experiments import Decision, ExperimentDesign, VariantInput as V


# --- reference values (published tables / calculators) ---------------------------------

def test_reference_values():
    assert normal_ppf(0.975) == pytest.approx(1.959964, abs=1e-6)
    assert normal_ppf(0.8) == pytest.approx(0.841621, abs=1e-6)
    assert normal_ppf(1e-5) == pytest.approx(-4.264891, abs=1e-5)
    assert chi_square_sf(3.841459, 1) == pytest.approx(0.05, abs=1e-6)
    assert chi_square_sf(5.991465, 2) == pytest.approx(0.05, abs=1e-6)
    assert chi_square_sf(20.0, 3) == pytest.approx(1.6974e-4, rel=1e-3)
    assert chi_square_sf(0.0, 2) == 1.0
    z, p = two_proportion_test(200, 2000, 250, 2000)
    assert z == pytest.approx(2.50196, abs=1e-4) and p == pytest.approx(0.01235, abs=1e-4)
    assert required_sample_size(0.05, 0.10, 0.05, 0.8) == 31234          # Evan Miller's calculator
    lo, hi = wilson_interval(10, 100)
    assert (lo, hi) == (pytest.approx(0.05523, abs=1e-4), pytest.approx(0.17437, abs=1e-4))
    assert holm([0.01, 0.04, 0.03]) == [pytest.approx(0.03), pytest.approx(0.06), pytest.approx(0.06)]


def test_probability_to_beat_is_symmetric_and_sensible():
    assert probability_to_beat(100, 1000, 100, 1000) == pytest.approx(0.5)
    assert probability_to_beat(150, 1000, 100, 1000) > 0.99
    assert probability_to_beat(100, 1000, 150, 1000) < 0.01


# --- decisions -----------------------------------------------------------------------------

def run(control, treatments, **design):
    variants = [V(key="control", is_control=True, exposures=control[0], conversions=control[1], expected_share=50)]
    for i, (n, c) in enumerate(treatments):
        variants.append(V(key=f"v{i + 1}", exposures=n, conversions=c, expected_share=50 / len(treatments) if len(treatments) > 1 else 50))
    if len(treatments) > 1:
        variants[0] = variants[0].model_copy(update={"expected_share": 50})
    return analyze_experiment(variants, ExperimentDesign(**design))


def test_not_enough_data_keeps_running():
    a = run((40, 4), [(42, 7)])
    assert a.decision == Decision.KEEP_RUNNING and a.winner is None
    assert "at least 100 exposures" in a.summary


def test_winner_only_at_the_planned_sample_size():
    # 10% vs 12% with 4000/arm: significant, and the planned size for MDE 20% (~3.8k) is reached.
    a = run((4000, 400), [(4000, 480)], minimum_detectable_effect=0.2)
    v1 = a.variants[1]
    assert v1.required_exposures <= 4000
    assert a.decision == Decision.WINNER and a.winner == "v1"
    assert v1.significant and v1.p_value_adjusted < 0.05 and v1.absolute_lift == pytest.approx(0.02)
    assert v1.lift_ci_low > 0 and v1.probability_to_beat_control > 0.99
    assert a.sample_ratio.checked and not a.sample_ratio.mismatch


def test_significant_early_is_trending_not_a_winner():
    # Same lift but only 1500/arm, below the planned ~3.8k: don't call it.
    a = run((1500, 150), [(1500, 200)], minimum_detectable_effect=0.2)
    assert a.variants[1].significant
    assert a.decision == Decision.TRENDING and a.winner is None
    assert any("planned sample size" in w for w in a.warnings)
    assert 0 < a.progress < 1


def test_no_difference_at_planned_size():
    a = run((5000, 500), [(5000, 505)], minimum_detectable_effect=0.2)
    assert a.decision == Decision.NO_SIGNIFICANT_DIFFERENCE and a.winner is None


def test_control_better():
    a = run((5000, 600), [(5000, 480)], minimum_detectable_effect=0.2)
    assert a.decision == Decision.CONTROL_BETTER and a.winner == "control"


def test_sample_ratio_mismatch_invalidates_results():
    # Planned 50/50 but 6000 vs 4000 exposures: classic SRM.
    a = run((6000, 600), [(4000, 600)])
    assert a.sample_ratio.mismatch and a.sample_ratio.p_value < 0.001
    assert a.decision == Decision.INVALID and a.winner is None


def test_holm_correction_with_several_variants():
    # v2 alone would be "significant" at p~0.03, but not after correcting for two comparisons.
    a = run((3000, 300), [(1500, 150), (1500, 185)], minimum_detectable_effect=0.5)
    v2 = next(v for v in a.variants if v.key == "v2")
    assert v2.p_value < 0.05 and v2.p_value_adjusted >= v2.p_value
    assert v2.significant == (v2.p_value_adjusted < 0.05)


@pytest.mark.parametrize("variants,needle", [
    ([V(key="a", is_control=True, exposures=1, conversions=0)], "at least two"),
    ([V(key="a", exposures=1, conversions=0), V(key="b", exposures=1, conversions=0)], "exactly one"),
    ([V(key="a", is_control=True, exposures=1, conversions=0), V(key="a", exposures=1, conversions=0)], "unique"),
])
def test_invalid_designs(variants, needle):
    a = analyze_experiment(variants)
    assert a.decision == Decision.INVALID and needle in a.summary


def test_conversions_above_exposures_are_capped_with_warning():
    a = analyze_experiment([V(key="c", is_control=True, exposures=200, conversions=20),
                            V(key="t", exposures=200, conversions=250)])
    assert a.variants[1].conversions == 200
    assert any("more conversions than exposures" in w for w in a.warnings)


def test_zero_baseline_does_not_crash():
    a = analyze_experiment([V(key="c", is_control=True, exposures=500, conversions=0),
                            V(key="t", exposures=500, conversions=0)])
    assert a.decision in (Decision.KEEP_RUNNING, Decision.NO_SIGNIFICANT_DIFFERENCE)
    assert any("0% or 100%" in w for w in a.warnings)
    assert not math.isnan(a.variants[1].p_value)


def test_srm_skipped_without_planned_shares():
    a = analyze_experiment([V(key="c", is_control=True, exposures=300, conversions=30),
                            V(key="t", exposures=200, conversions=20)])
    assert not a.sample_ratio.checked
