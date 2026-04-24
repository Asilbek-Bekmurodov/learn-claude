# Market Interface — Unified Python API for Stock Prices

This document specifies the unified `MarketDataClient` interface used throughout this project to retrieve stock prices. When `MASSIVE_API_KEY` is set in the environment the implementation delegates to the Massive REST API. When the key is absent it falls back to the built-in `MarketSimulator` (see `MARKET_SIMULATOR.md`).

All callers import from `market.interface` and never reference Massive or simulator internals directly.

---

## Design Goals

- **Single import point** — one class, one interface, regardless of backend.
- **Zero-config fallback** — missing API key → simulator, no exception, no special flag.
- **Async-first, sync available** — async methods are primary; sync wrappers provided for scripts and notebooks.
- **Typed returns** — all methods return dataclasses, not raw dicts.
- **Testable** — the simulator implements the same interface, so unit tests never need a live API key.

---

## Data Model

```python
# market/models.py
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Quote:
    ticker: str
    price: float          # last trade price (or mid if no trade available)
    bid: float
    ask: float
    bid_size: int
    ask_size: int
    volume: int           # shares traded today
    change: float         # absolute change from previous close
    change_pct: float     # percentage change from previous close
    timestamp: datetime


@dataclass
class Bar:
    ticker: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    vwap: float | None = None
    transactions: int | None = None


@dataclass
class DailyBar(Bar):
    """End-of-day bar; adds pre/after-hours fields."""
    pre_market: float | None = None
    after_hours: float | None = None
```

---

## Interface Protocol

```python
# market/interface.py
from __future__ import annotations
from typing import Protocol, Sequence
from datetime import date, datetime
from .models import Bar, DailyBar, Quote


class MarketDataProvider(Protocol):
    """Structural interface satisfied by both MassiveClient and MarketSimulator."""

    # --- Realtime ---

    async def get_quote(self, ticker: str) -> Quote:
        """Latest price, bid/ask, volume, and daily change for one ticker."""
        ...

    async def get_quotes(self, tickers: Sequence[str]) -> dict[str, Quote]:
        """Batch quotes for multiple tickers. Returns {ticker: Quote}."""
        ...

    # --- End of day ---

    async def get_eod_bar(self, ticker: str, day: date) -> DailyBar:
        """OHLCV + pre/after-hours for a specific trading date."""
        ...

    async def get_previous_close(self, ticker: str) -> DailyBar:
        """Convenience: EOD bar for the most recent completed trading day."""
        ...

    # --- Historical bars ---

    async def get_bars(
        self,
        ticker: str,
        timespan: str,          # "minute" | "hour" | "day" | "week"
        from_: date | datetime,
        to: date | datetime,
        multiplier: int = 1,
        adjusted: bool = True,
    ) -> list[Bar]:
        """OHLCV bars for a date range and timespan."""
        ...
```

---

## Factory Function

```python
# market/client.py
from __future__ import annotations
import os
from .interface import MarketDataProvider
from .massive_client import MassiveClient
from .simulator import MarketSimulator


def create_market_client(
    api_key: str | None = None,
    *,
    sim_tickers: list[str] | None = None,
    sim_seed: int = 42,
) -> MarketDataProvider:
    """
    Returns a MassiveClient if MASSIVE_API_KEY is available,
    otherwise a MarketSimulator.
    """
    key = api_key or os.environ.get("MASSIVE_API_KEY")
    if key:
        return MassiveClient(api_key=key)
    return MarketSimulator(tickers=sim_tickers or [], seed=sim_seed)
```

### Usage

```python
from market.client import create_market_client
import asyncio

client = create_market_client()   # live or sim, automatically

async def main():
    quote = await client.get_quote("AAPL")
    print(f"AAPL  ${quote.price:.2f}  ({quote.change_pct:+.2f}%)")

    quotes = await client.get_quotes(["AAPL", "MSFT", "GOOG"])
    for ticker, q in quotes.items():
        print(f"{ticker:6s}  ${q.price:.2f}")

    bars = await client.get_bars("AAPL", "day", date(2025, 1, 1), date(2025, 4, 1))
    for b in bars:
        print(b.timestamp.date(), b.close)

asyncio.run(main())
```

---

## MassiveClient Implementation

```python
# market/massive_client.py
from __future__ import annotations
import asyncio
from datetime import date, datetime, timezone
from typing import Sequence

from massive import RESTClient as _RESTClient

from .models import Bar, DailyBar, Quote


class MassiveClient:
    """Wraps the Massive REST SDK behind the MarketDataProvider protocol."""

    def __init__(self, api_key: str) -> None:
        self._client = _RESTClient(api_key=api_key)

    # -- helpers --

    def _snap_to_quote(self, snap) -> Quote:
        t = snap.ticker if hasattr(snap, "ticker") else snap
        lt = snap.last_trade
        lq = snap.last_quote
        day = snap.day
        return Quote(
            ticker=t,
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

    # -- protocol implementation --

    async def get_quote(self, ticker: str) -> Quote:
        snap = await asyncio.to_thread(
            self._client.get_snapshot_ticker, "stocks", ticker
        )
        return self._snap_to_quote(snap)

    async def get_quotes(self, tickers: Sequence[str]) -> dict[str, Quote]:
        joined = ",".join(tickers)
        snaps = await asyncio.to_thread(
            self._client.get_snapshot_all,
            "stocks",
            params={"tickers": joined},
        )
        return {s.ticker: self._snap_to_quote(s) for s in snaps}

    async def get_eod_bar(self, ticker: str, day: date) -> DailyBar:
        eod = await asyncio.to_thread(
            self._client.get_daily_open_close_agg,
            ticker,
            day.isoformat(),
            adjusted=True,
        )
        ts = datetime.fromisoformat(eod.from_)
        return DailyBar(
            ticker=ticker,
            timestamp=ts,
            open=eod.open,
            high=eod.high,
            low=eod.low,
            close=eod.close,
            volume=int(eod.volume),
            pre_market=eod.pre_market,
            after_hours=eod.after_hours,
        )

    async def get_previous_close(self, ticker: str) -> DailyBar:
        prev = await asyncio.to_thread(
            self._client.get_previous_close_agg, ticker
        )
        ts = datetime.fromtimestamp(prev.timestamp / 1000, tz=timezone.utc)
        return DailyBar(
            ticker=ticker,
            timestamp=ts,
            open=prev.open,
            high=prev.high,
            low=prev.low,
            close=prev.close,
            volume=int(prev.volume),
            vwap=prev.vwap,
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
        raw = await asyncio.to_thread(
            lambda: list(
                self._client.list_aggs(
                    ticker,
                    multiplier=multiplier,
                    timespan=timespan,
                    from_=from_.isoformat() if hasattr(from_, "isoformat") else from_,
                    to=to.isoformat() if hasattr(to, "isoformat") else to,
                    adjusted=adjusted,
                    limit=50000,
                )
            )
        )
        return [self._agg_to_bar(ticker, a) for a in raw]
```

---

## Sync Wrappers (optional, for scripts/notebooks)

```python
# market/sync.py
import asyncio
from datetime import date, datetime
from typing import Sequence

from .client import create_market_client
from .models import Bar, DailyBar, Quote


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class SyncMarketClient:
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

    def get_bars(self, ticker: str, timespan: str, from_: date, to: date, **kw) -> list[Bar]:
        return _run(self._client.get_bars(ticker, timespan, from_, to, **kw))
```

---

## Module Layout

```
market/
├── __init__.py          # re-exports create_market_client, SyncMarketClient
├── client.py            # factory: create_market_client()
├── interface.py         # MarketDataProvider Protocol
├── models.py            # Quote, Bar, DailyBar dataclasses
├── massive_client.py    # MassiveClient(MarketDataProvider)
├── simulator.py         # MarketSimulator(MarketDataProvider)
└── sync.py              # SyncMarketClient
```

---

## Error Handling

| Condition                              | Behaviour                                             |
|----------------------------------------|-------------------------------------------------------|
| `MASSIVE_API_KEY` absent               | Falls back to `MarketSimulator`, no exception         |
| Massive returns HTTP 429 (rate limit)  | `MassiveClient` raises `RateLimitError`               |
| Ticker not found (HTTP 404)            | Raises `TickerNotFoundError`                          |
| Simulator ticker not configured        | Raises `TickerNotFoundError` with message             |
| Network timeout                        | Raises `MarketDataError` wrapping the underlying cause|

All custom exceptions live in `market.exceptions`.

---

## Environment Variables

| Variable          | Default    | Description                                     |
|-------------------|------------|-------------------------------------------------|
| `MASSIVE_API_KEY` | *(none)*   | Activates live data; absent → simulator         |
| `MARKET_SIM_SEED` | `42`       | RNG seed for the simulator (parsed at startup)  |
| `MARKET_SIM_TICKERS` | *(none)* | Comma-separated list of tickers to pre-load in simulator |
