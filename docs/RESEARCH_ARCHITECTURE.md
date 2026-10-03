# Research Architecture

## 1. Master Learning Loop

```
OBSERVE → HYPOTHESIZE → BUILD CANDIDATE → EXPERIMENT → BACKTEST → ROBUSTNESS → RED TEAM
→ LOCK OOS → WALK-FORWARD → REGIME → STRESS → PAPER → DRIFT → HUMAN REVIEW
→ LIMITED LIVE → MONITOR → DRIFT/FAILURE → INVESTIGATE → NEW HYPOTHESIS
```

At every stage, **failure can end the research line** (`ARCHIVED: NO_EDGE_FOUND` / `INSUFFICIENT_EVIDENCE`). Failure does not have to send work back for another tweak. Archived is a successful terminal state and is displayed as such.

## 2. Hypothesis

```yaml
id: H-0047
title: Volatility expansion after compression in liquid NSE equities
statement: >
  After N-day realized-vol compression below the 20th percentile, a close outside
  the compression range predicts continuation over the next K days.
null_hypothesis: Post-compression breakout returns are indistinguishable from unconditional returns after costs.
rationale: ...
universe: {market: NSE, selector: liquid_cash_equities}
data_requirements: [ohlcv_1d]
falsification_criteria:          # declared BEFORE experiments run
  - DSR < 0.95 on walk-forward stitched OOS
  - fewer than 60% of WF windows profitable after costs
  - any regime with n ≥ 30 trades and expectancy < 0
budget: {max_experiments: 50, max_param_trials: 500, max_feature_variants: 100, max_strategy_variants: 10, oos_evaluations: 1}
status: OPEN | TESTING | SUPPORTED | REJECTED | ARCHIVED
created_by: user | agent:<run_id>
```

Falsification criteria are **frozen when the first experiment starts**. Changing them creates a new hypothesis that inherits the parent's spent budget.

## 3. Experiment Registry

`EXP-000001` records: hypothesis id, strategy id@version + code hash, dataset id@version + partitions used, parameters, code commit (working tree must be clean, otherwise the run is refused), random seed, cost model@version, slippage model@version, latency model, environment (Python version, lockfile hash, OS), backtest run ids, OOS result (if spent), walk-forward run id, regime results, stress results, red-team report id, final decision plus decider (deterministic gate or human).

`reproduce(EXP-id)` checks out the recorded commit into a temporary worktree, verifies dataset hashes, re-runs, and compares the trade list hash. A mismatch is a P1 defect.

## 4. Research Budget & Multiple-Testing Control

The **deterministic experiment runner** enforces the budget. Agents do not police it themselves. Every backtest, parameter evaluation, feature variant and strategy variant increments counters in `research_budget_ledger`. An exhausted budget makes the runner refuse with `BUDGET_EXHAUSTED`.

Uses of the trial count:
- the Deflated Sharpe Ratio input `N` is the hypothesis's cumulative parameter trials, including the parent's;
- **Probability of Backtest Overfitting (PBO)** via combinatorially symmetric cross-validation over the evaluated configurations;
- warnings when `trials / effective_independent_observations` exceeds a threshold, or when PBO > 0.5;
- for families of related hypotheses, Holm correction on reported p-values.

Pre-existing hand tuning, such as Strategy #001's weights, is recorded as `trials: unknown`. Any result built on it is labelled "**prior data-snooping unquantified**".

## 5. OOS Discipline

- `OOSVault` (DATA_ARCHITECTURE §4): one OOS evaluation per hypothesis by default.
- Agents receive OOS **metrics**, never OOS rows. After OOS is spent, any new variant must use a **new, later OOS window** that is not yet observed (for example, recorded forward data) or go straight to paper.
- Failing OOS is final for that hypothesis version. The UI shows OOS failure prominently and offers no "retry" button.

## 6. Walk-Forward

```
|── train ──|─ val ─|emb|─ oos ─|
      |── train ──|─ val ─|emb|─ oos ─|           (roll by step)
            |── train ──|─ val ─|emb|─ oos ─|
```

Anchored or rolling, with window lengths in config. Parameter selection inside each window uses only that window's train and validate data, with a predeclared selection rule (for example, the centre of the most stable plateau rather than the argmax). Every window is stored: chosen params, metrics, trades. The report shows the stitched OOS equity, the percentage of profitable windows, the median and dispersion of window Sharpe, the worst window, and parameter stability across windows.

## 7. Regime Engine

The first version is rule-based, computed point-in-time on an index (Nifty 50 / 500) and per symbol:

| Regime | Rule (initial, versioned) |
|---|---|
| bull / bear trend | 100-day SMA slope sign and price above/below the SMA, with ADX > 20 |
| sideways | ADX < 20 |
| high / low volatility | 20-day realized vol percentile against a trailing 2-year window, above 80 / below 20 |
| stress | drawdown from the 252-day high > 15% **and** high-vol |
| transition | label changed within the last k days |

A model-based HMM classifier may be added later as a separate versioned classifier. It is scored against synthetic `truth.json`.

## 8. Champion / Challenger

The champion is a frozen version. Challengers are new versions or strategies. All run the **identical** validation pipeline, gates and paper period on the same data. Promotion requires that the challenger beats the champion on the predeclared criteria with a significance test on paired daily returns, followed by human approval. The champion is never edited in place.

## 9. Evidence-First Reporting

Every research conclusion is a `ResearchArtifact` with `claims[]`. Each claim must reference evidence ids (`EXP-`, `BT-`, `WF-`, dataset@version, strategy@version, trade ids) and the metric values copied **from the database by code**, not typed by the model. The report renderer re-fetches each referenced metric and fails the artifact if a number in the text does not match (AI_AGENT_ARCHITECTURE §6).

## 10. Nightly Research Loop (Phase 15)

A scheduled job, run in the research plane:
1. review completed trades and misses;
2. compute performance and drift;
3. detect regime changes and anomalies;
4. open investigation artifacts;
5. let agents propose hypotheses and experiments **within budget**;
6. run approved experiments;
7. generate candidate *versions* (DRAFT only);
8. produce a morning report.

The job has no capability to change any LIVE or PAPER_RUNNING strategy, any risk limit, or the live lock.

## 11. Strategy Factory (Phase 10)

```
User idea (text) ─► ResearchAgent ─► Hypothesis (schema-validated, budget assigned)
   ─► data requirement resolution (deterministic: available? else BLOCKED_ON_DATA)
   ─► StrategyBuilderAgent ─► StrategySpec + strategy.py (DRAFT, generated/)
   ─► static checks (schema, import-linter, sandbox contract tests)
   ─► Validation pipeline (Loop B) ─► RedTeamAgent ─► Research report ─► paper candidate | ARCHIVED
```

Generated code runs only in the research plane, with sandbox contract tests, and is never imported by the trading plane until it is promoted through the lifecycle with human approval.
