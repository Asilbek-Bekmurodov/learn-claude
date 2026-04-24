from __future__ import annotations

from datetime import date, datetime
from typing import Protocol, Sequence

from .models import Bar, DailyBar, Quote


class MarketDataProvider(Protocol):
    """Structural interface satisfied by both MassiveClient and MarketSimulator."""

    async def get_quote(self, ticker: str) -> Quote:
        """Latest price, bid/ask, volume, and daily change for one ticker."""
        ...

    async def get_quotes(self, tickers: Sequence[str]) -> dict[str, Quote]:
        """Batch quotes for multiple tickers. Returns {ticker: Quote}."""
        ...

    async def get_eod_bar(self, ticker: str, day: date) -> DailyBar:
        """OHLCV + pre/after-hours for a specific trading date."""
        ...

    async def get_previous_close(self, ticker: str) -> DailyBar:
        """Convenience: EOD bar for the most recent completed trading day."""
        ...

    async def get_bars(
        self,
        ticker: str,
        timespan: str,
        from_: date | datetime,
        to: date | datetime,
        multiplier: int = 1,
        adjusted: bool = True,
    ) -> list[Bar]:
        """OHLCV bars for a date range and timespan."""
        ...
