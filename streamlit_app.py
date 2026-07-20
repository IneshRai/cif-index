"""
streamlit_app.py  -  CIF monitoring dashboard
=============================================

An interactive dashboard for the Castellan Infrastructure Family (CIF),
built as a MONITORING tool for early signs of a shift in the AI buildout,
not as a strategy. It reuses the exact index math in ctif_run.py so every
number matches the memo and the chart pack.

Run:
    pip install -r requirements.txt
    streamlit run streamlit_app.py

Data comes from the same local cache ctif_run.py builds, so if you have run
ctif_run.py once, this loads instantly. Set your Alpha Vantage key in
ctif_run.py (API_KEY) or the ALPHAVANTAGE_API_KEY environment variable.

Reminder shown throughout: the back-cast is hindsight-selected. Absolute
levels overstate what was achievable. The value here is the signals
(breadth, momentum, dispersion, distance from highs) and the sleeve-vs-
benchmark reads, not the headline return.
"""

import json
import os

import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go

import ctif_run as cif

st.set_page_config(page_title="CIF Monitor", layout="wide",
                   initial_sidebar_state="expanded")

SNAP = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "data_snapshot")

SLEEVE_CODE = {"Composite": "CIF-X", "Builders": "CIF-B",
               "Components": "CIF-C", "Resources": "CIF-R"}
SLEEVE_BENCH = {"Builders": "SPY", "Components": "SMH", "Resources": "XLU"}
COLORS = {"Composite": "#111111", "Builders": "#1f77b4",
          "Components": "#d62728", "Resources": "#2ca02c",
          "SPY": "#7f7f7f", "QQQ": "#9467bd", "SMH": "#ff7f0e",
          "XLU": "#17becf"}
WINDOWS = {"1M": 21, "3M": 63, "6M": 126, "1Y": 252, "Max": None}


# ---------------------------------------------------------------------------
# Data loading (cached)
# ---------------------------------------------------------------------------
def _have_snapshot():
    return os.path.exists(os.path.join(SNAP, "index_levels.csv"))


@st.cache_data(show_spinner="Loading snapshot...")
def load_from_snapshot():
    levels = pd.read_csv(os.path.join(SNAP, "index_levels.csv"),
                         index_col=0, parse_dates=True)
    bench = pd.read_csv(os.path.join(SNAP, "bench.csv"),
                        index_col=0, parse_dates=True)
    adj = pd.read_csv(os.path.join(SNAP, "adj.csv"),
                      index_col=0, parse_dates=True)
    weights = {}
    for sleeve in cif.SLEEVES:
        weights[sleeve] = pd.read_csv(
            os.path.join(SNAP, f"weights_{sleeve.lower()}.csv"),
            index_col=0, parse_dates=True)
    with open(os.path.join(SNAP, "meta.json")) as f:
        meta = json.load(f)
    return levels, weights, bench, adj, meta


@st.cache_data(show_spinner="Loading prices and computing index (live)...")
def load_live():
    px, shares = cif.load_all()
    levels, weights = cif.compute_index(px, shares, return_weights=True)
    bench = cif.benchmark_tr(px, levels.index)
    adj = pd.DataFrame({t: px[t]["adj_close"].reindex(levels.index).ffill()
                        for t, _ in cif.CONSTITUENTS if t in px})
    meta = {"asof": str(levels.index[-1].date()),
            "sleeve_of": {t: s for t, s in cif.CONSTITUENTS if t in px}}
    return levels, weights, bench, adj, meta


def key_ready():
    try:
        if "ALPHAVANTAGE_API_KEY" in st.secrets:
            cif.API_KEY = st.secrets["ALPHAVANTAGE_API_KEY"]
    except Exception:
        pass
    env = os.environ.get("ALPHAVANTAGE_API_KEY")
    if env:
        cif.API_KEY = env
    return cif.API_KEY and cif.API_KEY != "PUT_YOUR_ALPHAVANTAGE_KEY_HERE"


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def ret_over(s, days):
    s = s.dropna()
    if len(s) <= days:
        return np.nan
    return s.iloc[-1] / s.iloc[-1 - days] - 1.0


def window_slice(idx, code):
    n = WINDOWS[code]
    if n is None:
        return idx[0]
    return idx[max(0, len(idx) - 1 - n)]


def ann_vol(s):
    r = s.dropna().pct_change().dropna()
    return float(r.std(ddof=1)) * np.sqrt(252) if len(r) > 2 else np.nan


def max_dd(s, since=None):
    s = s.dropna()
    if since is not None:
        s = s.loc[since:]
    if len(s) < 2:
        return np.nan, None, None
    dd = s / s.cummax() - 1.0
    trough = dd.idxmin()
    peak = s.loc[:trough].idxmax()
    return float(dd.min()), peak, trough


def pct(x, signed=True):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "n/a"
    return f"{x:+.1%}" if signed else f"{x:.1%}"


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
def main():
    st.title("CIF Monitor")
    st.caption("Castellan Infrastructure Family. A monitoring instrument for "
               "the AI buildout supply chain.")

    use_snapshot = _have_snapshot()
    if not use_snapshot and not key_ready():
        st.warning("No data snapshot found and no Alpha Vantage API key set. "
                   "Either run refresh_data.py to build a snapshot, or set "
                   "API_KEY in ctif_run.py / the ALPHAVANTAGE_API_KEY "
                   "environment variable for a live pull.")
        st.stop()

    try:
        if use_snapshot:
            levels, weights, bench, adj, meta = load_from_snapshot()
        else:
            levels, weights, bench, adj, meta = load_live()
    except Exception as exc:
        st.error(f"Failed to load or compute: {exc}")
        st.stop()

    idx = levels.index
    asof = idx[-1]

    # Sidebar controls
    st.sidebar.header("Controls")
    win = st.sidebar.radio("Window", list(WINDOWS.keys()), index=3,
                           horizontal=True)
    ver = st.sidebar.radio("Version", ["TR", "PR"], index=0, horizontal=True,
                           help="Total return (with dividends) or price only")
    bench_pick = st.sidebar.multiselect(
        "Benchmarks on chart", list(bench.columns),
        default=[b for b in ["SPY", "SMH", "XLU"] if b in bench.columns])
    if st.sidebar.button("Refresh data (clear cache)"):
        st.cache_data.clear()
        st.rerun()
    src = "daily snapshot" if use_snapshot else "live pull"
    st.sidebar.caption(f"Source: {src}. Data through {asof.date()}. "
                       "Snapshot refreshes via the scheduled job; use Refresh "
                       "to reload it.")

    start = window_slice(idx, win)

    tab_overview, tab_drill, tab_signals = st.tabs(
        ["Overview", "Sleeve drilldown", "Monitoring signals"])

    # ===================== OVERVIEW =====================
    with tab_overview:
        cols = st.columns(4)
        for c, name in zip(cols, ["Composite", "Builders", "Components",
                                  "Resources"]):
            s = levels[f"{SLEEVE_CODE[name]}-{ver}"]
            d1 = ret_over(s, 1)
            wr = s.iloc[-1] / s.loc[:start].iloc[-1] - 1.0
            c.metric(name, pct(d1) + " 1D",
                     f"{pct(wr)} ({win})")

        st.subheader(f"Rebased performance ({start.date()} to {asof.date()})")
        fig = go.Figure()
        for name in ["Composite", "Builders", "Components", "Resources"]:
            s = levels[f"{SLEEVE_CODE[name]}-{ver}"].loc[start:].dropna()
            s = 100.0 * s / s.iloc[0]
            fig.add_trace(go.Scatter(
                x=s.index, y=s.values, name=name,
                line=dict(color=COLORS[name],
                          width=3 if name == "Composite" else 1.8)))
        for b in bench_pick:
            s = bench[b].loc[start:].dropna()
            if len(s):
                s = 100.0 * s / s.iloc[0]
                fig.add_trace(go.Scatter(
                    x=s.index, y=s.values, name=b,
                    line=dict(color=COLORS.get(b), width=1.4, dash="dash")))
        fig.update_layout(height=460, hovermode="x unified",
                          margin=dict(l=10, r=10, t=10, b=10),
                          legend=dict(orientation="h", y=1.02),
                          yaxis_title="Rebased to 100")
        st.plotly_chart(fig, use_container_width=True)

        st.subheader("Performance table (one consistent window)")
        st.caption(f"All columns cover {start.date()} to {asof.date()}.")
        rows = []
        series = [("CIF Composite", levels[f"CIF-X-{ver}"])]
        series += [(n, levels[f"{SLEEVE_CODE[n]}-{ver}"])
                   for n in ["Builders", "Components", "Resources"]]
        series += [(b, bench[b]) for b in bench.columns]
        for name, s in series:
            r = s.iloc[-1] / s.loc[:start].iloc[-1] - 1.0
            v = ann_vol(s.loc[start:])
            dd, pk, tr = max_dd(s, since=start)
            rows.append({
                "Series": name, f"{win} return": pct(r),
                f"{win} ann vol": pct(v, signed=False),
                f"{win} max DD": pct(dd),
                "peak to trough": (f"{pk.date()} to {tr.date()}"
                                   if pk is not None else "n/a")})
        st.dataframe(pd.DataFrame(rows), use_container_width=True,
                     hide_index=True)

    # ===================== SLEEVE DRILLDOWN =====================
    with tab_drill:
        sleeve = st.selectbox("Sleeve", ["Builders", "Components",
                                         "Resources"])
        w = weights[sleeve].iloc[-1].dropna().sort_values(ascending=False)
        members = list(w.index)

        # per-name stats
        recs = []
        for t in members:
            s = adj[t].dropna()
            recs.append({
                "Ticker": t, "Weight": w[t],
                "1D": ret_over(s, 1), "1W": ret_over(s, 5),
                f"{win}": s.iloc[-1] / s.loc[:start].iloc[-1] - 1.0
                if len(s.loc[:start]) else np.nan,
                "from 52w high": s.iloc[-1] / s.iloc[-252:].max() - 1.0
                if len(s) >= 2 else np.nan,
            })
        df = pd.DataFrame(recs)

        code = SLEEVE_CODE[sleeve]
        sl = levels[f"{code}-{ver}"]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric(f"{sleeve} 1D", pct(ret_over(sl, 1)))
        c2.metric(f"{sleeve} {win}",
                  pct(sl.iloc[-1] / sl.loc[:start].iloc[-1] - 1.0))
        adv = int((df["1D"] > 0).sum())
        c3.metric("Advancers today", f"{adv} / {len(df)}")
        bmk = SLEEVE_BENCH[sleeve]
        if bmk in bench.columns:
            b = bench[bmk]
            spread = ((sl.iloc[-1] / sl.loc[:start].iloc[-1])
                      - (b.iloc[-1] / b.loc[:start].iloc[-1]))
            c4.metric(f"vs {bmk} ({win})", pct(spread))

        st.subheader("Current weights")
        wfig = go.Figure(go.Bar(
            x=w.values * 100, y=w.index, orientation="h",
            marker_color=COLORS[sleeve]))
        wfig.update_layout(height=max(300, 22 * len(w)),
                           margin=dict(l=10, r=10, t=10, b=10),
                           xaxis_title="Weight (%)",
                           yaxis=dict(autorange="reversed"))
        st.plotly_chart(wfig, use_container_width=True)

        st.subheader("Constituents")
        show = df.copy()
        for c in ["Weight"]:
            show[c] = (show[c] * 100).map(lambda x: f"{x:.2f}%")
        for c in ["1D", "1W", win, "from 52w high"]:
            show[c] = show[c].map(lambda x: pct(x))
        st.dataframe(show, use_container_width=True, hide_index=True)

    # ===================== MONITORING SIGNALS =====================
    with tab_signals:
        st.caption("Early-warning reads. These are designed to move before "
                   "headline returns do: breadth thinning, momentum rolling "
                   "over, dispersion widening, names falling off their highs.")

        # Breadth: % of names above 50d and 200d moving average, per sleeve
        st.subheader("Breadth: share of names above moving averages")
        breadth = []
        for sleeve in ["Builders", "Components", "Resources"]:
            members = list(weights[sleeve].iloc[-1].dropna().index)
            sub = adj[members]
            ma50 = sub.rolling(50).mean().iloc[-1]
            ma200 = sub.rolling(200).mean().iloc[-1]
            last = sub.iloc[-1]
            above50 = float((last > ma50).mean())
            above200 = float((last > ma200).mean())
            breadth.append({"Sleeve": sleeve,
                            "above 50d MA": pct(above50, signed=False),
                            "above 200d MA": pct(above200, signed=False),
                            "median from 52w high":
                            pct(float((last / sub.iloc[-252:].max() - 1.0)
                                      .median()))})
        st.dataframe(pd.DataFrame(breadth), use_container_width=True,
                     hide_index=True)
        st.caption("When the share above the 200d moving average falls while "
                   "the index level is still high, participation is "
                   "narrowing, a classic pre-turn tell.")

        # Advancers minus decliners today, per sleeve
        st.subheader("Today: advancers minus decliners")
        adcols = st.columns(3)
        for c, sleeve in zip(adcols, ["Builders", "Components",
                                      "Resources"]):
            members = list(weights[sleeve].iloc[-1].dropna().index)
            d1 = adj[members].pct_change().iloc[-1]
            adv = int((d1 > 0).sum())
            dec = int((d1 < 0).sum())
            c.metric(sleeve, f"{adv} up / {dec} down", f"net {adv - dec:+d}")

        # Rolling 63d dispersion (cross-sectional stdev of returns)
        st.subheader("Dispersion within each sleeve (63d)")
        st.caption("Rising dispersion means names are decoupling, often a "
                   "sign the theme is fragmenting rather than moving together.")
        dfig = go.Figure()
        for sleeve in ["Builders", "Components", "Resources"]:
            members = list(weights[sleeve].iloc[-1].dropna().index)
            daily = adj[members].pct_change()
            disp = daily.std(axis=1).rolling(21).mean().loc[start:] * 100
            dfig.add_trace(go.Scatter(x=disp.index, y=disp.values,
                                      name=sleeve,
                                      line=dict(color=COLORS[sleeve])))
        dfig.update_layout(height=340, hovermode="x unified",
                           margin=dict(l=10, r=10, t=10, b=10),
                           legend=dict(orientation="h", y=1.02),
                           yaxis_title="Cross-sectional stdev of daily "
                                       "returns (%)")
        st.plotly_chart(dfig, use_container_width=True)

        # Sleeve momentum: 63d return, a simple regime gauge
        st.subheader("Sleeve momentum (63-day return)")
        mfig = go.Figure()
        for name in ["Composite", "Builders", "Components", "Resources"]:
            s = levels[f"{SLEEVE_CODE[name]}-{ver}"]
            mom = (s / s.shift(63) - 1.0).loc[start:] * 100
            mfig.add_trace(go.Scatter(x=mom.index, y=mom.values, name=name,
                                      line=dict(color=COLORS[name])))
        mfig.add_hline(y=0, line_dash="dot", line_color="#888")
        mfig.update_layout(height=340, hovermode="x unified",
                           margin=dict(l=10, r=10, t=10, b=10),
                           legend=dict(orientation="h", y=1.02),
                           yaxis_title="63-day return (%)")
        st.plotly_chart(mfig, use_container_width=True)
        st.caption("Momentum crossing below zero, especially in Builders or "
                   "Resources (the power and permitting legs), is the kind of "
                   "signal a policy shock like a data center moratorium would "
                   "produce first.")


if __name__ == "__main__":
    main()