"""
update_shares.py

Refreshes shares_outstanding in shares.csv using yfinance (free, no Bloomberg).

Shares outstanding move slowly (buybacks, issuance), so this is meant to run on
a slow cadence (weekly). It is intentionally separate from the daily price
refresh so a yfinance hiccup can never break the price snapshot.

Fail-safe behavior:
  - If yfinance returns a good value, use it and stamp as_of with today.
  - If the fetch fails or returns junk, KEEP the previous value in shares.csv
    (never overwrite a good number with a blank).
  - If there is no previous value either, leave it blank; the index engine
    falls back to equal-weighting those names rather than crashing.

Data quality note: yfinance shares are scraped from Yahoo and are generally
reliable for large caps but not audit-grade, and can be briefly stale around
corporate actions. Fine for a monitoring instrument; not a Bloomberg substitute.

Usage:
  python src/update_shares.py --constituents constituents.csv --output shares.csv
"""

import argparse
import logging
import sys
import time
from datetime import date

import pandas as pd

log = logging.getLogger("cif.update_shares")
COLUMNS = ["ticker", "shares_outstanding", "as_of", "name"]


def fetch_one(ticker):
    """Best-effort shares outstanding for one ticker, or None."""
    import yfinance as yf
    tk = yf.Ticker(ticker)
    getters = (
        lambda: tk.fast_info["shares"],
        lambda: getattr(tk.fast_info, "shares", None),
        lambda: tk.get_info().get("sharesOutstanding"),
    )
    for get in getters:
        try:
            v = get()
            if v and float(v) > 0:
                return float(v)
        except Exception:
            continue
    return None


def _prev_value(row):
    if not row:
        return None
    v = row.get("shares_outstanding")
    try:
        v = float(v)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Update shares.csv via yfinance")
    ap.add_argument("--constituents", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--sleep", type=float, default=1.0,
                    help="Seconds between tickers (be polite to Yahoo)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s")

    cons = pd.read_csv(args.constituents)
    cons["ticker"] = cons["ticker"].str.strip()
    name_col = "company_name" if "company_name" in cons.columns else None

    prev = {}
    try:
        old = pd.read_csv(args.output)
        for _, r in old.iterrows():
            prev[str(r["ticker"]).strip()] = r.to_dict()
    except FileNotFoundError:
        pass

    def name_for(t, prow):
        if prow and str(prow.get("name") or "").strip():
            return prow["name"]
        if name_col:
            m = cons.loc[cons["ticker"] == t, name_col]
            if len(m):
                return m.iloc[0]
        return ""

    today = date.today().isoformat()
    rows, updated, kept, blanks = [], 0, 0, []
    tickers = list(cons["ticker"])
    for i, t in enumerate(tickers, 1):
        prow = prev.get(t, {})
        prev_val = _prev_value(prow)
        val = fetch_one(t)
        if val:
            rows.append({"ticker": t, "shares_outstanding": int(val),
                         "as_of": today, "name": name_for(t, prow)})
            updated += 1
            log.info("[%d/%d] %s: %d", i, len(tickers), t, int(val))
        elif prev_val:
            rows.append({"ticker": t, "shares_outstanding": int(prev_val),
                         "as_of": prow.get("as_of") or "", "name": name_for(t, prow)})
            kept += 1
            log.warning("[%d/%d] %s: fetch failed, kept previous %d",
                        i, len(tickers), t, int(prev_val))
        else:
            rows.append({"ticker": t, "shares_outstanding": "",
                         "as_of": "", "name": name_for(t, prow)})
            blanks.append(t)
            log.error("[%d/%d] %s: fetch failed, no previous value",
                      i, len(tickers), t)
        time.sleep(args.sleep)

    pd.DataFrame(rows, columns=COLUMNS).to_csv(args.output, index=False)
    log.info("Done: %d updated, %d kept-previous, %d blank.%s",
             updated, kept, len(blanks),
             (" Blank: " + ", ".join(blanks)) if blanks else "")
    # Blanks are non-fatal: the engine equal-weights those names.
    return 0


if __name__ == "__main__":
    sys.exit(main())
