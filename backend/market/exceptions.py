class MarketDataError(Exception):
    """Base for all market data errors."""


class TickerNotFoundError(MarketDataError):
    """Raised when a ticker is unknown or unavailable."""


class RateLimitError(MarketDataError):
    """Raised when the Massive API returns HTTP 429."""
