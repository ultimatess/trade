"""
Strategy #001 - Social Momentum v1.

Migrated unchanged from the legacy FastReflexScorer plus the strategy-specific
pre-trade gates (OBI, relative volume, spam score). Parity with the legacy code is
proven by tests/golden/test_strategy_parity.py. IMMUTABLE: see FINGERPRINT.
"""

from __future__ import annotations

from pathlib import Path

from trade.core.strategy.contract import (
    Observation,
    Signal,
    Strategy,
    StrategyDecision,
    load_strategy_spec,
)

VERSION_DIR = Path(__file__).resolve().parent

# Legacy hand-set calibration table (D-003): not fitted to any data.
LEGACY_CALIBRATION_MAP = {
    0.50: 0.45,
    0.60: 0.54,
    0.65: 0.62,
    0.70: 0.68,
    0.75: 0.73,
    0.80: 0.77,
    0.85: 0.81,
    0.90: 0.84,
}


def legacy_calibrate(raw_prob: float) -> float:
    buckets = sorted(LEGACY_CALIBRATION_MAP)
    if raw_prob <= buckets[0]:
        return LEGACY_CALIBRATION_MAP[buckets[0]]
    if raw_prob >= buckets[-1]:
        return LEGACY_CALIBRATION_MAP[buckets[-1]]
    for low, high in zip(buckets, buckets[1:], strict=False):
        if low <= raw_prob <= high:
            weight = (raw_prob - low) / (high - low)
            return LEGACY_CALIBRATION_MAP[low] + weight * (LEGACY_CALIBRATION_MAP[high] - LEGACY_CALIBRATION_MAP[low])
    return raw_prob


class SocialMomentumV1(Strategy):
    def __init__(self) -> None:
        self.spec = load_strategy_spec(VERSION_DIR)
        self.p = dict(self.spec.parameters)
        self.exits = self.spec.exit_conditions

    def on_observation(self, obs: Observation) -> StrategyDecision:
        if obs.social is None:
            return StrategyDecision(rejection_codes=("NO_SOCIAL_SIGNAL",))
        m, s, p = obs.market, obs.social, self.p

        # Q1: is the social surge organic rather than bot spoofing?
        zscore_factor = min(1.0, max(0.0, (s.velocity_zscore - 2.0) / 4.0))
        organic_score = (s.unique_verified_ratio * 0.5) + (zscore_factor * 0.3) + ((1.0 - s.spam_cluster_score) * 0.2)
        p_organic = min(0.99, max(0.01, organic_score))

        # Q2: P(+TP before -SL) score from order-book imbalance, relative volume, VWAP side
        obi = m.order_book_imbalance
        obi_factor = min(1.0, max(0.0, (obi + 1.0) / 2.0))
        rvol_factor = min(1.0, m.relative_volume / 8.0)
        vwap_alignment = 1.0 if m.last_price >= m.vwap else 0.4
        raw_win = min(0.95, max(0.10, (obi_factor * 0.45) + (rvol_factor * 0.35) + (vwap_alignment * 0.20)))
        p_win = legacy_calibrate(raw_win)  # labelled "calibrated" in legacy code; it is not (D-003)

        # Q3: setup quality 0-100
        quality = (p_organic * 30.0) + (raw_win * 50.0) + (min(1.0, m.relative_volume / 5.0) * 20.0)

        b = p["kelly_payoff_ratio"]
        kelly = (b * p_win - (1.0 - p_win)) / b if b > 0 else 0.0

        rejections = []
        if obi < p["min_order_book_imbalance"]:
            rejections.append("OBI_BELOW_MIN")
        if m.relative_volume < p["min_relative_volume"]:
            rejections.append("RVOL_BELOW_MIN")
        if s.spam_cluster_score > p["max_spam_score"]:
            rejections.append("SPAM_CLUSTER_DETECTED")
        if p_organic < p["min_p_organic"]:
            rejections.append("P_ORGANIC_BELOW_MIN")
        if p_win < p["min_p_win"]:
            rejections.append("P_WIN_BELOW_MIN")
        if quality < p["min_quality_score"]:
            rejections.append("QUALITY_BELOW_MIN")

        reflex_passed = not {"P_ORGANIC_BELOW_MIN", "P_WIN_BELOW_MIN", "QUALITY_BELOW_MIN"} & set(rejections)
        fraction = min(p["kelly_fraction"] * kelly, p["max_fraction"]) if reflex_passed and kelly > 0 else 0.0
        diagnostics = {
            "p_organic": round(p_organic, 3),
            "p_win_raw": round(raw_win, 3),
            "p_win_calibrated": round(p_win, 3),
            "setup_quality": round(quality, 1),
            "recommended_fraction": round(fraction, 4),
            "reflex_passed": reflex_passed,
        }
        if rejections or fraction <= 0:
            return StrategyDecision(rejection_codes=tuple(rejections), diagnostics=diagnostics)

        tp, sl = float(self.exits["take_profit_pct"]), float(self.exits["stop_loss_pct"])
        signal = Signal(
            strategy_id=self.strategy_id,
            strategy_version=self.version,
            timestamp=obs.as_of,
            symbol=obs.symbol,
            side="BUY",
            reference_price=m.last_price,
            stop_price=round(m.last_price * (1.0 - sl), 2),
            take_profit_price=round(m.last_price * (1.0 + tp), 2),
            take_profit_pct=tp,
            stop_loss_pct=sl,
            expected_holding_period_s=int(self.exits["time_stop_minutes"]) * 60,
            probability=None,  # uncalibrated strategy: no probability is claimed
            confidence=round(p_win, 3),
            setup_quality=round(quality, 1),
            requested_fraction=round(fraction, 4),
            reason_codes=("ENTRY_SOCIAL_MOMENTUM",),
            features=(
                ("order_book_imbalance", obi),
                ("relative_volume", m.relative_volume),
                ("vwap", m.vwap),
                ("velocity_zscore", s.velocity_zscore),
                ("unique_verified_ratio", s.unique_verified_ratio),
                ("spam_cluster_score", s.spam_cluster_score),
            ),
        )
        return StrategyDecision(signals=(signal,), diagnostics=diagnostics)
