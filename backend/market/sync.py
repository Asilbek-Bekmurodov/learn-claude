from __future__ import annotations

import asyncio
from datetime import date, datetime
from typing import Sequence

from .client import create_market_client
from .models import Bar, DailyBar, Quote


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


class SyncMarketClient:
    """Synchronous wrapper around MarketDataProvider for scripts and notebooks."""

    def __init__(self, **kwargs):
        self._client = create_market_client(**kwargs)

    def get_quote(self, ticker: str) -> Quote:
        return _run(self._client.get_quote(ticker))

    def get_quotes(self, tickers: Sequence[str]) -> dict[str, Quote]:
        return _run(self._client.get_quotes(tickers))

    def get_eod_bar(self, ticker: str, day: date) -> DailyBar:
        return _run(self._client.get_eod_bar(ticker, day))

    def get_previous_close(self, ticker: str) -> DailyBar:
        return _run(self._client.get_previous_close(ticker))

    def get_bars(
        self,
        ticker: str,
        timespan: str,
        from_: date,
        to: date,
        **kw,
    ) -> list[Bar]:
        return _run(self._client.get_bars(ticker, timespan, from_, to, **kw))
