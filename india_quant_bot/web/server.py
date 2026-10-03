"""
Local Web Server & REST API for Autonomous Indian Quant Trading Bot.
Uses Python's standard library ThreadingHTTPServer. Zero external dependencies.
"""

import os
import json
import time
import logging
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime

from india_quant_bot.config.settings import config
from india_quant_bot.core.risk_engine import RiskEngine
from india_quant_bot.reflex.decision_engine import FastReflexScorer, CalibrationEngine
from india_quant_bot.social.scraper import SocialMomentumScanner
from india_quant_bot.execution.paper_broker import IndianPaperBroker
from india_quant_bot.backtest.backtester import BacktestRunner, BacktestMetrics
from india_quant_bot.core.models import MarketSnapshot, SocialSignal

logger = logging.getLogger("QuantWebServer")

class SystemStateHolder:
    """Thread-safe state manager for the quant trading system."""

    def __init__(self):
        self.lock = threading.RLock()
        self.risk_engine = RiskEngine()
        self.calibration_engine = CalibrationEngine()
        self.reflex_scorer = FastReflexScorer(self.calibration_engine)
        self.social_scanner = SocialMomentumScanner()
        self.broker = IndianPaperBroker(initial_capital=config.STARTING_CAPITAL)
        
        self.bot_running = False
        self.bot_thread = None
        self.recent_logs = []
        self.recent_signals = []
        self.latest_backtest_results = None

    def log(self, message: str, level: str = "INFO"):
        entry = {
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "level": level,
            "message": message
        }
        with self.lock:
            self.recent_logs.append(entry)
            if len(self.recent_logs) > 100:
                self.recent_logs.pop(0)

    def get_status_payload(self):
        with self.lock:
            kill_active = self.risk_engine.is_kill_switch_active()
            positions_data = []
            for p in self.broker.positions.values():
                if p.is_active:
                    positions_data.append({
                        "symbol": p.symbol,
                        "entry_price": p.entry_price,
                        "quantity": p.quantity,
                        "entry_time": datetime.fromtimestamp(p.entry_time).strftime("%H:%M:%S"),
                        "target_price": p.target_price,
                        "stop_loss_price": p.stop_loss_price,
                        "current_price": p.current_price,
                        "gross_unrealized_pnl": round(p.gross_unrealized_pnl, 2),
                        "pnl_pct": round(p.pnl_pct * 100, 2)
                    })

            trades_data = []
            for t in self.broker.trade_history[-20:]:
                trades_data.append({
                    "symbol": t.symbol,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "quantity": t.quantity,
                    "exit_reason": t.exit_reason,
                    "gross_pnl": round(t.gross_pnl, 2),
                    "total_charges": round(t.total_statutory_charges, 2),
                    "net_pnl": round(t.net_pnl, 2),
                    "exit_time": datetime.fromtimestamp(t.exit_time).strftime("%H:%M:%S")
                })

            return {
                "status": "HALTED (KILL SWITCH ACTIVE)" if kill_active else "ACTIVE & ARMED",
                "kill_switch_active": kill_active,
                "bot_running": self.bot_running,
                "total_equity": round(self.broker.total_equity, 2),
                "starting_capital": config.STARTING_CAPITAL,
                "cash": round(self.broker.cash, 2),
                "daily_realized_pnl": round(self.broker.daily_realized_pnl, 2),
                "daily_realized_loss": round(self.broker.daily_realized_loss, 2),
                "max_daily_loss": config.MAX_DAILY_LOSS,
                "brier_score": round(self.calibration_engine.compute_brier_score(), 4),
                "positions": positions_data,
                "trades": trades_data,
                "recent_logs": list(reversed(self.recent_logs[-30:])),
                "recent_signals": self.recent_signals[-10:],
                "latest_backtest": self.latest_backtest_results,
                "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S IST")
            }

    def execute_cycle(self, symbol: str, price: float, rvol: float, tweets: list, time_str: str = "11:00"):
        with self.lock:
            ts = time.time()
            self.log(f"Evaluating {symbol} @ ₹{price:.2f} ({time_str} IST)...")

            # 1. Update existing positions
            exit_result = self.broker.update_price_tick(symbol, price, ts)
            if exit_result:
                outcome = 1 if exit_result.net_pnl > 0 else 0
                self.calibration_engine.record_outcome(config.MIN_CALIBRATED_PROBABILITY, outcome)
                self.log(f"Position exit [{exit_result.exit_reason}] on {symbol}: Net P&L ₹{exit_result.net_pnl:+.2f}")

            # 2. Extract social signal
            signal = self.social_scanner.sanitize_and_extract_signal(symbol, tweets, ts)

            # 3. Build snapshot
            spread = price * 0.0008
            snapshot = MarketSnapshot(
                symbol=symbol,
                timestamp=ts,
                last_price=price,
                bid=round(price - (spread / 2), 2),
                ask=round(price + (spread / 2), 2),
                bid_depth=45000.0,
                ask_depth=15000.0,
                vwap=round(price * 0.998, 2),
                relative_volume=rvol,
                upper_circuit=round(price * 1.10, 2),
                lower_circuit=round(price * 0.90, 2),
                adv_inr=180_000_000.0
            )

            # 4. Deterministic Pre-trade Gates
            active_count = len([p for p in self.broker.positions.values() if p.is_active])
            passed_risk, vetoes = self.risk_engine.validate_pre_trade_gates(
                snapshot=snapshot,
                signal=signal,
                current_equity=self.broker.total_equity,
                current_positions_count=active_count,
                daily_loss_incurred=self.broker.daily_realized_loss,
                time_str=time_str
            )

            if not passed_risk:
                self.log(f"Risk Engine Veto on {symbol}: {', '.join(vetoes)}", level="WARNING")
                return {"success": False, "step": "RISK_VETO", "reasons": vetoes}

            # 5. Fast Reflex Scoring
            decision = self.reflex_scorer.evaluate(snapshot, signal)
            self.recent_signals.append({
                "symbol": symbol,
                "time": datetime.now().strftime("%H:%M:%S"),
                "p_organic": decision.p_organic,
                "p_win": decision.p_win_calibrated,
                "quality": decision.setup_quality,
                "kelly_pct": round(decision.recommended_fraction * 100, 2),
                "passed": decision.passed_all_gates
            })

            if not decision.passed_all_gates:
                self.log(f"Reflex Veto on {symbol}: {', '.join(decision.veto_reasons)}", level="WARNING")
                return {"success": False, "step": "REFLEX_VETO", "reasons": decision.veto_reasons}

            # 6. Execute Order
            order = self.broker.submit_bracket_entry(
                symbol=symbol,
                capital_fraction=decision.recommended_fraction,
                current_price=price,
                current_time=ts
            )

            if order:
                self.log(f"ORDER FILLED: {symbol} Qty {order.quantity} @ ₹{order.price:.2f} (Target +1%, Stop -0.7%)")
                return {"success": True, "step": "ORDER_FILLED", "order_id": order.client_order_id, "symbol": symbol}
            else:
                self.log(f"Broker rejected order for {symbol}", level="WARNING")
                return {"success": False, "step": "BROKER_REJECT"}

    def run_backtest(self, total_candles: int = 500):
        self.log("Starting 2-Year Multi-Regime Backtest Gate...")
        runner = BacktestRunner(seed=int(time.time()))
        metrics = runner.run_multi_regime_simulation(total_candles=total_candles)
        with self.lock:
            self.latest_backtest_results = metrics
        self.log(
            f"Backtest completed: {metrics['total_trades']} trades | "
            f"Hit Rate: {metrics['hit_rate_pct']:.1f}% | Sharpe: {metrics['sharpe_ratio']:.2f} | "
            f"Net P&L: ₹{metrics['net_pnl']:+,.2f}"
        )
        return metrics

    def trigger_kill(self, reason: str = "Operator manual kill switch engaged"):
        with self.lock:
            self.risk_engine.trigger_kill_switch(reason)
            current_prices = {s: p.current_price for s, p in self.broker.positions.items()}
            flattened = self.broker.flatten_all(current_prices, time.time(), reason="KILL_SWITCH")
            self.bot_running = False
            self.log(f"KILL SWITCH TRIGGERED: {reason}. Flattened {len(flattened)} open positions.", level="CRITICAL")

    def unlock(self):
        with self.lock:
            self.risk_engine.clear_kill_switch()
            self.log("Kill switch cleared. System re-armed and ready for execution.")

    def reset_account(self):
        with self.lock:
            self.broker = IndianPaperBroker(initial_capital=config.STARTING_CAPITAL)
            self.calibration_engine = CalibrationEngine()
            self.reflex_scorer = FastReflexScorer(self.calibration_engine)
            self.recent_signals.clear()
            self.log("Account reset to starting equity ₹1,00,000. Clean slate initialized.")

    def toggle_bot(self):
        with self.lock:
            if self.risk_engine.is_kill_switch_active():
                self.log("Cannot start bot while kill switch is active. Unlock system first.", level="ERROR")
                return False
            self.bot_running = not self.bot_running
            is_active = self.bot_running

        if is_active:
            self.log("Autonomous trading loop STARTED.")
            self.bot_thread = threading.Thread(target=self._background_worker, daemon=True)
            self.bot_thread.start()
        else:
            self.log("Autonomous trading loop STOPPED.")
        return is_active

    def _background_worker(self):
        """Simulates autonomous live market ticks and scans every 4 seconds."""
        symbols = ["TATASTEEL", "RELIANCE", "ZOMATO", "HDFCBANK", "INFY"]
        idx = 0
        prices = {"TATASTEEL": 156.40, "RELIANCE": 2480.0, "ZOMATO": 162.50, "HDFCBANK": 1440.0, "INFY": 1580.0}

        while self.bot_running:
            try:
                sym = symbols[idx % len(symbols)]
                idx += 1
                base_p = prices[sym]
                # Price drift simulation
                import random
                drift = random.uniform(-0.004, 0.005)
                prices[sym] = round(base_p * (1.0 + drift), 2)
                curr_p = prices[sym]

                # Random realistic setup
                rvol = random.uniform(2.5, 4.8)
                sample_tweets = [
                    f"${sym} strong buyer interest on delivery volume",
                    f"${sym} break of day's resistance on high tick velocity",
                    f"${sym} positive institutional brokerage report"
                ]

                self.execute_cycle(
                    symbol=sym,
                    price=curr_p,
                    rvol=rvol,
                    tweets=sample_tweets,
                    time_str="11:30"
                )
            except Exception as e:
                self.log(f"Background worker exception: {e}", level="ERROR")

            time.sleep(4.0)


# Instantiate singleton state holder
state_holder = SystemStateHolder()


class QuantRequestHandler(BaseHTTPRequestHandler):
    """Custom HTTP handler serving REST API and Web Dashboard."""

    def _send_json(self, data, status_code=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html_content, status_code=200):
        body = html_content.encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/status":
            self._send_json(state_holder.get_status_payload())
        elif parsed.path == "/" or parsed.path == "/index.html":
            html_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
            if os.path.exists(html_path):
                with open(html_path, "r", encoding="utf-8") as f:
                    self._send_html(f.read())
            else:
                self._send_html("<h1>Dashboard not found. Please create index.html</h1>", 404)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        content_length = int(self.headers.get("Content-Length", 0))
        post_body = self.rfile.read(content_length) if content_length > 0 else b"{}"
        
        try:
            payload = json.loads(post_body.decode("utf-8")) if post_body else {}
        except Exception:
            payload = {}

        if parsed.path == "/api/execute_cycle":
            symbol = payload.get("symbol", "TATASTEEL")
            price = float(payload.get("price", 156.40))
            rvol = float(payload.get("rvol", 4.2))
            tweets = payload.get("tweets", [f"${symbol} surge on volume breakout"])
            time_str = payload.get("time_str", "11:00")
            res = state_holder.execute_cycle(symbol, price, rvol, tweets, time_str)
            self._send_json(res)

        elif parsed.path == "/api/run_backtest":
            total_candles = int(payload.get("total_candles", 500))
            metrics = state_holder.run_backtest(total_candles)
            self._send_json(metrics)

        elif parsed.path == "/api/kill_switch":
            reason = payload.get("reason", "Operator Emergency Button Clicked")
            state_holder.trigger_kill(reason)
            self._send_json({"success": True, "message": "Kill switch engaged"})

        elif parsed.path == "/api/unlock":
            state_holder.unlock()
            self._send_json({"success": True, "message": "System unlocked"})

        elif parsed.path == "/api/reset_account":
            state_holder.reset_account()
            self._send_json({"success": True, "message": "Account reset"})

        elif parsed.path == "/api/toggle_bot":
            is_running = state_holder.toggle_bot()
            self._send_json({"success": True, "bot_running": is_running})

        else:
            self.send_response(404)
            self.end_headers()


def run_server(port: int = 8080):
    server_address = ("0.0.0.0", port)
    httpd = ThreadingHTTPServer(server_address, QuantRequestHandler)
    print(f"🚀 Quant Web Dashboard Server running at http://localhost:{port}")
    print(f"👉 Open http://localhost:{port} in your browser to view and control the bot.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
        state_holder.bot_running = False
        httpd.server_close()

if __name__ == "__main__":
    run_server(8080)
