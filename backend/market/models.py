from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Quote:
    ticker: str
    price: float
    bid: float
    ask: float
    bid_size: int
    ask_size: int
    volume: int
    change: float
    change_pct: float
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
