"""Statistical machinery. Values checked against closed forms where they exist."""

import math

import numpy as np
import pytest

from tradingbrain.stats.core import (binomial_test, bootstrap_ci, cohens_d,
                                     conclusion_from, describe, normal_cdf, normal_ppf,
                                     permutation_test, sample_size_verdict, welch_t,
                                     wilson_interval)


def test_normal_ppf_round_trips():
    for p in (0.01, 0.1, 0.5, 0.75, 0.975, 0.999):
        assert normal_cdf(normal_ppf(p)) == pytest.approx(p, abs=1e-8)
    assert normal_ppf(0.975) == pytest.approx(1.959964, abs=1e-5)


def test_wilson_interval_known_value():
    r = wilson_interval(50, 100)
    assert r.estimate == pytest.approx(0.5)
    assert r.ci_low == pytest.approx(0.4038, abs=1e-3)
    assert r.ci_high == pytest.approx(0.5962, abs=1e-3)


def test_wilson_stays_inside_zero_one_at_extremes():
    r = wilson_interval(0, 5)
    assert r.ci_low >= 0 and r.ci_high <= 1
    r = wilson_interval(5, 5)
    assert r.ci_low >= 0 and r.ci_high <= 1


def test_wilson_reports_insufficient_data():
    assert "INSUFFICIENT_DATA" in wilson_interval(0, 0).verdict
    assert "INSUFFICIENT_DATA" in wilson_interval(3, 5).verdict


def test_binomial_test_symmetry():
    assert binomial_test(5, 10, 0.5).p_value == pytest.approx(1.0)
    assert binomial_test(10, 10, 0.5).p_value == pytest.approx(2 / 1024)


def test_bootstrap_ci_brackets_the_mean():
    rng = np.random.default_rng(3)
    x = rng.normal(1.0, 0.5, 400)
    r = bootstrap_ci(x, np.mean, n_boot=2000)
    assert r.ci_low < x.mean() < r.ci_high
    assert (r.ci_high - r.ci_low) < 0.3


def test_bootstrap_refuses_tiny_samples():
    r = bootstrap_ci([1.0, 2.0], np.mean)
    assert "INSUFFICIENT_DATA" in r.verdict


def test_permutation_test_detects_a_real_difference():
    rng = np.random.default_rng(11)
    a = rng.normal(1.0, 1.0, 300)
    b = rng.normal(0.0, 1.0, 300)
    r = permutation_test(a, b, np.mean, n_perm=2000)
    assert r.p_value < 0.01
    assert r.effect_size > 0.5


def test_permutation_test_finds_nothing_in_noise():
    rng = np.random.default_rng(12)
    a = rng.normal(0, 1, 300)
    b = rng.normal(0, 1, 300)
    r = permutation_test(a, b, np.mean, n_perm=2000)
    assert r.p_value > 0.05


def test_welch_agrees_with_permutation_on_clear_signal():
    rng = np.random.default_rng(13)
    a = rng.normal(1.0, 1.0, 200)
    b = rng.normal(0.0, 1.0, 200)
    assert welch_t(a, b).p_value < 0.01


def test_cohens_d_sign_and_scale():
    a = np.array([1.0] * 50 + [1.2] * 50)
    b = np.array([0.0] * 50 + [0.2] * 50)
    assert cohens_d(a, b) > 5


def test_conclusion_vocabulary():
    assert conclusion_from(0.001, 500, 0.5) == "SUPPORTED"
    assert conclusion_from(0.001, 500, 0.01) == "MIXED"
    assert conclusion_from(0.5, 500, 0.01) == "UNSUPPORTED"
    assert conclusion_from(0.001, 12, 0.9) == "INSUFFICIENT_DATA"
    assert conclusion_from(None, 500, 0.5) == "INSUFFICIENT_DATA"


def test_sample_size_verdict_wording():
    assert "INSUFFICIENT_DATA" in sample_size_verdict(5)
    assert sample_size_verdict(50).startswith("MODERATE")
    assert sample_size_verdict(500).startswith("ADEQUATE_SAMPLE")


def test_describe_shape():
    d = describe([1, 2, 3, 4, 5], "x")
    assert d["n"] == 5 and d["median"] == 3 and d["pct_positive"] == 100.0
