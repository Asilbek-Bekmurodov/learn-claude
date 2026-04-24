from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from typing import Sequence

from .exceptions import RateLimitError, TickerNotFoundError, MarketDataError
from .models import Bar, DailyBar, Quote


class MassiveClient:
    """Wraps the Massive REST SDK behind the MarketDataProvider protocol."""

    def __init__(self, api_key: str) -> None:
        try:
            from massive import RESTClient as _RESTClient  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "Install the 'massive' package: pip install -U massive"
            ) from exc
        self._client = _RESTClient(api_key=api_key)

    # -- helpers --

    def _snap_to_quote(self, snap) -> Quote:
        lt = snap.last_trade
        lq = snap.last_quote
        day = snap.day
        return Quote(
            ticker=snap.ticker,
            price=lt.price,
            bid=lq.bid_price,
            ask=lq.ask_price,
            bid_size=lq.bid_size or 0,
            ask_size=lq.ask_size or 0,
            volume=int(day.volume) if day else 0,
            change=snap.todays_change or 0.0,
            change_pct=snap.todays_change_perc or 0.0,
            timestamp=datetime.fromtimestamp(snap.updated / 1e9, tz=timezone.utc),
        )

    def _agg_to_bar(self, ticker: str, agg) -> Bar:
        ts = datetime.fromtimestamp(agg.timestamp / 1000, tz=timezone.utc)
        return Bar(
            ticker=ticker,
            timestamp=ts,
            open=agg.open,
            high=agg.high,
            low=agg.low,
            close=agg.close,
            volume=int(agg.volume),
            vwap=agg.vwap,
            transactions=agg.transactions,
        )

    def _handle_http_error(self, exc: Exception, ticker: str) -> None:
        msg = str(exc)
        if "429" in msg:
            raise RateLimitError(f"Rate limit exceeded for {ticker}") from exc
        if "404" in msg:
            raise TickerNotFoundError(f"Ticker not found: {ticker}") from exc
        raise MarketDataError(f"Massive API error for {ticker}: {exc}") from exc

    # -- protocol implementation --

    async def get_quote(self, ticker: str) -> Quote:
        try:
            snap = await asyncio.to_thread(
                self._client.get_snapshot_ticker, "stocks", ticker
            )
        except Exception as exc:
            self._handle_http_error(exc, ticker)
        return self._snap_to_quote(snap)

    async def get_quotes(self, tickers: Sequence[str]) -> dict[str, Quote]:
        joined = ",".join(tickers)
        try:
            snaps = await asyncio.to_thread(
                self._client.get_snapshot_all,
                "stocks",
                params={"tickers": joined},
            )
        except Exception as exc:
            self._handle_http_error(exc, joined)
        return {s.ticker: self._snap_to_quote(s) for s in snaps}

    async def get_eod_bar(self, ticker: str, day: date) -> DailyBar:
        try:
            eod = await asyncio.to_thread(
                self._client.get_daily_open_close_agg,
                ticker,
                day.isoformat(),
                adjusted=True,
            )
        except Exception as exc:
            self._handle_http_error(exc, ticker)
        ts = datetime.fromisoformat(eod.from_)
        return DailyBar(
            ticker=ticker,
            timestamp=ts,
            open=eod.open,
            high=eod.high,
            low=eod.low,
            close=eod.close,
            volume=int(eod.volume),
            pre_market=getattr(eod, "pre_market", None),
            after_hours=getattr(eod, "after_hours", None),
        )

    async def get_previous_close(self, ticker: str) -> DailyBar:
        try:
            prev = await asyncio.to_thread(
                self._client.get_previous_close_agg, ticker
            )
        except Exception as exc:
            self._handle_http_error(exc, ticker)
        ts = datetime.fromtimestamp(prev.timestamp / 1000, tz=timezone.utc)
        return DailyBar(
            ticker=ticker,
            timestamp=ts,
            open=prev.open,
            high=prev.high,
            low=prev.low,
            close=prev.close,
            volume=int(prev.volume),
            vwap=getattr(prev, "vwap", None),
        )

    async def get_bars(
        self,
        ticker: str,
        timespan: str,
        from_: date | datetime,
        to: date | datetime,
        multiplier: int = 1,
        adjusted: bool = True,
    ) -> list[Bar]:
        from_str = from_.isoformat() if hasattr(from_, "isoformat") else str(from_)
        to_str = to.isoformat() if hasattr(to, "isoformat") else str(to)
        try:
            raw = await asyncio.to_thread(
                lambda: list(
                    self._client.list_aggs(
                        ticker,
                        multiplier=multiplier,
                        timespan=timespan,
                        from_=from_str,
                        to=to_str,
                        adjusted=adjusted,
                        limit=50000,
                    )
                )
            )
        except Exception as exc:
            self._handle_http_error(exc, ticker)
        return [self._agg_to_bar(ticker, a) for a in raw]
