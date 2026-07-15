"""
fetch_index_data.py

Pulls TIME_SERIES_DAILY_ADJUSTED from Alpha Vantage for every ticker in the
constituents file (plus optional extra tickers such as benchmarks) and writes
one CSV per ticker to the data directory in the engine's expected format:

  date, open, high, low, close, adjusted_close, volume,
  dividend_amount, split_coefficient

API key comes from the ALPHAVANTAGE_API_KEY environment variable or --api-key.

Usage
  python src/fetch_index_data.py --constituents constituents.csv \
      --output-dir data/raw --outputsize full
  python src/fetch_index_data.py --tickers SPY QQQ SMH XLU RACK DTCR \
      --output-dir data/benchmarks --outputsize full

The first full pull doubles as ticker verification: any symbol that fails
after retries is reported at the end and must be investigated before the
index is computed.
"""

import argparse
import logging
import os
import sys
import time

import pandas as pd
import requests

BASE_URL = "https://www.alphavantage.co/query"
FIELD_MAP = {
    "1. open": "open",
    "2. high": "high",
    "3. low": "low",
    "4. close": "close",
    "5. adjusted close": "adjusted_close",
    "6. volume": "volume",
    "7. dividend amount": "dividend_amount",
    "8. split coefficient": "split_coefficient",
}

log = logging.getLogger("ctif.fetch")


def parse_daily_adjusted(payload, ticker):
    """Convert the Alpha Vantage JSON payload into the engine's CSV schema."""
    if "Error Message" in payload:
        raise ValueError(f"{ticker}: API error: {payload['Error Message']}")
    for k in ("Note", "Information"):
        if k in payload and "Time Series (Daily)" not in payload:
            raise RuntimeError(f"{ticker}: throttled or notice: {payload[k]}")
    series = payload.get("Time Series (Daily)")
    if not series:
        raise ValueError(f"{ticker}: no time series in response")
    rows = []
    for day, values in series.items():
        row = {"date": day}
        for src, dst in FIELD_MAP.items():
            if src not in values:
                raise ValueError(f"{ticker} {day}: missing field {src}")
            row[dst] = float(values[src])
        rows.append(row)
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    return df


def validate_frame(df, ticker):
    problems = []
    if df["date"].duplicated().any():
        problems.append("duplicate dates")
    if (df["close"] <= 0).any() or (df["adjusted_close"] <= 0).any():
        problems.append("non-positive prices")
    if (df["split_coefficient"] <= 0).any():
        problems.append("non-positive split coefficient")
    if (df["dividend_amount"] < 0).any():
        problems.append("negative dividend")
    if problems:
        raise ValueError(f"{ticker}: validation failed: {problems}")
    log.info("%s: %d rows %s to %s, %d dividends, %d splits",
             ticker, len(df), df['date'].iloc[0].date(),
             df['date'].iloc[-1].date(),
             int((df['dividend_amount'] > 0).sum()),
             int((df['split_coefficient'] != 1.0).sum()))


def fetch_one(session, ticker, api_key, outputsize, retries=3, backoff=5.0):
    params = {
        "function": "TIME_SERIES_DAILY_ADJUSTED",
        "symbol": ticker,
        "outputsize": outputsize,
        "apikey": api_key,
        "datatype": "json",
    }
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(BASE_URL, params=params, timeout=60)
            resp.raise_for_status()
            df = parse_daily_adjusted(resp.json(), ticker)
            validate_frame(df, ticker)
            return df
        except Exception as exc:
            last_err = exc
            log.warning("%s attempt %d/%d failed: %s",
                        ticker, attempt, retries, exc)
            time.sleep(backoff * attempt)
    raise RuntimeError(f"{ticker}: failed after {retries} attempts: {last_err}")


def merge_incremental(existing_path, new_df):
    old = pd.read_csv(existing_path, parse_dates=["date"])
    merged = pd.concat([old, new_df], ignore_index=True)
    merged = merged.drop_duplicates(subset="date", keep="last")
    return merged.sort_values("date").reset_index(drop=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Fetch daily adjusted prices")
    ap.add_argument("--constituents", help="CSV with a ticker column")
    ap.add_argument("--tickers", nargs="*", default=[],
                    help="Extra tickers, for example benchmarks")
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--outputsize", choices=["full", "compact"],
                    default="full",
                    help="full for history builds, compact for daily updates")
    ap.add_argument("--api-key", default=os.environ.get("ALPHAVANTAGE_API_KEY"))
    ap.add_argument("--sleep", type=float, default=0.9,
                    help="Seconds between requests (premium 75/min)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s")
    if not args.api_key:
        log.error("No API key. Set ALPHAVANTAGE_API_KEY or pass --api-key")
        return 2

    tickers = list(dict.fromkeys(args.tickers))
    if args.constituents:
        cons = pd.read_csv(args.constituents)
        tickers = list(dict.fromkeys(list(cons["ticker"].str.strip())
                                     + tickers))
    if not tickers:
        log.error("No tickers to fetch")
        return 2

    os.makedirs(args.output_dir, exist_ok=True)
    session = requests.Session()
    failures = []
    for i, t in enumerate(tickers, 1):
        log.info("[%d/%d] %s", i, len(tickers), t)
        try:
            df = fetch_one(session, t, args.api_key, args.outputsize)
            path = os.path.join(args.output_dir, f"{t}.csv")
            if args.outputsize == "compact" and os.path.exists(path):
                df = merge_incremental(path, df)
            df.to_csv(path, index=False)
        except Exception as exc:
            log.error("%s FAILED: %s", t, exc)
            failures.append(t)
        time.sleep(args.sleep)

    if failures:
        log.error("FAILED tickers (verify symbols before computing): %s",
                  failures)
        return 1
    log.info("All %d tickers fetched and validated.", len(tickers))
    return 0


if __name__ == "__main__":
    sys.exit(main())
