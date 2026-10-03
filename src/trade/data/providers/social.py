"""
Free Social Momentum & Cashtag Ingestion Pipeline.
Extracts trending signals from StockTwits and social scrapers without paid APIs.
Text is reduced to numeric features and is never passed to a model.
Bot filtering is a duplicate-text heuristic only.
"""

import json
import logging
import time
import urllib.request
from typing import Any

from trade.core.signals.models import SocialSignal

logger = logging.getLogger("SocialScanner")


class SocialMomentumScanner:
    """Free ingestion pipeline for retail social momentum."""

    def __init__(self) -> None:
        self.mention_history: dict[str, list[float]] = {}  # {symbol: [timestamps]}

    def record_mention(self, symbol: str, timestamp: float | None = None) -> None:
        """Records a timestamped mention for rolling velocity calculations."""
        ts = timestamp or time.time()
        sym = symbol.upper().replace("$", "")
        if sym not in self.mention_history:
            self.mention_history[sym] = []
        self.mention_history[sym].append(ts)

    def get_velocity_zscore(self, symbol: str, current_time: float) -> float:
        """Calculates 1-hour mention velocity acceleration against 24h baseline."""
        sym = symbol.upper().replace("$", "")
        timestamps = self.mention_history.get(sym, [])
        if not timestamps:
            return 0.0

        # Filter last 1 hour vs last 24 hours
        one_hour_ago = current_time - 3600
        twenty_four_hours_ago = current_time - 86400

        recent_mentions = [t for t in timestamps if t >= one_hour_ago and t <= current_time]
        baseline_mentions = [t for t in timestamps if t >= twenty_four_hours_ago and t <= current_time]

        hourly_baseline_mean = max(1.0, len(baseline_mentions) / 24.0)
        recent_count = len(recent_mentions)

        # Standard Poisson / Gaussian approximation for z-score
        std_dev = max(1.0, float(hourly_baseline_mean**0.5))
        zscore = (recent_count - hourly_baseline_mean) / std_dev
        return round(zscore, 2)

    def fetch_stocktwits_trending(self) -> list[dict[str, Any]]:
        """
        Polls StockTwits public trending endpoint (no API key). Currently blocked by Cloudflare (HTTP 403) and unused.
        Returns list of trending symbol dictionaries.
        """
        url = "https://api.stocktwits.com/api/2/streams/trending.json"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
        try:
            with urllib.request.urlopen(req, timeout=5) as response:  # noqa: S310 - fixed https URL
                if response.status == 200:
                    data = json.loads(response.read().decode("utf-8"))
                    symbols: list[dict[str, Any]] = data.get("symbols", [])
                    return symbols
        except Exception as e:
            logger.warning(f"StockTwits public feed poll failed: {e}")
        return []

    def sanitize_and_extract_signal(self, symbol: str, recent_tweets: list[str], current_time: float) -> SocialSignal:
        """
        Extracts purely numeric features from social stream.
        Treats text strictly as passive data to prevent prompt injection.
        """
        sym = symbol.upper().replace("$", "")
        for _ in recent_tweets:
            self.record_mention(sym, current_time)

        mentions_count = len(recent_tweets)
        velocity_z = self.get_velocity_zscore(sym, current_time)

        # Bot Spam Entropy: Check for duplicate identical tweets (classic pump botnet pattern)
        if mentions_count > 0:
            unique_texts = len(set(recent_tweets))
            duplication_rate = 1.0 - (unique_texts / mentions_count)
            spam_score = round(duplication_rate, 2)
            # Simulated verified ratio
            unique_verified_ratio = round(min(1.0, 0.4 + (0.5 * (1.0 - spam_score))), 2)
        else:
            spam_score = 0.0
            unique_verified_ratio = 0.8

        return SocialSignal(
            symbol=sym,
            timestamp=current_time,
            mentions_count=mentions_count,
            velocity_zscore=velocity_z,
            unique_verified_ratio=unique_verified_ratio,
            spam_cluster_score=spam_score,
        )
