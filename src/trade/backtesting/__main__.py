"""
Legacy synthetic simulation runner.
NOT EVIDENCE OF EDGE: the generator draws features from the same regime label as
the price shock (docs/CURRENT_STATE.md section 6). Kept as a Phase 1-2 golden fixture.
"""

from trade.backtesting.engine import BacktestRunner


def main() -> None:
    print("=" * 75)
    print("LEGACY SYNTHETIC SIMULATION (500 x 1-min synthetic candles) - NOT EVIDENCE OF EDGE")
    print("Gates: Sharpe > 1.5 | Max Drawdown < 15% | Hit Rate > 55% | t-stat > 2.0")
    print("=" * 75)

    runner = BacktestRunner(seed=42)
    metrics = runner.run_multi_regime_simulation(total_candles=500)

    def gate(passed: bool) -> str:
        return "PASS" if passed else "FAIL"

    m = metrics
    print(f"Total Completed Trades : {m['total_trades']}")
    print(f"Wins / Losses          : {m['wins']} W / {m['losses']} L")
    print(f"Hit Rate               : {m['hit_rate_pct']:.2f}%  (Gate: >= 55.0%) -> {gate(m['gate_hit_rate_passed'])}")
    print(f"Net Realized P&L       : ₹{m['net_pnl']:+,.2f} (After all Indian statutory charges)")
    print(f"Ending Equity          : ₹{m['final_equity']:,.2f}")
    print(f"Sharpe Ratio           : {m['sharpe_ratio']:.2f}  (Gate: >= 1.50)  -> {gate(m['gate_sharpe_passed'])}")
    print(f"Max Drawdown           : {m['max_drawdown_pct']:.2f}%  (Gate: <= 15.0%) -> {gate(m['gate_max_dd_passed'])}")
    print(f"t-stat (all trades)    : {m['t_statistic']:.2f}  (Gate: >= 2.00)  -> {gate(m['gate_t_stat_passed'])}")
    print("-" * 75)
    status = "PASSED" if m["passed_all_gates"] else "REJECTED"
    print(f"LEGACY GATE STATUS     : {status} on synthetic data; not a validation result")
    print("=" * 75)


if __name__ == "__main__":
    main()
