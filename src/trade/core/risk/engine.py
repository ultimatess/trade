"""
Deterministic Risk Engine & Kill Switch (Layer 3 Core).
Owns 100% veto authority over model recommendations.
Enforces hardcoded capital caps, circuit limits, and emergency flattening.
"""

import os
import logging
from typing import Tuple, List, Optional
from trade.core.config import config
from trade.core.market_state.models import MarketSnapshot
from trade.core.signals.models import SocialSignal

logger = logging.getLogger("RiskEngine")

class RiskEngine:
    """Zero-trust deterministic risk guardian."""

    def __init__(self, lockfile_path: Optional[str] = None):
        self.lockfile_path = lockfile_path or config.LOCKFILE_PATH

    def is_kill_switch_active(self) -> bool:
        """Checks if emergency halt lockfile is present."""
        return os.path.exists(self.lockfile_path)

    def trigger_kill_switch(self, reason: str) -> None:
        """Engages the emergency kill switch, dropping lockfile."""
        logger.critical(f"EMERGENCY KILL SWITCH ENGAGED: {reason}")
        try:
            with open(self.lockfile_path, "w") as f:
                f.write(f"REASON: {reason}\nTRIGGERED_AT: {os.times()}\n")
        except Exception as e:
            logger.error(f"Failed to write lockfile: {e}")

    def clear_kill_switch(self) -> bool:
        """Allows manual administrative unlock."""
        if os.path.exists(self.lockfile_path):
            os.remove(self.lockfile_path)
            logger.info("Kill switch reset by authorized operator.")
            return True
        return False

    def validate_pre_trade_gates(
        self,
        snapshot: MarketSnapshot,
        signal: SocialSignal,
        current_equity: float,
        current_positions_count: int,
        daily_loss_incurred: float,
        time_str: str  # Format "HH:MM"
    ) -> Tuple[bool, List[str]]:
        """
        Executes strict deterministic gate validation before an order can touch the broker.
        Returns: (passed: bool, veto_reasons: List[str])
        """
        veto_reasons: List[str] = []

        # Gate 1: Kill switch check
        if self.is_kill_switch_active():
            veto_reasons.append("KILL_SWITCH_ACTIVE")
            return False, veto_reasons

        # Gate 2: Daily max loss boundary
        if daily_loss_incurred >= config.MAX_DAILY_LOSS:
            self.trigger_kill_switch(f"Daily loss ₹{daily_loss_incurred} breached limit ₹{config.MAX_DAILY_LOSS}")
            veto_reasons.append("DAILY_LOSS_LIMIT_EXCEEDED")
            return False, veto_reasons

        # Gate 3: Market Trading Hours (IST)
        if time_str < config.MARKET_START_ENTRY or time_str > config.MARKET_STOP_ENTRY:
            veto_reasons.append(f"OUTSIDE_TRADING_WINDOW: {time_str}")

        # Gate 4: Concurrent Open Positions
        if current_positions_count >= config.MAX_CONCURRENT_POSITIONS:
            veto_reasons.append(f"MAX_POSITIONS_REACHED: {current_positions_count}/{config.MAX_CONCURRENT_POSITIONS}")

        # Gate 5: Circuit Limit Proximity (Crucial for India)
        if snapshot.dist_to_upper_circuit_pct < config.CIRCUIT_BUFFER_PCT:
            veto_reasons.append(f"TOO_CLOSE_TO_UPPER_CIRCUIT: {snapshot.dist_to_upper_circuit_pct:.3%}")
        if snapshot.dist_to_lower_circuit_pct < config.CIRCUIT_BUFFER_PCT:
            veto_reasons.append(f"TOO_CLOSE_TO_LOWER_CIRCUIT: {snapshot.dist_to_lower_circuit_pct:.3%}")

        # Gate 6: Bid-Ask Spread Limit
        if snapshot.spread_pct > config.MAX_SPREAD_PCT:
            veto_reasons.append(f"SPREAD_TOO_WIDE: {snapshot.spread_pct:.3%}")

        # Gate 7: Liquidity (Minimum ADV ₹10 Crore)
        if snapshot.adv_inr < config.MIN_ADV_INR:
            veto_reasons.append(f"INSUFFICIENT_ADV: ₹{snapshot.adv_inr/1e7:.2f}Cr < ₹10Cr")

        # Gate 8: Order Book Imbalance (OBI >= +0.35)
        if snapshot.order_book_imbalance < config.MIN_ORDER_BOOK_IMBALANCE:
            veto_reasons.append(f"INSUFFICIENT_BUY_DEPTH_OBI: {snapshot.order_book_imbalance:.2f} < {config.MIN_ORDER_BOOK_IMBALANCE}")

        # Gate 9: Relative Volume Breakout
        if snapshot.relative_volume < config.MIN_RELATIVE_VOLUME:
            veto_reasons.append(f"VOLUME_SURGE_ABSENT: {snapshot.relative_volume:.1f}x < {config.MIN_RELATIVE_VOLUME}x")

        # Gate 10: Social Anti-Spam / Bot Cluster Check
        if signal.spam_cluster_score > 0.30:
            veto_reasons.append(f"BOT_SPAM_CLUSTER_DETECTED: score {signal.spam_cluster_score:.2f} > 0.30")

        passed = len(veto_reasons) == 0
        return passed, veto_reasons
