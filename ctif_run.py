"""
ctif_run.py  -  Castellan Technology Infrastructure Family, one-file runner
============================================================================

Drop this into a PyCharm project, set your Alpha Vantage key below, and run.
It fetches real prices for the 53 constituents plus benchmarks, caches them
locally (so re-runs are instant and do not burn API calls), computes the
index per the CTIF methodology, and shows matplotlib charts including a
1-year comparison against SPY, QQQ, SMH, and XLU.

This single file mirrors the validated multi-file engine for convenience.
The multi-file repo (with the 39-check validation suite) remains the
reference implementation; this exists so you can see charts in one run.

Requirements:  pip install pandas numpy requests matplotlib
Python 3.9+.

IMPORTANT: index history is a BACK-CAST. The universe is selected with
hindsight (2026's known buildout winners), so it will tend to look like it
beat the market by a wide margin. That gap is mostly selection, not skill.
The sleeve-versus-sector-benchmark comparisons (Components vs SMH, Resources
vs XLU) are more informative than the headline CTIF-vs-SPY line.
"""

# ===========================================================================
# CONFIG  -  edit these two lines
# ===========================================================================
API_KEY = "PUT_YOUR_ALPHAVANTAGE_KEY_HERE"
CACHE_DIR = "ctif_cache"          # local folder for downloaded CSVs

BASE_DATE = "2022-01-03"
CAP = 0.10                        # 10 percent single-name cap
FORCE_REFRESH = False             # True to re-download even if cached
REQUEST_SLEEP = 0.9              # seconds between API calls (premium: 75/min)

# ===========================================================================
# UNIVERSE  -  ticker, sleeve
# ===========================================================================
CONSTITUENTS = [
    # Builders (14)
    ("FIX", "Builders"), ("EME", "Builders"), ("PWR", "Builders"),
    ("MTZ", "Builders"), ("STRL", "Builders"), ("IESC", "Builders"),
    ("AGX", "Builders"), ("VRT", "Builders"), ("ETN", "Builders"),
    ("HUBB", "Builders"), ("NVT", "Builders"), ("MOD", "Builders"),
    ("POWL", "Builders"), ("GEV", "Builders"),
    # Components (20)
    ("MU", "Components"), ("SNDK", "Components"), ("WDC", "Components"),
    ("STX", "Components"), ("AVGO", "Components"), ("MRVL", "Components"),
    ("ANET", "Components"), ("APH", "Components"), ("ALAB", "Components"),
    ("CRDO", "Components"), ("COHR", "Components"), ("LITE", "Components"),
    ("CIEN", "Components"), ("FN", "Components"), ("MPWR", "Components"),
    ("DELL", "Components"), ("HPE", "Components"), ("SMCI", "Components"),
    ("CLS", "Components"), ("GLW", "Components"),
    # Resources (19)
    ("VST", "Resources"), ("CEG", "Resources"), ("NRG", "Resources"),
    ("TLN", "Resources"), ("PEG", "Resources"), ("D", "Resources"),
    ("AEP", "Resources"), ("SO", "Resources"), ("ETR", "Resources"),
    ("EXC", "Resources"), ("CCJ", "Resources"), ("BWXT", "Resources"),
    ("OKLO", "Resources"), ("SMR", "Resources"), ("LEU", "Resources"),
    ("BE", "Resources"), ("EQIX", "Resources"), ("DLR", "Resources"),
    ("IRM", "Resources"),
]

# ticker -> sector benchmark it should be read against, plus broad market
BENCHMARKS = ["SPY", "QQQ", "SMH", "XLU"]

# ===========================================================================
import os
import sys
import time

import numpy as np
import pandas as pd
import requests

BASE_URL = "https://www.alphavantage.co/query"
SLEEVES = ["Builders", "Components", "Resources"]
SEASONING_DAYS = 63


# ---------------------------------------------------------------------------
# Data fetch and cache
# ---------------------------------------------------------------------------
def fetch_prices(ticker):
    """Return a DataFrame indexed by date with close, adj_close, div, split."""
    params = {"function": "TIME_SERIES_DAILY_ADJUSTED", "symbol": ticker,
              "outputsize": "full", "apikey": API_KEY, "datatype": "json"}
    r = requests.get(BASE_URL, params=params, timeout=60)
    r.raise_for_status()
    js = r.json()
    if "Error Message" in js:
        raise ValueError(f"{ticker}: {js['Error Message']}")
    if "Time Series (Daily)" not in js:
        note = js.get("Note") or js.get("Information") or js
        raise RuntimeError(f"{ticker}: no data. API said: {note}")
    ts = js["Time Series (Daily)"]
    rows = []
    for day, v in ts.items():
        rows.append({
            "date": day,
            "close": float(v["4. close"]),
            "adj_close": float(v["5. adjusted close"]),
            "div": float(v["7. dividend amount"]),
            "split": float(v["8. split coefficient"]),
        })
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").set_index("date")


def fetch_shares(ticker):
    params = {"function": "OVERVIEW", "symbol": ticker, "apikey": API_KEY}
    r = requests.get(BASE_URL, params=params, timeout=60)
    r.raise_for_status()
    js = r.json()
    val = js.get("SharesOutstanding")
    if val in (None, "", "None", "0"):
        return None
    return float(val)


def get_cached(ticker, kind, fetch_fn):
    """kind is 'px' or 'sh'. Cache to CACHE_DIR, fetch on miss."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{ticker}_{kind}.csv")
    if os.path.exists(path) and not FORCE_REFRESH:
        if kind == "px":
            return pd.read_csv(path, parse_dates=["date"]).set_index("date")
        return pd.read_csv(path)["shares"].iloc[0]
    result = fetch_fn(ticker)
    time.sleep(REQUEST_SLEEP)
    if kind == "px":
        result.to_csv(path)
    else:
        pd.DataFrame({"shares": [result if result else np.nan]}).to_csv(
            path, index=False)
    return result


def load_all():
    """Fetch/cache every ticker and benchmark. Returns price dict, shares."""
    px, shares = {}, {}
    all_tickers = [t for t, _ in CONSTITUENTS] + BENCHMARKS
    for i, t in enumerate(all_tickers, 1):
        print(f"[{i}/{len(all_tickers)}] {t} ...", end=" ", flush=True)
        try:
            px[t] = get_cached(t, "px", fetch_prices)
            if t not in BENCHMARKS:
                shares[t] = get_cached(t, "sh", fetch_shares)
            print(f"{len(px[t])} rows")
        except Exception as e:
            print(f"FAILED: {e}")
    return px, shares


# ---------------------------------------------------------------------------
# Per-ticker return construction
# ---------------------------------------------------------------------------
def build_returns(df):
    """From one ticker frame, return pr, tr (daily factors-1), sa_close."""
    close, adj = df["close"], df["adj_close"]
    div = df["div"].fillna(0.0)
    split = df["split"].replace(0.0, 1.0).fillna(1.0)
    prev = close.shift(1)
    tr_adj = adj / adj.shift(1) - 1.0                       # dividends+splits
    pr_raw = (close * split) / prev - 1.0                  # splits only
    event = (div > 0) | (split != 1.0)
    pr = tr_adj.copy()
    tr = tr_adj.copy()
    pr[event] = pr_raw[event]
    # split-adjusted close in current basis (for market cap proxy)
    fwd = split.iloc[::-1].cumprod().iloc[::-1]
    sa_close = close * split / fwd
    return pr, tr, sa_close


# ---------------------------------------------------------------------------
# Weighting
# ---------------------------------------------------------------------------
def cap_weights(mcap, cap=CAP):
    w = mcap / mcap.sum()
    for _ in range(200):
        over = w > cap + 1e-12
        if not over.any():
            break
        excess = float((w[over] - cap).sum())
        w[over] = cap
        under = ~over
        w[under] += excess * w[under] / float(w[under].sum())
    return w / w.sum()


# ---------------------------------------------------------------------------
# Calendar and rebalance schedule
# ---------------------------------------------------------------------------
def nth_friday(year, month, n):
    d = pd.Timestamp(year, month, 1)
    off = (4 - d.weekday()) % 7
    return d + pd.Timedelta(days=off + 7 * (n - 1))


def snap(target, calendar):
    idx = calendar.searchsorted(target, side="right") - 1
    return calendar[idx] if idx >= 0 else None


def rebalance_schedule(calendar, base):
    sched = [(base, base)]
    for yr in range(calendar[0].year, calendar[-1].year + 1):
        for m in (3, 6, 9, 12):
            eff = snap(nth_friday(yr, m, 3), calendar)
            ref = snap(nth_friday(yr, m, 2), calendar)
            if eff is None or ref is None or eff <= base or eff >= calendar[-1]:
                continue
            sched.append((ref, eff))
    return sorted(sched, key=lambda x: x[1])


# ---------------------------------------------------------------------------
# Index engine
# ---------------------------------------------------------------------------
def run_sleeve(members, pr, tr, sa, shares, spans, calendar, schedule,
               base, cap=CAP):
    """Return (levels DataFrame with PR and TR, ndarray of dates)."""
    def targets(ref, eff):
        elig = []
        for t in members:
            first, last = spans[t]
            seasoned = (calendar.get_loc(ref) - calendar.get_loc(first)
                        >= SEASONING_DAYS) if first in calendar else False
            if first <= ref and (ref == eff or seasoned) and last > eff:
                elig.append(t)
        if not elig:
            return None
        px = sa.loc[ref, elig]
        mcap = px * pd.Series({t: shares.get(t, np.nan) for t in elig})
        mcap = mcap.dropna()
        if mcap.empty:
            return None
        return cap_weights(mcap.astype(float), cap)

    eff_map = {e: r for r, e in schedule}
    w = targets(schedule[0][0], base)
    start = calendar.get_loc(base)
    dates, lv_pr, lv_tr = [base], [100.0], [100.0]
    for pos in range(start + 1, len(calendar)):
        day = calendar[pos]
        rp, rt = pr.loc[day, w.index], tr.loc[day, w.index]
        dead = rp.isna() | rt.isna()
        if dead.any():
            w = w[~dead]
            w = w / w.sum()
            rp, rt = rp[~dead], rt[~dead]
        lv_pr.append(lv_pr[-1] * (1 + float((w * rp).sum())))
        lv_tr.append(lv_tr[-1] * (1 + float((w * rt).sum())))
        dates.append(day)
        grown = w * (1 + rp)
        w = grown / grown.sum()
        if day in eff_map:
            nt = targets(eff_map[day], day)
            if nt is not None:
                w = nt
    return pd.DataFrame({"PR": lv_pr, "TR": lv_tr},
                        index=pd.DatetimeIndex(dates))


def compute_index(px, shares):
    pr_map, tr_map, sa_map, spans = {}, {}, {}, {}
    for t, _ in CONSTITUENTS:
        if t not in px:
            continue
        p, r, s = build_returns(px[t])
        pr_map[t], tr_map[t], sa_map[t] = p, r, s
        spans[t] = (s.index[0], s.index[-1])
    cal = pd.DatetimeIndex(sorted(set().union(
        *[s.index for s in sa_map.values()])))
    pr = pd.DataFrame({t: pr_map[t].reindex(cal) for t in pr_map})
    tr = pd.DataFrame({t: tr_map[t].reindex(cal) for t in tr_map})
    sa = pd.DataFrame({t: sa_map[t].reindex(cal) for t in sa_map})
    base = snap(pd.Timestamp(BASE_DATE), cal)
    sched = rebalance_schedule(cal, base)

    out = {}
    for sleeve in SLEEVES:
        members = [t for t, s in CONSTITUENTS if s == sleeve and t in pr_map]
        lv = run_sleeve(members, pr, tr, sa, shares, spans, cal, sched, base)
        out[f"CTIF-{sleeve[0]}-PR"] = lv["PR"]
        out[f"CTIF-{sleeve[0]}-TR"] = lv["TR"]
    levels = pd.DataFrame(out)

    # equal-thirds composite, reset quarterly, drift on sleeve PR between
    for ver in ("PR", "TR"):
        cols = [f"CTIF-{s[0]}-{ver}" for s in SLEEVES]
        pr_cols = [f"CTIF-{s[0]}-PR" for s in SLEEVES]
        rets = levels[cols].pct_change()
        pr_rets = levels[pr_cols].pct_change()
        eff_days = {e for _, e in sched}
        w = np.array([1 / 3, 1 / 3, 1 / 3])
        comp = [100.0]
        for i in range(1, len(levels)):
            r = rets.iloc[i].values
            comp.append(comp[-1] * (1 + float(np.nansum(w * r))))
            pr_r = pr_rets.iloc[i].values
            grown = w * (1 + np.nan_to_num(pr_r))
            w = grown / grown.sum()
            if levels.index[i] in eff_days:
                w = np.array([1 / 3, 1 / 3, 1 / 3])
        levels[f"CTIF-X-{ver}"] = comp
    return levels


# ---------------------------------------------------------------------------
# Benchmarks
# ---------------------------------------------------------------------------
def benchmark_tr(px, calendar):
    out = {}
    for b in BENCHMARKS:
        if b not in px:
            continue
        adj = px[b]["adj_close"].reindex(calendar).ffill()
        out[b] = 100.0 * adj / adj.dropna().iloc[0]
    return pd.DataFrame(out, index=calendar)


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------
def make_charts(levels, bench):
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter
    import logging
    logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
    from matplotlib import font_manager
    have = {f.name for f in font_manager.fontManager.ttflist}
    for cand in ("Arial", "Helvetica", "Liberation Sans", "DejaVu Sans"):
        if cand in have:
            plt.rcParams["font.family"] = cand
            break

    ctif_styles = {"CTIF-X": ("black", "-", 2.2), "CTIF-B": ("black", "--", 1.2),
                   "CTIF-C": ("0.4", ":", 1.4), "CTIF-R": ("black", "-.", 1.2)}
    names = {"CTIF-X": "Composite", "CTIF-B": "Builders",
             "CTIF-C": "Components", "CTIF-R": "Resources"}

    # 1) Full history, TR
    fig, ax = plt.subplots(figsize=(11, 6))
    for code, (c, ls, lw) in ctif_styles.items():
        s = levels[f"{code}-TR"].dropna()
        ax.plot(s.index, s.values, color=c, linestyle=ls, linewidth=lw,
                label=names[code])
    ax.set_title("CTIF index family, total return (base 100 = 2022-01-03)")
    ax.set_ylabel("Index level")
    ax.legend(frameon=False)
    ax.grid(axis="y", color="0.85", linewidth=0.6)
    fig.tight_layout()

    # 2) Trailing 1 year, CTIF composite vs benchmarks, rebased to 100
    fig2, ax2 = plt.subplots(figsize=(11, 6))
    end = levels.index[-1]
    start = end - pd.Timedelta(days=365)
    comp = levels["CTIF-X-TR"].loc[start:]
    comp = 100.0 * comp / comp.iloc[0]
    ax2.plot(comp.index, comp.values, color="black", linewidth=2.4,
             label="CTIF Composite")
    bench_styles = [("0.35", "--", 1.5), ("0.5", ":", 1.6),
                    ("0.2", "-.", 1.4), ("0.6", (0, (5, 1)), 1.5)]
    for (b, (c, ls, lw)) in zip(bench.columns, bench_styles):
        s = bench[b].loc[start:].dropna()
        if s.empty:
            continue
        s = 100.0 * s / s.iloc[0]
        ax2.plot(s.index, s.values, color=c, linestyle=ls, linewidth=lw,
                 label=b)
    ax2.set_title("Trailing 1 year, total return, rebased to 100")
    ax2.set_ylabel("Rebased level")
    ax2.legend(frameon=False)
    ax2.grid(axis="y", color="0.85", linewidth=0.6)
    fig2.tight_layout()

    # 3) Drawdown, TR
    fig3, ax3 = plt.subplots(figsize=(11, 4.5))
    for code, (c, ls, lw) in ctif_styles.items():
        s = levels[f"{code}-TR"].dropna()
        dd = s / s.cummax() - 1.0
        ax3.plot(dd.index, dd.values, color=c, linestyle=ls, linewidth=lw,
                 label=names[code])
    ax3.set_title("Drawdown from running peak, total return")
    ax3.yaxis.set_major_formatter(PercentFormatter(xmax=1.0))
    ax3.legend(frameon=False, loc="lower left")
    ax3.grid(axis="y", color="0.85", linewidth=0.6)
    fig3.tight_layout()

    plt.show()


def print_summary(levels, bench):
    end = levels.index[-1]
    start = end - pd.Timedelta(days=365)

    def ret(s):
        s = s.loc[start:].dropna()
        return s.iloc[-1] / s.iloc[0] - 1.0 if len(s) > 1 else np.nan

    print("\n" + "=" * 46)
    print(f"Trailing 1-year total return (as of {end.date()})")
    print("=" * 46)
    labels = {"CTIF-X-TR": "CTIF Composite", "CTIF-B-TR": "  Builders",
              "CTIF-C-TR": "  Components", "CTIF-R-TR": "  Resources"}
    for col, lab in labels.items():
        print(f"{lab:<18} {ret(levels[col]):+8.2%}")
    for b in bench.columns:
        print(f"{b:<18} {ret(bench[b]):+8.2%}")
    print("=" * 46)
    print("Reminder: back-cast, hindsight-selected universe. The gap vs")
    print("SPY is mostly selection. Compare Components to SMH and")
    print("Resources to XLU for a fairer read.\n")


# ---------------------------------------------------------------------------
def main():
    if API_KEY == "PUT_YOUR_ALPHAVANTAGE_KEY_HERE":
        print("Set API_KEY at the top of the file first.")
        sys.exit(1)
    print("Loading data (first run downloads and caches; later runs are "
          "instant)...")
    px, shares = load_all()
    missing = [t for t, _ in CONSTITUENTS if t not in px]
    if missing:
        print(f"\nWARNING: no data for {missing}. They will be skipped. "
              "Check for ticker changes or delistings.")
    print("\nComputing index...")
    levels = compute_index(px, shares)
    bench = benchmark_tr(px, levels.index)
    print_summary(levels, bench)
    print("Rendering charts...")
    make_charts(levels, bench)
    levels.to_csv("ctif_levels.csv")
    print("Saved ctif_levels.csv")


if __name__ == "__main__":
    main()
