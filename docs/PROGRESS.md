# Progress Log & Resume Guide

Last updated: 2026-10-03. Read this first when resuming. Then read `AGENTS.md` (rules) and `docs/MIGRATION_PLAN.md` (plan).

---

## 1. Where Things Stand

| Phase | Status | Branch |
|---|---|---|
| Restructure (`india_quant_bot/` → target layout) | Done | `refactor/target-layout` |
| 0 — Audit + architecture docs | Done | `docs/architecture-review` |
| 1 — Foundation (packaging, golden master, safety, CI, social recorder) | Done; CI green | `phase-1/foundation` |
| 2 — Strategy contract + Strategy #001 v1 + defect fixes | Done; CI green (`caafb70`) | `phase-2/strategy-contract` (**current**) |
| 3 — Event-driven backtester | **Next; not started** | — |

- Branches are **stacked**: refactor → docs → phase-1 → phase-2. Each contains everything before it. `main` still has the original code; nothing is merged yet. Merging (one PR from `phase-2/strategy-contract` into `main`) is the operator's decision.
- Health at last check: `make ci` passes (591 tests); the GitHub Actions run passes on Python 3.11 and 3.13; the gitleaks secret scan is clean.

## 2. Resume Checklist

```bash
cd ~/Workspace/trade
git checkout phase-2/strategy-contract && git pull
make setup          # uv sync (Python 3.13 venv in .venv)
make ci             # must pass before starting new work
make health         # config fingerprints, kill switch, universe, recorder status
git checkout -b phase-3/event-backtester
```

## 3. Operator Decisions (binding)

| Date | Decision | Where it lives |
|---|---|---|
| 2026-10-03 | Max drawdown **6%** from the high-water mark → halt for manual review | `config/risk_limits.yaml`; enforced (D-006) |
| 2026-10-03 | Operational DB: **SQLite** (models kept Postgres-portable) | `docs/TARGET_ARCHITECTURE.md` §7; DB lands in Phase 6 |
| 2026-10-03 | **Record real social data forward**; Strategy #001 is validated on recorded data, not run as paper-only research | `docs/DATA_ARCHITECTURE.md` §7; `src/trade/data/recorders/` |
| 2026-10-03 | Phases 1 and 2 approved ("go ahead") | — |

Decisions made during implementation (the operator may override):
- The daily loss limit is **net** (realized + unrealized; winners offset losers), resetting each IST session. Legacy was gross losing trades only.
- Uncalibrated strategies cannot use Kelly sizing; they are sized at a fixed ₹20,000 (`uncalibrated_kelly_policy: fixed_notional`).
- On any portfolio halt (daily loss, drawdown, invalid state), positions are flattened immediately.
- Reddit is the only social source (StockTwits is blocked by Cloudflare; X is paid).

## 4. Approach (how the work is done; keep doing it)

1. **Characterise before changing.** `tests/golden/` records legacy outputs. The `reflex` and `risk` fixtures are pinned to a frozen, self-contained oracle (`tests/golden/legacy/oracle.py`, never edited). Other fixtures change **only** in a commit naming a defect ID, with before/after metrics appended to `tests/golden/CHANGELOG.md` (`python -m tests.golden.compare <before_dir>`).
2. **Separate mechanical and behavioural commits.** Moves and renames go in their own commits with golden fixtures unchanged. Each output-changing fix gets its own commit.
3. **Parity first, then fix.** Strategy #001 was proven identical to legacy (450 cases, end-to-end backtest unchanged) *before* any defect fix.
4. **Immutable strategy versions.** `strategies/<id>/v<N>/FINGERPRINT` pins `strategy.yaml` + `strategy.py`. Changing behaviour means creating v2. System-level policy (sizing, risk) may refuse what a strategy requests, without editing the strategy.
5. **Fail closed everywhere.** Any exception, NaN or missing input → DENY or halt. Kill-switch write failure → still halted.
6. **Capability over instruction.** Nothing outside the risk engine can approve orders; layering is enforced by `lint-imports`.
7. **No fake evidence.** Synthetic results are labelled NOT EVIDENCE; unbuilt features show NOT IMPLEMENTED; no hard-coded metrics.
8. **Verify tests can fail.** Critical tests were mutation-checked (deliberately break the code and confirm the test fails).
9. **Per-phase discipline:** objective → affected files → acceptance criteria → tests → implement → `make ci` → docs → commit → push → confirm GitHub CI.

## 5. Current Architecture (as built)

```
Observation (symbol, as_of, session_time, MarketSnapshot, SocialSignal)   # rejects inputs stamped after as_of
  → Strategy.on_observation → StrategyDecision(signals | rejection_codes, diagnostics)
  → Strategy.validate (declared reason codes, identity, timestamp, no uncalibrated probability)
  → RiskEngine.check_portfolio (every cycle: kill switch, net daily loss, 6% drawdown → kill + flatten)
  → Sizer (system policy) → OrderIntent (quantity + fill-anchored bracket)
  → RiskEngine.evaluate → RiskDecision ALLOW | DENY(reason_codes)
  → ExecutionAdapter.submit_intent (IndianPaperBroker today)
```

| Piece | File |
|---|---|
| Contract (Observation, Signal, StrategySpec, Strategy, fingerprint) | `src/trade/core/strategy/contract.py` |
| Strategy #001 v1 | `src/trade/strategies/social_momentum/v1/` |
| Pipeline | `src/trade/core/pipeline.py` |
| Risk engine + kill switch | `src/trade/core/risk/engine.py` |
| Sizer | `src/trade/core/risk/sizing.py` |
| Portfolio view | `src/trade/core/portfolio/view.py` |
| Paper broker | `src/trade/brokers/paper.py` |
| Config (risk, costs, runtime; strict YAML; SHA-256) | `src/trade/core/config.py`, `config/*.yaml` |
| Social recorder + symbol tagger | `src/trade/data/recorders/` |
| Legacy synthetic backtest (NOT EVIDENCE) | `src/trade/backtesting/engine.py` |
| Web dashboard (loopback, operator token) | `src/trade/web/` |

## 6. Open Items

| Item | Owner | Notes |
|---|---|---|
| Reddit OAuth credentials → start `make record-social` | **Operator** | History accumulates only while the recorder runs. See `docs/tutorials/00-record-social-data.md` |
| Merge stacked branches to `main` | **Operator** | Suggested: one PR from `phase-2/strategy-contract` |
| Mark the "critical safety tests" job as a required check | **Operator** | GitHub branch protection |
| D-005 brokerage model (flat ₹20 vs min(₹20, 0.03%)) | **Operator** | Depends on the actual broker |
| Order-book depth recording (needed for OBI) | Later | Needs a broker feed (Phase 13) |
| D-009 strategy ownership / shorts, D-012 persistence | Phases 3/6/7 | |
| D-010 Social Momentum v2 (unique-author features from recorded data) | Phase 3+ | v1 stays as-is |
| Tagger v1 accuracy unmeasured; no company-name matching | Later | Raw data is kept, so it can be re-tagged |
| Synthetic demo loop in the web server (random ticks) | Phase 7 | Labelled; to be replaced by the PAPER runner |

## 7. Next: Phase 3 Plan (draft; write it into MIGRATION_PLAN.md before coding)

Objective: replace the circular legacy backtester with an event-driven engine using point-in-time data, with leakage tests that fail the build.

1. `MarketEvent` / event queue ordered by `available_at`; `SimulatedClock`.
2. Point-in-time `SnapshotBuilder` with a feature registry (name, version, lookback, `source_time`).
3. `SimulatedExchange`: next-quote fills plus latency, slippage models, participation-capped partial fills, gap-aware stops, stop-first intrabar rule, session flatten.
4. Generic `CostModel` interpreting `config/cost_models/*.yaml` (parity with `IndianTaxCalculator`).
5. Deterministic demo datasets (TREND, MEAN_REVERSION, HIGH/LOW_VOL, CRASH, GAP, LIQUIDITY_SHOCK, REGIME_TRANSITION): raw observables only, with a separate `truth.json`.
6. Leakage suite (`tests/leakage/`, critical): poisoned-future, feature/signal timestamp integrity, reproducibility (trade-list hash), generator-emits-no-features.
7. Metrics from the daily marked-to-market equity curve (Sharpe/Sortino/Calmar, drawdown duration, profit factor, etc.).
8. Run Strategy #001 v1 through the same `DecisionPipeline`; remove the legacy generator (document the removal in MIGRATION_PLAN).

Reference docs: `docs/BACKTEST_ARCHITECTURE.md`, `docs/DATA_ARCHITECTURE.md`, `docs/TEST_STRATEGY.md` §3.

## 8. Commit History (oldest → newest)

| Commit | Summary |
|---|---|
| `1b98aab` | Restructure into target layout |
| `0a2d2d4` | Phase 0 audit + architecture docs |
| `862746a` | Golden-master characterisation |
| `e813e14` | `src/trade` packaging, pyproject, uv lock |
| `1777862` | Config split (risk/costs/strategy/runtime) |
| `c642132` | Fail-closed kill switch |
| `88ed7fc` | Web hardening (loopback, token, XSS, CORS) |
| `4cf4717` | Remove unsupported claims; NOT EVIDENCE labels |
| `8fbba2a` | Real social recorder + secrets hygiene |
| `ffd8392` | Makefile, health, ruff, strict mypy |
| `4a20255` | GitHub Actions CI + gitleaks |
| `a37d15a` | Scaffold, AGENTS.md, tutorials |
| `9510b93` | Phase 2 plan |
| `351558b` | 2.1 Strategy contract + v1 + parity |
| `91e2c66` | 2.2 Shared pipeline, order-level risk, Sizer |
| `32c7272` | 2.3 D-001 cash accounting |
| `82a5e79` | 2.4 D-002 calibration pairing |
| `d1df054` | 2.5 D-006 drawdown / net daily loss / flatten |
| `569ff05` | 2.6 D-003/D-004 fixed-notional sizing |
| `caafb70` | 2.7 Facade removal; docs |

## 9. Gotchas Learned

- `ruff format` also rewrites Python code blocks inside Markdown. It is now restricted to `*.py` (`pyproject.toml`). Keep it that way.
- Never pipe `make ci` into `tail` without `set -o pipefail`; the failure gets hidden.
- Formatting a pinned strategy file changes its fingerprint. Run `ruff format` *before* pinning a new version.
- Run `python -m tests.golden.characterize --write` only in a defect-fix commit, after copying the old fixtures for `compare`.
- The paper loop needs about 8 distinct mentions to get a velocity z-score above 2; fewer won't trigger an entry (relevant when writing tests).
