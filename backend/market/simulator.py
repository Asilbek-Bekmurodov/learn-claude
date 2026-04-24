from __future__ import annotations

import math
import hashlib
import random
from datetime import date, datetime, timedelta, timezone
from typing import Sequence

from .models import Bar, DailyBar, Quote

MINUTES_PER_DAY = 390
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

SPREAD_FRACTION = 0.0005  # 5 bps half-spread


def _ticker_seed(ticker: str, base_seed: int) -> int:
    h = int(hashlib.md5(ticker.encode()).hexdigest(), 16)
    return (base_seed ^ h) & 0xFFFFFFFF


def _default_price_for(ticker: str) -> float:
    h = int(hashlib.md5(ticker.encode()).hexdigest(), 16)
    return 20.0 + (h % 4800) / 10.0  # range: $20–$500


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
        state = self._get_or_create(ticker)
        dt = 1.0 / (TRADING_DAYS_PER_YEAR * MINUTES_PER_DAY)
        open_price = state.price
        day_high = state.price
        day_low = state.price
        day_volume = 0

        for _ in range(MINUTES_PER_DAY):
            prev_vol = state.day_volume
            state.step(dt)
            day_high = max(day_high, state.price)
            day_low = min(day_low, state.price)
            day_volume += state.day_volume - prev_vol

        close = round(state.price, 4)
        ts = datetime(day.year, day.month, day.day, 16, 0, tzinfo=timezone.utc)

        # Reset state for the next simulated day
        state.prev_close = close
        state.day_open = close
        state.day_high = close
        state.day_low = close
        state.day_volume = 0

        return DailyBar(
            ticker=ticker,
            timestamp=ts,
            open=round(open_price, 4),
            high=round(day_high, 4),
            low=round(day_low, 4),
            close=close,
            volume=day_volume,
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
            bar_volume_start = state.day_volume

            for _ in range(minutes_per_bar):
                state.step(dt_per_step)
                bar_high = max(bar_high, state.price)
                bar_low = min(bar_low, state.price)

            bar_volume = state.day_volume - bar_volume_start
            ts = datetime(current.year, current.month, current.day,
                          9, 30, tzinfo=timezone.utc)
            bars.append(Bar(
                ticker=ticker,
                timestamp=ts,
                open=round(bar_open, 4),
                high=round(bar_high, 4),
                low=round(bar_low, 4),
                close=round(state.price, 4),
                volume=max(0, bar_volume),
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
