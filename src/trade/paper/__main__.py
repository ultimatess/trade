"""
Paper Trading Entry Point.
Runs a demonstration cycle of the live execution loop against the paper broker.
"""

import logging

from trade.core.config import config, log_config_fingerprint
from trade.paper.trading_system import QuantTradingSystem


def main() -> None:
    log_config_fingerprint(logging.getLogger("Config"))
    system = QuantTradingSystem()
    print("=" * 70)
    print("AUTONOMOUS QUANT TRADING SYSTEM - NSE INTRADAY")
    print(f"Starting NAV: ₹{system.broker.total_equity:,.2f} | Max Loss Gate: ₹{config.MAX_DAILY_LOSS:,.2f}")
    print("=" * 70)

    # Run demonstration cycle with a high-conviction momentum setup
    test_tweets = [
        "$TATASTEEL massive volume spike on institutional buying",
        "$TATASTEEL breakout above 155 resistance with strong delivery",
        "$TATASTEEL quarterly numbers beat estimates",
    ]

    system.run_single_cycle(
        symbol="TATASTEEL",
        current_price=156.40,
        bid_depth=45000,
        ask_depth=12000,
        relative_volume=4.2,
        adv_inr=180_000_000.0,
        sample_tweets=test_tweets,
        simulated_time_str="10:15",
    )


if __name__ == "__main__":
    main()
