"""
weekly_report.py

Generates a one-page markdown summary from index_levels.csv: current levels,
trailing returns, sleeve spread, and the next rebalance date. This is the
minimal delivery layer; the dashboard versus weekly report decision is open.

Usage
  python src/weekly_report.py --levels output/index_levels.csv \
      --output output/weekly_report.md
"""

import argparse
import logging
import sys
from datetime import date

import pandas as pd

from compute_index import REBALANCE_MONTHS, nth_friday

log = logging.getLogger("ctif.report")

SERIES_ORDER = ["CTIF-X", "CTIF-B", "CTIF-C", "CTIF-R", "CTIF-AGG"]
LABELS = {"CTIF-X": "Composite (equal sleeves)",
          "CTIF-B": "Builders",
          "CTIF-C": "Components",
          "CTIF-R": "Resources",
          "CTIF-AGG": "Aggregate cap-weight (diagnostic)"}


def trailing_return(s, periods):
    if len(s) <= periods:
        return None
    return float(s.iloc[-1] / s.iloc[-1 - periods] - 1.0)


def since_return(s, start_ts):
    prior = s.loc[:start_ts]
    if prior.empty:
        return None
    return float(s.iloc[-1] / prior.iloc[-1] - 1.0)


def fmt(x):
    return "n/a" if x is None else f"{x:+.2%}"


def next_rebalance(after):
    candidates = []
    for year in (after.year, after.year + 1):
        for month in REBALANCE_MONTHS:
            d = nth_friday(year, month, 3)
            if d > after:
                candidates.append(d)
    return min(candidates)


def build_report(levels):
    asof = levels.index[-1]
    year_start = pd.Timestamp(asof.year, 1, 1)
    q_month = 3 * ((asof.month - 1) // 3) + 1
    quarter_start = pd.Timestamp(asof.year, q_month, 1)
    month_start = pd.Timestamp(asof.year, asof.month, 1)

    lines = [
        "# CTIF Weekly Summary",
        f"As of {asof.date()}. Total return versions. Base 100.00 on "
        "2022-01-03. History before the live date is back-cast; see "
        "methodology section 11.",
        "",
        "| Series | Level | 1W | MTD | QTD | YTD | TR-PR YTD gap |",
        "|---|---|---|---|---|---|---|",
    ]
    for code in SERIES_ORDER:
        tr_col, pr_col = f"{code}-TR", f"{code}-PR"
        if tr_col not in levels.columns:
            continue
        s_tr, s_pr = levels[tr_col].dropna(), levels[pr_col].dropna()
        ytd_tr = since_return(s_tr, year_start - pd.Timedelta(days=1))
        ytd_pr = since_return(s_pr, year_start - pd.Timedelta(days=1))
        gap = None if None in (ytd_tr, ytd_pr) else ytd_tr - ytd_pr
        lines.append(
            f"| {LABELS[code]} | {s_tr.iloc[-1]:,.2f} "
            f"| {fmt(trailing_return(s_tr, 5))} "
            f"| {fmt(since_return(s_tr, month_start - pd.Timedelta(days=1)))} "
            f"| {fmt(since_return(s_tr, quarter_start - pd.Timedelta(days=1)))} "
            f"| {fmt(ytd_tr)} | {fmt(gap)} |")

    b = since_return(levels["CTIF-B-TR"].dropna(), year_start)
    c = since_return(levels["CTIF-C-TR"].dropna(), year_start)
    r = since_return(levels["CTIF-R-TR"].dropna(), year_start)
    if None not in (b, c, r):
        spread = max(b, c, r) - min(b, c, r)
        leader = max([("Builders", b), ("Components", c), ("Resources", r)],
                     key=lambda kv: kv[1])[0]
        lines += ["", f"Sleeve dispersion YTD: {spread:.2%} "
                      f"(leader: {leader})."]
    lines += ["", f"Next scheduled rebalance (third Friday): "
                  f"{next_rebalance(asof.date())}."]
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="CTIF weekly summary")
    ap.add_argument("--levels", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    levels = pd.read_csv(args.levels, parse_dates=["date"]).set_index("date")
    report = build_report(levels)
    with open(args.output, "w") as f:
        f.write(report)
    log.info("Wrote %s", args.output)
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
