# Risk Architecture

**The RiskEngine is authoritative. It is deterministic code with no LLM in its path. It fails closed.**

## 1. Position in the Pipeline

```
Signal ──► Sizer ──► OrderIntent ──► RiskEngine.evaluate() ──► ALLOW ──► ExecutionAdapter
                                          │
                                          └──► DENY(reason_codes) ──► Ledger (RISK_DENIED)
```

Risk evaluates the **sized order intent**: symbol, side, quantity, notional and the strategy that owns it. It does not evaluate the raw signal. This removes the current gap where the broker's computed size is never checked (CURRENT_STATE §5).

```python
class RiskDecision(BaseModel, frozen=True):
    decision_id: UUID
    intent_id: UUID
    verdict: Literal["ALLOW", "DENY"]
    reason_codes: tuple[RiskReason, ...]   # empty iff ALLOW
    limits_hash: str                       # hash of RiskLimits in force
    inputs_hash: str                       # hash of (intent, portfolio, market) evaluated
    evaluated_at: datetime
```

## 2. Fail-Closed Contract

- `evaluate()` is wrapped so that **any exception results in `DENY(RISK_ENGINE_ERROR)`**.
- Every input carries an `as_of` time. If market data, the portfolio snapshot or reconciliation is older than its limit, the result is `DENY(STALE_*)`.
- A missing value (`None`, NaN) in any field a check needs gives `DENY(MISSING_INPUT)`. It is never treated as zero.
- If the risk engine itself is unavailable (trading plane cannot construct it, limits file missing, or the hash does not match the approved one), **the trader refuses to start**.

## 3. RiskLimits

Loaded **once** at trader start-up from `config/risk_limits.yaml`. The object is a frozen Pydantic model. Its SHA-256 is recorded in the ledger (`RISK_CONFIG_LOADED`). There is no setter, no API route and no agent tool that modifies it. Changing it requires editing the file, restarting, and an operator-acknowledged ledger event.

| Limit | Scope | Existing value (to carry over) |
|---|---|---|
| `max_order_notional` | order | ₹20,000 |
| `max_position_notional` | symbol | ₹20,000 |
| `max_open_positions` | portfolio | 2 |
| `max_gross_exposure` | portfolio | ₹40,000 |
| `max_leverage` | portfolio | 1.0 (MIS treated as 1× for now) |
| `max_symbol_concentration_pct` | portfolio | 20% NAV |
| `max_correlated_exposure` | portfolio | new — computed from correlation clusters |
| `max_daily_loss` | portfolio, **realized + unrealized net** | ₹2,000 |
| `max_drawdown_pct` | portfolio, from high-water mark | **6%** → GLOBAL_KILL + manual review (operator decision 2026-10-03; replaces unenforced 8% in legacy config) |
| `max_spread_pct` | order | 0.15% |
| `min_adv` | instrument | ₹10 Cr |
| `circuit_buffer_pct` | instrument | 1.5% (from real band data, not fabricated) |
| `max_data_age` | system | new (e.g. 5 s for 1-minute strategies) |
| `max_orders_per_minute` | strategy and global | new |
| `trading_window` | session | 09:30–14:30 entries; flatten 15:10 IST |
| `strategy_must_be_approved` | strategy | lifecycle ∈ {PAPER_RUNNING (paper), LIVE (live)} |

Strategy-level `risk_requirements` can only tighten these limits. The effective limit is `min(global, strategy)`.

Strategy #001's OBI, RVOL and spam thresholds move **out of** risk into the strategy's entry conditions. They are alpha logic, not capital protection.

## 4. Check Order

The checks are ordered. **All of them run and every failure is reported**. The exception is the hard-stop group, which short-circuits.

1. **Hard stop (short-circuit):** kill switch at any applicable scope; live lock (LIVE mode); reconciliation not OK; clock invalid; risk limits hash mismatch.
2. **Strategy:** version approved for this mode; strategy not suspended; order rate.
3. **Session:** trading window; market open according to the `TradingCalendarProvider`; not past flatten time.
4. **Data:** quote age; snapshot age; spread; circuit proximity; ADV.
5. **Order:** notional; quantity > 0; price within band.
6. **Portfolio:** positions count; gross exposure; symbol concentration; correlated exposure; daily loss *including this order's stop-loss risk*; drawdown.

## 5. Position Sizing

`Sizer` runs before risk. It can only *propose* a quantity. Supported methods: `fixed_notional`, `pct_equity`, `risk_per_trade` (uses `stop_price`), `atr`, `vol_target`, `fractional_kelly`.

Fractional Kelly is permitted only if **all** of these hold. Otherwise the Sizer falls back to `fixed_notional` and records `KELLY_DISABLED_<reason>`:
- the strategy version has a `CalibrationResult` with `n ≥ min_calibration_samples` (default 300 out-of-sample outcomes);
- the Brier score and expected calibration error are within the configured gates;
- the payoff ratio `b` is measured from realized trades net of costs, not assumed;
- `fraction ≤ 0.25` and the result is capped by RiskLimits.

## 6. Kill-Switch Hierarchy

| Scope | Key | Triggered by |
|---|---|---|
| `GLOBAL_KILL` | — | operator; daily loss; drawdown; reconciliation failure; clock invalid; repeated risk-engine errors |
| `BROKER_KILL` | broker id | disconnect beyond timeout; rejected-order burst; unknown order state |
| `STRATEGY_KILL` | strategy id + version | drift detector; strategy loss limit; exception in `on_snapshot` |
| `SYMBOL_KILL` | symbol | circuit hit; data quality failure; corporate action |
| `ORDER_KILL` | order id | stuck order beyond timeout |

On trigger, in this order:
1. Stop new orders in scope. The in-memory flag is set first.
2. Cancel pending orders according to policy.
3. Flatten according to policy (`flatten_immediately` / `flatten_at_market_open` / `hold`). The default for GLOBAL is `flatten_immediately`.
4. **Persist** the state to the operational DB and to an absolute-path lockfile under `$TRADE_STATE_DIR`. If persisting fails, the process stays halted in memory and raises an alert. **It never continues trading.**
5. Alert the operator.
6. Require an explicit operator reset. Agents have no reset capability. The reset is a ledger event that includes the operator id.

On start-up, the trader reads the kill state **before** connecting to any data or broker. If the state is unreadable, it treats GLOBAL_KILL as active.

## 7. Reconciliation

The `Reconciler` runs on a schedule (default 30 s in PAPER/LIVE) and after every reconnect. It compares local positions, open orders and fills with the broker's. If any mismatch exceeds tolerance (tolerance is zero for quantity), it sets `RECONCILIATION_FAILURE` and engages `BROKER_KILL`, then `GLOBAL_KILL` if the mismatch persists. Trading stays halted until the operator resolves it. The system never "corrects" local state to match without an operator decision.

## 8. Fail-Closed Conditions → NO TRADE

Market data unavailable · data stale · broker unavailable · database unavailable · portfolio state uncertain · order state uncertain (a submitted order with no acknowledgement within timeout) · risk engine unavailable · strategy unavailable or hash mismatch · clock skew > threshold against an NTP or exchange time reference · reconciliation failed or overdue.

## 9. Live Lock

LOCKED by default and after every restart. Unlock requires **all** of:
1. the full test suite passes on the deployed commit (CI record matches the running commit hash);
2. the strategy version is `LIVE_APPROVED` (paper validation passed plus human approval);
3. the critical risk test group passed on this commit;
4. reconciliation is OK within its window;
5. the kill-switch drill passed in the current session (an automated trigger-and-reset against the paper adapter);
6. there are no open critical alerts;
7. explicit operator confirmation, using a typed confirmation phrase plus the operator token.

Limited live: `live_capital_fraction` (default 10% of RiskLimits) is ramped only by operator action, and only after a minimum number of live trades with drift inside limits.

## 10. Agent Isolation (Risk View)

- Agents run in a separate process without broker credentials and without DB write grants on risk, lifecycle, kill or live-lock tables.
- The trading plane exposes **no** network endpoint that changes limits, lifecycle or the live lock. The UI endpoints for kill/reset/approve require the operator token and are bound to `127.0.0.1`.
- Security tests (TEST_STRATEGY §6) attempt each bypass and must fail.
