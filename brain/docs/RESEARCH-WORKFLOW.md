# The research workflow

The loop this system is built around:

```
source claim  ->  qualitative concept  ->  competing definitions
      ->  hypothesis  ->  experiment  ->  verdict  ->  (maybe) a strategy version
```

Nothing skips a step, and a strategy version is never changed by an automated
process — approval is an explicit human act (`strategies.approved` defaults to 0
and no code path sets it).

## 1. Record what a source says

Knowledge Base → each item stores the observation, whether the rule was stated or
inferred, the candidate quantitative interpretations, and the backtest hypothesis.
Click **Why?** on any row to see the whole chain, including whether the source
page was ever actually fetched.

## 2. Do not choose a definition — enumerate them

`research/formalizer.py`. "Tight consolidation" becomes five candidates:
range ratio ≤ 0.7 / ≤ 1.0 / ≤ 1.2, depth ≤ 15%, depth ≤ 25%. The ambiguity note
is stored and displayed; it is never resolved by fiat.

## 3. Run an experiment

Research Lab, three kinds:

- **split** — find every historical occurrence of a setup, split the occurrences
  by a measured feature at a threshold fixed in advance, compare forward
  outcomes. Two-sided permutation test, Welch's t alongside, Wilson intervals on
  every proportion.
- **variants** — backtest each competing definition over the same period and
  universe; compare with bootstrap intervals on expectancy. Overlapping intervals
  are reported as `MIXED`, not as a ranking.
- **filter** — same strategy, rule on vs off. Reports the expectancy delta, the
  drawdown delta and how many trades the filter removed.

## 4. Read the verdict properly

`SUPPORTED / MIXED / UNSUPPORTED / INSUFFICIENT_DATA`. Split experiments are
direction-aware: a detectable effect pointing *against* the hypothesis returns
`UNSUPPORTED` with a `DIRECTION REVERSED` note, because "we found a significant
difference" is not the same as "the source was right".

## 5. Assume it is overfit until it survives

Before any result changes anything:

1. **Walk-forward** — does the selected parameter still work on a window the
   selection never saw? Read the *degradation*, not the test score.
2. **Parameter stability** — did the optimum jump every fold? Then it is noise.
3. **Sensitivity** — is the surface a plateau or a spike? A spike is `FRAGILE`.
4. **Monte Carlo** — what does the drawdown distribution look like at your risk
   per trade? Expectancy in R does not change with position size; ruin does.
5. **Sample size** — under 30 trades, the metrics describe that simulation and
   nothing else.

## 6. Version, do not overwrite

Strategy versions are immutable. A change bumps the version and records the
reason, so `SAR v1.0` and `SAR v1.1` can be compared side by side forever.

## Worked example: the flagship hypothesis

> *Does volume contraction during consolidation improve breakout expectancy?*

Research Lab → kind `split`, feature `volume_contraction`. The lab builds a
permissive base definition (so the volume rule does not pre-filter the sample),
finds every qualifying breakout, splits on 2nd-half/1st-half base volume < 1.0,
and compares 10-day forward returns with a permutation test.

On the synthetic data shipped with this repo the answer comes back
**UNSUPPORTED, direction reversed** — the non-contracting group did better. That
is a fact about the generator, not about markets, and the UI says so. Point it at
real CSVs and re-run: that is the entire purpose of the machine.
