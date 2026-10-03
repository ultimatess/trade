"""
Probability Calibration Engine.
Tracks Brier score and applies monotonic venue recalibration to raw strategy forecasts.
"""

from typing import List, Dict, Tuple


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
