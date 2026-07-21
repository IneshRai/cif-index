"""
make_synthetic_demo.py

Generates a full-scale SYNTHETIC demonstration universe with the same
structure as the real CTIF universe: 53 tickers (14 Builders, 20 Components,
19 Resources), a mega-cap that forces the 10 percent cap to bind, dividend
payers in Resources, one stock split, one mid-history IPO, and one delisting.
Prices are geometric random walks with a sleeve common factor. Nothing here
has market meaning; the purpose is to exercise and demonstrate the production
pipeline end to end.

Usage
  python src/make_synthetic_demo.py --output-dir demo_synthetic
"""

import argparse
import logging
import os
import sys

import numpy as np
import pandas as pd

log = logging.getLogger("ctif.demo")


def make_frame(dates, close, div=None, split=None):
    """Vendor-consistent price frame (same construction as validate_math)."""
    n = len(dates)
    close = np.asarray(close, dtype=float)
    div = np.zeros(n) if div is None else np.asarray(div, dtype=float)
    split = np.ones(n) if split is None else np.asarray(split, dtype=float)
    tr_factor = np.ones(n)
    for i in range(1, n):
        tr_factor[i] = (close[i] * split[i] + div[i]) / close[i - 1]
    adj = close[0] * np.cumprod(tr_factor)
    return pd.DataFrame({
        "date": dates, "open": close, "high": close, "low": close,
        "close": close, "adjusted_close": adj, "volume": 1_000_000.0,
        "dividend_amount": div, "split_coefficient": split,
    })


SLEEVES = {
    # sleeve: (prefix, count, annual drift, common factor vol, idio vol,
    #          dividend yield annual, factor beta range)
    "BUILDERS":   ("BLD", 14, 0.30, 0.017, 0.020, 0.005, (0.8, 1.3)),
    "COMPONENTS": ("CMP", 20, 0.24, 0.021, 0.026, 0.003, (0.8, 1.5)),
    "RESOURCES":  ("RES", 19, 0.12, 0.011, 0.015, 0.028, (0.7, 1.2)),
}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Synthetic CTIF demo universe")
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--start", default="2022-01-03")
    ap.add_argument("--end", default="2026-07-10")
    ap.add_argument("--seed", type=int, default=20260713)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    raw_dir = os.path.join(args.output_dir, "raw")
    os.makedirs(raw_dir, exist_ok=True)
    rng = np.random.default_rng(args.seed)
    dates = pd.bdate_range(args.start, args.end)
    n = len(dates)
    log.info("Calendar: %d trading days %s to %s", n,
             dates[0].date(), dates[-1].date())

    cons_rows, share_rows = [], []
    for sleeve, (pfx, count, drift, fvol, ivol, dy, betas) in SLEEVES.items():
        common = rng.normal(0, fvol, n)
        for k in range(1, count + 1):
            ticker = f"{pfx}{k:02d}"
            beta = float(rng.uniform(*betas))
            mu = drift * float(rng.uniform(0.4, 1.6)) / 252.0
            s0, s1 = 0, n
            div_every, split_day = 0, None
            note = "synthetic"
            if ticker == "CMP20":            # mid-history IPO
                s0 = 260
                note = "synthetic IPO, lists in early 2023"
            if ticker == "RES19":            # delisting
                s1 = 700
                note = "synthetic delisting, acquired mid 2024"
            if ticker == "BLD07":            # 2-for-1 split
                split_day = 500
                note = "synthetic 2-for-1 split"
            if sleeve == "RESOURCES" and k <= 12:
                div_every = 63               # quarterly payers
            m = s1 - s0
            ret = mu + beta * common[s0:s1] + rng.normal(0, ivol, m)
            ret[0] = 0.0
            close = float(rng.uniform(30, 300)) * np.cumprod(1.0 + ret)
            div = np.zeros(m)
            if div_every:
                per = close[div_every::div_every] * dy / 4.0
                div[div_every::div_every] = np.round(per, 2)
            split = np.ones(m)
            if split_day is not None and s0 <= split_day < s1:
                split[split_day - s0] = 2.0
                close[split_day - s0:] = close[split_day - s0:] / 2.0
            make_frame(dates[s0:s1], close, div=div, split=split).to_csv(
                os.path.join(raw_dir, f"{ticker}.csv"), index=False)

            shares = float(rng.lognormal(np.log(1.2e9), 0.7))
            if ticker == "CMP01":            # the mega-cap; cap must bind
                shares = 4.5e9
                close_scale = 250.0 / close[0]
                note = "synthetic mega-cap, 10 percent cap binds"
                # rewrite with a high starting price for a huge mcap
                close = close * close_scale
                make_frame(dates[s0:s1], close, div=div, split=split).to_csv(
                    os.path.join(raw_dir, f"{ticker}.csv"), index=False)
                shares = 40.0e9
            cons_rows.append({
                "ticker": ticker, "company_name": f"Synthetic {ticker}",
                "sleeve": sleeve, "category": "synthetic",
                "add_date": dates[s0].date(), "notes": note})
            share_rows.append({"ticker": ticker,
                               "shares_outstanding": shares})

    pd.DataFrame(cons_rows).to_csv(
        os.path.join(args.output_dir, "constituents.csv"), index=False)
    pd.DataFrame(share_rows).to_csv(
        os.path.join(args.output_dir, "shares.csv"), index=False)

    bench_dir = os.path.join(args.output_dir, "benchmarks")
    os.makedirs(bench_dir, exist_ok=True)
    market = rng.normal(0.0004, 0.010, n)
    for name, beta, extra in [("SYN-BROAD", 1.0, 0.004),
                              ("SYN-TECH", 1.4, 0.009)]:
        ret = beta * market + rng.normal(0, extra, n)
        ret[0] = 0.0
        close = 100.0 * np.cumprod(1.0 + ret)
        make_frame(dates, close).to_csv(
            os.path.join(bench_dir, f"{name}.csv"), index=False)
    log.info("Wrote 2 synthetic benchmarks to %s", bench_dir)

    with open(os.path.join(args.output_dir, "SYNTHETIC_DATA_ONLY.txt"), "w") as f:
        f.write("THIS DIRECTORY CONTAINS SYNTHETIC DEMONSTRATION DATA ONLY.\n"
                "Prices are random walks. Levels and returns have no market\n"
                "meaning. The purpose is to demonstrate the production\n"
                "pipeline. Real runs require Alpaca pulls per README.\n")
    log.info("Wrote %d tickers to %s", len(cons_rows), raw_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
