# Migration Plan

Each phase follows the development discipline: objective, affected files, architecture impact, acceptance criteria, tests, implement, run tests, fix, review against spec, update docs. **Working functionality is never deleted without a documented reason here.**

## Phase Overview

| Phase | Objective | Exit criterion (summary) |
|---|---|---|
| 0 | Repository audit + architecture docs | Docs reviewed and accepted by operator |
| 1 | Packaging, tooling, CI, golden master, config split, safety/security hardening — **no strategy behaviour change** | §Phase 1 acceptance |
| 2 | Strategy contract; Social Momentum → Strategy #001 v1 (parity); defect fixes D-001…D-006 as separate, golden-updating commits | Parity suite green; defects closed with justification |
| 3 | Event-driven backtester, SimulatedExchange, CostModel, point-in-time SnapshotBuilder, demo scenario datasets, leakage tests | Leakage suite green; legacy backtester removed |
| 4 | Dataset versioning, partitions, OOSVault, walk-forward | OOS isolation tests green |
| 5 | Regime engine, parameter robustness, stress, Monte Carlo | Reports generated on demo datasets |
| 6 | DB (SQLAlchemy/Alembic), Strategy Registry, Experiment Registry, research budget, reproduce | `reproduce(EXP)` round-trips |
| 7 | Paper trading on the shared trading-plane graph; event ledger; trade journal | Paper demo with journal reconstruction |
| 8 | Drift engine | Drift report on paper period |
| 9 | Agent runtime + ResearchAgent + TradeReviewAgent | Security suite green with scripted adversarial model |
| 10 | Strategy Factory (Milestone 2) | Volatility-expansion idea → research report |
| 11 | Red Team agent + deterministic bias checks | Red-team report blocks a planted-leak strategy |
| 12 | Portfolio research | Correlation / risk-contribution reports |
| 13 | Broker adapters + Reconciler (read-only first) | Adapter contract suite green against broker sandbox |
| 14 | Live safety gates: live lock, limited live | All unlock conditions tested |
| 15 | Nightly research loop | Morning report generated; no write path to live |

UI work is delivered inside each phase for the feature that phase adds. A page for a feature that is not built yet shows **NOT IMPLEMENTED**.

## Removals (documented)

| Item | Removed in | Reason |
|---|---|---|
| `BacktestRunner` synthetic generator | Phase 3 | Circular: features are drawn from the same regime as forward returns (CURRENT_STATE §6). Kept until then as a golden fixture labelled `LEGACY SYNTHETIC — NOT EVIDENCE` |
| `web/server.py` background random-tick worker | Phase 7 | Fabricated prices and tweets presented as an "autonomous loop"; replaced by the PAPER runner on demo or real feeds |
| "zero-error", "2-year backtest", "out-of-sample t-stat", "hardware kill switch", "atomic OCO" claims | Phase 1 | Not supported by the code |
| Hard-coded calibration map as a sizing input | Phase 2 | Not empirical; kept only for v1 parity |

---

## Phase 1 — Detailed Plan

### Objective
Make the repository a well-engineered, testable base **without changing any strategy, risk or backtest output**. Fix only safety and security defects that do not alter strategy outputs. Start recording real social data (operator decision 2026-10-03).

### Affected files
All Python files (move under `src/trade/`, imports become `trade.*`); new `pyproject.toml`, `Makefile`, `.env.example`, `.github/workflows/ci.yml`, `config/*.yaml`, `tests/golden/**`, `tests/risk/**`, `tests/security/**`, `AGENTS.md`; new `src/trade/data/recorders/`; `README.md` and `strategy.md` corrections.

### Architecture impact
- A single `trade` package with a `src/` layout, so name collisions go away.
- Configuration splits into `RiskLimits`, `CostModel` config, strategy params and runtime settings. A compatibility `TradingConfig` facade reads from them, so code behaviour is unchanged.
- The kill switch becomes fail-closed (an absolute state dir; persistence failure means halted).
- The web server binds to `127.0.0.1`, has no wildcard CORS, requires an operator token on mutating routes, and escapes rendered HTML.
- New raw-data recorder in the data plane; it has no path into the trading plane.

### Not in Phase 1
New strategy interface, defect fixes D-001…D-006 (they change outputs → Phase 2, including enforcement of the 6% drawdown limit), new backtester, DB, agents.

### First Implementation Tasks

| # | Task | Done when |
|---|---|---|
| 1 | **Golden-master capture.** Write `tests/golden/` fixtures from the *current* code (TEST_STRATEGY §2) before moving anything | Fixtures committed; golden tests pass on current code |
| 2 | **Packaging.** `src/trade/` layout, `pyproject.toml` (Python ≥ 3.11, pinned dev deps: pytest, hypothesis, ruff, mypy, import-linter), lockfile; rewrite imports to `trade.*` | `pip install -e .` works; golden + existing tests pass |
| 3 | **Makefile.** `setup, dev, test, test-critical, demo, backtest, paper, lint, format, typecheck, reset, health`. Targets for features that do not exist yet print `NOT IMPLEMENTED` and exit non-zero | Every target runs or truthfully reports NOT IMPLEMENTED |
| 4 | **CI.** GitHub Actions: format check, lint, mypy (strict on `trade.core`), tests, `-m critical`, secret scan | CI green on the branch; critical job marked required |
| 5 | **Config split.** `config/risk_limits.yaml`, `config/cost_models/nse_intraday.yaml`, `strategies/social_momentum/v1/params.yaml`, `config/runtime.yaml`; frozen loaders with SHA-256; `TradingConfig` facade preserved | Golden suite unchanged; config hash logged at start-up |
| 6 | **Kill-switch fail-closed.** Absolute `$TRADE_STATE_DIR`; `is_kill_switch_active()` returns True on any read error; trigger keeps an in-memory halt even if the write fails; backtest uses an isolated temporary state dir instead of clearing a shared `/tmp` file | New critical tests: write failure → halted; unreadable → halted; cwd change doesn't bypass |
| 7 | **Web hardening.** Bind `127.0.0.1`; remove `Access-Control-Allow-Origin: *`; operator token (generated into the state dir at first run) required on all POST routes; `/api/unlock` requires a typed confirmation; escape all `innerHTML` data (or use `textContent`); fix the multiple-thread toggle race; vendor CSS locally | Security tests: 401 without token; XSS payload inert; no wildcard CORS |
| 8 | **Honest labelling.** Remove the "zero-error" claim and other unsupported claims from README/strategy.md/console output; label the legacy backtest output `LEGACY SYNTHETIC — NOT EVIDENCE`; record D-001…D-012 in `docs/KNOWN_DEFECTS.md`; fix the friction table in `strategy.md` to match the code or flag the discrepancy | Grep finds no unsupported claims; the golden *metric values* are unchanged (only labels change) |
| 9 | **Scaffold target tree.** Add `strategies/{trend_following,volatility_expansion}`, `research/{regimes,stress,monte_carlo,drift}`, `agents/{trade_reviewer,regime,portfolio,operations,runtime}`, `docs/tutorials/` with NOT IMPLEMENTED stubs; `AGENTS.md` (rules for coding agents working in this repo: invariants, forbidden actions, test commands) | Tree matches TARGET_ARCHITECTURE §6; every placeholder package is marked NOT IMPLEMENTED; tutorials index lists unbuilt tutorials as NOT IMPLEMENTED |
| 10 | **Secrets hygiene.** `.env.example`, `.gitignore` entries, log redaction filter for configured secret keys, gitleaks in CI | Secret-scan job green; redaction unit test |
| 11 | **Real social recorder** (operator decision 2026-10-03). Reddit OAuth source, append-only JSONL, dedupe, `available_at` stamping, author hashing, versioned symbol tagger against the dated Nifty 500 snapshot, `make record-social`. Starts the data clock early because history can only accumulate in calendar time | Unit tests with recorded HTTP fixtures; missing credentials → refuses to start; one live poll succeeds once credentials are configured |

### Phase 1 Acceptance Criteria

1. **Behaviour parity:** every golden fixture from Task 1 passes unchanged after Tasks 2–10. The only allowed differences are output *labels* (Task 8).
2. Commands `make setup`, `make test`, `make test-critical`, `make lint`, `make typecheck`, `make backtest`, `make paper` all succeed from a fresh clone, with no network after `setup`. Unbuilt targets report NOT IMPLEMENTED.
3. CI runs format, lint, typecheck, tests, the critical tests and the secret scan, and is green.
4. Kill switch: write failure and unreadable state both result in halted (tests). The lockfile location is independent of the current working directory.
5. API: no unauthenticated mutating route; localhost bind; no wildcard CORS; XSS test passes.
6. No source file outside `src/trade/` contains Python logic except thin entry points.
7. No unsupported marketing claims remain; known defects are documented with ids.
8. The original 7 tests still pass. Weak assertions are tightened only through the golden suite, not by editing the old tests.
9. Docs updated: CURRENT_STATE (revision), README quickstart, AGENTS.md.
10. Social recorder: tests green; `make record-social` refuses to run without credentials and records deduplicated, timestamped raw posts with them.

### Risks
- Golden capture of D-001 makes the suite encode a bug. This is intentional, so that Phase 2 fixes it visibly, but reviewers must not read the golden values as "correct".
- The import move is large. It is done in one mechanical commit, separate from all logic changes, to keep review tractable.
