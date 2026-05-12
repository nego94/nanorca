"""
bot/data/news_scraper.py — News event scraper (CoinMarketCap + Twitter/X).

Fetches critical news that may impact trading decisions.
Runs as part of the signal-building pipeline every N cycles.
Claude assigns impact scores and sentiment to each news item.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

log = logging.getLogger("nanorca.data.news")

CMC_BASE = "https://pro-api.coinmarketcap.com/v1"


class NewsScraper:
    """Fetches news from CMC and Twitter/X and stores critical events in DB."""

    def __init__(self, config, db) -> None:
        self._config = config
        self._db = db
        self._http = httpx.AsyncClient(timeout=10)

    async def fetch_cmc_news(self) -> list[dict[str, Any]]:
        """
        Fetch latest crypto news from CoinMarketCap News API.

        TODO Phase 2: implement real CMC news endpoint.
        Returns list of {headline, url, source, fetched_at}.
        """
        if not self._config.cmc_api_key:
            log.debug("CMC API key not set — skipping news fetch")
            return []

        # TODO Phase 2: GET https://pro-api.coinmarketcap.com/v1/content/posts/latest
        log.debug("CMC news fetch (stub)")
        return []

    async def fetch_twitter_crypto(self, query: str = "BTC OR ETH crypto breaking") -> list[dict[str, Any]]:
        """
        Fetch recent tweets matching a crypto news query.

        TODO Phase 2: GET https://api.twitter.com/2/tweets/search/recent
        Returns list of {text, author, created_at, url}.
        """
        if not self._config.twitter_bearer_token:
            log.debug("Twitter bearer token not set — skipping")
            return []

        # TODO Phase 2: implement real Twitter v2 search
        log.debug("Twitter crypto fetch (stub)")
        return []

    async def run_and_alert(self, telegram, claude_brain) -> None:
        """
        Fetch news, score with Claude, save to DB, alert on critical events.

        Called periodically (every 5 cycles) from the scheduler.
        TODO Phase 3: wire this into the scheduler.
        """
        cmc_news = await self.fetch_cmc_news()
        twitter_news = await self.fetch_twitter_crypto()
        all_news = cmc_news + twitter_news

        if not all_news:
            return

        for item in all_news:
            # TODO Phase 3: send to Claude for impact scoring
            # For now, just store with neutral sentiment
            try:
                await self._db.log_event(
                    "news_fetched",
                    "info",
                    item.get("headline", "No headline"),
                    payload=item,
                )
            except Exception as e:
                log.error(f"Failed to save news event: {e}")

    async def close(self) -> None:
        await self._http.aclose()
