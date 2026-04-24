# Massive API (formerly Polygon.io) — Stock Price Reference

Massive (rebranded from Polygon.io on October 30, 2025) provides REST and WebSocket access to U.S. stock market data. The base REST URL is `https://api.massive.com`. The legacy `api.polygon.io` domain remains temporarily supported.

**Python client**: `pip install -U massive`  
**Authentication**: Pass `MASSIVE_API_KEY` as an environment variable, or supply it explicitly to the client constructor.

---

## Authentication

```python
from massive import RESTClient

# Picks up MASSIVE_API_KEY from the environment automatically
client = RESTClient()

# Or supply explicitly
client = RESTClient(api_key="YOUR_API_KEY")
```

All REST calls are authenticated via the `Authorization: Bearer <key>` header, which the SDK handles automatically.

---

## Key Endpoints

### 1. Single Ticker Snapshot (realtime)

Returns the latest minute bar, day bar, previous-day bar, last trade, and last quote for one ticker. This is the primary endpoint for real-time price data.

**REST**
```
GET https://api.massive.com/v2/snapshot/locale/us/markets/stocks/tickers/{stocksTicker}
```

| Parameter      | Location | Type   | Required | Description                         |
|----------------|----------|--------|----------|-------------------------------------|
| `stocksTicker` | path     | string | yes      | Case-sensitive ticker (e.g., `AAPL`) |

**Response fields**

| Field                  | Type    | Description                             |
|------------------------|---------|-----------------------------------------|
| `ticker.ticker`        | string  | Symbol                                  |
| `ticker.day.o/h/l/c`  | number  | Today's OHLC bar                        |
| `ticker.day.v`         | number  | Today's volume                          |
| `ticker.day.vw`        | number  | Today's VWAP                            |
| `ticker.min.o/h/l/c`  | number  | Latest minute bar OHLC                  |
| `ticker.lastTrade.p`   | number  | Last trade price                        |
| `ticker.lastTrade.s`   | integer | Last trade size (shares)                |
| `ticker.lastTrade.t`   | integer | Timestamp (nanoseconds)                 |
| `ticker.lastQuote.P`   | number  | Ask price                               |
| `ticker.lastQuote.p`   | number  | Bid price                               |
| `ticker.prevDay.c`     | number  | Previous day close                      |
| `ticker.todaysChange`  | number  | Price change from previous day close    |
| `ticker.todaysChangePerc` | number | Percentage change                    |
| `ticker.updated`       | integer | Last update (nanoseconds epoch)         |

**Example response**
```json
{
  "status": "OK",
  "request_id": "657e430f1ae768891f018e08e03598d8",
  "ticker": {
    "day":       {"o": 119.62, "h": 120.53, "l": 118.81, "c": 120.4229, "v": 28727868, "vw": 119.725},
    "min":       {"o": 120.41, "h": 120.47, "l": 120.37, "c": 120.4201, "v": 270796},
    "lastTrade": {"p": 120.47, "s": 236,  "t": 1605195918306274000},
    "lastQuote": {"P": 120.47, "p": 120.46, "t": 1605195918507251700},
    "prevDay":   {"o": 117.00, "h": 119.63, "l": 116.44, "c": 119.49},
    "ticker": "AAPL",
    "todaysChange": 0.98,
    "todaysChangePerc": 0.82,
    "updated": 1605195918306274000
  }
}
```

**Python SDK**
```python
snapshot = client.get_snapshot_ticker("stocks", "AAPL")
print(snapshot.ticker.last_trade.price)     # last trade price
print(snapshot.ticker.day.close)            # today's close so far
print(snapshot.ticker.todays_change_perc)   # % change
```

---

### 2. Multi-Ticker Snapshot (realtime, batch)

Returns snapshots for a comma-separated list of tickers, or for the entire market if no tickers are specified.

**REST**
```
GET https://api.massive.com/v2/snapshot/locale/us/markets/stocks/tickers
```

| Parameter | Location | Type   | Required | Description                                             |
|-----------|----------|--------|----------|---------------------------------------------------------|
| `tickers` | query    | string | no       | Comma-separated list (e.g., `AAPL,MSFT,GOOG`). Omit for full market. |

**Python SDK**
```python
# Specific tickers
snapshots = client.get_snapshot_all("stocks", params={"tickers": "AAPL,MSFT,GOOG"})
for snap in snapshots:
    print(snap.ticker, snap.last_trade.price)

# Entire market
all_snaps = client.get_snapshot_all("stocks")
```

---

### 3. Daily Ticker Summary / Open-Close (end of day)

Returns OHLC, volume, pre-market, and after-hours prices for a specific trading date. This is the canonical end-of-day endpoint.

**REST**
```
GET https://api.massive.com/v1/open-close/{stocksTicker}/{date}
```

| Parameter      | Location | Type    | Required | Description                          |
|----------------|----------|---------|----------|--------------------------------------|
| `stocksTicker` | path     | string  | yes      | Ticker symbol                        |
| `date`         | path     | string  | yes      | Date in `YYYY-MM-DD` format          |
| `adjusted`     | query    | boolean | no       | Split-adjusted prices (default: true)|

**Response fields**

| Field        | Type    | Description                   |
|--------------|---------|-------------------------------|
| `symbol`     | string  | Ticker                        |
| `from`       | string  | Requested date                |
| `open`       | number  | Opening price                 |
| `high`       | number  | Daily high                    |
| `low`        | number  | Daily low                     |
| `close`      | number  | Closing price                 |
| `volume`     | number  | Total volume                  |
| `afterHours` | number  | After-hours closing price     |
| `preMarket`  | number  | Pre-market opening price      |
| `status`     | string  | `OK` or `NotFound`            |

**Example response**
```json
{
  "status": "OK",
  "symbol": "AAPL",
  "from": "2023-01-09",
  "open": 324.66,
  "high": 326.20,
  "low": 322.30,
  "close": 325.12,
  "volume": 26122646,
  "afterHours": 322.10,
  "preMarket": 324.50
}
```

**Python SDK**
```python
eod = client.get_daily_open_close_agg("AAPL", "2023-01-09", adjusted=True)
print(eod.close, eod.volume)
```

---

### 4. Aggregate Bars (OHLCV candles)

Returns OHLCV bars for any timespan (minute, hour, day, week, month, quarter, year) and any date range. The primary endpoint for historical intraday or multi-day OHLCV data.

**REST**
```
GET https://api.massive.com/v2/aggs/ticker/{stocksTicker}/range/{multiplier}/{timespan}/{from}/{to}
```

| Parameter      | Location | Type    | Required | Description                                   |
|----------------|----------|---------|----------|-----------------------------------------------|
| `stocksTicker` | path     | string  | yes      | Ticker symbol                                 |
| `multiplier`   | path     | integer | yes      | Size of the timespan multiplier               |
| `timespan`     | path     | string  | yes      | `minute`, `hour`, `day`, `week`, `month`      |
| `from`         | path     | string  | yes      | Start date `YYYY-MM-DD`                       |
| `to`           | path     | string  | yes      | End date `YYYY-MM-DD`                         |
| `adjusted`     | query    | boolean | no       | Split-adjusted (default: true)                |
| `sort`         | query    | string  | no       | `asc` or `desc`                               |
| `limit`        | query    | integer | no       | Max results per page (default: 5000)          |

**Agg bar object fields**

| Field          | Description                         |
|----------------|-------------------------------------|
| `o`            | Open                                |
| `h`            | High                                |
| `l`            | Low                                 |
| `c`            | Close                               |
| `v`            | Volume                              |
| `vw`           | VWAP                                |
| `t`            | Timestamp (milliseconds UTC epoch)  |
| `n`            | Number of transactions              |

**Python SDK — paginated iteration**
```python
from massive import RESTClient
from massive.rest.models import Agg
import datetime

client = RESTClient()

aggs = []
for a in client.list_aggs(
    "AAPL",
    multiplier=1,
    timespan="day",
    from_="2024-01-01",
    to="2024-12-31",
    limit=50000,
    adjusted=True,
):
    aggs.append(a)

for bar in aggs:
    ts = datetime.datetime.fromtimestamp(bar.timestamp / 1000, tz=datetime.timezone.utc)
    print(f"{ts.date()}  O={bar.open}  H={bar.high}  L={bar.low}  C={bar.close}  V={bar.volume}")
```

---

### 5. Last Trade

Returns the most recent trade for a ticker.

**REST**
```
GET https://api.massive.com/v2/last/trade/{stocksTicker}
```

**Python SDK**
```python
trade = client.get_last_trade("AAPL")
print(trade.price, trade.size, trade.timestamp)
```

---

### 6. Last Quote (NBBO)

Returns the most recent National Best Bid and Offer quote.

**REST**
```
GET https://api.massive.com/v2/last/nbbo/{stocksTicker}
```

**Python SDK**
```python
quote = client.get_last_quote("AAPL")
print(quote.bid_price, quote.ask_price, quote.bid_size, quote.ask_size)
```

---

### 7. Previous Day Bar

Returns the prior trading day's OHLCV bar for a ticker.

**REST**
```
GET https://api.massive.com/v2/aggs/ticker/{stocksTicker}/prev
```

**Python SDK**
```python
prev = client.get_previous_close_agg("AAPL")
print(prev.close, prev.volume)
```

---

## Rate Limits & Plans

| Plan     | Rate limit          | Notes                          |
|----------|---------------------|--------------------------------|
| Free     | 5 req/min           | Limited history                |
| Starter  | Unlimited           | Real-time delayed 15 min       |
| Business | Unlimited           | Real-time + `fmv` field        |

Requests exceeding the rate limit return HTTP 429. The SDK does not auto-retry by default.

---

## Complete Working Example — Fetch Prices for Multiple Tickers

```python
import os
from massive import RESTClient

client = RESTClient(api_key=os.environ["MASSIVE_API_KEY"])

TICKERS = ["AAPL", "MSFT", "GOOG", "AMZN", "TSLA"]

# --- Realtime: batch snapshot ---
snapshots = client.get_snapshot_all("stocks", params={"tickers": ",".join(TICKERS)})
for snap in snapshots:
    t = snap.ticker
    print(
        f"{t:6s}  price={snap.last_trade.price:.2f}"
        f"  bid={snap.last_quote.bid_price:.2f}"
        f"  ask={snap.last_quote.ask_price:.2f}"
        f"  change={snap.todays_change_perc:+.2f}%"
    )

# --- End of day: previous close ---
for ticker in TICKERS:
    prev = client.get_previous_close_agg(ticker)
    print(f"{ticker:6s}  prev_close={prev.close:.2f}  volume={prev.volume:,}")

# --- Historical daily bars ---
for a in client.list_aggs("AAPL", 1, "day", "2025-01-01", "2025-04-01"):
    print(a.timestamp, a.open, a.close)
```
