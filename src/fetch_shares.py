"""
fetch_shares.py

Pulls SharesOutstanding from the Alpha Vantage OVERVIEW endpoint for every
constituent and writes shares.csv (ticker, shares_outstanding, as_of, name).

Run this at each quarterly reference date so weights use point-in-time shares
from the first production quarter forward. The back-cast holds the latest
snapshot constant, as disclosed in methodology.md section 10.

Usage
  python src/fetch_shares.py --constituents constituents.csv --output shares.csv
"""

import argparse
import logging
import os
import sys
import time
from datetime import date

import pandas as pd
import requests

BASE_URL = "https://www.alphavantage.co/query"
log = logging.getLogger("ctif.shares")


def parse_overview(payload, ticker):
    if "Error Message" in payload:
        raise ValueError(f"{ticker}: API error: {payload['Error Message']}")
    for k in ("Note", "Information"):
        if k in payload and "SharesOutstanding" not in payload:
            raise RuntimeError(f"{ticker}: throttled or notice: {payload[k]}")
    raw = payload.get("SharesOutstanding")
    if raw in (None, "", "None", "0"):
        raise ValueError(f"{ticker}: SharesOutstanding missing in OVERVIEW")
    shares = float(raw)
    if shares <= 0:
        raise ValueError(f"{ticker}: non-positive shares {shares}")
    return shares, payload.get("Name", "")


def fetch_one(session, ticker, api_key, retries=3, backoff=5.0):
    params = {"function": "OVERVIEW", "symbol": ticker, "apikey": api_key}
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(BASE_URL, params=params, timeout=60)
            resp.raise_for_status()
            return parse_overview(resp.json(), ticker)
        except Exception as exc:
            last_err = exc
            log.warning("%s attempt %d/%d failed: %s",
                        ticker, attempt, retries, exc)
            time.sleep(backoff * attempt)
    raise RuntimeError(f"{ticker}: failed after {retries} attempts: {last_err}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Fetch shares outstanding")
    ap.add_argument("--constituents", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--api-key", default=os.environ.get("ALPHAVANTAGE_API_KEY"))
    ap.add_argument("--sleep", type=float, default=0.9)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s")
    if not args.api_key:
        log.error("No API key. Set ALPHAVANTAGE_API_KEY or pass --api-key")
        return 2

    cons = pd.read_csv(args.constituents)
    tickers = list(cons["ticker"].str.strip())
    session = requests.Session()
    rows, failures = [], []
    for i, t in enumerate(tickers, 1):
        log.info("[%d/%d] %s", i, len(tickers), t)
        try:
            shares, name = fetch_one(session, t, args.api_key)
            log.info("%s: %.0f shares (%s)", t, shares, name)
            rows.append({"ticker": t, "shares_outstanding": shares,
                         "as_of": date.today().isoformat(), "name": name})
        except Exception as exc:
            log.error("%s FAILED: %s", t, exc)
            failures.append(t)
        time.sleep(args.sleep)

    pd.DataFrame(rows).to_csv(args.output, index=False)
    log.info("Wrote %d rows to %s", len(rows), args.output)
    if failures:
        log.error("FAILED tickers: %s. Fix before computing the index.",
                  failures)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
