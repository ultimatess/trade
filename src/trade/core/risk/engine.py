"""
Deterministic Risk Engine & Kill Switch (Layer 3 Core).
Owns 100% veto authority over model recommendations.
Enforces hardcoded capital caps, circuit limits, and emergency flattening.
"""

import os
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Tuple, List, Optional
from trade.core.config import config, state_dir
from trade.core.market_state.models import MarketSnapshot
from trade.core.signals.models import SocialSignal

logger = logging.getLogger("RiskEngine")

class RiskEngine:
    """Deterministic risk guardian. The kill switch fails closed."""

    def __init__(self, lockfile_path: Optional[str] = None):
        # Absolute path: the kill switch must not depend on the working directory.
        path = Path(lockfile_path) if lockfile_path else state_dir() / config.LOCKFILE_PATH
        self.lockfile_path = str(path.resolve())
        # In-memory halt survives a failed lockfile write; only an explicit clear resets it.
        self._halted_in_memory = False

    def is_kill_switch_active(self) -> bool:
        """Active if halted in memory, the lockfile exists, or the lockfile state cannot be read."""
        if self._halted_in_memory:
            return True
        try:
            Path(self.lockfile_path).stat()
            return True
        except FileNotFoundError:
            return False
        except OSError as e:
            logger.critical(f"Kill switch state unreadable ({e}); treating as ACTIVE")
            return True

    def trigger_kill_switch(self, reason: str) -> None:
        """Engages the kill switch: halt in memory first, then persist the lockfile."""
        self._halted_in_memory = True
        logger.critical(f"EMERGENCY KILL SWITCH ENGAGED: {reason}")
        try:
            Path(self.lockfile_path).parent.mkdir(parents=True, exist_ok=True)
            with open(self.lockfile_path, "w") as f:
                f.write(f"REASON: {reason}\nTRIGGERED_AT: {datetime.now(timezone.utc).isoformat()}\n")
        except Exception as e:
            logger.critical(f"Failed to persist kill switch lockfile ({e}); remaining HALTED in memory")

    def clear_kill_switch(self) -> bool:
        """Manual operator unlock. Stays halted if the lockfile cannot be removed."""
        try:
            os.remove(self.lockfile_path)
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.critical(f"Failed to remove kill switch lockfile ({e}); remaining HALTED")
            return False
        was_active = self._halted_in_memory
        self._halted_in_memory = False
        if was_active or not self.is_kill_switch_active():
            logger.info("Kill switch reset by operator.")
        return True

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
