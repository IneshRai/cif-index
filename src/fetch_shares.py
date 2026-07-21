"""
fetch_shares.py

Shares outstanding maintenance for the CIF index.

Alpaca does NOT provide fundamentals (no shares-outstanding endpoint), so under
an Alpaca-only data stack shares.csv is a maintained input rather than an API
pull. This is consistent with index practice and with methodology section 10:
point-in-time shares are captured at each quarterly reference date and held
constant between rebalances (the back-cast holds the latest snapshot constant).

Recommended source: Bloomberg field CUR_MKT_CAP / EQY_SH_OUT (or IQ_SHARES) for
each constituent at the reference date, exported to shares.csv.

This script does two things, neither of which hits a network:

  --scaffold : write/refresh a shares.csv template listing every constituent,
               preserving any share counts already filled in.
  (default)  : validate an existing shares.csv against the constituents file,
               reporting any missing tickers, blank/zero shares, or extras.

Usage
  python src/fetch_shares.py --constituents constituents.csv --output shares.csv --scaffold
  python src/fetch_shares.py --constituents constituents.csv --output shares.csv
"""

import argparse
import logging
import os
import sys
from datetime import date

import pandas as pd

log = logging.getLogger("cif.shares")

COLUMNS = ["ticker", "shares_outstanding", "as_of", "name"]


def scaffold(constituents_path, output_path):
    cons = pd.read_csv(constituents_path)
    cons["ticker"] = cons["ticker"].str.strip()
    name_col = "company_name" if "company_name" in cons.columns else None

    existing = {}
    if os.path.exists(output_path):
        old = pd.read_csv(output_path)
        for _, r in old.iterrows():
            existing[str(r["ticker"]).strip()] = r.to_dict()

    rows = []
    for _, c in cons.iterrows():
        t = c["ticker"]
        prev = existing.get(t, {})
        rows.append({
            "ticker": t,
            "shares_outstanding": prev.get("shares_outstanding", ""),
            "as_of": prev.get("as_of", ""),
            "name": prev.get("name", c[name_col] if name_col else ""),
        })
    pd.DataFrame(rows, columns=COLUMNS).to_csv(output_path, index=False)
    filled = sum(1 for r in rows if str(r["shares_outstanding"]).strip()
                 not in ("", "nan"))
    log.info("Wrote template %s with %d tickers (%d already filled). "
             "Populate shares_outstanding from Bloomberg, then re-run without "
             "--scaffold to validate.", output_path, len(rows), filled)


def validate(constituents_path, output_path):
    if not os.path.exists(output_path):
        log.error("%s does not exist. Run with --scaffold first.", output_path)
        return 1
    cons = pd.read_csv(constituents_path)
    cons["ticker"] = cons["ticker"].str.strip()
    need = set(cons["ticker"])

    sh = pd.read_csv(output_path)
    sh["ticker"] = sh["ticker"].astype(str).str.strip()
    have = set(sh["ticker"])

    problems = []
    missing = sorted(need - have)
    if missing:
        problems.append(f"missing tickers: {missing}")
    extra = sorted(have - need)
    if extra:
        log.warning("shares.csv has tickers not in constituents: %s", extra)

    vals = pd.to_numeric(sh["shares_outstanding"], errors="coerce")
    blanks = sorted(sh.loc[vals.isna(), "ticker"])
    blanks = [t for t in blanks if t in need]
    if blanks:
        problems.append(f"blank/non-numeric shares: {blanks}")
    nonpos = sorted(sh.loc[(vals <= 0).fillna(False), "ticker"])
    nonpos = [t for t in nonpos if t in need]
    if nonpos:
        problems.append(f"non-positive shares: {nonpos}")

    if problems:
        for p in problems:
            log.error("%s", p)
        return 1
    log.info("shares.csv OK: %d constituents all present with positive shares.",
             len(need))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Maintain shares.csv (no network)")
    ap.add_argument("--constituents", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--scaffold", action="store_true",
                    help="Write/refresh a template instead of validating")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s")

    if args.scaffold:
        scaffold(args.constituents, args.output)
        return 0
    return validate(args.constituents, args.output)


if __name__ == "__main__":
    sys.exit(main())
