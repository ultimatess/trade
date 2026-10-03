"""
Frozen legacy oracle for Strategy #001 parity (Phase 2).

A self-contained copy of the pre-refactor FastReflexScorer, CalibrationEngine.calibrate
and RiskEngine.validate_pre_trade_gates logic as of commit 1b98aab, with the legacy
TradingConfig constants inlined. It depends on nothing in `trade`, so refactors of the
production code cannot change it. The golden fixtures `reflex.json` and `risk.json`
pin this oracle; the parity tests compare Strategy #001 v1 against it.

DO NOT EDIT. This file is evidence, not production code.
"""

from typing import Any

LEGACY = {
    "MAX_CONCURRENT_POSITIONS": 2,
    "MAX_DAILY_LOSS": 2_000.0,
    "MAX_SPREAD_PCT": 0.0015,
    "CIRCUIT_BUFFER_PCT": 0.015,
    "MIN_ADV_INR": 100_000_000.0,
    "MIN_ORDER_BOOK_IMBALANCE": 0.35,
    "MIN_RELATIVE_VOLUME": 3.0,
    "MIN_CALIBRATED_PROBABILITY": 0.65,
    "MIN_QUALITY_SCORE": 75.0,
    "MARKET_START_ENTRY": "09:30",
    "MARKET_STOP_ENTRY": "14:30",
}

CALIBRATION_MAP = {0.50: 0.45, 0.60: 0.54, 0.65: 0.62, 0.70: 0.68, 0.75: 0.73, 0.80: 0.77, 0.85: 0.81, 0.90: 0.84}


def spread_pct(s: dict[str, Any]) -> float:
    return 1.0 if s["last_price"] <= 0 else (s["ask"] - s["bid"]) / s["last_price"]


def order_book_imbalance(s: dict[str, Any]) -> float:
    total = s["bid_depth"] + s["ask_depth"]
    return 0.0 if total <= 0 else (s["bid_depth"] - s["ask_depth"]) / total


def dist_upper(s: dict[str, Any]) -> float:
    return 0.0 if s["last_price"] <= 0 else (s["upper_circuit"] - s["last_price"]) / s["last_price"]


def dist_lower(s: dict[str, Any]) -> float:
    return 0.0 if s["last_price"] <= 0 else (s["last_price"] - s["lower_circuit"]) / s["last_price"]


def calibrate(raw_prob: float) -> float:
    buckets = sorted(CALIBRATION_MAP.keys())
    if raw_prob <= buckets[0]:
        return CALIBRATION_MAP[buckets[0]]
    if raw_prob >= buckets[-1]:
        return CALIBRATION_MAP[buckets[-1]]
    for i in range(len(buckets) - 1):
        low, high = buckets[i], buckets[i + 1]
        if low <= raw_prob <= high:
            weight = (raw_prob - low) / (high - low)
            return CALIBRATION_MAP[low] + weight * (CALIBRATION_MAP[high] - CALIBRATION_MAP[low])
    return raw_prob


def reflex_evaluate(snap: dict[str, Any], sig: dict[str, Any]) -> dict[str, Any]:
    zscore_factor = min(1.0, max(0.0, (sig["velocity_zscore"] - 2.0) / 4.0))
    organic_score = (
        (sig["unique_verified_ratio"] * 0.5) + (zscore_factor * 0.3) + ((1.0 - sig["spam_cluster_score"]) * 0.2)
    )
    p_organic = min(0.99, max(0.01, organic_score))
    obi_factor = min(1.0, max(0.0, (order_book_imbalance(snap) + 1.0) / 2.0))
    rvol_factor = min(1.0, snap["relative_volume"] / 8.0)
    vwap_alignment = 1.0 if snap["last_price"] >= snap["vwap"] else 0.4
    raw_win_prob = (obi_factor * 0.45) + (rvol_factor * 0.35) + (vwap_alignment * 0.20)
    raw_win_prob = min(0.95, max(0.10, raw_win_prob))
    p_win_calibrated = calibrate(raw_win_prob)
    setup_quality = (p_organic * 30.0) + (raw_win_prob * 50.0) + (min(1.0, snap["relative_volume"] / 5.0) * 20.0)
    b = 0.75
    p = p_win_calibrated
    q = 1.0 - p
    kelly_fraction = (b * p - q) / b if b > 0 else 0.0
    veto_reasons = []
    if p_organic < 0.70:
        veto_reasons.append(f"REFLEX_ORGANIC_PROB_LOW: {p_organic:.2f} < 0.70")
    if p_win_calibrated < LEGACY["MIN_CALIBRATED_PROBABILITY"]:
        veto_reasons.append(f"REFLEX_WIN_PROB_LOW: {p_win_calibrated:.2f} < {LEGACY['MIN_CALIBRATED_PROBABILITY']}")
    if setup_quality < LEGACY["MIN_QUALITY_SCORE"]:
        veto_reasons.append(f"REFLEX_QUALITY_SCORE_LOW: {setup_quality:.1f} < {LEGACY['MIN_QUALITY_SCORE']}")
    passed = len(veto_reasons) == 0
    recommended_fraction = min(0.25 * kelly_fraction, 0.20) if passed and kelly_fraction > 0 else 0.0
    return {
        "symbol": snap["symbol"],
        "timestamp": snap["timestamp"],
        "p_organic": round(p_organic, 3),
        "p_win_raw": round(raw_win_prob, 3),
        "p_win_calibrated": round(p_win_calibrated, 3),
        "setup_quality": round(setup_quality, 1),
        "recommended_fraction": round(recommended_fraction, 4),
        "passed_all_gates": passed,
        "veto_reasons": veto_reasons,
    }


def pre_trade_gates(
    snap: dict[str, Any], sig: dict[str, Any], ctx: dict[str, Any], kill_switch_active: bool
) -> tuple[bool, list[str], bool]:
    """Returns (passed, veto_reasons, kill_switch_triggered)."""
    c = LEGACY
    if kill_switch_active:
        return False, ["KILL_SWITCH_ACTIVE"], False
    if ctx["daily_loss_incurred"] >= c["MAX_DAILY_LOSS"]:
        return False, ["DAILY_LOSS_LIMIT_EXCEEDED"], True
    r = []
    t = ctx["time_str"]
    if t < c["MARKET_START_ENTRY"] or t > c["MARKET_STOP_ENTRY"]:
        r.append(f"OUTSIDE_TRADING_WINDOW: {t}")
    n = ctx["current_positions_count"]
    if n >= c["MAX_CONCURRENT_POSITIONS"]:
        r.append(f"MAX_POSITIONS_REACHED: {n}/{c['MAX_CONCURRENT_POSITIONS']}")
    if dist_upper(snap) < c["CIRCUIT_BUFFER_PCT"]:
        r.append(f"TOO_CLOSE_TO_UPPER_CIRCUIT: {dist_upper(snap):.3%}")
    if dist_lower(snap) < c["CIRCUIT_BUFFER_PCT"]:
        r.append(f"TOO_CLOSE_TO_LOWER_CIRCUIT: {dist_lower(snap):.3%}")
    if spread_pct(snap) > c["MAX_SPREAD_PCT"]:
        r.append(f"SPREAD_TOO_WIDE: {spread_pct(snap):.3%}")
    if snap["adv_inr"] < c["MIN_ADV_INR"]:
        r.append(f"INSUFFICIENT_ADV: ₹{snap['adv_inr'] / 1e7:.2f}Cr < ₹10Cr")
    if order_book_imbalance(snap) < c["MIN_ORDER_BOOK_IMBALANCE"]:
        r.append(f"INSUFFICIENT_BUY_DEPTH_OBI: {order_book_imbalance(snap):.2f} < {c['MIN_ORDER_BOOK_IMBALANCE']}")
    if snap["relative_volume"] < c["MIN_RELATIVE_VOLUME"]:
        r.append(f"VOLUME_SURGE_ABSENT: {snap['relative_volume']:.1f}x < {c['MIN_RELATIVE_VOLUME']}x")
    if sig["spam_cluster_score"] > 0.30:
        r.append(f"BOT_SPAM_CLUSTER_DETECTED: score {sig['spam_cluster_score']:.2f} > 0.30")
    return len(r) == 0, r, False
