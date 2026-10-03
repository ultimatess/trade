"""Dashboard API hardening (docs/CURRENT_STATE.md S-001..S-005)."""

import http.client
import json
import re
import stat
import threading
from pathlib import Path

import pytest

pytestmark = pytest.mark.critical

INDEX = Path(__file__).resolve().parents[2] / "src" / "trade" / "web" / "static" / "index.html"


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_STATE_DIR", str(tmp_path / "state"))
    from trade.core.risk.engine import RiskEngine
    from trade.web import server as srv

    monkeypatch.setattr(srv.state_holder, "risk_engine", RiskEngine(lockfile_path=str(tmp_path / "k.lock")))
    httpd = srv.make_server(port=0)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    port = httpd.server_address[1]
    token = (tmp_path / "state" / "operator_token").read_text()
    yield {"port": port, "token": token, "srv": srv, "state": tmp_path / "state"}
    httpd.shutdown()
    httpd.server_close()


def request(server, method, path, body=None, token=None, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", server["port"], timeout=5)
    headers = {"Host": host or f"127.0.0.1:{server['port']}", "Content-Type": "application/json"}
    if token is not None:
        headers["X-Operator-Token"] = token
    data = json.dumps(body).encode() if body is not None else None
    conn.request(method, path, body=data, headers=headers)
    resp = conn.getresponse()
    payload = resp.read()
    conn.close()
    return resp, payload


MUTATING = ["/api/execute_cycle", "/api/run_backtest", "/api/kill_switch", "/api/unlock",
            "/api/reset_account", "/api/toggle_bot"]


@pytest.mark.parametrize("path", MUTATING)
def test_mutating_routes_require_token(server, path):
    assert request(server, "POST", path, {})[0].status == 401
    assert request(server, "POST", path, {}, token="wrong")[0].status == 401


def test_kill_switch_cannot_be_cleared_without_token(server):
    assert request(server, "POST", "/api/kill_switch", {}, token=server["token"])[0].status == 200
    assert request(server, "POST", "/api/unlock", {"confirm": "UNLOCK"})[0].status == 401
    assert server["srv"].state_holder.risk_engine.is_kill_switch_active()


def test_unlock_requires_typed_confirmation(server):
    request(server, "POST", "/api/kill_switch", {}, token=server["token"])
    assert request(server, "POST", "/api/unlock", {}, token=server["token"])[0].status == 400
    assert server["srv"].state_holder.risk_engine.is_kill_switch_active()
    assert request(server, "POST", "/api/unlock", {"confirm": "UNLOCK"}, token=server["token"])[0].status == 200
    assert not server["srv"].state_holder.risk_engine.is_kill_switch_active()


@pytest.mark.parametrize("method,path", [("GET", "/api/status"), ("GET", "/"), ("POST", "/api/toggle_bot")])
def test_foreign_host_header_rejected(server, method, path):
    """Blocks DNS-rebinding: a page on evil.example resolving to 127.0.0.1."""
    resp, _ = request(server, method, path, {} if method == "POST" else None, token=server["token"],
                      host="evil.example:8080")
    assert resp.status == 403


def test_no_cors_headers_and_preflight_refused(server):
    for method, path in [("GET", "/api/status"), ("OPTIONS", "/api/unlock")]:
        resp, _ = request(server, method, path)
        assert resp.getheader("Access-Control-Allow-Origin") is None
    assert request(server, "OPTIONS", "/api/unlock")[0].status == 405


def test_refuses_non_loopback_bind(server):
    with pytest.raises(ValueError):
        server["srv"].make_server(port=0, host="0.0.0.0")


def test_operator_token_file_is_private(server):
    mode = stat.S_IMODE((server["state"] / "operator_token").stat().st_mode)
    assert mode == 0o600
    assert len(server["token"]) >= 32


def test_oversized_body_rejected(server):
    conn = http.client.HTTPConnection("127.0.0.1", server["port"], timeout=5)
    conn.putrequest("POST", "/api/execute_cycle")
    conn.putheader("X-Operator-Token", server["token"])
    conn.putheader("Content-Length", str(10_000_000))
    conn.endheaders()
    assert conn.getresponse().status == 413
    conn.close()


@pytest.mark.parametrize("bad", [
    {"symbol": "<img src=x onerror=alert(1)>"},
    {"symbol": "INFY", "price": "nan"},
    {"symbol": "INFY", "price": -5},
    {"symbol": "INFY", "time_str": "25:99"},
    {"symbol": "INFY", "tweets": "not-a-list"},
])
def test_execute_cycle_rejects_untrusted_input(server, bad):
    assert request(server, "POST", "/api/execute_cycle", bad, token=server["token"])[0].status == 400


def test_dashboard_served_with_token_and_local_assets(server):
    resp, body = request(server, "GET", "/")
    html = body.decode()
    assert resp.status == 200 and server["token"] in html
    assert "cdn.tailwindcss.com" not in html
    assert request(server, "GET", "/static/vendor/tailwind-play.js")[0].status == 200


def test_dashboard_escapes_all_server_strings():
    html = INDEX.read_text()
    assert "function esc(" in html
    for field in ["p.symbol", "t.symbol", "t.exit_time", "t.exit_reason", "s.symbol", "s.time",
                  "l.timestamp", "l.level", "l.message"]:
        assert "${" + field + "}" not in html, f"unescaped {field}"
    # every POST goes through the authenticated helper
    assert not re.search(r'fetch\("/api/(?!status)', html)
