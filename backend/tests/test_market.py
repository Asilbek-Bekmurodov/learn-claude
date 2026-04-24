"""Full unit tests for the market data backend.

All tests run against MarketSimulator — no live API key required.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

# Ensure the backend package is importable from the tests/ directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from market.exceptions import MarketDataError, RateLimitError, TickerNotFoundError
from market.models import Bar, DailyBar, Quote
from market.simulator import (
    DEFAULT_PARAMS,
    MINUTES_PER_DAY,
    TRADING_DAYS_PER_YEAR,
    MarketSimulator,
    _default_price_for,
    _ticker_seed,
)
from market.client import create_market_client
from market.sync import SyncMarketClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sim():
    return MarketSimulator(tickers=["AAPL", "MSFT"], seed=0)


@pytest.fixture
def full_sim():
    return MarketSimulator(tickers=list(DEFAULT_PARAMS.keys() - {"_default"}), seed=42)


# ===========================================================================
# models.py
# ===========================================================================

class TestModels:
    def test_quote_fields(self):
        q = Quote(
            ticker="AAPL", price=150.0, bid=149.9, ask=150.1,
            bid_size=100, ask_size=200, volume=1_000_000,
            change=1.5, change_pct=1.01,
            timestamp=datetime.now(tz=timezone.utc),
        )
        assert q.ticker == "AAPL"
        assert q.price == 150.0
        assert q.bid < q.ask

    def test_bar_fields(self):
        ts = datetime(2025, 1, 2, 9, 30, tzinfo=timezone.utc)
        b = Bar(ticker="MSFT", timestamp=ts, open=100, high=105, low=99, close=103, volume=500_000)
        assert b.vwap is None
        assert b.transactions is None
        assert b.high >= b.open

    def test_daily_bar_inherits_bar(self):
        ts = datetime(2025, 1, 2, 16, 0, tzinfo=timezone.utc)
        db = DailyBar(
            ticker="GOOG", timestamp=ts,
            open=170, high=180, low=165, close=175, volume=10_000_000,
            pre_market=169.0, after_hours=176.0,
        )
        assert isinstance(db, Bar)
        assert db.pre_market == 169.0
        assert db.after_hours == 176.0

    def test_daily_bar_optional_fields_default_none(self):
        ts = datetime(2025, 1, 2, 16, 0, tzinfo=timezone.utc)
        db = DailyBar(ticker="X", timestamp=ts, open=10, high=11, low=9, close=10.5, volume=100)
        assert db.pre_market is None
        assert db.after_hours is None


# ===========================================================================
# exceptions.py
# ===========================================================================

class TestExceptions:
    def test_hierarchy(self):
        assert issubclass(TickerNotFoundError, MarketDataError)
        assert issubclass(RateLimitError, MarketDataError)
        assert issubclass(MarketDataError, Exception)

    def test_raise_ticker_not_found(self):
        with pytest.raises(TickerNotFoundError, match="ZZZZ"):
            raise TickerNotFoundError("Ticker not found: ZZZZ")

    def test_raise_rate_limit(self):
        with pytest.raises(RateLimitError):
            raise RateLimitError("HTTP 429")

    def test_raise_market_data_error(self):
        with pytest.raises(MarketDataError):
            raise MarketDataError("generic error")


# ===========================================================================
# simulator.py — internal helpers
# ===========================================================================

class TestSimulatorHelpers:
    def test_ticker_seed_deterministic(self):
        assert _ticker_seed("AAPL", 42) == _ticker_seed("AAPL", 42)

    def test_ticker_seed_differs_by_ticker(self):
        assert _ticker_seed("AAPL", 42) != _ticker_seed("MSFT", 42)

    def test_ticker_seed_differs_by_base(self):
        assert _ticker_seed("AAPL", 42) != _ticker_seed("AAPL", 99)

    def test_default_price_range(self):
        for ticker in ["ZZZZ", "XYZ", "TEST", "FOO"]:
            p = _default_price_for(ticker)
            assert 20.0 <= p <= 500.0

    def test_default_price_deterministic(self):
        assert _default_price_for("ABCD") == _default_price_for("ABCD")

    def test_default_price_differs_by_ticker(self):
        assert _default_price_for("AAA") != _default_price_for("BBB")


# ===========================================================================
# simulator.py — MarketSimulator
# ===========================================================================

class TestMarketSimulatorQuote:
    def test_get_quote_returns_positive_price(self, sim):
        q = run(sim.get_quote("AAPL"))
        assert q.price > 0

    def test_get_quote_bid_less_than_ask(self, sim):
        q = run(sim.get_quote("AAPL"))
        assert q.bid < q.ask

    def test_get_quote_ticker_field(self, sim):
        q = run(sim.get_quote("MSFT"))
        assert q.ticker == "MSFT"

    def test_get_quote_returns_quote_type(self, sim):
        q = run(sim.get_quote("AAPL"))
        assert isinstance(q, Quote)

    def test_get_quote_timestamp_is_timezone_aware(self, sim):
        q = run(sim.get_quote("AAPL"))
        assert q.timestamp.tzinfo is not None

    def test_get_quote_volume_non_negative(self, sim):
        q = run(sim.get_quote("AAPL"))
        assert q.volume >= 0

    def test_get_quote_bid_size_and_ask_size_positive(self, sim):
        q = run(sim.get_quote("AAPL"))
        assert q.bid_size > 0
        assert q.ask_size > 0

    def test_get_quote_unknown_ticker_auto_created(self, sim):
        q = run(sim.get_quote("ZZZZ"))
        assert q.ticker == "ZZZZ"
        assert q.price > 0

    def test_get_quote_price_advances_on_each_call(self, sim):
        q1 = run(sim.get_quote("AAPL"))
        q2 = run(sim.get_quote("AAPL"))
        # Prices should differ (with overwhelming probability)
        assert q1.price != q2.price

    def test_get_quote_spread_fraction(self, sim):
        q = run(sim.get_quote("AAPL"))
        spread = q.ask - q.bid
        # Spread should be approximately 2 * 5 bps of price
        assert spread / q.price < 0.01  # spread < 1%


class TestMarketSimulatorQuotes:
    def test_get_quotes_returns_all_tickers(self, sim):
        quotes = run(sim.get_quotes(["AAPL", "MSFT"]))
        assert set(quotes.keys()) == {"AAPL", "MSFT"}

    def test_get_quotes_values_are_quotes(self, sim):
        quotes = run(sim.get_quotes(["AAPL", "MSFT"]))
        for q in quotes.values():
            assert isinstance(q, Quote)

    def test_get_quotes_empty_list(self, sim):
        quotes = run(sim.get_quotes([]))
        assert quotes == {}

    def test_get_quotes_single_ticker(self, sim):
        quotes = run(sim.get_quotes(["AAPL"]))
        assert "AAPL" in quotes
        assert quotes["AAPL"].price > 0

    def test_get_quotes_unknown_tickers(self, sim):
        quotes = run(sim.get_quotes(["FOO", "BAR"]))
        assert "FOO" in quotes
        assert "BAR" in quotes


class TestMarketSimulatorEodBar:
    def test_get_eod_bar_returns_daily_bar(self, sim):
        bar = run(sim.get_eod_bar("AAPL", date(2025, 1, 2)))
        assert isinstance(bar, DailyBar)

    def test_get_eod_bar_ticker_field(self, sim):
        bar = run(sim.get_eod_bar("AAPL", date(2025, 1, 2)))
        assert bar.ticker == "AAPL"

    def test_get_eod_bar_timestamp_matches_date(self, sim):
        day = date(2025, 3, 14)
        bar = run(sim.get_eod_bar("AAPL", day))
        assert bar.timestamp.date() == day

    def test_get_eod_bar_ohlc_invariant(self, sim):
        bar = run(sim.get_eod_bar("MSFT", date(2025, 1, 3)))
        assert bar.low <= bar.open
        assert bar.low <= bar.close
        assert bar.high >= bar.open
        assert bar.high >= bar.close

    def test_get_eod_bar_positive_price(self, sim):
        bar = run(sim.get_eod_bar("AAPL", date(2025, 1, 2)))
        assert bar.close > 0
        assert bar.open > 0


class TestMarketSimulatorPreviousClose:
    def test_get_previous_close_returns_daily_bar(self, sim):
        bar = run(sim.get_previous_close("AAPL"))
        assert isinstance(bar, DailyBar)

    def test_get_previous_close_ticker_field(self, sim):
        bar = run(sim.get_previous_close("MSFT"))
        assert bar.ticker == "MSFT"

    def test_get_previous_close_timestamp_is_yesterday(self, sim):
        bar = run(sim.get_previous_close("AAPL"))
        yesterday = date.today() - timedelta(days=1)
        assert bar.timestamp.date() == yesterday


class TestMarketSimulatorBars:
    def test_get_bars_returns_list(self, sim):
        bars = run(sim.get_bars("AAPL", "day", date(2025, 1, 2), date(2025, 1, 10)))
        assert isinstance(bars, list)

    def test_get_bars_elements_are_bar_type(self, sim):
        bars = run(sim.get_bars("AAPL", "day", date(2025, 1, 2), date(2025, 1, 10)))
        for b in bars:
            assert isinstance(b, Bar)

    def test_get_bars_timestamps_sorted_ascending(self, sim):
        bars = run(sim.get_bars("AAPL", "day", date(2025, 1, 2), date(2025, 1, 31)))
        timestamps = [b.timestamp for b in bars]
        assert timestamps == sorted(timestamps)

    def test_get_bars_ohlcv_invariant(self, sim):
        bars = run(sim.get_bars("AAPL", "day", date(2025, 1, 2), date(2025, 1, 10)))
        for bar in bars:
            assert bar.low <= bar.open <= bar.high, f"low <= open <= high failed: {bar}"
            assert bar.low <= bar.close <= bar.high, f"low <= close <= high failed: {bar}"
            assert bar.volume >= 0

    def test_get_bars_skips_weekends(self, sim):
        bars = run(sim.get_bars("AAPL", "day", date(2025, 1, 2), date(2025, 1, 12)))
        for b in bars:
            assert b.timestamp.weekday() < 5, f"Weekend bar found: {b.timestamp}"

    def test_get_bars_ticker_field_set(self, sim):
        bars = run(sim.get_bars("MSFT", "day", date(2025, 1, 6), date(2025, 1, 8)))
        for b in bars:
            assert b.ticker == "MSFT"

    def test_get_bars_vwap_set(self, sim):
        bars = run(sim.get_bars("AAPL", "day", date(2025, 1, 2), date(2025, 1, 3)))
        for b in bars:
            assert b.vwap is not None

    def test_get_bars_empty_range_weekend_only(self, sim):
        # 2025-01-04 and 2025-01-05 are Saturday and Sunday
        bars = run(sim.get_bars("AAPL", "day", date(2025, 1, 4), date(2025, 1, 5)))
        assert bars == []

    def test_get_bars_hourly_timespan(self, sim):
        bars = run(sim.get_bars("AAPL", "hour", date(2025, 1, 2), date(2025, 1, 2)))
        assert len(bars) > 0
        for b in bars:
            assert b.low <= b.close <= b.high

    def test_get_bars_weekly_timespan(self, sim):
        bars = run(sim.get_bars("AAPL", "week", date(2025, 1, 6), date(2025, 1, 31)))
        assert len(bars) > 0

    def test_get_bars_multiplier_2_day(self, sim):
        sim2 = MarketSimulator(tickers=["AAPL"], seed=0)
        bars = run(sim2.get_bars("AAPL", "day", date(2025, 1, 6), date(2025, 1, 20), multiplier=2))
        assert len(bars) > 0


class TestMarketSimulatorDeterminism:
    def test_same_seed_same_price(self):
        sim1 = MarketSimulator(tickers=["TSLA"], seed=99)
        sim2 = MarketSimulator(tickers=["TSLA"], seed=99)
        q1 = run(sim1.get_quote("TSLA"))
        q2 = run(sim2.get_quote("TSLA"))
        assert q1.price == q2.price

    def test_different_seed_different_price(self):
        sim1 = MarketSimulator(tickers=["AAPL"], seed=1)
        sim2 = MarketSimulator(tickers=["AAPL"], seed=2)
        q1 = run(sim1.get_quote("AAPL"))
        q2 = run(sim2.get_quote("AAPL"))
        assert q1.price != q2.price

    def test_same_seed_same_bars(self):
        sim1 = MarketSimulator(tickers=["AAPL"], seed=77)
        sim2 = MarketSimulator(tickers=["AAPL"], seed=77)
        bars1 = run(sim1.get_bars("AAPL", "day", date(2025, 1, 6), date(2025, 1, 10)))
        bars2 = run(sim2.get_bars("AAPL", "day", date(2025, 1, 6), date(2025, 1, 10)))
        assert [b.close for b in bars1] == [b.close for b in bars2]

    def test_tickers_independent(self):
        sim = MarketSimulator(tickers=["AAPL", "MSFT"], seed=42)
        qa = run(sim.get_quote("AAPL"))
        qm = run(sim.get_quote("MSFT"))
        assert qa.price != qm.price

    def test_known_ticker_uses_default_params_price(self):
        sim = MarketSimulator(seed=42)
        state = sim._get_or_create("AAPL")
        # Initial price should match DEFAULT_PARAMS before any stepping
        assert state.prev_close == DEFAULT_PARAMS["AAPL"]["price"]

    def test_unknown_ticker_stable_price(self):
        p1 = _default_price_for("UNKNOWN")
        p2 = _default_price_for("UNKNOWN")
        assert p1 == p2


class TestMarketSimulatorCustomParams:
    def test_custom_params_override(self):
        custom = {"AAPL": {"price": 50.0, "mu": 0.0, "sigma": 0.01, "adv": 1_000_000}}
        sim = MarketSimulator(tickers=["AAPL"], seed=42, params=custom)
        state = sim._get_or_create("AAPL")
        assert state.price == 50.0

    def test_custom_ticker_not_in_defaults(self):
        custom = {"MYCO": {"price": 75.0, "mu": 0.05, "sigma": 0.20, "adv": 500_000}}
        sim = MarketSimulator(tickers=["MYCO"], seed=42, params=custom)
        q = run(sim.get_quote("MYCO"))
        assert q.ticker == "MYCO"
        assert q.price > 0

    def test_sim_pre_initialises_tickers(self):
        sim = MarketSimulator(tickers=["AAPL", "MSFT", "GOOG"], seed=42)
        assert "AAPL" in sim._states
        assert "MSFT" in sim._states
        assert "GOOG" in sim._states

    def test_sim_lazy_creates_unspecified_tickers(self):
        sim = MarketSimulator(seed=42)
        assert "NVDA" not in sim._states
        run(sim.get_quote("NVDA"))
        assert "NVDA" in sim._states


# ===========================================================================
# client.py — create_market_client factory
# ===========================================================================

class TestCreateMarketClient:
    def test_returns_simulator_without_api_key(self):
        env = {k: v for k, v in os.environ.items() if k != "MASSIVE_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            client = create_market_client()
        assert isinstance(client, MarketSimulator)

    def test_returns_simulator_with_explicit_none(self):
        env = {k: v for k, v in os.environ.items() if k != "MASSIVE_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            client = create_market_client(api_key=None)
        assert isinstance(client, MarketSimulator)

    def test_sim_tickers_forwarded(self):
        env = {k: v for k, v in os.environ.items() if k != "MASSIVE_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            client = create_market_client(sim_tickers=["AAPL", "TSLA"])
        assert isinstance(client, MarketSimulator)
        assert "AAPL" in client._states
        assert "TSLA" in client._states

    def test_sim_seed_forwarded(self):
        env = {k: v for k, v in os.environ.items() if k != "MASSIVE_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            c1 = create_market_client(sim_seed=7)
            c2 = create_market_client(sim_seed=7)
        q1 = run(c1.get_quote("AAPL"))
        q2 = run(c2.get_quote("AAPL"))
        assert q1.price == q2.price

    def test_uses_env_api_key(self):
        """When MASSIVE_API_KEY is set, MassiveClient should be returned (import may fail without package)."""
        with patch.dict(os.environ, {"MASSIVE_API_KEY": "test-key"}):
            try:
                from market.massive_client import MassiveClient
                client = create_market_client()
                assert isinstance(client, MassiveClient)
            except ImportError:
                pytest.skip("massive package not installed; skipping live-client factory test")


# ===========================================================================
# sync.py — SyncMarketClient
# ===========================================================================

class TestSyncMarketClient:
    @pytest.fixture
    def sync_client(self):
        env = {k: v for k, v in os.environ.items() if k != "MASSIVE_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            return SyncMarketClient(sim_tickers=["AAPL", "MSFT"], sim_seed=0)

    def test_get_quote(self, sync_client):
        q = sync_client.get_quote("AAPL")
        assert isinstance(q, Quote)
        assert q.price > 0

    def test_get_quotes(self, sync_client):
        quotes = sync_client.get_quotes(["AAPL", "MSFT"])
        assert set(quotes.keys()) == {"AAPL", "MSFT"}

    def test_get_eod_bar(self, sync_client):
        bar = sync_client.get_eod_bar("AAPL", date(2025, 1, 2))
        assert isinstance(bar, DailyBar)
        assert bar.close > 0

    def test_get_previous_close(self, sync_client):
        bar = sync_client.get_previous_close("AAPL")
        assert isinstance(bar, DailyBar)

    def test_get_bars(self, sync_client):
        bars = sync_client.get_bars("AAPL", "day", date(2025, 1, 6), date(2025, 1, 10))
        assert len(bars) > 0
        for b in bars:
            assert isinstance(b, Bar)

    def test_get_bars_kwargs_forwarded(self, sync_client):
        bars = sync_client.get_bars("AAPL", "day", date(2025, 1, 6), date(2025, 1, 10), multiplier=1, adjusted=True)
        assert len(bars) > 0


# ===========================================================================
# interface.py — MarketDataProvider Protocol structural check
# ===========================================================================

class TestMarketDataProviderProtocol:
    def test_simulator_satisfies_protocol(self):
        from market.interface import MarketDataProvider
        sim = MarketSimulator(seed=0)
        # Runtime check: all protocol methods exist and are callable
        assert callable(getattr(sim, "get_quote", None))
        assert callable(getattr(sim, "get_quotes", None))
        assert callable(getattr(sim, "get_eod_bar", None))
        assert callable(getattr(sim, "get_previous_close", None))
        assert callable(getattr(sim, "get_bars", None))


# ===========================================================================
# massive_client.py — MassiveClient (mocked — no live network)
# ===========================================================================

class TestMassiveClientMocked:
    """Test MassiveClient logic using a mocked massive.RESTClient."""

    def _make_snap(self, ticker="AAPL", price=150.0, bid=149.9, ask=150.1,
                   volume=1_000_000, change=1.5, change_pct=1.01, updated_ns=None):
        snap = MagicMock()
        snap.ticker = ticker
        snap.last_trade.price = price
        snap.last_quote.bid_price = bid
        snap.last_quote.ask_price = ask
        snap.last_quote.bid_size = 100
        snap.last_quote.ask_size = 200
        snap.day.volume = volume
        snap.todays_change = change
        snap.todays_change_perc = change_pct
        snap.updated = (updated_ns or 1_700_000_000_000_000_000)
        return snap

    def _make_agg(self, open=100, high=110, low=98, close=105,
                  volume=500_000, vwap=103.5, transactions=12345, timestamp_ms=None):
        agg = MagicMock()
        agg.open = open
        agg.high = high
        agg.low = low
        agg.close = close
        agg.volume = volume
        agg.vwap = vwap
        agg.transactions = transactions
        agg.timestamp = timestamp_ms or 1_700_000_000_000
        return agg

    def _make_eod(self, ticker="AAPL", from_="2025-01-02", open=100, high=110,
                  low=98, close=105, volume=500_000, pre=99.0, after=106.0):
        eod = MagicMock()
        eod.from_ = from_
        eod.open = open
        eod.high = high
        eod.low = low
        eod.close = close
        eod.volume = volume
        eod.pre_market = pre
        eod.after_hours = after
        return eod

    @pytest.fixture
    def massive_client(self):
        with patch.dict("sys.modules", {"massive": MagicMock()}):
            from market.massive_client import MassiveClient
            mock_rest = MagicMock()
            with patch("market.massive_client.MassiveClient.__init__", lambda self, api_key: None):
                client = MassiveClient.__new__(MassiveClient)
                client._client = mock_rest
            return client, mock_rest

    def test_get_quote_returns_quote(self, massive_client):
        client, rest = massive_client
        rest.get_snapshot_ticker.return_value = self._make_snap()
        q = run(client.get_quote("AAPL"))
        assert isinstance(q, Quote)
        assert q.ticker == "AAPL"
        assert q.price == 150.0

    def test_get_quote_bid_ask(self, massive_client):
        client, rest = massive_client
        rest.get_snapshot_ticker.return_value = self._make_snap(bid=149.9, ask=150.1)
        q = run(client.get_quote("AAPL"))
        assert q.bid == 149.9
        assert q.ask == 150.1

    def test_get_quotes_batch(self, massive_client):
        client, rest = massive_client
        snaps = [
            self._make_snap("AAPL", price=150.0),
            self._make_snap("MSFT", price=420.0),
        ]
        rest.get_snapshot_all.return_value = snaps
        quotes = run(client.get_quotes(["AAPL", "MSFT"]))
        assert set(quotes.keys()) == {"AAPL", "MSFT"}
        assert quotes["AAPL"].price == 150.0
        assert quotes["MSFT"].price == 420.0

    def test_get_eod_bar(self, massive_client):
        client, rest = massive_client
        rest.get_daily_open_close_agg.return_value = self._make_eod()
        bar = run(client.get_eod_bar("AAPL", date(2025, 1, 2)))
        assert isinstance(bar, DailyBar)
        assert bar.close == 105
        assert bar.pre_market == 99.0
        assert bar.after_hours == 106.0

    def test_get_previous_close(self, massive_client):
        client, rest = massive_client
        prev = MagicMock()
        prev.open = 100
        prev.high = 110
        prev.low = 98
        prev.close = 105
        prev.volume = 500_000
        prev.vwap = 103.5
        prev.timestamp = 1_700_000_000_000
        rest.get_previous_close_agg.return_value = prev
        bar = run(client.get_previous_close("AAPL"))
        assert isinstance(bar, DailyBar)
        assert bar.close == 105

    def test_get_bars(self, massive_client):
        client, rest = massive_client
        rest.list_aggs.return_value = iter([
            self._make_agg(close=100, timestamp_ms=1_700_000_000_000),
            self._make_agg(close=101, timestamp_ms=1_700_086_400_000),
        ])
        bars = run(client.get_bars("AAPL", "day", date(2025, 1, 2), date(2025, 1, 3)))
        assert len(bars) == 2
        assert bars[0].close == 100
        assert bars[1].close == 101

    def test_get_quote_raises_rate_limit_on_429(self, massive_client):
        client, rest = massive_client
        rest.get_snapshot_ticker.side_effect = Exception("HTTP 429 Too Many Requests")
        with pytest.raises(RateLimitError):
            run(client.get_quote("AAPL"))

    def test_get_quote_raises_ticker_not_found_on_404(self, massive_client):
        client, rest = massive_client
        rest.get_snapshot_ticker.side_effect = Exception("HTTP 404 Not Found")
        with pytest.raises(TickerNotFoundError):
            run(client.get_quote("FAKE"))

    def test_get_quote_raises_market_data_error_on_generic(self, massive_client):
        client, rest = massive_client
        rest.get_snapshot_ticker.side_effect = Exception("Connection refused")
        with pytest.raises(MarketDataError):
            run(client.get_quote("AAPL"))


# ===========================================================================
# Integration-style: simulator as drop-in for MarketDataProvider
# ===========================================================================

class TestSimulatorAsProvider:
    """Verify the simulator satisfies the full MarketDataProvider contract."""

    @pytest.fixture
    def provider(self):
        return MarketSimulator(tickers=["AAPL", "GOOG"], seed=123)

    def test_full_workflow(self, provider):
        # 1. realtime quotes
        quote = run(provider.get_quote("AAPL"))
        assert quote.price > 0

        # 2. batch quotes
        quotes = run(provider.get_quotes(["AAPL", "GOOG"]))
        assert len(quotes) == 2

        # 3. EOD bar
        eod = run(provider.get_eod_bar("AAPL", date(2025, 6, 1)))
        assert eod.close > 0
        assert eod.high >= eod.low

        # 4. historical bars
        bars = run(provider.get_bars("GOOG", "day", date(2025, 1, 6), date(2025, 1, 17)))
        assert len(bars) > 0
        for b in bars:
            assert b.low <= b.close <= b.high

    def test_multiple_calls_price_evolves(self, provider):
        prices = [run(provider.get_quote("AAPL")).price for _ in range(5)]
        # Not all the same
        assert len(set(prices)) > 1
