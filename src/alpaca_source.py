"""
alpaca_source.py

Shared Alpaca market-data layer for the CIF engine. Produces price frames in
the exact schema the index engine expects, sourced entirely from Alpaca:

  date, open, high, low, close, adjusted_close, volume,
  dividend_amount, split_coefficient

How the columns are sourced
  close            raw daily bar close        (GET /v2/stocks/{sym}/bars, adjustment=raw)
  open/high/low/volume  raw daily bar          (same call)
  adjusted_close   split+dividend adjusted close (same endpoint, adjustment=all)
  dividend_amount  cash dividend per share on the ex-date  (GET /v1/corporate-actions)
  split_coefficient  new_rate / old_rate on the split ex-date (same CA call), else 1.0

This preserves the engine's dual-source construction: non-event days use the
adjusted series; dividend/split days use raw close * split (+ div) and are
reconciled against the adjusted series.

Credentials
  ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY environment variables, or pass
  key_id / secret_key explicitly.

Feed
  Free plan is IEX only (feed="iex"). SIP (feed="sip", 100% market volume)
  requires a paid Alpaca market-data subscription. Historical SIP without a
  subscription is available only with a 15-minute delay, which is fine for an
  end-of-day snapshot but the default here is iex to work on the free tier.

Notes / things to verify against a live response the first time you run this:
  The corporate-actions field names below (cash_dividend -> "rate",
  forward_split/reverse_split -> "new_rate"/"old_rate", "ex_date") match
  Alpaca's market-data v1 corporate-actions schema. The parser is defensive
  (accepts "rate" or "cash") but if a split ratio ever looks inverted, check
  new_rate/old_rate ordering against the raw response.
"""

import logging
import os
import time

import pandas as pd
import requests

DATA_URL = "https://data.alpaca.markets"
DEFAULT_START = "2015-01-01"          # Alpaca stock history depth; CA data from Apr 2020
CA_TYPES = "cash_dividend,forward_split,reverse_split"

log = logging.getLogger("cif.alpaca")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
def resolve_credentials(key_id=None, secret_key=None):
    key_id = key_id or os.environ.get("ALPACA_API_KEY_ID")
    secret_key = secret_key or os.environ.get("ALPACA_API_SECRET_KEY")
    if not key_id or not secret_key:
        raise RuntimeError(
            "No Alpaca credentials. Set ALPACA_API_KEY_ID and "
            "ALPACA_API_SECRET_KEY, or pass them explicitly."
        )
    return key_id, secret_key


def _headers(key_id, secret_key):
    return {
        "APCA-API-KEY-ID": key_id,
        "APCA-API-SECRET-KEY": secret_key,
        "accept": "application/json",
    }


# ---------------------------------------------------------------------------
# Bars
# ---------------------------------------------------------------------------
def fetch_bars(session, symbol, key_id, secret_key, start=DEFAULT_START,
               end=None, feed="iex", adjustment="raw", retries=3, backoff=5.0):
    """
    Return a list of daily bar dicts for one symbol at the given adjustment.
    Each bar: {t, o, h, l, c, v, n, vw}. Handles pagination.
    """
    url = f"{DATA_URL}/v2/stocks/{symbol}/bars"
    bars, page_token = [], None
    while True:
        params = {
            "timeframe": "1Day",
            "start": start,
            "adjustment": adjustment,
            "feed": feed,
            "limit": 10000,
        }
        if end:
            params["end"] = end
        if page_token:
            params["page_token"] = page_token

        last_err = None
        for attempt in range(1, retries + 1):
            try:
                r = session.get(url, params=params,
                                headers=_headers(key_id, secret_key), timeout=60)
                r.raise_for_status()
                js = r.json()
                break
            except Exception as exc:
                last_err = exc
                log.warning("%s bars attempt %d/%d failed: %s",
                            symbol, attempt, retries, exc)
                time.sleep(backoff * attempt)
        else:
            raise RuntimeError(f"{symbol}: bars failed after {retries}: {last_err}")

        page = js.get("bars") or []
        bars.extend(page)
        page_token = js.get("next_page_token")
        if not page_token:
            break
    return bars


# ---------------------------------------------------------------------------
# Corporate actions (dividends + splits) for the whole universe in one sweep
# ---------------------------------------------------------------------------
def fetch_corporate_actions(session, symbols, key_id, secret_key,
                            start=DEFAULT_START, end=None, retries=3,
                            backoff=5.0):
    """
    Return (divs, splits):
      divs[(symbol, 'YYYY-MM-DD')]   = cash dividend per share on the ex-date
      splits[(symbol, 'YYYY-MM-DD')] = split factor (new_rate/old_rate) on ex-date
    Symbols are batched into one query (comma-separated); paginated.
    """
    if end is None:
        end = pd.Timestamp.today().strftime("%Y-%m-%d")
    url = f"{DATA_URL}/v1/corporate-actions"
    divs, splits = {}, {}
    page_token = None
    while True:
        params = {
            "symbols": ",".join(symbols),
            "types": CA_TYPES,
            "start": start,
            "end": end,
            "limit": 1000,
        }
        if page_token:
            params["page_token"] = page_token

        last_err = None
        for attempt in range(1, retries + 1):
            try:
                r = session.get(url, params=params,
                                headers=_headers(key_id, secret_key), timeout=60)
                r.raise_for_status()
                js = r.json()
                break
            except Exception as exc:
                last_err = exc
                log.warning("corporate actions attempt %d/%d failed: %s",
                            attempt, retries, exc)
                time.sleep(backoff * attempt)
        else:
            raise RuntimeError(f"corporate actions failed after {retries}: {last_err}")

        ca = js.get("corporate_actions", {}) or {}

        for d in ca.get("cash_dividends", []) or []:
            sym = d.get("symbol")
            ex = d.get("ex_date")
            rate = d.get("rate", d.get("cash"))
            if sym and ex and rate is not None:
                divs[(sym, ex)] = divs.get((sym, ex), 0.0) + float(rate)

        for key in ("forward_splits", "reverse_splits"):
            for s in ca.get(key, []) or []:
                sym = s.get("symbol")
                ex = s.get("ex_date")
                new_rate = s.get("new_rate")
                old_rate = s.get("old_rate")
                if sym and ex and new_rate and old_rate:
                    splits[(sym, ex)] = float(new_rate) / float(old_rate)

        page_token = js.get("next_page_token")
        if not page_token:
            break
    return divs, splits


# ---------------------------------------------------------------------------
# Assemble the engine schema
# ---------------------------------------------------------------------------
def assemble_frame(symbol, raw_bars, adj_bars, divs, splits):
    """
    Combine raw bars, adjusted-close bars, and corporate actions into a frame
    with the engine's column schema, indexed by ascending date.
    """
    adj_close = {b["t"][:10]: float(b["c"]) for b in adj_bars}
    rows = []
    for b in raw_bars:
        day = b["t"][:10]
        close = float(b["c"])
        rows.append({
            "date": day,
            "open": float(b["o"]),
            "high": float(b["h"]),
            "low": float(b["l"]),
            "close": close,
            # fall back to raw close if the adjusted feed lacks a day
            "adjusted_close": adj_close.get(day, close),
            "volume": float(b["v"]),
            "dividend_amount": float(divs.get((symbol, day), 0.0)),
            "split_coefficient": float(splits.get((symbol, day), 1.0)),
        })
    if not rows:
        raise ValueError(f"{symbol}: no bars returned (check symbol/feed/dates)")
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def fetch_price_frame(session, symbol, key_id, secret_key, start=DEFAULT_START,
                      end=None, feed="iex", divs=None, splits=None):
    """
    Convenience: fetch raw + adjusted bars for one symbol and assemble.
    Corporate actions can be passed in (fetched once for the whole universe)
    or fetched per-symbol if omitted.
    """
    raw = fetch_bars(session, symbol, key_id, secret_key, start, end, feed, "raw")
    adj = fetch_bars(session, symbol, key_id, secret_key, start, end, feed, "all")
    if divs is None or splits is None:
        divs, splits = fetch_corporate_actions(session, [symbol], key_id,
                                               secret_key, start, end)
    return assemble_frame(symbol, raw, adj, divs, splits)
