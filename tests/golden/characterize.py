"""
Golden-master characterisation of legacy behaviour (Phase 1, Task 1).

Captures the exact outputs of the pre-refactor implementation so that every
Phase 1 change can be proven behaviour-preserving. Known defects (D-001 etc.,
see docs/CURRENT_STATE.md) are captured AS-IS on purpose: golden values are
"what the code did", not "what is correct".

Regenerate only in a commit that references a defect id:
    python -m tests.golden.characterize --write
"""

import dataclasses
import json
import random
import sys
import tempfile
from pathlib import Path
from typing import Any

from tests.golden.legacy import oracle
from trade.backtesting.engine import BacktestRunner
from trade.brokers.paper import IndianPaperBroker
from trade.core.execution.charges import IndianTaxCalculator
from trade.core.risk.engine import RiskEngine
from trade.data.providers.social import SocialMomentumScanner
from trade.paper.trading_system import QuantTradingSystem

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _case_grid(n: int = 300, seed: int = 7) -> list[dict[str, Any]]:
    """Deterministic (snapshot, signal, context) inputs spanning pass and veto regions."""
    rng = random.Random(seed)
    cases = []
    for i in range(n):
        price = rng.choice([45.0, 156.4, 1450.0, 2480.0])
        spread = price * rng.choice([0.0004, 0.0012, 0.0030])
        upper = price * rng.choice([1.005, 1.02, 1.10])
        lower = price * rng.choice([0.995, 0.98, 0.90])
        cases.append(
            {
                "snapshot": dict(
                    symbol=f"SYM{i % 7}",
                    timestamp=1_700_000_000.0 + 60 * i,
                    last_price=price,
                    bid=price - spread / 2,
                    ask=price + spread / 2,
                    bid_depth=rng.uniform(0, 60_000),
                    ask_depth=rng.uniform(0, 60_000),
                    vwap=price * rng.uniform(0.99, 1.01),
                    relative_volume=rng.uniform(0.5, 9.0),
                    upper_circuit=upper,
                    lower_circuit=lower,
                    adv_inr=rng.choice([5e7, 1.5e8, 5e8]),
                ),
                "signal": dict(
                    symbol=f"SYM{i % 7}",
                    timestamp=1_700_000_000.0 + 60 * i,
                    mentions_count=rng.randint(0, 200),
                    velocity_zscore=rng.uniform(-1.0, 8.0),
                    unique_verified_ratio=rng.uniform(0.0, 1.0),
                    spam_cluster_score=rng.uniform(0.0, 1.0),
                ),
                "context": dict(
                    current_equity=100_000.0,
                    current_positions_count=rng.randint(0, 3),
                    daily_loss_incurred=rng.choice([0.0, 1999.99, 2000.0, 2500.0]),
                    time_str=rng.choice(["09:15", "09:30", "11:00", "14:30", "14:31", "15:20"]),
                ),
            }
        )
    # Near-threshold region: most gates pass, so individual gate flips are exercised.
    for i in range(150):
        price = rng.choice([156.4, 1450.0])
        cases.append(
            {
                "snapshot": dict(
                    symbol=f"NP{i % 5}",
                    timestamp=1_700_100_000.0 + 60 * i,
                    last_price=price,
                    bid=price * 0.9998,
                    ask=price * 1.0002,
                    bid_depth=rng.uniform(25_000, 60_000),
                    ask_depth=rng.uniform(2_000, 18_000),
                    vwap=price * rng.uniform(0.995, 1.003),
                    relative_volume=rng.uniform(2.5, 8.0),
                    upper_circuit=price * 1.10,
                    lower_circuit=price * 0.90,
                    adv_inr=1.5e8,
                ),
                "signal": dict(
                    symbol=f"NP{i % 5}",
                    timestamp=1_700_100_000.0 + 60 * i,
                    mentions_count=rng.randint(20, 120),
                    velocity_zscore=rng.uniform(2.0, 8.0),
                    unique_verified_ratio=rng.uniform(0.5, 1.0),
                    spam_cluster_score=rng.uniform(0.0, 0.4),
                ),
                "context": dict(
                    current_equity=100_000.0,
                    current_positions_count=rng.randint(0, 2),
                    daily_loss_incurred=rng.choice([0.0, 1999.99]),
                    time_str=rng.choice(["09:30", "11:00", "14:30"]),
                ),
            }
        )
    return cases


def characterize_reflex() -> list[dict[str, Any]]:
    """Pinned to the frozen legacy oracle (tests/golden/legacy/oracle.py)."""
    return [oracle.reflex_evaluate(c["snapshot"], c["signal"]) for c in _case_grid()]


def characterize_risk() -> list[dict[str, Any]]:
    """Pinned to the frozen legacy oracle; each case starts with a clear kill switch."""
    out = []
    for c in _case_grid():
        passed, reasons, triggered = oracle.pre_trade_gates(c["snapshot"], c["signal"], c["context"], False)
        out.append({"passed": passed, "reasons": reasons, "kill_after": triggered})
    return out


def characterize_charges() -> list[dict[str, Any]]:
    out = []
    for buy in [10.0, 100.0, 156.4, 1450.0, 2480.0]:
        for move in [-0.0075, -0.007, 0.0, 0.005, 0.01]:
            for qty in [1, 13, 200, 1000]:
                out.append(
                    {
                        "buy": buy,
                        "sell": round(buy * (1 + move), 2),
                        "qty": qty,
                        "charges": IndianTaxCalculator.calculate_charges(buy, round(buy * (1 + move), 2), qty),
                    }
                )
    return out


def _broker_state(b: IndianPaperBroker) -> dict[str, Any]:
    return {
        "cash": b.cash,
        "total_equity": b.total_equity,
        "daily_realized_loss": b.daily_realized_loss,
        "daily_realized_pnl": b.daily_realized_pnl,
        "positions": {k: dataclasses.asdict(v) for k, v in sorted(b.positions.items())},
        "trades": [dataclasses.asdict(t) for t in b.trade_history],
    }


def characterize_broker() -> dict[str, Any]:
    scenarios: dict[str, Any] = {}

    b = IndianPaperBroker(100_000.0)
    order = b.submit_bracket_entry("A", 0.2, 100.0, 1000.0)
    b.update_price_tick("A", 99.9, 1060.0)  # no exit
    b.update_price_tick("A", b.positions["A"].target_price + 0.1, 1120.0)
    scenarios["take_profit"] = {"order_qty": order.quantity, "order_price": order.price, **_broker_state(b)}

    b = IndianPaperBroker(100_000.0)
    b.submit_bracket_entry("B", 0.075, 1450.0, 1000.0)
    b.update_price_tick("B", 1430.0, 1060.0)
    scenarios["stop_loss"] = _broker_state(b)

    b = IndianPaperBroker(100_000.0)
    b.submit_bracket_entry("C", 0.1, 156.4, 1000.0)
    b.update_price_tick("C", 156.5, 1000.0 + 35 * 60)
    scenarios["timeout"] = _broker_state(b)

    b = IndianPaperBroker(100_000.0)
    b.submit_bracket_entry("D", 0.1, 500.0, 1000.0)
    b.submit_bracket_entry("E", 0.1, 800.0, 1000.0)
    dup = b.submit_bracket_entry("D", 0.1, 500.0, 1001.0)
    tiny = b.submit_bracket_entry("F", 0.001, 500.0, 1001.0)
    b.flatten_all({"D": 498.0}, 1200.0, reason="KILL_SWITCH")
    scenarios["flatten_dup_tiny"] = {"dup_is_none": dup is None, "tiny_is_none": tiny is None, **_broker_state(b)}

    b = IndianPaperBroker(100_000.0)
    t = 1000.0
    for _ in range(6):  # repeated trades expose D-001 cash drift
        b.submit_bracket_entry("G", 0.2, 100.0, t)
        b.update_price_tick("G", b.positions["G"].target_price, t + 60)
        t += 120
    scenarios["repeated_trades_cash_drift"] = _broker_state(b)
    return scenarios


def characterize_social() -> list[dict[str, Any]]:
    s = SocialMomentumScanner()
    t0 = 1_700_000_000.0
    seq = [
        ("TATASTEEL", ["a", "b", "c"], t0),
        ("TATASTEEL", ["a", "a", "a", "b"], t0 + 600),
        ("$tatasteel", [], t0 + 1200),
        ("RELIANCE", ["x"] * 10, t0 + 4000),
        ("TATASTEEL", ["p", "q"], t0 + 90000),
    ]
    return [dataclasses.asdict(s.sanitize_and_extract_signal(sym, tw, ts)) for sym, tw, ts in seq]


def characterize_backtest() -> dict[str, Any]:
    out = {}
    with tempfile.TemporaryDirectory():
        for seed, n in [(1, 500), (2, 500), (3, 500), (42, 500), (123, 500), (123, 150)]:
            out[f"seed{seed}_n{n}"] = BacktestRunner(seed=seed).run_multi_regime_simulation(total_candles=n)
    return out


def characterize_paper_cycle() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        system = QuantTradingSystem()
        system.risk_engine = RiskEngine(lockfile_path=str(Path(tmp) / "paper.lock"))
        tweets = ["$TATASTEEL volume spike", "$TATASTEEL breakout", "$TATASTEEL numbers beat"]
        steps = [
            ("TATASTEEL", 156.40, 45000, 12000, 4.2, 1.8e8, tweets, "10:15", 1_700_000_000.0),
            ("TATASTEEL", 158.10, 45000, 12000, 4.2, 1.8e8, tweets, "10:20", 1_700_000_300.0),
            ("ZOMATO", 162.50, 5000, 30000, 4.0, 1.8e8, ["x"], "10:25", 1_700_000_600.0),
            ("RELIANCE", 2480.0, 50000, 9000, 5.0, 5e8, ["r1", "r2", "r3"], "11:00", 1_700_002_000.0),
            ("RELIANCE", 2470.0, 50000, 9000, 5.0, 5e8, [], "15:12", 1_700_010_000.0),
        ]
        for sym, px, bd, ad, rv, adv, tw, ts_str, ts in steps:
            system.run_single_cycle(sym, px, bd, ad, rv, adv, tw, simulated_time_str=ts_str, timestamp=ts)
        return {
            "broker": _broker_state(system.broker),
            "brier": system.calibration_engine.compute_brier_score(),
            "calibration_history": system.calibration_engine.history,
        }


SUITES = {
    "reflex": characterize_reflex,
    "risk": characterize_risk,
    "charges": characterize_charges,
    "broker": characterize_broker,
    "social": characterize_social,
    "backtest": characterize_backtest,
    "paper_cycle": characterize_paper_cycle,
}


def normalize(obj: Any) -> Any:
    """Round-trip through JSON so tuples/lists and float repr compare identically."""
    return json.loads(json.dumps(obj, sort_keys=True))


def main(argv: list[str]) -> int:
    if "--write" not in argv:
        print("usage: python -m tests.golden.characterize --write")
        return 2
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for name, fn in SUITES.items():
        (FIXTURE_DIR / f"{name}.json").write_text(json.dumps(normalize(fn()), indent=1, sort_keys=True) + "\n")
        print(f"wrote {name}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
