"""
compute_index.py

CTIF index family calculation engine. Implements methodology.md v1.0 exactly:
price return (PR) and total return (TR) series for three sleeves, the
equal-sleeve composite, and a supplementary aggregate cap-weighted composite.

Inputs
  constituents.csv  ticker, company_name, sleeve, category, add_date, notes
  shares.csv        ticker, shares_outstanding (from fetch_shares.py)
  data/raw/<TICKER>.csv  date, open, high, low, close, adjusted_close,
                         volume, dividend_amount, split_coefficient

Usage
  python src/compute_index.py --data-dir data/raw --constituents constituents.csv \
      --shares shares.csv --output-dir output --base-date 2022-01-03

All index math lives in importable functions so validate_math.py can test it.
"""

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

CAP = 0.10
SEASONING_DAYS = 63
REBALANCE_MONTHS = (3, 6, 9, 12)
BASE_VALUE = 100.0
RECON_TOL = 0.0010  # 10 bps reconciliation tolerance on event days
DELIST_BUFFER = 3   # trading days: series ending earlier than this before
                    # calendar end is treated as a delisting

log = logging.getLogger("ctif")


# ----------------------------------------------------------------------------
# Calendar helpers
# ----------------------------------------------------------------------------

def nth_friday(year, month, n):
    """Date of the nth Friday of a month. Friday is weekday 4."""
    first = date(year, month, 1)
    offset = (4 - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def snap_to_calendar(target, calendar):
    """Last trading day on or before target. Returns None if before calendar."""
    ts = pd.Timestamp(target)
    idx = calendar.searchsorted(ts, side="right") - 1
    if idx < 0:
        return None
    return calendar[idx]


def build_rebalance_schedule(calendar, base_date):
    """
    Effective dates: 3rd Friday close of Mar/Jun/Sep/Dec, snapped to the prior
    trading day when needed. Reference dates: 2nd Friday, snapped likewise.
    The base date acts as the initial effective date (seeding), with itself
    as its reference date. Returns list of (reference_ts, effective_ts).
    """
    base = pd.Timestamp(base_date)
    sched = [(base, base)]
    for year in range(calendar[0].year, calendar[-1].year + 1):
        for month in REBALANCE_MONTHS:
            eff = snap_to_calendar(nth_friday(year, month, 3), calendar)
            ref = snap_to_calendar(nth_friday(year, month, 2), calendar)
            if eff is None or ref is None:
                continue
            if eff <= base or eff >= calendar[-1]:
                continue
            sched.append((ref, eff))
    sched.sort(key=lambda pair: pair[1])
    return sched


# ----------------------------------------------------------------------------
# Data loading and per-ticker return construction
# ----------------------------------------------------------------------------

REQUIRED_COLS = ["close", "adjusted_close", "dividend_amount", "split_coefficient"]


def load_price_file(path):
    df = pd.read_csv(path, parse_dates=["date"])
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"{path}: missing columns {missing}")
    df = df.sort_values("date").set_index("date")
    if df.index.has_duplicates:
        raise ValueError(f"{path}: duplicate dates")
    return df[REQUIRED_COLS].astype(float)


def build_ticker_series(df, ticker, quality):
    """
    From one ticker's raw frame, build on the ticker's own dates:
      pr_ret  price return per methodology 8.1
      tr_ret  total return per methodology 8.1 with dual-source reconciliation
      sa_close  split-adjusted close in the current share basis
    """
    close = df["close"]
    adj = df["adjusted_close"]
    div = df["dividend_amount"].fillna(0.0)
    split = df["split_coefficient"].replace(0.0, 1.0).fillna(1.0)

    if (close <= 0).any() or (adj <= 0).any():
        bad = close.index[(close <= 0) | (adj <= 0)]
        raise ValueError(f"{ticker}: non-positive prices on {list(bad[:3])}")

    prev_close = close.shift(1)
    tr_adj = adj / adj.shift(1) - 1.0
    pr_raw = (close * split) / prev_close - 1.0
    tr_raw = (close * split + div) / prev_close - 1.0

    event = (div > 0) | (split != 1.0)
    # Non-event days: PR equals TR, taken from the vendor-adjusted series.
    pr_ret = tr_adj.copy()
    tr_ret = tr_adj.copy()
    # Event days: raw construction is the definition; adjusted series is the check.
    pr_ret[event] = pr_raw[event]
    tr_ret[event] = tr_raw[event]

    gaps = (tr_raw - tr_adj).abs()
    flagged = event & gaps.notna() & (gaps > RECON_TOL)
    for ts in df.index[flagged]:
        quality.append(
            f"RECON {ticker} {ts.date()}: raw TR {tr_raw.loc[ts]:+.4%} vs "
            f"adjusted TR {tr_adj.loc[ts]:+.4%} (gap {gaps.loc[ts]:.4%}); "
            "raw construction used, review vendor data"
        )

    # Split-adjusted close in the current share basis:
    # sa_close(t) = close(t) * k(t) / prod(k(s) for s >= t)
    fwd_prod = split.iloc[::-1].cumprod().iloc[::-1]
    sa_close = close * split / fwd_prod

    return pr_ret, tr_ret, sa_close


def load_universe(data_dir, tickers, quality):
    """
    Returns wide frames on the master calendar (union of ticker dates):
    pr, tr, sa_close, plus per-ticker first/last valid dates.
    Interior missing days are carried forward as zero returns and warned.
    """
    pr_map, tr_map, sa_map = {}, {}, {}
    for t in tickers:
        path = os.path.join(data_dir, f"{t}.csv")
        if not os.path.exists(path):
            raise FileNotFoundError(f"No price file for {t} at {path}")
        raw = load_price_file(path)
        pr_map[t], tr_map[t], sa_map[t] = build_ticker_series(raw, t, quality)

    calendar = pd.DatetimeIndex(
        sorted(set().union(*[s.index for s in sa_map.values()]))
    )
    pr = pd.DataFrame(index=calendar, columns=tickers, dtype=float)
    tr = pd.DataFrame(index=calendar, columns=tickers, dtype=float)
    sa = pd.DataFrame(index=calendar, columns=tickers, dtype=float)
    spans = {}

    for t in tickers:
        s_pr = pr_map[t].reindex(calendar)
        s_tr = tr_map[t].reindex(calendar)
        s_sa = sa_map[t].reindex(calendar)
        first, last = sa_map[t].index[0], sa_map[t].index[-1]
        inside = (calendar >= first) & (calendar <= last)
        interior_missing = inside & s_sa.isna().to_numpy()
        n_miss = int(interior_missing.sum())
        if n_miss:
            quality.append(
                f"GAPS {t}: {n_miss} interior missing day(s) carried forward "
                "as zero return"
            )
            s_sa = s_sa.where(~pd.Series(interior_missing, index=calendar), np.nan)
            s_sa = s_sa.ffill().where(pd.Series(inside, index=calendar))
            s_pr[interior_missing] = 0.0
            s_tr[interior_missing] = 0.0
        # First listed day has no prior close: zero return inside the span.
        pos_first = calendar.get_loc(first)
        s_pr.iloc[pos_first] = 0.0 if np.isnan(s_pr.iloc[pos_first]) else s_pr.iloc[pos_first]
        s_tr.iloc[pos_first] = 0.0 if np.isnan(s_tr.iloc[pos_first]) else s_tr.iloc[pos_first]
        pr[t], tr[t], sa[t] = s_pr, s_tr, s_sa
        spans[t] = (first, last)
        if calendar.get_loc(last) < len(calendar) - 1 - DELIST_BUFFER:
            quality.append(
                f"DELIST {t}: series ends {last.date()}, treated as delisted; "
                "final weight redistributed pro rata"
            )
    return pr, tr, sa, spans, calendar


# ----------------------------------------------------------------------------
# Weighting
# ----------------------------------------------------------------------------

def cap_weights(mcap, cap=CAP, max_iter=200):
    """
    Iterative single-name capping. mcap: positive Series indexed by ticker.
    Returns weights summing to 1 with every weight <= cap (plus float eps).
    """
    if (mcap <= 0).any():
        raise ValueError("Non-positive market caps passed to cap_weights")
    n = len(mcap)
    if n * cap < 1.0 - 1e-12:
        raise ValueError(f"Cap {cap} infeasible with {n} members")
    w = mcap / mcap.sum()
    for _ in range(max_iter):
        over = w > cap + 1e-12
        if not over.any():
            break
        excess = float((w[over] - cap).sum())
        w[over] = cap
        under = ~over
        w[under] = w[under] + excess * w[under] / float(w[under].sum())
    else:
        raise RuntimeError("cap_weights failed to converge")
    return w / w.sum()


def build_targets(members, sa, shares, ref_ts, cap=CAP):
    """Capped cap-weight targets from split-adjusted price * shares at ref date."""
    px = sa.loc[ref_ts, members]
    if px.isna().any():
        missing = list(px.index[px.isna()])
        raise ValueError(f"No reference price at {ref_ts.date()} for {missing}")
    mcap = px * pd.Series({m: shares[m] for m in members})
    return cap_weights(mcap.astype(float), cap=cap)


def select_members(tickers, spans, calendar, add_dates, ref_ts, eff_ts,
                   seasoning=SEASONING_DAYS):
    """
    Eligibility at a review: add_date passed, seasoned, still trading.
    At the initial constitution (ref equals effective, the base date) the
    seasoning requirement is waived: it governs entrants at later reviews,
    not founding members already listed at the base date.
    """
    initial = ref_ts == eff_ts
    ref_pos = calendar.get_loc(ref_ts)
    out = []
    for t in tickers:
        first, last = spans[t]
        if add_dates[t] > ref_ts:
            continue
        if first > ref_ts:
            continue
        if not initial and (ref_pos - calendar.get_loc(first)) < seasoning:
            continue
        if last <= eff_ts:
            continue
        out.append(t)
    return out


# ----------------------------------------------------------------------------
# Index engine
# ----------------------------------------------------------------------------

def run_index(pr, tr, calendar, schedule, target_fn, name, quality):
    """
    Generic chained-return engine per methodology 8.2.

    target_fn(ref_ts, eff_ts) -> weight Series for that rebalance, or None to
    keep prior weights (used when membership cannot be formed).

    Returns (levels DataFrame [PR, TR], daily weights DataFrame).
    """
    eff_map = {eff: ref for ref, eff in schedule}
    base_ts = schedule[0][1]
    start_pos = calendar.get_loc(base_ts)

    weights = target_fn(schedule[0][0], base_ts)
    if weights is None or weights.empty:
        raise ValueError(f"{name}: cannot seed weights at base date")

    dates = [base_ts]
    lv_pr, lv_tr = [BASE_VALUE], [BASE_VALUE]
    wt_rows = {base_ts: weights.copy()}

    for pos in range(start_pos + 1, len(calendar)):
        t = calendar[pos]
        r_pr = pr.loc[t, weights.index]
        r_tr = tr.loc[t, weights.index]

        dead = r_pr.isna() | r_tr.isna()
        if dead.any():
            for d in weights.index[dead]:
                quality.append(
                    f"DROP {name} {t.date()}: {d} weight "
                    f"{weights[d]:.4%} redistributed (no price)"
                )
            weights = weights[~dead]
            if weights.empty:
                raise ValueError(f"{name}: all members dropped at {t.date()}")
            weights = weights / weights.sum()
            r_pr, r_tr = r_pr[~dead], r_tr[~dead]

        ret_pr = float((weights * r_pr).sum())
        ret_tr = float((weights * r_tr).sum())
        lv_pr.append(lv_pr[-1] * (1.0 + ret_pr))
        lv_tr.append(lv_tr[-1] * (1.0 + ret_tr))
        dates.append(t)

        grown = weights * (1.0 + r_pr)
        weights = grown / grown.sum()

        if t in eff_map:
            new_w = target_fn(eff_map[t], t)
            if new_w is not None and not new_w.empty:
                weights = new_w
            else:
                quality.append(
                    f"REBAL {name} {t.date()}: no valid targets, "
                    "prior weights carried"
                )
        wt_rows[t] = weights.copy()

    levels = pd.DataFrame(
        {f"{name}-PR": lv_pr, f"{name}-TR": lv_tr},
        index=pd.DatetimeIndex(dates, name="date"),
    )
    wts = pd.DataFrame(wt_rows).T
    wts.index.name = "date"
    return levels, wts


def run_sleeve(sleeve, tickers, pr, tr, sa, spans, calendar, schedule,
               add_dates, shares, quality, rebal_reports, cap=CAP):
    def target_fn(ref_ts, eff_ts):
        members = select_members(tickers, spans, calendar, add_dates,
                                 ref_ts, eff_ts)
        if len(members) == 0:
            return None
        w = build_targets(members, sa, shares, ref_ts, cap=cap)
        px = sa.loc[ref_ts, members]
        rebal_reports.append(pd.DataFrame({
            "effective_date": eff_ts.date(), "reference_date": ref_ts.date(),
            "sleeve": sleeve, "ticker": members,
            "ref_price_split_adj": px.values,
            "shares": [shares[m] for m in members],
            "mcap_proxy": (px * pd.Series({m: shares[m] for m in members})).values,
            "target_weight": w.values,
        }))
        return w
    return run_index(pr, tr, calendar, schedule, target_fn,
                     f"CTIF-{sleeve[0]}", quality)


def run_composite(sleeve_levels, calendar, schedule, quality, name="CTIF-X",
                  sleeve_weights=None):
    """
    Composite over sleeve return series, equal thirds reset quarterly,
    drifting on sleeve PR returns for both PR and TR versions (8.3).
    """
    pr_cols = [c for c in sleeve_levels.columns if c.endswith("-PR")]
    tr_cols = [c for c in sleeve_levels.columns if c.endswith("-TR")]
    pr_lv = sleeve_levels[pr_cols]
    tr_lv = sleeve_levels[tr_cols]
    pr_ret = pr_lv / pr_lv.shift(1) - 1.0
    tr_ret = tr_lv / tr_lv.shift(1) - 1.0
    tr_ret.columns = pr_ret.columns = [c[:-3] for c in pr_cols]

    if sleeve_weights is None:
        sleeve_weights = pd.Series(1.0 / len(pr_ret.columns),
                                   index=pr_ret.columns)

    def target_fn(ref_ts, eff_ts):
        return sleeve_weights.copy()

    cal = sleeve_levels.index
    sched = [(r, e) for r, e in schedule if e in cal]
    return run_index(pr_ret, tr_ret, cal, sched, target_fn, name, quality)


# ----------------------------------------------------------------------------
# Statistics
# ----------------------------------------------------------------------------

def series_stats(levels):
    rows = []
    for col in levels.columns:
        s = levels[col].dropna()
        n = len(s) - 1
        if n < 2:
            continue
        total = s.iloc[-1] / s.iloc[0] - 1.0
        ann = (1.0 + total) ** (252.0 / n) - 1.0
        daily = s / s.shift(1) - 1.0
        vol = float(daily.std(ddof=1)) * np.sqrt(252.0)
        dd = float((s / s.cummax() - 1.0).min())
        rows.append({"series": col, "start": s.index[0].date(),
                     "end": s.index[-1].date(), "total_return": total,
                     "ann_return": ann, "ann_vol": vol, "max_drawdown": dd})
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description="CTIF index calculation engine")
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--constituents", required=True)
    ap.add_argument("--shares", required=True,
                    help="CSV with columns ticker, shares_outstanding")
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--base-date", default="2022-01-03")
    ap.add_argument("--cap", type=float, default=CAP)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s")
    os.makedirs(args.output_dir, exist_ok=True)
    quality = []

    cons = pd.read_csv(args.constituents, parse_dates=["add_date"])
    cons["ticker"] = cons["ticker"].str.strip()
    if cons["ticker"].duplicated().any():
        raise ValueError("Duplicate tickers in constituents file")
    tickers = list(cons["ticker"])
    add_dates = dict(zip(cons["ticker"], cons["add_date"]))
    sleeves = {s: list(g["ticker"]) for s, g in cons.groupby("sleeve")}
    log.info("Universe: %d tickers across sleeves %s",
             len(tickers), {k: len(v) for k, v in sleeves.items()})

    sh = pd.read_csv(args.shares)
    shares = dict(zip(sh["ticker"].str.strip(),
                      sh["shares_outstanding"].astype(float)))
    missing = [t for t in tickers if t not in shares]
    if missing:
        raise ValueError(f"shares file missing tickers: {missing}")

    log.info("Loading price data")
    pr, tr, sa, spans, calendar = load_universe(args.data_dir, tickers, quality)
    base_ts = snap_to_calendar(args.base_date, calendar)
    if base_ts is None or str(base_ts.date()) != args.base_date:
        log.warning("Base date snapped to %s", base_ts.date())
    schedule = build_rebalance_schedule(calendar, base_ts)
    log.info("Calendar %s to %s, %d rebalances after base",
             calendar[0].date(), calendar[-1].date(), len(schedule) - 1)

    rebal_reports = []
    all_levels, all_weights = [], {}
    for sleeve in ["BUILDERS", "COMPONENTS", "RESOURCES"]:
        if sleeve not in sleeves:
            raise ValueError(f"Sleeve {sleeve} missing from constituents")
        log.info("Computing sleeve %s (%d names)", sleeve, len(sleeves[sleeve]))
        levels, wts = run_sleeve(sleeve, sleeves[sleeve], pr, tr, sa, spans,
                                 calendar, schedule, add_dates, shares,
                                 quality, rebal_reports, cap=args.cap)
        all_levels.append(levels)
        all_weights[sleeve] = wts

    sleeve_levels = pd.concat(all_levels, axis=1)
    log.info("Computing composite")
    comp_levels, comp_wts = run_composite(sleeve_levels, calendar, schedule,
                                          quality)
    all_weights["COMPOSITE"] = comp_wts

    log.info("Computing supplementary aggregate composite")
    agg_reports = []
    agg_levels, agg_wts = run_sleeve("AGGREGATE", tickers, pr, tr, sa, spans,
                                     calendar, schedule, add_dates, shares,
                                     quality, agg_reports, cap=args.cap)
    agg_levels.columns = ["CTIF-AGG-PR", "CTIF-AGG-TR"]
    all_weights["AGGREGATE"] = agg_wts

    out_levels = pd.concat([sleeve_levels, comp_levels, agg_levels], axis=1)
    out_levels.to_csv(os.path.join(args.output_dir, "index_levels.csv"),
                      float_format="%.6f")
    for sleeve, wts in all_weights.items():
        wts.to_csv(os.path.join(args.output_dir,
                                f"weights_{sleeve.lower()}.csv"),
                   float_format="%.8f")
    if rebal_reports:
        rr = pd.concat(rebal_reports, ignore_index=True)
        for eff, g in rr.groupby("effective_date"):
            g.to_csv(os.path.join(args.output_dir,
                                  f"rebalance_report_{eff}.csv"), index=False)
    series_stats(out_levels).to_csv(
        os.path.join(args.output_dir, "stats_summary.csv"), index=False,
        float_format="%.6f")
    with open(os.path.join(args.output_dir, "quality_log.txt"), "w") as f:
        f.write("\n".join(quality) if quality else "No quality events.\n")
    with open(os.path.join(args.output_dir, "run_metadata.json"), "w") as f:
        json.dump({
            "run_timestamp_utc": datetime.utcnow().isoformat(),
            "methodology_version": "1.0",
            "base_date": str(base_ts.date()),
            "base_value": BASE_VALUE,
            "cap": args.cap,
            "data_first": str(calendar[0].date()),
            "data_last": str(calendar[-1].date()),
            "n_constituents": len(tickers),
            "n_quality_events": len(quality),
        }, f, indent=2)

    log.info("Done. %d quality events logged. Outputs in %s",
             len(quality), args.output_dir)
    tr_cols = [c for c in out_levels.columns if c.endswith("-TR")]
    log.info("Final TR levels:\n%s", out_levels[tr_cols].iloc[-1].round(2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
