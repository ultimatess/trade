"""
Local Web Server & REST API for the Indian Quant Trading Bot.
Standard-library ThreadingHTTPServer, loopback-only, operator-token protected.
"""

import hmac
import json
import logging
import math
import os
import re
import secrets
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from trade.backtesting.engine import BacktestRunner
from trade.brokers.paper import IndianPaperBroker
from trade.core.config import config, log_config_fingerprint, state_dir
from trade.core.market_state.models import MarketSnapshot
from trade.core.pipeline import DecisionPipeline
from trade.core.risk.engine import RiskEngine
from trade.core.strategy.calibration import CalibrationEngine
from trade.core.strategy.contract import Observation
from trade.data.providers.social import SocialMomentumScanner
from trade.strategies.social_momentum.v1.strategy import SocialMomentumV1

logger = logging.getLogger("QuantWebServer")


class SystemStateHolder:
    """Thread-safe state manager for the quant trading system."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.risk_engine = RiskEngine()
        self.calibration_engine = CalibrationEngine()
        self.strategy = SocialMomentumV1()
        self.social_scanner = SocialMomentumScanner()
        self.broker = IndianPaperBroker(initial_capital=config.STARTING_CAPITAL)

        self.bot_running = False
        self.bot_thread: threading.Thread | None = None
        self._bot_generation = 0
        self.recent_logs: list[dict[str, str]] = []
        self.recent_signals: list[dict[str, Any]] = []
        self.latest_backtest_results: dict[str, Any] | None = None

    def log(self, message: str, level: str = "INFO") -> None:
        entry = {"timestamp": datetime.now().strftime("%H:%M:%S"), "level": level, "message": message}
        with self.lock:
            self.recent_logs.append(entry)
            if len(self.recent_logs) > 100:
                self.recent_logs.pop(0)

    def get_status_payload(self) -> dict[str, Any]:
        with self.lock:
            kill_active = self.risk_engine.is_kill_switch_active()
            positions_data = []
            for p in self.broker.positions.values():
                if p.is_active:
                    positions_data.append(
                        {
                            "symbol": p.symbol,
                            "entry_price": p.entry_price,
                            "quantity": p.quantity,
                            "entry_time": datetime.fromtimestamp(p.entry_time).strftime("%H:%M:%S"),
                            "target_price": p.target_price,
                            "stop_loss_price": p.stop_loss_price,
                            "current_price": p.current_price,
                            "gross_unrealized_pnl": round(p.gross_unrealized_pnl, 2),
                            "pnl_pct": round(p.pnl_pct * 100, 2),
                        }
                    )

            trades_data = []
            for t in self.broker.trade_history[-20:]:
                trades_data.append(
                    {
                        "symbol": t.symbol,
                        "entry_price": t.entry_price,
                        "exit_price": t.exit_price,
                        "quantity": t.quantity,
                        "exit_reason": t.exit_reason,
                        "gross_pnl": round(t.gross_pnl, 2),
                        "total_charges": round(t.total_statutory_charges, 2),
                        "net_pnl": round(t.net_pnl, 2),
                        "exit_time": datetime.fromtimestamp(t.exit_time).strftime("%H:%M:%S"),
                    }
                )

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
                "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S IST"),
            }

    def execute_cycle(
        self, symbol: str, price: float, rvol: float, tweets: list[str], time_str: str = "11:00"
    ) -> dict[str, Any]:
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
                adv_inr=180_000_000.0,
            )

            # 4. Strategy -> Signal -> Sizer -> RiskEngine (authoritative) -> Broker
            obs = Observation(symbol=symbol, as_of=ts, session_time=time_str, market=snapshot, social=signal)
            result = DecisionPipeline(self.strategy, self.risk_engine).process(
                obs, self.broker.portfolio_view(ts), self.broker
            )
            d = result.decision.diagnostics if result.decision else {}
            if d:
                self.recent_signals.append(
                    {
                        "symbol": symbol,
                        "time": datetime.now().strftime("%H:%M:%S"),
                        "p_organic": d["p_organic"],
                        "p_win": d["p_win_calibrated"],
                        "quality": d["setup_quality"],
                        "kelly_pct": round(d["recommended_fraction"] * 100, 2),
                        "passed": bool(result.decision and result.decision.signals),
                    }
                )
            reasons = list(result.reason_codes)
            if result.step == "ORDER_FILLED":
                order = result.orders[0]
                self.log(f"ORDER FILLED: {symbol} Qty {order.quantity} @ ₹{order.price:.2f} (bracket +1% / -0.7%)")
                return {"success": True, "step": "ORDER_FILLED", "order_id": order.client_order_id, "symbol": symbol}
            label = {
                "NO_SIGNAL": "Strategy rejected",
                "RISK_DENIED": "Risk Engine DENY",
                "RISK_HALTED": "Risk HALT",
                "SIZING_REJECTED": "Sizing rejected",
                "BROKER_REJECT": "Broker rejected",
            }[result.step]
            self.log(f"{label} on {symbol}: {', '.join(reasons)}", level="WARNING")
            return {"success": False, "step": result.step, "reasons": reasons}

    def run_backtest(self, total_candles: int = 500) -> dict[str, Any]:
        self.log("Starting legacy synthetic simulation (not evidence of edge)...")
        runner = BacktestRunner(seed=int(time.time()))
        metrics = runner.run_multi_regime_simulation(total_candles=total_candles)
        with self.lock:
            self.latest_backtest_results = metrics
        self.log(
            f"Legacy synthetic simulation completed: {metrics['total_trades']} trades | "
            f"Hit Rate: {metrics['hit_rate_pct']:.1f}% | Sharpe: {metrics['sharpe_ratio']:.2f} | "
            f"Net P&L: ₹{metrics['net_pnl']:+,.2f}"
        )
        return metrics

    def trigger_kill(self, reason: str = "Operator manual kill switch engaged") -> None:
        with self.lock:
            self.risk_engine.trigger_kill_switch(reason)
            current_prices = {s: p.current_price for s, p in self.broker.positions.items()}
            flattened = self.broker.flatten_all(current_prices, time.time(), reason="KILL_SWITCH")
            self.bot_running = False
            self.log(f"KILL SWITCH TRIGGERED: {reason}. Flattened {len(flattened)} open positions.", level="CRITICAL")

    def unlock(self) -> bool:
        with self.lock:
            if not self.risk_engine.clear_kill_switch():
                self.log("Kill switch clear FAILED. System remains HALTED.", level="CRITICAL")
                return False
            self.log("Kill switch cleared by operator. System re-armed.")
            return True

    def reset_account(self) -> None:
        with self.lock:
            self.broker = IndianPaperBroker(initial_capital=config.STARTING_CAPITAL)
            self.calibration_engine = CalibrationEngine()
            self.recent_signals.clear()
            self.log("Account reset to starting equity ₹1,00,000. Clean slate initialized.")

    def toggle_bot(self) -> bool:
        with self.lock:
            if self.risk_engine.is_kill_switch_active():
                self.log("Cannot start bot while kill switch is active. Unlock system first.", level="ERROR")
                return False
            self.bot_running = not self.bot_running
            is_active = self.bot_running
            if is_active:
                # Generation counter: a worker from a previous start exits even if
                # the bot is re-enabled before it wakes, so only one worker runs.
                self._bot_generation += 1
                self.bot_thread = threading.Thread(
                    target=self._background_worker, args=(self._bot_generation,), daemon=True
                )
                self.bot_thread.start()

        self.log("Autonomous trading loop STARTED." if is_active else "Autonomous trading loop STOPPED.")
        return is_active

    def _background_worker(self, generation: int) -> None:
        """Simulates autonomous live market ticks and scans every 4 seconds."""
        symbols = ["TATASTEEL", "RELIANCE", "ZOMATO", "HDFCBANK", "INFY"]
        idx = 0
        prices = {"TATASTEEL": 156.40, "RELIANCE": 2480.0, "ZOMATO": 162.50, "HDFCBANK": 1440.0, "INFY": 1580.0}

        while self.bot_running and generation == self._bot_generation:
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
                    f"${sym} positive institutional brokerage report",
                ]

                self.execute_cycle(symbol=sym, price=curr_p, rvol=rvol, tweets=sample_tweets, time_str="11:30")
            except Exception as e:
                self.log(f"Background worker exception: {e}", level="ERROR")

            time.sleep(4.0)


# Instantiate singleton state holder
state_holder = SystemStateHolder()


MAX_BODY_BYTES = 64 * 1024
SYMBOL_RE = re.compile(r"^[A-Z0-9&_-]{1,20}$")
TIME_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
STATIC_FILES = {"/static/vendor/tailwind-play.js": ("vendor/tailwind-play.js", "application/javascript")}
UNLOCK_CONFIRMATION = "UNLOCK"


class BadRequest(ValueError):
    pass


def load_or_create_operator_token() -> str:
    """Per-installation operator token, stored 0600 in the state dir. Required on every mutating route."""
    path = state_dir() / "operator_token"
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        path.parent.mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(32)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(token)
        return token


def parse_cycle_payload(payload: dict[str, Any]) -> tuple[str, float, float, list[str], str]:
    """Validate /api/execute_cycle input. Market inputs are untrusted data."""
    symbol = payload.get("symbol", "TATASTEEL")
    if not isinstance(symbol, str) or not SYMBOL_RE.match(symbol):
        raise BadRequest("symbol must match [A-Z0-9&_-]{1,20}")
    try:
        price = float(payload.get("price", 156.40))
        rvol = float(payload.get("rvol", 4.2))
    except (TypeError, ValueError):
        raise BadRequest("price and rvol must be numbers") from None
    if not (math.isfinite(price) and price > 0 and math.isfinite(rvol) and rvol >= 0):
        raise BadRequest("price must be > 0 and rvol >= 0")
    tweets = payload.get("tweets", [f"${symbol} surge on volume breakout"])
    if (
        not isinstance(tweets, list)
        or len(tweets) > 500
        or not all(isinstance(t, str) and len(t) <= 2000 for t in tweets)
    ):
        raise BadRequest("tweets must be a list of at most 500 strings of at most 2000 chars")
    time_str = payload.get("time_str", "11:00")
    if not isinstance(time_str, str) or not TIME_RE.match(time_str):
        raise BadRequest("time_str must be HH:MM")
    return symbol, price, rvol, tweets, time_str


class QuantRequestHandler(BaseHTTPRequestHandler):
    """Serves the dashboard and REST API on loopback only.

    Security model (docs/CURRENT_STATE.md S-001..S-003):
    - bound to 127.0.0.1; Host header must be a loopback name (blocks DNS rebinding)
    - no CORS headers at all, so other origins cannot read responses
    - every POST requires the X-Operator-Token header (blocks cross-site requests)
    """

    server_version = "TradeOS"
    sys_version = ""
    operator_token = ""
    allowed_hosts: frozenset[str] = frozenset()

    def _send(self, body: bytes, content_type: str, status_code: int = 200) -> None:
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, data: Any, status_code: int = 200) -> None:
        self._send(json.dumps(data).encode("utf-8"), "application/json", status_code)

    def _send_html(self, html_content: str, status_code: int = 200) -> None:
        self._send(html_content.encode("utf-8"), "text/html; charset=utf-8", status_code)

    def _host_ok(self) -> bool:
        return self.headers.get("Host", "") in self.allowed_hosts

    def do_OPTIONS(self) -> None:
        self._send_json({"error": "method not allowed"}, 405)

    def do_GET(self) -> None:
        if not self._host_ok():
            return self._send_json({"error": "forbidden host"}, 403)
        parsed = urlparse(self.path)
        if parsed.path == "/api/status":
            self._send_json(state_holder.get_status_payload())
        elif parsed.path == "/" or parsed.path == "/index.html":
            html_path = os.path.join(STATIC_DIR, "index.html")
            with open(html_path, encoding="utf-8") as f:
                html = f.read()
            # Same-origin page receives the token; other origins cannot read this response.
            self._send_html(html.replace("__OPERATOR_TOKEN__", self.operator_token))
        elif parsed.path in STATIC_FILES:
            rel, ctype = STATIC_FILES[parsed.path]
            with open(os.path.join(STATIC_DIR, rel), "rb") as f:
                self._send(f.read(), ctype)
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        if not self._host_ok():
            return self._send_json({"error": "forbidden host"}, 403)
        if not hmac.compare_digest(self.headers.get("X-Operator-Token", ""), self.operator_token):
            return self._send_json({"error": "operator token required"}, 401)
        try:
            content_length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return self._send_json({"error": "bad Content-Length"}, 400)
        if content_length > MAX_BODY_BYTES:
            return self._send_json({"error": "request too large"}, 413)
        post_body = self.rfile.read(content_length) if content_length > 0 else b"{}"
        try:
            payload = json.loads(post_body.decode("utf-8")) if post_body else {}
        except (ValueError, UnicodeDecodeError):
            return self._send_json({"error": "invalid JSON"}, 400)
        if not isinstance(payload, dict):
            return self._send_json({"error": "JSON object required"}, 400)

        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/execute_cycle":
                self._send_json(state_holder.execute_cycle(*parse_cycle_payload(payload)))

            elif parsed.path == "/api/run_backtest":
                try:
                    total_candles = int(payload.get("total_candles", 500))
                except (TypeError, ValueError):
                    raise BadRequest("total_candles must be an integer") from None
                if not 1 <= total_candles <= 20_000:
                    raise BadRequest("total_candles must be between 1 and 20000")
                self._send_json(state_holder.run_backtest(total_candles))

            elif parsed.path == "/api/kill_switch":
                reason = str(payload.get("reason", "Operator Emergency Button Clicked"))[:200]
                state_holder.trigger_kill(reason)
                self._send_json({"success": True, "message": "Kill switch engaged"})

            elif parsed.path == "/api/unlock":
                if payload.get("confirm") != UNLOCK_CONFIRMATION:
                    raise BadRequest(f'unlock requires {{"confirm": "{UNLOCK_CONFIRMATION}"}}')
                if not state_holder.unlock():
                    return self._send_json({"success": False, "message": "Unlock failed; system remains HALTED"}, 500)
                self._send_json({"success": True, "message": "System unlocked"})

            elif parsed.path == "/api/reset_account":
                state_holder.reset_account()
                self._send_json({"success": True, "message": "Account reset"})

            elif parsed.path == "/api/toggle_bot":
                is_running = state_holder.toggle_bot()
                self._send_json({"success": True, "bot_running": is_running})

            else:
                self._send_json({"error": "not found"}, 404)
        except BadRequest as e:
            self._send_json({"error": str(e)}, 400)


def make_server(port: int = 8080, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise ValueError("The dashboard binds to loopback only; remote access is not supported.")
    QuantRequestHandler.operator_token = load_or_create_operator_token()
    httpd = ThreadingHTTPServer((host, port), QuantRequestHandler)
    bound = httpd.server_address[1]
    QuantRequestHandler.allowed_hosts = frozenset(
        {f"127.0.0.1:{bound}", f"localhost:{bound}", f"[::1]:{bound}"}
        | ({"127.0.0.1", "localhost"} if bound == 80 else set())
    )
    return httpd


def run_server(port: int = 8080) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    log_config_fingerprint(logger)
    httpd = make_server(port)
    print(f"Quant Web Dashboard running at http://127.0.0.1:{port} (loopback only)")
    print(f"Operator token stored in {state_dir() / 'operator_token'}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
        state_holder.bot_running = False
        httpd.server_close()


if __name__ == "__main__":
    run_server(8080)
