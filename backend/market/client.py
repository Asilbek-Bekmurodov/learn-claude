from __future__ import annotations

import os

from .interface import MarketDataProvider
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
        from .massive_client import MassiveClient
        return MassiveClient(api_key=key)
    return MarketSimulator(tickers=sim_tickers or [], seed=sim_seed)
