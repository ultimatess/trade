"""
Strategy decision models. Strategy output is advisory only.
"""

from dataclasses import dataclass, field


@dataclass
class ReflexDecision:
    """Output from the Fast Reflex layer. Advisory only."""

    symbol: str
    timestamp: float
    p_organic: float
    p_win_raw: float
    p_win_calibrated: float
    setup_quality: float
    recommended_fraction: float
    passed_all_gates: bool
    veto_reasons: list[str] = field(default_factory=list)
