# Market Simulator — Design and Code Structure

This document describes the `MarketSimulator` class that implements the `MarketDataProvider` protocol when no `MASSIVE_API_KEY` is available. It produces plausible, deterministic stock price data for development, testing, and offline use.

---

## Goals

- **Drop-in replacement** for `MassiveClient` — same protocol, same return types.
- **Deterministic** — given the same seed and ticker list, produces identical price paths across runs.
- **Stateful & evolving** — each call to `get_quote` advances simulated time so prices drift realistically during a session.
- **Configurable** — per-ticker starting price, volatility, drift, and spreads can be overridden.
- **No network access** — runs fully offline.

---

## Price Model

The simulator uses **Geometric Brownian Motion (GBM)**, the standard continuous-time model for equity prices:

```
S(t+dt) = S(t) * exp((mu - sigma^2/2) * dt + sigma * sqrt(dt) * Z)
```

Where:
- `S(t)` is the price at time `t`
- `mu` is the annualised drift (default: 0.08, i.e., 8% expected annual return)
- `sigma` is the annualised volatility (default: 0.20 for a typical large-cap stock)
- `dt` is the time step in years (e.g., 1 minute = 1/(252 * 390) years)
- `Z ~ N(0, 1)` is a standard normal random variable

**Why GBM**: ensures prices stay positive, produces log-normal returns consistent with the Black-Scholes assumption, and has well-understood parameter semantics (mu, sigma map directly to observable market statistics).

**Bid/ask spread** is modelled as a fixed fraction of price (default: 0.05%, or 5 bps), symmetric around mid. Volume is generated from a log-normal distribution parameterised to the ticker's average daily volume.

---

## Default Ticker Parameters

```python
DEFAULT_PARAMS = {
    "AAPL":  {"price": 195.0,  "mu": 0.10, "sigma": 0.22, "adv": 55_000_000},
    "MSFT":  {"price": 420.0,  "mu": 0.12, "sigma": 0.20, "adv": 22_000_000},
    "GOOG":  {"price": 175.0,  "mu": 0.09, "sigma": 0.21, "adv": 18_000_000},
    "AMZN":  {"price": 200.0,  "mu": 0.11, "sigma": 0.24, "adv": 35_000_000},
    "TSLA":  {"price": 250.0,  "mu": 0.15, "sigma": 0.55, "adv": 90_000_000},
    "NVDA":  {"price": 900.0,  "mu": 0.18, "sigma": 0.45, "adv": 40_000_000},
    "META":  {"price": 530.0,  "mu": 0.13, "sigma": 0.28, "adv": 15_000_000},
    "SPY":   {"price": 530.0,  "mu": 0.08, "sigma": 0.14, "adv": 80_000_000},
    # Unknown tickers get these defaults:
    "_default": {"price": 100.0, "mu": 0.08, "sigma": 0.25, "adv": 5_000_000},
}
```

For tickers not in the table, a deterministic starting price is derived from the ticker string's hash so the same ticker always starts at the same price across runs.

---

## State Machine

Each ticker maintains independent state in a `TickerState` dataclass:

```python
@dataclass
class TickerState:
    ticker: str
    price: float          # current mid price
    prev_close: float     # price at start of simulated day
    day_open: float       # first price of the simulated day
    day_high: float
    day_low: float
    day_volume: int
    mu: float
    sigma: float
    adv: int              # average daily volume (for volume generation)
    last_updated: datetime
```

State is initialised once in `__init__` and mutated in place by each `get_quote` call (and by `_advance_bars` for historical queries).

---

## Full Implementation

```python
# market/simulator.py
from __future__ import annotations

import math
import hashlib
import random
from datetime import date, datetime, timedelta, timezone
from typing import Sequence

from .models import Bar, DailyBar, Quote
from .exceptions import TickerNotFoundError

MINUTES_PER_DAY = 390          # regular US market hours
TRADING_DAYS_PER_YEAR = 252

DEFAULT_PARAMS: dict[str, dict] = {
    "AAPL": {"price": 195.0,  "mu": 0.10, "sigma": 0.22, "adv": 55_000_000},
    "MSFT": {"price": 420.0,  "mu": 0.12, "sigma": 0.20, "adv": 22_000_000},
    "GOOG": {"price": 175.0,  "mu": 0.09, "sigma": 0.21, "adv": 18_000_000},
    "AMZN": {"price": 200.0,  "mu": 0.11, "sigma": 0.24, "adv": 35_000_000},
    "TSLA": {"price": 250.0,  "mu": 0.15, "sigma": 0.55, "adv": 90_000_000},
    "NVDA": {"price": 900.0,  "mu": 0.18, "sigma": 0.45, "adv": 40_000_000},
    "META": {"price": 530.0,  "mu": 0.13, "sigma": 0.28, "adv": 15_000_000},
    "SPY":  {"price": 530.0,  "mu": 0.08, "sigma": 0.14, "adv": 80_000_000},
    "_default": {"price": 100.0, "mu": 0.08, "sigma": 0.25, "adv": 5_000_000},
}

SPREAD_FRACTION = 0.0005    # 5 bps half-spread


def _ticker_seed(ticker: str, base_seed: int) -> int:
    """Stable per-ticker seed derived from the ticker string."""
    h = int(hashlib.md5(ticker.encode()).hexdigest(), 16)
    return (base_seed ^ h) & 0xFFFFFFFF


def _default_price_for(ticker: str) -> float:
    """Deterministic starting price for unknown tickers based on their name."""
    h = int(hashlib.md5(ticker.encode()).hexdigest(), 16)
    return 20.0 + (h % 4800) / 10.0    # range: $20–$500


class _TickerState:
    __slots__ = (
        "ticker", "price", "prev_close", "day_open",
        "day_high", "day_low", "day_volume",
        "mu", "sigma", "adv", "rng", "last_updated",
    )

    def __init__(self, ticker: str, params: dict, rng: random.Random) -> None:
        self.ticker = ticker
        self.price = params["price"]
        self.prev_close = params["price"]
        self.day_open = params["price"]
        self.day_high = params["price"]
        self.day_low = params["price"]
        self.day_volume = 0
        self.mu = params["mu"]
        self.sigma = params["sigma"]
        self.adv = params["adv"]
        self.rng = rng
        self.last_updated = datetime.now(tz=timezone.utc)

    def step(self, dt_years: float) -> None:
        """Advance price by one GBM step."""
        z = self.rng.gauss(0.0, 1.0)
        drift = (self.mu - 0.5 * self.sigma ** 2) * dt_years
        diffusion = self.sigma * math.sqrt(dt_years) * z
        self.price = max(0.01, self.price * math.exp(drift + diffusion))
        self.day_high = max(self.day_high, self.price)
        self.day_low = min(self.day_low, self.price)
        # Simulate traded volume proportional to time step
        minute_fraction = dt_years * TRADING_DAYS_PER_YEAR * MINUTES_PER_DAY
        vol_this_step = int(
            self.rng.lognormvariate(
                math.log(max(1, self.adv * minute_fraction / MINUTES_PER_DAY)),
                0.5,
            )
        )
        self.day_volume += vol_this_step
        self.last_updated = datetime.now(tz=timezone.utc)

    def to_quote(self) -> Quote:
        half_spread = self.price * SPREAD_FRACTION
        bid = round(self.price - half_spread, 4)
        ask = round(self.price + half_spread, 4)
        change = round(self.price - self.prev_close, 4)
        change_pct = round(change / self.prev_close * 100, 4) if self.prev_close else 0.0
        return Quote(
            ticker=self.ticker,
            price=round(self.price, 4),
            bid=bid,
            ask=ask,
            bid_size=int(self.rng.randint(100, 1000)),
            ask_size=int(self.rng.randint(100, 1000)),
            volume=self.day_volume,
            change=change,
            change_pct=change_pct,
            timestamp=self.last_updated,
        )


class MarketSimulator:
    """
    Implements MarketDataProvider using Geometric Brownian Motion.
    Automatically instantiated by create_market_client() when MASSIVE_API_KEY is absent.
    """

    def __init__(
        self,
        tickers: list[str] | None = None,
        seed: int = 42,
        params: dict[str, dict] | None = None,
    ) -> None:
        self._seed = seed
        self._params = {**DEFAULT_PARAMS, **(params or {})}
        self._states: dict[str, _TickerState] = {}
        for ticker in (tickers or []):
            self._get_or_create(ticker)

    # -- internal --

    def _resolve_params(self, ticker: str) -> dict:
        if ticker in self._params:
            return self._params[ticker]
        p = dict(self._params["_default"])
        p["price"] = _default_price_for(ticker)
        return p

    def _get_or_create(self, ticker: str) -> _TickerState:
        if ticker not in self._states:
            rng = random.Random(_ticker_seed(ticker, self._seed))
            self._states[ticker] = _TickerState(ticker, self._resolve_params(ticker), rng)
        return self._states[ticker]

    def _step_dt(self, ticker: str, minutes: float = 1.0) -> _TickerState:
        dt = minutes / (TRADING_DAYS_PER_YEAR * MINUTES_PER_DAY)
        state = self._get_or_create(ticker)
        state.step(dt)
        return state

    def _build_bar(self, ticker: str, ts: datetime, state: _TickerState) -> Bar:
        return Bar(
            ticker=ticker,
            timestamp=ts,
            open=state.day_open,
            high=state.day_high,
            low=state.day_low,
            close=round(state.price, 4),
            volume=state.day_volume,
            vwap=round((state.day_open + state.day_high + state.day_low + state.price) / 4, 4),
        )

    # -- protocol implementation --

    async def get_quote(self, ticker: str) -> Quote:
        state = self._step_dt(ticker, minutes=1.0)
        return state.to_quote()

    async def get_quotes(self, tickers: Sequence[str]) -> dict[str, Quote]:
        result = {}
        for ticker in tickers:
            state = self._step_dt(ticker, minutes=1.0)
            result[ticker] = state.to_quote()
        return result

    async def get_eod_bar(self, ticker: str, day: date) -> DailyBar:
        # Replay enough steps to simulate a full trading day
        state = self._get_or_create(ticker)
        dt = 1.0 / (TRADING_DAYS_PER_YEAR * MINUTES_PER_DAY)
        open_price = state.price
        for _ in range(MINUTES_PER_DAY):
            state.step(dt)
        ts = datetime(day.year, day.month, day.day, 16, 0, tzinfo=timezone.utc)
        close = round(state.price, 4)
        state.prev_close = close
        state.day_open = close
        state.day_high = close
        state.day_low = close
        state.day_volume = 0
        return DailyBar(
            ticker=ticker,
            timestamp=ts,
            open=round(open_price, 4),
            high=state.day_high,
            low=state.day_low,
            close=close,
            volume=state.day_volume,
        )

    async def get_previous_close(self, ticker: str) -> DailyBar:
        yesterday = date.today() - timedelta(days=1)
        return await self.get_eod_bar(ticker, yesterday)

    async def get_bars(
        self,
        ticker: str,
        timespan: str,
        from_: date | datetime,
        to: date | datetime,
        multiplier: int = 1,
        adjusted: bool = True,
    ) -> list[Bar]:
        state = self._get_or_create(ticker)

        # Convert to date for iteration
        start = from_.date() if isinstance(from_, datetime) else from_
        end = to.date() if isinstance(to, datetime) else to

        minutes_per_bar = {
            "minute": 1,
            "hour":   60,
            "day":    MINUTES_PER_DAY,
            "week":   MINUTES_PER_DAY * 5,
        }.get(timespan, MINUTES_PER_DAY) * multiplier

        dt_per_step = 1.0 / (TRADING_DAYS_PER_YEAR * MINUTES_PER_DAY)
        bars: list[Bar] = []
        current = start

        while current <= end:
            # Skip weekends
            if current.weekday() >= 5:
                current += timedelta(days=1)
                continue

            bar_open = state.price
            bar_high = state.price
            bar_low = state.price

            for _ in range(minutes_per_bar):
                state.step(dt_per_step)
                bar_high = max(bar_high, state.price)
                bar_low = min(bar_low, state.price)

            ts = datetime(current.year, current.month, current.day,
                          9, 30, tzinfo=timezone.utc)
            bars.append(Bar(
                ticker=ticker,
                timestamp=ts,
                open=round(bar_open, 4),
                high=round(bar_high, 4),
                low=round(bar_low, 4),
                close=round(state.price, 4),
                volume=state.day_volume,
                vwap=round((bar_open + bar_high + bar_low + state.price) / 4, 4),
            ))

            if timespan in ("day", "week"):
                state.day_volume = 0
                state.day_open = state.price
                state.day_high = state.price
                state.day_low = state.price
                current += timedelta(days=(5 if timespan == "week" else 1))
            else:
                current += timedelta(minutes=minutes_per_bar)

        return bars
```

---

## Exceptions

```python
# market/exceptions.py

class MarketDataError(Exception):
    """Base for all market data errors."""

class TickerNotFoundError(MarketDataError):
    """Raised when a ticker is unknown or unavailable."""

class RateLimitError(MarketDataError):
    """Raised when the Massive API returns HTTP 429."""
```

---

## Testing with the Simulator

Because `MarketSimulator` implements `MarketDataProvider`, tests use it without any mocking:

```python
# tests/test_market.py
import asyncio
import pytest
from datetime import date
from market.simulator import MarketSimulator


@pytest.fixture
def sim():
    return MarketSimulator(tickers=["AAPL", "MSFT"], seed=0)


def test_get_quote_returns_positive_price(sim):
    q = asyncio.run(sim.get_quote("AAPL"))
    assert q.price > 0
    assert q.bid < q.ask


def test_get_quotes_batch(sim):
    quotes = asyncio.run(sim.get_quotes(["AAPL", "MSFT"]))
    assert set(quotes) == {"AAPL", "MSFT"}


def test_bars_are_sorted(sim):
    bars = asyncio.run(sim.get_bars("AAPL", "day", date(2025, 1, 2), date(2025, 1, 31)))
    timestamps = [b.timestamp for b in bars]
    assert timestamps == sorted(timestamps)


def test_bars_ohlcv_invariant(sim):
    bars = asyncio.run(sim.get_bars("AAPL", "day", date(2025, 1, 2), date(2025, 1, 10)))
    for bar in bars:
        assert bar.low <= bar.open <= bar.high
        assert bar.low <= bar.close <= bar.high
        assert bar.volume >= 0


def test_deterministic_with_same_seed():
    sim1 = MarketSimulator(tickers=["TSLA"], seed=99)
    sim2 = MarketSimulator(tickers=["TSLA"], seed=99)
    q1 = asyncio.run(sim1.get_quote("TSLA"))
    q2 = asyncio.run(sim2.get_quote("TSLA"))
    assert q1.price == q2.price


def test_unknown_ticker_auto_created(sim):
    q = asyncio.run(sim.get_quote("ZZZZ"))
    assert q.ticker == "ZZZZ"
    assert q.price > 0
```

---

## Simulator Limitations

| Limitation | Reason / Workaround |
|---|---|
| No intraday seasonality | Real volumes spike at open/close; simulator is flat. Add a volume-shape multiplier if needed. |
| No correlations | Each ticker is independent. For portfolio simulation, use a correlated GBM variant with a Cholesky-factored covariance matrix. |
| No earnings/news jumps | GBM is continuous; add Poisson jump events to simulate gaps if needed. |
| No bid/ask bounce | Spread is fixed fraction; real spreads widen in illiquid conditions. |
| Historical bars replay live state | Calling `get_bars` mutates the same RNG state as `get_quote`. Don't mix them in tests. |
