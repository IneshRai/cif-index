"""
ctif_run.py  -  Castellan Infrastructure Family, one-file runner
============================================================================

Drop this into a PyCharm project, set your Alpaca API keys below (or via
environment variables), and run. It fetches real prices for the 53
constituents plus benchmarks from Alpaca, caches them locally (so re-runs are
instant and do not burn API calls), computes the index per the CIF
methodology, and shows matplotlib charts including a 1-year comparison against
SPY, QQQ, SMH, and XLU.

Data source: Alpaca Market Data API.
  prices  GET /v2/stocks/{symbol}/bars  (raw close + adjustment=all adj close)
  events  GET /v1/corporate-actions     (dividends and splits)
  shares  shares.csv (maintained input; Alpaca has no fundamentals endpoint)

The free Alpaca plan is IEX-only (~2.5% of consolidated volume); set FEED to
"sip" if you have a paid subscription for full market coverage.

This single file mirrors the validated multi-file engine for convenience.
The multi-file repo (with the validation suite) remains the reference
implementation; this exists so you can see charts in one run.

Requirements:  pip install pandas numpy requests matplotlib
Python 3.9+.

IMPORTANT: index history is a BACK-CAST. The universe is selected with
hindsight (2026's known buildout winners), so it will tend to look like it
beat the market by a wide margin. That gap is mostly selection, not skill.
The sleeve-versus-sector-benchmark comparisons (Components vs SMH, Resources
vs XLU) are more informative than the headline CIF-vs-SPY line.
"""

import os

# ===========================================================================
# CONFIG  -  edit these
# ===========================================================================
# Alpaca market-data credentials. Prefer environment variables; the literals
# below are a fallback for local runs. NEVER commit real keys.
ALPACA_API_KEY_ID = os.environ.get("ALPACA_API_KEY_ID", "PUT_YOUR_ALPACA_KEY_ID_HERE")
ALPACA_API_SECRET_KEY = os.environ.get("ALPACA_API_SECRET_KEY", "PUT_YOUR_ALPACA_SECRET_HERE")

CACHE_DIR = "ctif_cache"          # local folder for downloaded CSVs
SHARES_FILE = "shares.csv"        # maintained shares outstanding (see fetch_shares.py)
FEED = os.environ.get("CIF_FEED") or "iex"   # "iex" (free) or "sip" (paid)
HISTORY_START = "2015-01-01"      # bar history start; CA data begins Apr 2020

BASE_DATE = "2022-01-03"
CAP = 0.10                        # 10 percent single-name cap
FORCE_REFRESH = False             # True to re-download even if cached
REQUEST_SLEEP = 0.3               # seconds between API calls

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
import sys
import time

import numpy as np
import pandas as pd
import requests

DATA_URL = "https://data.alpaca.markets"
CA_TYPES = "cash_dividend,forward_split,reverse_split"
SLEEVES = ["Builders", "Components", "Resources"]
SEASONING_DAYS = 63


# ---------------------------------------------------------------------------
# Data fetch and cache (Alpaca)
# ---------------------------------------------------------------------------
def _headers():
    return {"APCA-API-KEY-ID": ALPACA_API_KEY_ID,
            "APCA-API-SECRET-KEY": ALPACA_API_SECRET_KEY,
            "accept": "application/json"}


def _get(session, url, params, retries=3, backoff=5.0):
    last = None
    for attempt in range(1, retries + 1):
        try:
            r = session.get(url, params=params, headers=_headers(), timeout=60)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last = exc
            time.sleep(backoff * attempt)
    raise RuntimeError(f"request failed after {retries}: {last}")


def _bars(session, ticker, adjustment):
    """All daily bars for one ticker at a given adjustment, paginated."""
    url = f"{DATA_URL}/v2/stocks/{ticker}/bars"
    out, token = [], None
    while True:
        params = {"timeframe": "1Day", "start": HISTORY_START,
                  "adjustment": adjustment, "feed": FEED, "limit": 10000}
        if token:
            params["page_token"] = token
        js = _get(session, url, params)
        out.extend(js.get("bars") or [])
        token = js.get("next_page_token")
        if not token:
            break
    return out


_CA_CACHE = {"divs": None, "splits": None}


def _load_corporate_actions(session, symbols):
    """Fetch dividends and splits for the whole universe once, then cache."""
    if _CA_CACHE["divs"] is not None:
        return _CA_CACHE["divs"], _CA_CACHE["splits"]
    url = f"{DATA_URL}/v1/corporate-actions"
    end = pd.Timestamp.today().strftime("%Y-%m-%d")
    divs, splits, token = {}, {}, None
    while True:
        params = {"symbols": ",".join(symbols), "types": CA_TYPES,
                  "start": HISTORY_START, "end": end, "limit": 1000}
        if token:
            params["page_token"] = token
        js = _get(session, url, params)
        ca = js.get("corporate_actions", {}) or {}
        for d in ca.get("cash_dividends", []) or []:
            sym, ex = d.get("symbol"), d.get("ex_date")
            rate = d.get("rate", d.get("cash"))
            if sym and ex and rate is not None:
                divs[(sym, ex)] = divs.get((sym, ex), 0.0) + float(rate)
        for k in ("forward_splits", "reverse_splits"):
            for s in ca.get(k, []) or []:
                sym, ex = s.get("symbol"), s.get("ex_date")
                nr, orr = s.get("new_rate"), s.get("old_rate")
                if sym and ex and nr and orr:
                    splits[(sym, ex)] = float(nr) / float(orr)
        token = js.get("next_page_token")
        if not token:
            break
    _CA_CACHE["divs"], _CA_CACHE["splits"] = divs, splits
    return divs, splits


def fetch_prices(ticker, session=None, divs=None, splits=None):
    """Return a DataFrame indexed by date with close, adj_close, div, split."""
    own = session is None
    session = session or requests.Session()
    if divs is None or splits is None:
        divs, splits = _load_corporate_actions(session, [ticker])
    raw = _bars(session, ticker, "raw")
    adj = _bars(session, ticker, "all")
    if own:
        pass
    adj_close = {b["t"][:10]: float(b["c"]) for b in adj}
    rows = []
    for b in raw:
        day = b["t"][:10]
        c = float(b["c"])
        rows.append({
            "date": day,
            "close": c,
            "adj_close": adj_close.get(day, c),
            "div": float(divs.get((ticker, day), 0.0)),
            "split": float(splits.get((ticker, day), 1.0)),
        })
    if not rows:
        raise RuntimeError(f"{ticker}: no bars (check symbol/feed/dates)")
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").set_index("date")


def load_shares_file():
    """Read maintained shares.csv into {ticker: shares_outstanding}."""
    if not os.path.exists(SHARES_FILE):
        print(f"WARNING: {SHARES_FILE} not found. Cap weights need shares; "
              "run: python src/fetch_shares.py --constituents constituents.csv "
              f"--output {SHARES_FILE} --scaffold  then fill it from Bloomberg.")
        return {}
    sh = pd.read_csv(SHARES_FILE)
    sh["ticker"] = sh["ticker"].astype(str).str.strip()
    vals = pd.to_numeric(sh["shares_outstanding"], errors="coerce")
    out = {t: float(v) for t, v in zip(sh["ticker"], vals)
           if pd.notna(v) and v > 0}
    return out


def get_cached(ticker, fetch_fn):
    """Price cache to CACHE_DIR, fetch on miss."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{ticker}_px.csv")
    if os.path.exists(path) and not FORCE_REFRESH:
        return pd.read_csv(path, parse_dates=["date"]).set_index("date")
    result = fetch_fn(ticker)
    time.sleep(REQUEST_SLEEP)
    result.to_csv(path)
    return result


def load_all():
    """Fetch/cache every ticker and benchmark. Returns price dict, shares."""
    px = {}
    all_tickers = [t for t, _ in CONSTITUENTS] + BENCHMARKS
    session = requests.Session()
    # Pre-load corporate actions for the whole universe in one sweep (unless
    # everything is cached, in which case we lazily skip the network entirely).
    need_fetch = FORCE_REFRESH or any(
        not os.path.exists(os.path.join(CACHE_DIR, f"{t}_px.csv"))
        for t in all_tickers)
    if need_fetch:
        try:
            _load_corporate_actions(session, all_tickers)
        except Exception as e:
            print(f"WARNING: corporate-actions fetch failed ({e}); "
                  "dividends/splits will default to none for freshly fetched "
                  "tickers.")
    divs = _CA_CACHE["divs"] or {}
    splits = _CA_CACHE["splits"] or {}

    for i, t in enumerate(all_tickers, 1):
        print(f"[{i}/{len(all_tickers)}] {t} ...", end=" ", flush=True)
        try:
            px[t] = get_cached(
                t, lambda tk: fetch_prices(tk, session, divs, splits))
            print(f"{len(px[t])} rows")
        except Exception as e:
            print(f"FAILED: {e}")
    shares = load_shares_file()
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
    wt_rows = {base: w.copy()}
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
        wt_rows[day] = w.copy()
    levels = pd.DataFrame({"PR": lv_pr, "TR": lv_tr},
                          index=pd.DatetimeIndex(dates))
    weights = pd.DataFrame(wt_rows).T.sort_index()
    weights.index.name = "date"
    return levels, weights


def compute_index(px, shares, return_weights=False):
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

    out, weights = {}, {}
    for sleeve in SLEEVES:
        members = [t for t, s in CONSTITUENTS if s == sleeve and t in pr_map]
        lv, wt = run_sleeve(members, pr, tr, sa, shares, spans, cal, sched,
                            base)
        out[f"CIF-{sleeve[0]}-PR"] = lv["PR"]
        out[f"CIF-{sleeve[0]}-TR"] = lv["TR"]
        weights[sleeve] = wt
    levels = pd.DataFrame(out)

    # equal-thirds composite, reset quarterly, drift on sleeve PR between
    for ver in ("PR", "TR"):
        cols = [f"CIF-{s[0]}-{ver}" for s in SLEEVES]
        pr_cols = [f"CIF-{s[0]}-PR" for s in SLEEVES]
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
        levels[f"CIF-X-{ver}"] = comp
    if return_weights:
        return levels, weights
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
def _windows(levels):
    """Return {horizon: (start_date, end_date)} for the return columns."""
    import pandas as pd
    end = levels.index[-1]
    s = levels.iloc[:, 0].dropna()
    def prev_close(cut):
        w = levels.index[levels.index <= cut]
        return w[-1] if len(w) else levels.index[0]
    wk = levels.index[-6] if len(levels) > 6 else levels.index[0]
    return {
        "1W": (wk, end),
        "YTD": (prev_close(pd.Timestamp(end.year, 1, 1) -
                           pd.Timedelta(days=1)), end),
        "1Y": (prev_close(end - pd.Timedelta(days=365)), end),
        "Full": (levels.index[0], end),
    }


def _ret(s, start):
    s = s.dropna()
    w = s.loc[:start]
    base = w.iloc[-1] if len(w) else s.iloc[0]
    return s.iloc[-1] / base - 1.0


def _ann_vol(s):
    r = s.dropna().pct_change().dropna()
    return float(r.std(ddof=1)) * (252 ** 0.5) if len(r) > 2 else float("nan")


def _max_dd(s, since=None):
    """Return (mdd, peak_date, trough_date, recovery_date_or_None)."""
    s = s.dropna()
    if since is not None:
        s = s.loc[since:]
    if len(s) < 2:
        return float("nan"), None, None, None
    dd = s / s.cummax() - 1.0
    trough = dd.idxmin()
    mdd = float(dd.min())
    peak = s.loc[:trough].idxmax()
    peak_val = s.loc[peak]
    after = s.loc[trough:]
    rec = after[after >= peak_val]
    recovery = rec.index[0] if len(rec) else None
    return mdd, peak, trough, recovery


# Distinct colors for on-screen readability.
COLORS = {
    "CIF Composite": "#111111", "Composite": "#111111",
    "Builders": "#1f77b4", "Components": "#d62728", "Resources": "#2ca02c",
    "SPY": "#7f7f7f", "QQQ": "#9467bd", "SMH": "#ff7f0e", "XLU": "#17becf",
}

SERIES_ROWS = [("CIF-X", "CIF Composite"), ("CIF-B", "Builders"),
               ("CIF-C", "Components"), ("CIF-R", "Resources")]


def make_charts(levels, bench):
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter
    import pandas as pd
    import logging
    logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
    from matplotlib import font_manager
    have = {f.name for f in font_manager.fontManager.ttflist}
    for cand in ("Arial", "Helvetica", "Liberation Sans", "DejaVu Sans"):
        if cand in have:
            plt.rcParams["font.family"] = cand
            break

    ctif = [("CIF-X", "Composite", 2.6), ("CIF-B", "Builders", 1.6),
            ("CIF-C", "Components", 1.6), ("CIF-R", "Resources", 1.6)]

    # 1) Full history, TR
    fig1, ax = plt.subplots(figsize=(11, 6))
    for code, name, lw in ctif:
        s = levels[f"{code}-TR"].dropna()
        ax.plot(s.index, s.values, color=COLORS[name], linewidth=lw,
                label=name)
    ax.set_title("CIF index family, total return "
                 f"({levels.index[0].date()} = 100, "
                 f"through {levels.index[-1].date()})")
    ax.set_ylabel("Index level")
    ax.legend(frameon=False)
    ax.grid(True, color="0.9", linewidth=0.6)
    fig1.tight_layout()

    end = levels.index[-1]
    start = end - pd.Timedelta(days=365)

    def rebased(s):
        s = s.loc[start:].dropna()
        return 100.0 * s / s.iloc[0] if len(s) else s

    # 2) Trailing 1 year: composite + benchmarks
    fig2, ax2 = plt.subplots(figsize=(11, 6))
    c = rebased(levels["CIF-X-TR"])
    ax2.plot(c.index, c.values, color=COLORS["Composite"], linewidth=2.8,
             label="CIF Composite")
    for b in bench.columns:
        s = rebased(bench[b])
        if len(s):
            ax2.plot(s.index, s.values, color=COLORS.get(b), linewidth=1.8,
                     linestyle="--", label=b)
    ax2.set_title(f"Trailing 1 year, total return, rebased to 100 "
                  f"({start.date()} to {end.date()})")
    ax2.set_ylabel("Rebased level")
    ax2.legend(frameon=False)
    ax2.grid(True, color="0.9", linewidth=0.6)
    fig2.tight_layout()

    # 3) Trailing 1 year: composite + sleeves vs sector benchmarks
    fig3, ax3 = plt.subplots(figsize=(11, 6))
    cx = rebased(levels["CIF-X-TR"])
    ax3.plot(cx.index, cx.values, color=COLORS["Composite"], linewidth=2.8,
             label="CIF Composite")
    for code, name, _ in ctif[1:]:
        s = rebased(levels[f"{code}-TR"])
        ax3.plot(s.index, s.values, color=COLORS[name], linewidth=2.0,
                 label=name)
    for b in ["SMH", "XLU", "SPY"]:
        if b in bench.columns:
            s = rebased(bench[b])
            ax3.plot(s.index, s.values, color=COLORS.get(b), linewidth=1.5,
                     linestyle="--", label=b)
    ax3.set_title(f"Trailing 1 year, composite and sleeves vs benchmarks "
                  f"({start.date()} to {end.date()})")
    ax3.set_ylabel("Rebased level")
    ax3.legend(frameon=False, ncol=2)
    ax3.grid(True, color="0.9", linewidth=0.6)
    fig3.tight_layout()

    # 4) Drawdown
    fig4, ax4 = plt.subplots(figsize=(11, 4.8))
    for code, name, lw in ctif:
        s = levels[f"{code}-TR"].dropna()
        dd = s / s.cummax() - 1.0
        ax4.plot(dd.index, dd.values, color=COLORS[name], linewidth=lw,
                 label=name)
    ax4.set_title("Drawdown from running peak, total return (full history)")
    ax4.yaxis.set_major_formatter(PercentFormatter(xmax=1.0))
    ax4.legend(frameon=False, loc="lower left")
    ax4.grid(True, color="0.9", linewidth=0.6)
    fig4.tight_layout()

    # 5) Two tables: returns (with date windows) and risk (with dd dates)
    make_table_figures(levels, bench)

    plt.show()


def _all_series(levels, bench):
    rows = [(name, levels[f"{code}-TR"]) for code, name in SERIES_ROWS]
    rows += [(b, bench[b]) for b in bench.columns]
    return rows

def make_table_figures(levels, bench):
    """Single clean trailing-1-year table: return, vol, drawdown with dates.
    A separate since-inception table is also drawn, clearly labeled."""
    import matplotlib.pyplot as plt
    win = _windows(levels)
    rows = _all_series(levels, bench)
    y_start, end = win["1Y"]

    # ---- Main table: everything trailing 1 year ----
    cols = ["1Y return", "1Y ann vol", "1Y max DD", "peak to trough (1Y)"]
    cell, colr = [], []
    for name, s in rows:
        r1 = _ret(s, y_start)
        v1 = _ann_vol(s.loc[y_start:])
        mdd, pk, tr, _ = _max_dd(s, since=y_start)
        window = f"{pk.date()} to {tr.date()}" if pk is not None else "n/a"
        cell.append([f"{r1:+.1%}", f"{v1:.1%}", f"{mdd:.1%}", window])
        colr.append(["#e2f0e2" if r1 >= 0 else "#fde0e0", "white",
                     "#fde0e0", "white"])
    fig, ax = plt.subplots(figsize=(11, 0.7 + 0.42 * len(rows)))
    ax.axis("off")
    ax.set_title("CIF trailing 1-year performance, total return", pad=20,
                 fontsize=14)
    ax.text(0.5, 1.015,
            f"All figures cover {y_start.date()} to {end.date()} "
            "(one consistent window).",
            transform=ax.transAxes, ha="center", va="bottom",
            fontsize=9, color="0.35")
    t = ax.table(cellText=cell, rowLabels=[n for n, _ in rows],
                 colLabels=cols, cellColours=colr, cellLoc="center",
                 loc="center")
    t.auto_set_font_size(False); t.set_fontsize(10.5); t.scale(1, 1.55)
    for (ri, ci), c in t.get_celld().items():
        if ri == 0 or ci == -1:
            c.set_text_props(weight="bold")
        if ri == 1:
            c.set_text_props(weight="bold")
    fig.tight_layout()

    # ---- Separate since-inception table (clearly labeled, not mixed in) ----
    icols = ["Return", "Ann vol", "Max DD", "peak to trough"]
    icell = []
    for name, s in rows:
        rf = _ret(s, win["Full"][0])
        vf = _ann_vol(s)
        mdd, pk, tr, rec = _max_dd(s)
        window = f"{pk.date()} to {tr.date()}" if pk is not None else "n/a"
        icell.append([f"{rf:+.1%}", f"{vf:.1%}", f"{mdd:.1%}", window])
    figi, axi = plt.subplots(figsize=(11, 0.7 + 0.42 * len(rows)))
    axi.axis("off")
    axi.set_title("CIF since inception (context only, different window)",
                  pad=20, fontsize=14)
    axi.text(0.5, 1.015,
             f"All figures cover {win['Full'][0].date()} to {end.date()}. "
             "Drawdowns here include the 2022 bear market.",
             transform=axi.transAxes, ha="center", va="bottom",
             fontsize=9, color="0.35")
    ti = axi.table(cellText=icell, rowLabels=[n for n, _ in rows],
                   colLabels=icols, cellLoc="center", loc="center")
    ti.auto_set_font_size(False); ti.set_fontsize(10.5); ti.scale(1, 1.55)
    for (ri, ci), c in ti.get_celld().items():
        if ri == 0 or ci == -1:
            c.set_text_props(weight="bold")
        if ri == 1:
            c.set_text_props(weight="bold")
    figi.tight_layout()


def print_summary(levels, bench):
    win = _windows(levels)
    rows = _all_series(levels, bench)
    y_start, end = win["1Y"]
    print("\n" + "=" * 84)
    print("CIF trailing 1-year performance, total return")
    print(f"  window: {y_start.date()} to {end.date()} (one consistent "
          "window for every column)")
    print("=" * 84)
    print(f"{'Series':<16}{'1Y ret':>9}{'1Y vol':>9}{'1Y maxDD':>10}   "
          f"{'peak to trough (1Y)':<26}")
    print("-" * 84)
    for name, s in rows:
        r1 = _ret(s, y_start)
        v1 = _ann_vol(s.loc[y_start:])
        mdd, pk, tr, _ = _max_dd(s, since=y_start)
        window = f"{pk.date()} to {tr.date()}" if pk is not None else "n/a"
        print(f"{name:<16}{r1:>+9.1%}{v1:>9.1%}{mdd:>+10.1%}   "
              f"{window:<26}")
    print("=" * 84)
    print("Back-cast, hindsight-selected universe. The gap vs SPY is mostly")
    print("selection; the fair read is sleeve vs sector benchmark (Components")
    print("vs SMH, Resources vs XLU). A separate since-2022 table is shown")
    print("for context; its drawdowns include the 2022 bear market.\n")

# ---------------------------------------------------------------------------
def main():
    if (ALPACA_API_KEY_ID.startswith("PUT_YOUR")
            or ALPACA_API_SECRET_KEY.startswith("PUT_YOUR")):
        print("Set ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY (env vars or "
              "the CONFIG block at the top of the file) first.")
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