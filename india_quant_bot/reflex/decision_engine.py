"""
Fast Reflex Decision Engine & Sizing Layer (Layer 2).
Provides sub-second calibrated probabilistic scoring on compact numeric states.
Calculates capped quarter-Kelly sizing and monitors Brier calibration scores.
"""

import math
import logging
from typing import List, Dict, Tuple, Optional
from india_quant_bot.config.settings import config
from india_quant_bot.core.models import MarketSnapshot, SocialSignal, ReflexDecision

logger = logging.getLogger("ReflexEngine")

class CalibrationEngine:
    """Tracks Brier score and applies monotonic reliability calibration."""

    def __init__(self):
        self.history: List[Tuple[float, int]] = []  # (forecast_prob, outcome 0 or 1)
        # Empirical piecewise calibration table: {forecast_bucket: actual_win_rate}
        self.calibration_map: Dict[float, float] = {
            0.50: 0.45,
            0.60: 0.54,
            0.65: 0.62,
            0.70: 0.68,
            0.75: 0.73,
            0.80: 0.77,
            0.85: 0.81,
            0.90: 0.84,
        }

    def record_outcome(self, forecast_prob: float, outcome: int) -> None:
        """Stores decision prediction and binary outcome (1=win, 0=loss)."""
        self.history.append((forecast_prob, outcome))

    def compute_brier_score(self) -> float:
        """Computes current Brier calibration score (closer to 0 is better)."""
        if not self.history:
            return 0.0
        return sum((p - y) ** 2 for p, y in self.history) / len(self.history)

    def calibrate(self, raw_prob: float) -> float:
        """Applies venue recalibration to raw model output."""
        # Interpolate against calibration table to counter venue overconfidence
        buckets = sorted(self.calibration_map.keys())
        if raw_prob <= buckets[0]:
            return self.calibration_map[buckets[0]]
        if raw_prob >= buckets[-1]:
            return self.calibration_map[buckets[-1]]

        for i in range(len(buckets) - 1):
            low, high = buckets[i], buckets[i+1]
            if low <= raw_prob <= high:
                weight = (raw_prob - low) / (high - low)
                return self.calibration_map[low] + weight * (self.calibration_map[high] - self.calibration_map[low])
        return raw_prob


class FastReflexScorer:
    """Sub-second quantitative decision scorer."""

    def __init__(self, calibration_engine: Optional[CalibrationEngine] = None):
        self.calibration = calibration_engine or CalibrationEngine()

    def evaluate(self, snapshot: MarketSnapshot, signal: SocialSignal) -> ReflexDecision:
        """
        Evaluates compact numeric snapshot and returns structured ReflexDecision.
        Executes in < 2ms locally.
        """
        # Question 1: Is social surge organic rather than bot spoofing?
        # Higher unique verified ratio and lower spam score -> high organic probability
        zscore_factor = min(1.0, max(0.0, (signal.velocity_zscore - 2.0) / 4.0))
        organic_score = (signal.unique_verified_ratio * 0.5) + (zscore_factor * 0.3) + ((1.0 - signal.spam_cluster_score) * 0.2)
        p_organic = min(0.99, max(0.01, organic_score))

        # Question 2: Directional momentum probability P(Delta P >= +1.0% before -0.7%)
        # Influenced by Order Book Imbalance, Relative Volume, VWAP divergence
        obi_factor = min(1.0, max(0.0, (snapshot.order_book_imbalance + 1.0) / 2.0))
        rvol_factor = min(1.0, snapshot.relative_volume / 8.0)
        vwap_alignment = 1.0 if snapshot.last_price >= snapshot.vwap else 0.4

        raw_win_prob = (obi_factor * 0.45) + (rvol_factor * 0.35) + (vwap_alignment * 0.20)
        raw_win_prob = min(0.95, max(0.10, raw_win_prob))

        # Recalibrate probability for local venue
        p_win_calibrated = self.calibration.calibrate(raw_win_prob)

        # Question 3: Setup Quality Score (0 to 100)
        setup_quality = (p_organic * 30.0) + (raw_win_prob * 50.0) + (min(1.0, snapshot.relative_volume / 5.0) * 20.0)

        # Sizing Calculation: Capped Quarter-Kelly
        # b = net win / net loss approx = 145.74 / 194.26 = 0.75
        b = 0.75
        p = p_win_calibrated
        q = 1.0 - p
        kelly_fraction = (b * p - q) / b if b > 0 else 0.0

        veto_reasons: List[str] = []
        if p_organic < 0.70:
            veto_reasons.append(f"REFLEX_ORGANIC_PROB_LOW: {p_organic:.2f} < 0.70")
        if p_win_calibrated < config.MIN_CALIBRATED_PROBABILITY:
            veto_reasons.append(f"REFLEX_WIN_PROB_LOW: {p_win_calibrated:.2f} < {config.MIN_CALIBRATED_PROBABILITY}")
        if setup_quality < config.MIN_QUALITY_SCORE:
            veto_reasons.append(f"REFLEX_QUALITY_SCORE_LOW: {setup_quality:.1f} < {config.MIN_QUALITY_SCORE}")

        passed_gates = len(veto_reasons) == 0

        # Sizing: Capped at 1/4 Kelly, ceiling at 20% NAV (₹20,000 on ₹1 Lakh)
        if passed_gates and kelly_fraction > 0:
            quarter_kelly = 0.25 * kelly_fraction
            recommended_fraction = min(quarter_kelly, 0.20)  # Capped at 20% NAV
        else:
            recommended_fraction = 0.0

        return ReflexDecision(
            symbol=snapshot.symbol,
            timestamp=snapshot.timestamp,
            p_organic=round(p_organic, 3),
            p_win_raw=round(raw_win_prob, 3),
            p_win_calibrated=round(p_win_calibrated, 3),
            setup_quality=round(setup_quality, 1),
            recommended_fraction=round(recommended_fraction, 4),
            passed_all_gates=passed_gates,
            veto_reasons=veto_reasons
        )
