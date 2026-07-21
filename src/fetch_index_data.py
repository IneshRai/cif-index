"""
fetch_index_data.py

Pulls daily bars from Alpaca for every ticker in the constituents file (plus
optional extra tickers such as benchmarks) and writes one CSV per ticker to
the data directory in the engine's expected format:

  date, open, high, low, close, adjusted_close, volume,
  dividend_amount, split_coefficient

Raw bars supply close/OHLCV, fully-adjusted bars supply adjusted_close, and
the corporate-actions endpoint supplies dividend_amount and split_coefficient.
See alpaca_source.py for the source-to-column mapping.

Credentials come from ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY or the flags.

Usage
  python src/fetch_index_data.py --constituents constituents.csv \
      --output-dir data/raw --start 2015-01-01
  python src/fetch_index_data.py --tickers SPY QQQ SMH XLU \
      --output-dir data/benchmarks --start 2015-01-01

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

import alpaca_source as az

log = logging.getLogger("cif.fetch")


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
             ticker, len(df), df["date"].iloc[0].date(),
             df["date"].iloc[-1].date(),
             int((df["dividend_amount"] > 0).sum()),
             int((df["split_coefficient"] != 1.0).sum()))


def merge_incremental(existing_path, new_df):
    old = pd.read_csv(existing_path, parse_dates=["date"])
    merged = pd.concat([old, new_df], ignore_index=True)
    merged = merged.drop_duplicates(subset="date", keep="last")
    return merged.sort_values("date").reset_index(drop=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Fetch daily bars from Alpaca")
    ap.add_argument("--constituents", help="CSV with a ticker column")
    ap.add_argument("--tickers", nargs="*", default=[],
                    help="Extra tickers, for example benchmarks")
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--start", default=az.DEFAULT_START,
                    help="History start date YYYY-MM-DD")
    ap.add_argument("--end", default=None, help="Optional end date YYYY-MM-DD")
    ap.add_argument("--feed", default="iex", choices=["iex", "sip"],
                    help="iex is free; sip needs a paid subscription")
    ap.add_argument("--incremental", action="store_true",
                    help="Merge into existing CSVs instead of overwriting")
    ap.add_argument("--key-id", default=None)
    ap.add_argument("--secret-key", default=None)
    ap.add_argument("--sleep", type=float, default=0.3,
                    help="Seconds between tickers")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s")

    try:
        key_id, secret_key = az.resolve_credentials(args.key_id, args.secret_key)
    except RuntimeError as exc:
        log.error("%s", exc)
        return 2

    tickers = list(dict.fromkeys(args.tickers))
    if args.constituents:
        cons = pd.read_csv(args.constituents)
        tickers = list(dict.fromkeys(list(cons["ticker"].str.strip()) + tickers))
    if not tickers:
        log.error("No tickers to fetch")
        return 2

    os.makedirs(args.output_dir, exist_ok=True)
    session = requests.Session()

    log.info("Fetching corporate actions for %d symbols", len(tickers))
    divs, splits = az.fetch_corporate_actions(session, tickers, key_id,
                                              secret_key, args.start, args.end)
    log.info("Corporate actions: %d dividend events, %d split events",
             len(divs), len(splits))

    failures = []
    for i, t in enumerate(tickers, 1):
        log.info("[%d/%d] %s", i, len(tickers), t)
        try:
            raw = az.fetch_bars(session, t, key_id, secret_key, args.start,
                                args.end, args.feed, "raw")
            adj = az.fetch_bars(session, t, key_id, secret_key, args.start,
                                args.end, args.feed, "all")
            df = az.assemble_frame(t, raw, adj, divs, splits)
            validate_frame(df, t)
            path = os.path.join(args.output_dir, f"{t}.csv")
            if args.incremental and os.path.exists(path):
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
