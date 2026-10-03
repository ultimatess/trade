"""Paper broker cash accounting (D-001 regression)."""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tests import factories
from trade.brokers.paper import IndianPaperBroker

pytestmark = pytest.mark.critical


def test_entry_debits_notional_and_nav_is_conserved():
    b = IndianPaperBroker(100_000.0)
    order = b.submit_intent(factories.intent("A", 100, 100.0), 100.0, 1000.0)
    assert b.cash == pytest.approx(100_000.0 - order.price * 100)
    assert b.total_equity == pytest.approx(100_000.0)  # fill price marked; no P&L yet


def test_entry_beyond_cash_is_rejected():
    b = IndianPaperBroker(10_000.0)
    assert b.submit_intent(factories.intent("A", 200, 100.0), 100.0, 1000.0) is None
    assert b.cash == 10_000.0 and not b.positions


@settings(max_examples=200, deadline=None)
@given(moves=st.lists(st.floats(min_value=-0.03, max_value=0.03), min_size=1, max_size=30))
def test_cash_equals_start_plus_realized_net_pnl(moves):
    b = IndianPaperBroker(100_000.0)
    t, price = 1000.0, 100.0
    for i, m in enumerate(moves):
        if not b.positions.get("A") or not b.positions["A"].is_active:
            b.submit_intent(factories.intent("A", 150, price, signal_id=str(i)), price, t)
        price = max(1.0, price * (1 + m))
        t += 60
        b.update_price_tick("A", price, t)
    b.flatten_all({"A": price}, t + 60, reason="TEST")
    assert b.cash == pytest.approx(100_000.0 + sum(tr.net_pnl for tr in b.trade_history), abs=1e-6)
    assert b.total_equity == pytest.approx(b.cash)
