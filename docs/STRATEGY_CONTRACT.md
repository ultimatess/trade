# Strategy Contract

Every strategy, whether hand-written, migrated or AI-generated, implements this contract. Execution never parses natural language. It consumes only the typed `Signal`.

## 1. Strategy Specification (`strategy.yaml`)

```yaml
strategy_id: social_momentum          # stable slug
version: 1                            # integer; immutable once registered
name: Social Momentum 1% Scalp
description: >
  Long-only NSE intraday entry when social mention acceleration coincides
  with order-book buying pressure and a relative-volume surge.
hypothesis_id: H-0001                 # required for every non-legacy strategy
universe:
  market: NSE
  instruments: {selector: liquid_cash_equities, min_adv_inr: 100000000}
timeframe: 1m
session: {entry_start: "09:30", entry_end: "14:30", flatten_at: "15:10", tz: Asia/Kolkata}
required_data:                        # resolved against data providers
  - {kind: ohlcv, timeframe: 1m, lookback: 20}
  - {kind: quote}
  - {kind: order_book, depth: 5}
  - {kind: social_mentions, lookback: 24h}
features:                             # each must exist in the feature registry
  - {name: relative_volume, version: 1, params: {window: 20}}
  - {name: order_book_imbalance, version: 1}
  - {name: vwap_session, version: 1}
  - {name: mention_velocity_z, version: 1, params: {recent: 1h, baseline: 24h}}
  - {name: mention_duplication_rate, version: 1}
parameters:                           # every tunable value; nothing tunable may be hidden in code
  min_obi: {value: 0.35, search_range: null}
  min_rvol: {value: 3.0}
  max_spam: {value: 0.30}
  min_p_organic: {value: 0.70}
  min_p_win: {value: 0.65}
  min_quality: {value: 75.0}
entry_conditions: [obi_ge_min, rvol_ge_min, spam_le_max, p_organic_ge_min, p_win_ge_min, quality_ge_min]
exit_conditions:
  take_profit: {type: pct_from_fill, value: 0.010}
  stop: {type: pct_from_fill, value: 0.007}
  time_stop: {minutes: 35}
  session_flatten: true
invalidation_conditions: [spam_cluster_detected, data_stale]
position_sizing: {method: fractional_kelly, fraction: 0.25, cap_fraction: 0.20, requires_calibration: true}
risk_requirements: {max_positions: 2, max_notional_inr: 20000}
failure_modes:
  - Bot-driven pump & dump with organic-looking text
  - Social signal lags price (signal arrives after move)
  - Thin books where OBI flips within seconds
cost_model: nse_intraday@1
```

`risk_requirements` can only **tighten** the global `RiskLimits`. A strategy that requests looser limits is rejected at registration.

## 2. Python Interface

> **Implementation status (Phase 2):** implemented in `src/trade/core/strategy/contract.py` with frozen dataclasses (Pydantic is deferred until agent JSON schemas are needed). The strategy input is an `Observation` (symbol, `as_of`, session time, market snapshot, optional social signal). It rejects any input stamped after `as_of`. The output is a `StrategyDecision` (signals, plus machine-readable rejection codes and diagnostics). v1 is stateless, so `StrategyState` is not implemented yet. Bracket offsets (`take_profit_pct`, `stop_loss_pct`) travel with the signal and are anchored to the fill price by execution.

```python
class Strategy(ABC):
    spec: StrategySpec                     # parsed + validated strategy.yaml

    @abstractmethod
    def required_features(self) -> list[FeatureRef]: ...

    @abstractmethod
    def on_snapshot(self, snap: MarketSnapshot, state: StrategyState) -> list[Signal]:
        """Pure function of (snapshot, own state). No I/O, no clock reads,
        no randomness except via state.rng seeded by the runner."""

    def on_fill(self, fill: Fill, state: StrategyState) -> None: ...
    def on_session_end(self, state: StrategyState) -> None: ...
```

Enforced constraints, checked by contract tests in `tests/contract/`:
- `on_snapshot` must not do network or file I/O. This is enforced by running under a sandbox fixture that patches `socket`, `open` and `time`.
- It must be deterministic: same snapshot sequence plus same seed gives an identical signal sequence.
- It may access only features declared in `required_features()`. Accessing an undeclared feature raises an error.
- It must not import `trade.brokers`, `trade.core.execution.adapters` or `trade.agents`. This is enforced by an import-linter rule.

## 3. Signal

```python
class Signal(BaseModel, frozen=True):
    signal_id: UUID
    strategy_id: str
    strategy_version: int
    timestamp: datetime                    # == snapshot.as_of; never later data
    symbol: str
    side: Literal["BUY", "SELL", "FLAT"]
    reference_price: Decimal
    stop_price: Decimal | None
    take_profit_price: Decimal | None
    expected_holding_period: timedelta
    probability: float | None              # only if calibrated; else None
    confidence: float | None               # model self-reported; never used as probability
    setup_quality: float | None
    reason_codes: tuple[str, ...]          # machine-readable, from a declared enum
    feature_snapshot_id: str               # hash of the exact feature values used
```

Validation rules:
- `timestamp` must equal the source snapshot's `as_of`.
- For BUY, `stop_price < reference_price < take_profit_price`. For SELL, the order is reversed.
- `probability` must be `None` unless the strategy version has a passing `CalibrationResult`.
- Unknown reason codes are rejected.

A Signal is a **proposal**. It contains no quantity. Quantity is decided by the Sizer and then by the RiskEngine.

## 4. Versioning & Immutability

- `(strategy_id, version)` is registered once. The registry stores the SHA-256 of `strategy.yaml` plus `strategy.py` (plus pinned model artifacts). If the code is loaded with a different hash, the load fails.
- Any change, including a parameter value, creates `version + 1` in `DRAFT`.
- Champion/challenger: challengers are new versions or new strategies. The champion is never edited in place.

## 5. Lifecycle

```
DRAFT → RESEARCH → BACKTESTING → VALIDATION → OOS → WALK_FORWARD → STRESS_TEST
      → PAPER_PENDING → PAPER_RUNNING → PAPER_VALIDATED → LIVE_PENDING
      → LIVE_APPROVED → LIVE → (SUSPENDED ⇄ investigation) → RETIRED
Any state → ARCHIVED (no edge found / insufficient evidence)
```

- Transitions run in `trade.core.strategy.lifecycle`, a deterministic state machine.
- Each transition has a gate function that reads stored evidence (experiment IDs, metrics). The gate is not computed on the fly by an agent.
- `PAPER_VALIDATED → LIVE_PENDING`, `LIVE_PENDING → LIVE_APPROVED` and `SUSPENDED → LIVE` require a **human approval record**.
- Every transition writes a `STRATEGY_PROMOTED` / `STRATEGY_SUSPENDED` / `STRATEGY_CHANGED` ledger event.
- Agents have no tool that calls `transition()`.

## 6. Strategy #001 — Social Momentum v1 Parity

The migration must reproduce the current `FastReflexScorer` and gate behaviour exactly:
- The same weights, thresholds and hard-coded calibration map, declared as v1 legacy parameters with `provenance: hand_set_unvalidated`.
- Golden-master tests take a fixed corpus of (snapshot, social signal) inputs and assert identical `p_organic`, `p_win_raw`, `p_win_calibrated`, `setup_quality`, `recommended_fraction` and veto reasons, comparing the old implementation against `SocialMomentumV1`.
- Because the calibration map is not empirical, v1 is marked `probability: uncalibrated`. Kelly sizing is reproduced for parity tests only. Under the new Sizer, an uncalibrated strategy falls back to fixed-notional sizing.
