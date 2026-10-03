# Strategy #001 — Social Momentum v1

| | |
|---|---|
| Status | Legacy migration; **unvalidated** (no historical social or order-book data yet) |
| Provenance | Hand-set weights and thresholds; number of prior trials unknown |
| Probability | **Uncalibrated.** The "calibration" table is hard-coded (D-003). No probability is emitted |
| Parity | Identical to the legacy FastReflexScorer + strategy gates on 450 golden cases (`tests/golden/test_strategy_parity.py`) |
| Immutability | `strategy.yaml` + `strategy.py` are pinned by `FINGERPRINT`; changes require v2 |

**Entry (long only):** OBI ≥ 0.35, RVOL ≥ 3.0, spam ≤ 0.30, p_organic ≥ 0.70, p_win score ≥ 0.65, quality ≥ 75.
**Sizing:** v1 *requests* quarter-Kelly, but it is uncalibrated, so system policy refuses Kelly and sizes at a fixed ₹20,000 (`config/risk_limits.yaml`).
**Exit:** +1.0% take profit, −0.7% stop (anchored to the fill), 35-minute time stop, flatten at 15:10 IST.

Specification and rationale: [`/strategy.md`](../../../../../strategy.md). Known limitations: `docs/KNOWN_DEFECTS.md`.
A v2 built on recorded Reddit data would replace the "verified ratio" (not available on Reddit) with unique-author features (D-010).
