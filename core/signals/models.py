"""
Signal models: sanitized, purely numeric alternative-data features.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SocialSignal:
    """Sanitized social velocity signal. Contains NO raw prompt text."""
    symbol: str
    timestamp: float
    mentions_count: int
    velocity_zscore: float
    unique_verified_ratio: float
    spam_cluster_score: float  # 0.0 (clean) to 1.0 (pure bot spam)
