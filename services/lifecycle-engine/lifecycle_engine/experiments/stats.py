"""Deterministic statistics for A/B experiments on conversion metrics.

Standard library only. Fixed-horizon frequentist test (two-proportion z-test, Holm
correction across treatments), Wilson intervals, a Bayesian probability-to-beat-control
(normal approximation of Beta posteriors), a chi-square sample-ratio-mismatch check and
the planned sample size for a minimum detectable effect.
"""

from __future__ import annotations

import math

from ..schemas.experiments import (
    Decision,
    ExperimentAnalysis,
    ExperimentDesign,
    SampleRatioCheck,
    VariantInput,
    VariantResult,
)

SQRT2 = math.sqrt(2.0)


def normal_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / SQRT2)


def normal_ppf(p: float) -> float:
    """Inverse standard normal CDF (Acklam's algorithm with one Newton refinement)."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    a = [-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02, 1.383577518672690e02,
         -3.066479806614716e01, 2.506628277459239e00]
    b = [-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02, 6.680131188771972e01,
         -1.328068155288572e01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00, -2.549732539343734e00,
         4.374664141464968e00, 2.938163982698783e00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00, 3.754408661907416e00]
    lo = 0.02425
    if p < lo:
        q = math.sqrt(-2 * math.log(p))
        x = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    elif p <= 1 - lo:
        q = p - 0.5
        r = q * q
        x = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
            (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    else:
        q = math.sqrt(-2 * math.log(1 - p))
        x = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    e = normal_cdf(x) - p  # Newton step
    return x - e * math.sqrt(2 * math.pi) * math.exp(x * x / 2)


def gamma_q(a: float, x: float) -> float:
    """Regularized upper incomplete gamma Q(a, x) (series / continued fraction)."""
    if x < 0 or a <= 0:
        raise ValueError("invalid arguments")
    if x == 0:
        return 1.0
    gln = math.lgamma(a)
    if x < a + 1:
        ap, s, delta = a, 1.0 / a, 1.0 / a
        for _ in range(1000):
            ap += 1
            delta *= x / ap
            s += delta
            if abs(delta) < abs(s) * 1e-15:
                break
        return max(0.0, 1.0 - s * math.exp(-x + a * math.log(x) - gln))
    b = x + 1 - a
    c = 1.0 / 1e-300
    d = 1.0 / b
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = 1e-300 if abs(d) < 1e-300 else d
        c = b + an / c
        c = 1e-300 if abs(c) < 1e-300 else c
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1) < 1e-15:
            break
    return min(1.0, math.exp(-x + a * math.log(x) - gln) * h)


def chi_square_sf(chi2: float, df: int) -> float:
    return gamma_q(df / 2.0, chi2 / 2.0)


def wilson_interval(conversions: int, n: int, z: float = 1.959963984540054) -> tuple[float, float] | tuple[None, None]:
    if n == 0:
        return None, None
    p = conversions / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def two_proportion_test(c1: int, n1: int, c2: int, n2: int) -> tuple[float, float]:
    """(z, two-sided p) for H0: p1 == p2, pooled standard error. Group 2 is the treatment."""
    p_pool = (c1 + c2) / (n1 + n2)
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / n1 + 1 / n2))
    if se == 0:
        return 0.0, 1.0
    z = (c2 / n2 - c1 / n1) / se
    return z, 2 * (1 - normal_cdf(abs(z)))


def lift_interval(c1: int, n1: int, c2: int, n2: int, alpha: float) -> tuple[float, float]:
    """Wald interval for p2 - p1 (unpooled)."""
    p1, p2 = c1 / n1, c2 / n2
    se = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    z = normal_ppf(1 - alpha / 2)
    d = p2 - p1
    return d - z * se, d + z * se


def probability_to_beat(c_t: int, n_t: int, c_c: int, n_c: int) -> float:
    """P(rate_treatment > rate_control) with Beta(1 + c, 1 + n - c) posteriors (normal approximation)."""
    def moments(c, n):
        a, b = 1 + c, 1 + n - c
        mean = a / (a + b)
        var = a * b / ((a + b) ** 2 * (a + b + 1))
        return mean, var
    m_t, v_t = moments(c_t, n_t)
    m_c, v_c = moments(c_c, n_c)
    return normal_cdf((m_t - m_c) / math.sqrt(v_t + v_c))


def required_sample_size(baseline: float, relative_mde: float, alpha: float, power: float) -> int | None:
    """Per-variant sample size to detect a relative lift of `relative_mde` over `baseline`."""
    p1 = baseline
    p2 = min(baseline * (1 + relative_mde), 0.9999)
    if not 0 < p1 < 1 or p2 <= p1:
        return None
    z_a = normal_ppf(1 - alpha / 2)
    z_b = normal_ppf(power)
    p_bar = (p1 + p2) / 2
    n = (z_a * math.sqrt(2 * p_bar * (1 - p_bar)) + z_b * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2 / (p2 - p1) ** 2
    return math.ceil(n)


def holm(p_values: list[float]) -> list[float]:
    order = sorted(range(len(p_values)), key=lambda i: p_values[i])
    adjusted = [0.0] * len(p_values)
    running = 0.0
    m = len(p_values)
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * p_values[i]))
        adjusted[i] = running
    return adjusted


def sample_ratio_check(variants: list[VariantInput], srm_alpha: float) -> SampleRatioCheck:
    shares = [v.expected_share for v in variants]
    total = sum(v.exposures for v in variants)
    if any(s is None for s in shares) or len(variants) < 2:
        return SampleRatioCheck(checked=False, detail="planned traffic shares not set for every variant")
    if sum(shares) <= 0 or total == 0:
        return SampleRatioCheck(checked=False, detail="no exposures yet")
    expected = [total * s / sum(shares) for s in shares]
    if any(e == 0 for e in expected):
        return SampleRatioCheck(checked=False, detail="a variant has a planned share of 0%")
    chi2 = sum((v.exposures - e) ** 2 / e for v, e in zip(variants, expected))
    p = chi_square_sf(chi2, len(variants) - 1)
    mismatch = p < srm_alpha
    detail = (f"traffic split differs from the plan (chi-square {chi2:.1f}, p={p:.2g}); results are not trustworthy "
              "until the cause is found (assignment bug, bot traffic, events dropped for one variant)"
              if mismatch else f"traffic split matches the plan (p={p:.2g})")
    return SampleRatioCheck(checked=True, chi_square=round(chi2, 4), p_value=p, mismatch=mismatch, detail=detail)


def analyze_experiment(variants: list[VariantInput], design: ExperimentDesign | None = None) -> ExperimentAnalysis:
    design = design or ExperimentDesign()
    warnings: list[str] = []
    controls = [v for v in variants if v.is_control]
    keys = [v.key for v in variants]
    if len(variants) < 2 or len(controls) != 1 or len(set(keys)) != len(keys):
        problem = ("an experiment needs at least two variants" if len(variants) < 2 else
                   "exactly one variant must be the control" if len(controls) != 1 else "variant keys must be unique")
        return ExperimentAnalysis(
            decision=Decision.INVALID, summary=f"Invalid experiment: {problem}.",
            variants=[VariantResult(key=v.key, is_control=v.is_control, exposures=v.exposures, conversions=v.conversions,
                                    conversion_rate=None, rate_ci_low=None, rate_ci_high=None) for v in variants],
            sample_ratio=SampleRatioCheck(checked=False, detail="not checked"), total_exposures=sum(v.exposures for v in variants),
            total_conversions=sum(v.conversions for v in variants), progress=0.0, warnings=[problem], design=design)
    for v in variants:
        if v.conversions > v.exposures:
            warnings.append(f"'{v.key}' has more conversions than exposures; conversions are capped at exposures")
    control = controls[0]
    clean = [v.model_copy(update={"conversions": min(v.conversions, v.exposures)}) for v in variants]
    control = next(v for v in clean if v.is_control)
    treatments = [v for v in clean if not v.is_control]

    baseline = control.conversions / control.exposures if control.exposures else None
    required = None
    if baseline is not None and 0 < baseline < 1:
        required = required_sample_size(baseline, design.minimum_detectable_effect, design.alpha, design.power)
    elif baseline is not None:
        warnings.append("control conversion rate is 0% or 100%; the planned sample size can't be estimated yet")

    results: dict[str, VariantResult] = {}
    raw_p: list[float] = []
    for v in clean:
        lo, hi = wilson_interval(v.conversions, v.exposures)
        results[v.key] = VariantResult(
            key=v.key, is_control=v.is_control, exposures=v.exposures, conversions=v.conversions,
            conversion_rate=(v.conversions / v.exposures) if v.exposures else None,
            rate_ci_low=lo, rate_ci_high=hi, required_exposures=required)
    comparable = control.exposures > 0 and all(t.exposures > 0 for t in treatments)
    if comparable:
        for t in treatments:
            z, p = two_proportion_test(control.conversions, control.exposures, t.conversions, t.exposures)
            lo, hi = lift_interval(control.conversions, control.exposures, t.conversions, t.exposures, design.alpha)
            rc, rt = control.conversions / control.exposures, t.conversions / t.exposures
            results[t.key] = results[t.key].model_copy(update={
                "absolute_lift": rt - rc, "relative_lift": ((rt - rc) / rc) if rc > 0 else None,
                "lift_ci_low": lo, "lift_ci_high": hi, "p_value": p,
                "probability_to_beat_control": probability_to_beat(t.conversions, t.exposures, control.conversions, control.exposures),
            })
            raw_p.append(p)
        for t, adj in zip(treatments, holm(raw_p)):
            results[t.key] = results[t.key].model_copy(update={
                "p_value_adjusted": adj, "significant": adj < design.alpha})

    srm = sample_ratio_check(clean, design.srm_alpha)
    smallest = min(v.exposures for v in clean)
    target = max(required or 0, design.min_exposures_per_variant)
    progress = min(1.0, smallest / target) if target else 0.0
    total_exposures = sum(v.exposures for v in clean)
    total_conversions = sum(v.conversions for v in clean)
    ordered = [results[v.key] for v in variants]

    def done(decision, summary, winner=None):
        return ExperimentAnalysis(decision=decision, winner=winner, summary=summary, variants=ordered, sample_ratio=srm,
                                  total_exposures=total_exposures, total_conversions=total_conversions,
                                  progress=round(progress, 4), warnings=warnings, design=design)

    if srm.mismatch:
        return done(Decision.INVALID, "Sample ratio mismatch: " + srm.detail + ".")
    if not comparable or smallest < design.min_exposures_per_variant:
        return done(Decision.KEEP_RUNNING,
                    f"Keep running: each variant needs at least {design.min_exposures_per_variant} exposures "
                    f"(smallest has {smallest}).")
    sig_pos = [results[t.key] for t in treatments if results[t.key].significant and results[t.key].absolute_lift > 0]
    sig_neg = [results[t.key] for t in treatments if results[t.key].significant and results[t.key].absolute_lift < 0]
    reached = required is not None and smallest >= required
    if not reached:
        warnings.append("decide at the planned sample size: stopping as soon as a result looks significant "
                        "inflates false positives")
        if sig_pos or sig_neg:
            best = max(sig_pos, key=lambda r: r.absolute_lift) if sig_pos else None
            what = (f"'{best.key}' is ahead of control" if best else "control is ahead of every variant")
            return done(Decision.TRENDING, f"Trending: {what}, but only {progress:.0%} of the planned sample is in. "
                                           "Keep running before deciding.")
        target_txt = f" of {required} per variant" if required else ""
        return done(Decision.KEEP_RUNNING, f"Keep running: {progress:.0%} of the planned sample{target_txt} collected, "
                                           "no significant difference yet.")
    if sig_pos:
        best = max(sig_pos, key=lambda r: r.absolute_lift)
        rel = f" ({best.relative_lift:+.1%} relative)" if best.relative_lift is not None else ""
        return done(Decision.WINNER, f"'{best.key}' beats control by {best.absolute_lift:+.2%} points{rel}, "
                                     f"adjusted p={best.p_value_adjusted:.3g}.", winner=best.key)
    if sig_neg and len(sig_neg) == len(treatments):
        return done(Decision.CONTROL_BETTER, "Control is significantly better than every variant.", winner=control.key)
    return done(Decision.NO_SIGNIFICANT_DIFFERENCE,
                f"No significant difference at the planned sample size (MDE {design.minimum_detectable_effect:.0%} relative). "
                "Keep the simpler option.")
