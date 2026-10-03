"""
Shared decision pipeline: the single path from observation to order in every mode.

    Observation -> Strategy -> Signal -> Sizer -> OrderIntent -> RiskEngine -> ALLOW -> ExecutionAdapter

Backtest, paper and (later) live differ only in the ExecutionAdapter and data source.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

from trade.core.execution.intent import OrderIntent
from trade.core.execution.models import Order
from trade.core.portfolio.view import PortfolioView
from trade.core.risk.engine import RiskDecision, RiskEngine
from trade.core.risk.sizing import Sizer
from trade.core.strategy.contract import Observation, Strategy, StrategyDecision

Step = Literal["RISK_HALTED", "NO_SIGNAL", "SIZING_REJECTED", "RISK_DENIED", "ORDER_FILLED", "BROKER_REJECT"]


class ExecutionAdapter(Protocol):
    def submit_intent(self, intent: OrderIntent, current_price: float, current_time: float) -> Order | None: ...


@dataclass(frozen=True)
class PipelineResult:
    step: Step
    decision: StrategyDecision | None = None
    reason_codes: tuple[str, ...] = ()
    intents: tuple[OrderIntent, ...] = ()
    risk_decisions: tuple[RiskDecision, ...] = ()
    orders: tuple[Order, ...] = field(default_factory=tuple)


class DecisionPipeline:
    def __init__(self, strategy: Strategy, risk_engine: RiskEngine, sizer: Sizer | None = None):
        self.strategy = strategy
        self.risk = risk_engine
        self.sizer = sizer or Sizer(risk_engine.limits)

    def process(self, obs: Observation, portfolio: PortfolioView, executor: ExecutionAdapter) -> PipelineResult:
        halt = self.risk.check_portfolio(portfolio)
        if halt:
            return PipelineResult("RISK_HALTED", reason_codes=halt)

        decision = self.strategy.validate(self.strategy.on_observation(obs), obs)
        if not decision.signals:
            return PipelineResult("NO_SIGNAL", decision, decision.rejection_codes)

        intents, risk_decisions, orders, reasons = [], [], [], []
        step: Step = "NO_SIGNAL"
        for signal in decision.signals:
            sized = self.sizer.size(signal, self.strategy.spec, portfolio)
            if sized.intent is None:
                step, reasons = "SIZING_REJECTED", list(sized.reason_codes)
                continue
            intents.append(sized.intent)
            verdict = self.risk.evaluate(sized.intent, portfolio, obs.market, obs.session_time)
            risk_decisions.append(verdict)
            if not verdict.allowed:
                step, reasons = "RISK_DENIED", list(verdict.reason_codes)
                continue
            order = executor.submit_intent(sized.intent, obs.market.last_price, obs.as_of)
            if order is None:
                step, reasons = "BROKER_REJECT", ["BROKER_REJECT"]
                continue
            orders.append(order)
            step = "ORDER_FILLED"
        return PipelineResult(step, decision, tuple(reasons), tuple(intents), tuple(risk_decisions), tuple(orders))
