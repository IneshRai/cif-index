"""
validate_math.py

Validation suite for the CTIF engine. Every test has a hand-computed or
analytically exact expectation. Run before trusting any output:

  python src/validate_math.py

Exits 0 only if every test passes.
"""

import os
import shutil
import sys
import tempfile

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import compute_index as ci
import fetch_index_data as fid  # noqa: F401  (kept for suite import stability)
import fetch_shares as fsh
import alpaca_source as az

RESULTS = []


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition)))
    status = "PASS" if condition else "FAIL"
    line = f"[{status}] {name}"
    if detail and not condition:
        line += f"  ({detail})"
    print(line)


def approx(a, b, tol=1e-9):
    return abs(a - b) <= tol


# ----------------------------------------------------------------------------
# Synthetic data generator: builds a vendor-consistent price file
# ----------------------------------------------------------------------------

def make_frame(dates, close, div=None, split=None):
    """
    Engine-schema frame where adjusted_close is exactly consistent
    with close, dividends, and splits (a correct vendor).
    """
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


def frames_to_wide(frames):
    """Run build_ticker_series per ticker, assemble master-calendar frames."""
    quality = []
    pr_map, tr_map, sa_map = {}, {}, {}
    for t, f in frames.items():
        df = f.sort_values("date").set_index("date")[ci.REQUIRED_COLS]
        pr_map[t], tr_map[t], sa_map[t] = ci.build_ticker_series(df, t, quality)
    cal = pd.DatetimeIndex(
        sorted(set().union(*[s.index for s in sa_map.values()])))
    pr = pd.DataFrame({t: pr_map[t].reindex(cal) for t in frames})
    tr = pd.DataFrame({t: tr_map[t].reindex(cal) for t in frames})
    sa = pd.DataFrame({t: sa_map[t].reindex(cal) for t in frames})
    return pr, tr, sa, cal, quality


# ----------------------------------------------------------------------------
# Capping
# ----------------------------------------------------------------------------

def test_capping():
    w = ci.cap_weights(pd.Series({"A": 80.0, "B": 15.0, "C": 5.0}), cap=0.5)
    check("cap basic: [80,15,5] cap 50% -> [.5,.375,.125]",
          approx(w["A"], 0.5) and approx(w["B"], 0.375)
          and approx(w["C"], 0.125), str(w.round(6).to_dict()))

    w = ci.cap_weights(pd.Series({"A": 60.0, "B": 30.0, "C": 5.0, "D": 5.0}),
                       cap=0.4)
    check("cap iterative: [60,30,5,5] cap 40% -> [.4,.4,.1,.1]",
          all(approx(w[k], v) for k, v in
              {"A": 0.4, "B": 0.4, "C": 0.1, "D": 0.1}.items()),
          str(w.round(6).to_dict()))
    check("cap sums to 1", approx(float(w.sum()), 1.0))

    try:
        ci.cap_weights(pd.Series({"A": 1.0, "B": 1.0}), cap=0.4)
        check("cap infeasible raises", False)
    except ValueError:
        check("cap infeasible raises", True)


# ----------------------------------------------------------------------------
# Per-ticker return construction
# ----------------------------------------------------------------------------

def test_split_adjusted_close():
    dates = pd.bdate_range("2022-01-03", periods=4)
    f = make_frame(dates, [100, 100, 50, 50], split=[1, 1, 2, 1])
    df = f.set_index("date")[ci.REQUIRED_COLS]
    _, _, sa = ci.build_ticker_series(df, "SPLIT", [])
    check("sa_close: 2-for-1 split maps all history to current basis (50)",
          np.allclose(sa.values, 50.0), str(sa.values))


def test_dividend_pr_tr():
    dates = pd.bdate_range("2022-01-03", periods=6)
    f = make_frame(dates, [100] * 6, div=[0, 0, 1.0, 0, 0, 0])
    pr, tr, sa, cal, _ = frames_to_wide({"AAA": f})
    sched = [(cal[0], cal[0])]
    levels, _ = ci.run_index(pr, tr, cal, sched,
                             lambda r, e: pd.Series({"AAA": 1.0}), "T", [])
    check("dividend: PR index flat at 100",
          np.allclose(levels["T-PR"].values, 100.0),
          str(levels["T-PR"].values))
    check("dividend: TR index exactly 101 after ex-date",
          approx(levels["T-TR"].iloc[-1], 101.0)
          and approx(levels["T-TR"].iloc[1], 100.0),
          str(levels["T-TR"].values))
    check("TR >= PR at every date",
          bool((levels["T-TR"] >= levels["T-PR"] - 1e-12).all()))


def test_split_day_return():
    dates = pd.bdate_range("2022-01-03", periods=4)
    f = make_frame(dates, [100, 100, 50, 50], split=[1, 1, 2, 1])
    pr, tr, sa, cal, _ = frames_to_wide({"SPL": f})
    levels, _ = ci.run_index(pr, tr, cal, [(cal[0], cal[0])],
                             lambda r, e: pd.Series({"SPL": 1.0}), "T", [])
    check("split day: no phantom return, PR and TR flat at 100",
          np.allclose(levels.values, 100.0), str(levels.values))


def test_reconciliation_gate():
    dates = pd.bdate_range("2022-01-03", periods=4)
    f = make_frame(dates, [100, 100, 100, 100], div=[0, 0, 1.0, 0])
    f.loc[2, "adjusted_close"] *= 1.005  # corrupt vendor adjustment 50 bps
    quality = []
    ci.build_ticker_series(f.set_index("date")[ci.REQUIRED_COLS], "BAD",
                           quality)
    check("reconciliation gate flags corrupted vendor adjustment",
          any("RECON BAD" in q for q in quality), str(quality))


# ----------------------------------------------------------------------------
# Buy-and-hold equivalence against an explicit simulator
# ----------------------------------------------------------------------------

def test_buy_and_hold_equivalence():
    rng = np.random.default_rng(7)
    n = 60
    dates = pd.bdate_range("2022-01-03", periods=n)
    shares = {"A": 3.0e9, "B": 1.0e9, "C": 0.5e9}
    closes, divs, frames, mcap = {}, {}, {}, {}
    for t, drift in [("A", 0.0004), ("B", 0.0002), ("C", -0.0001)]:
        steps = 1.0 + drift + rng.normal(0, 0.02, n)
        steps[0] = 1.0
        closes[t] = 100.0 * np.cumprod(steps)
        divs[t] = np.zeros(n)
    divs["B"][30] = 1.25  # one cash dividend mid-period
    for t in shares:
        frames[t] = make_frame(dates, closes[t], div=divs[t])
        mcap[t] = closes[t][0] * shares[t]

    pr, tr, sa, cal, _ = frames_to_wide(frames)
    w0 = ci.cap_weights(pd.Series(mcap), cap=0.6)
    levels, _ = ci.run_index(pr, tr, cal, [(cal[0], cal[0])],
                             lambda r, e: w0.copy(), "T", [])

    tickers = list(shares)
    p = np.column_stack([closes[t] for t in tickers])
    d = np.column_stack([divs[t] for t in tickers])
    n_pr = np.array([w0[t] / p[0, i] for i, t in enumerate(tickers)])
    n_tr = n_pr.copy()
    pr_path, tr_path = [100.0], [100.0]
    v_pr0 = float(n_pr @ p[0])
    v_tr_prev = float(n_tr @ p[0])
    for i in range(1, n):
        pr_path.append(100.0 * float(n_pr @ p[i]) / v_pr0)
        value = float(n_tr @ p[i])
        cash = float(n_tr @ d[i])
        tr_path.append(tr_path[-1] * (value + cash) / v_tr_prev)
        n_tr = n_tr * (1.0 + cash / value)  # whole-basket reinvestment
        v_tr_prev = float(n_tr @ p[i])

    check("buy-and-hold: engine PR equals explicit fixed-share portfolio",
          np.allclose(levels["T-PR"].values, pr_path, atol=1e-9))
    check("buy-and-hold: engine TR equals explicit basket-reinvest portfolio",
          np.allclose(levels["T-TR"].values, tr_path, atol=1e-9))


# ----------------------------------------------------------------------------
# Rebalance continuity and weight reset
# ----------------------------------------------------------------------------

def test_rebalance_continuity():
    rng = np.random.default_rng(11)
    n = 40
    dates = pd.bdate_range("2022-01-03", periods=n)
    frames = {}
    for t, drift in [("A", 0.004), ("B", -0.002)]:
        steps = 1.0 + drift + rng.normal(0, 0.01, n)
        steps[0] = 1.0
        frames[t] = make_frame(dates, 100.0 * np.cumprod(steps))
    pr, tr, sa, cal, _ = frames_to_wide(frames)

    eff, ref = cal[20], cal[15]
    targets = {cal[0]: pd.Series({"A": 0.5, "B": 0.5}),
               ref: pd.Series({"A": 0.3, "B": 0.7})}
    lv_rebal, wts = ci.run_index(pr, tr, cal,
                                 [(cal[0], cal[0]), (ref, eff)],
                                 lambda r, e: targets[r].copy(), "T", [])
    lv_hold, _ = ci.run_index(pr, tr, cal, [(cal[0], cal[0])],
                              lambda r, e: targets[cal[0]].copy(), "T", [])

    check("rebalance: level continuous through effective date (no jump)",
          np.allclose(lv_rebal.loc[:eff, "T-PR"].values,
                      lv_hold.loc[:eff, "T-PR"].values, atol=1e-12))
    check("rebalance: paths diverge after effective date",
          not approx(float(lv_rebal["T-PR"].iloc[-1]),
                     float(lv_hold["T-PR"].iloc[-1]), tol=1e-6))
    check("rebalance: weights equal targets at effective close",
          approx(float(wts.loc[eff, "A"]), 0.3)
          and approx(float(wts.loc[eff, "B"]), 0.7))


# ----------------------------------------------------------------------------
# Delisting redistribution
# ----------------------------------------------------------------------------

def test_delisting():
    n = 40
    dates = pd.bdate_range("2022-01-03", periods=n)
    a = make_frame(dates, 100.0 * np.cumprod([1.0] + [1.001] * (n - 1)))
    b = make_frame(dates[:25], [100.0] * 25)
    pr, tr, sa, cal, _ = frames_to_wide({"A": a, "B": b})
    quality = []
    levels, wts = ci.run_index(
        pr, tr, cal, [(cal[0], cal[0])],
        lambda r, e: pd.Series({"A": 0.5, "B": 0.5}), "T", quality)
    post = levels["T-PR"].iloc[25:]
    ratios = (post / post.shift(1)).dropna()
    check("delisting: drop event logged with redistribution",
          any("DROP T" in q and "B" in q for q in quality), str(quality))
    check("delisting: post-drop index return equals surviving member",
          np.allclose(ratios.values, 1.001, atol=1e-12))
    check("delisting: weights renormalize to 100% in survivor",
          approx(float(wts.iloc[-1]["A"]), 1.0))


# ----------------------------------------------------------------------------
# Composite reset
# ----------------------------------------------------------------------------

def test_composite_reset():
    n = 30
    dates = pd.bdate_range("2022-01-03", periods=n)
    lv = pd.DataFrame(index=dates)
    for name, r in [("CTIF-B", 0.000), ("CTIF-C", 0.010), ("CTIF-R", 0.002)]:
        path = 100.0 * np.cumprod([1.0] + [1.0 + r] * (n - 1))
        lv[f"{name}-PR"] = path
        lv[f"{name}-TR"] = path
    eff, ref = dates[15], dates[12]
    comp, wts = ci.run_composite(lv, dates,
                                 [(dates[0], dates[0]), (ref, eff)], [])
    check("composite: weights reset to exactly 1/3 at effective date",
          np.allclose(wts.loc[eff].values, 1.0 / 3.0, atol=1e-12),
          str(wts.loc[eff].values))
    r_comp = float(comp.loc[dates[16], "CTIF-X-PR"]
                   / comp.loc[eff, "CTIF-X-PR"] - 1.0)
    check("composite: day-after-reset return is the simple sleeve mean",
          approx(r_comp, (0.000 + 0.010 + 0.002) / 3.0, tol=1e-12),
          f"{r_comp:.6%}")
    drifted = wts.loc[dates[14]]
    check("composite: weights drift toward stronger sleeve between resets",
          float(drifted["CTIF-C"]) > 1.0 / 3.0 > float(drifted["CTIF-B"]))


# ----------------------------------------------------------------------------
# Full pipeline end to end on a synthetic universe
# ----------------------------------------------------------------------------

def test_end_to_end():
    tmp = tempfile.mkdtemp(prefix="ctif_e2e_")
    data_dir = os.path.join(tmp, "raw")
    out_dir = os.path.join(tmp, "out")
    os.makedirs(data_dir)
    rng = np.random.default_rng(42)
    dates = pd.bdate_range("2022-01-03", "2023-12-29")
    n = len(dates)

    spec = {  # ticker: (sleeve, drift, dividend_every, split_day, start, end)
        "BLD1": ("BUILDERS", 0.0008, 0, None, 0, n),
        "BLD2": ("BUILDERS", 0.0004, 63, None, 0, n),
        "BLD3": ("BUILDERS", 0.0002, 0, 250, 0, n),      # 2-for-1 split
        "CMP1": ("COMPONENTS", 0.0012, 0, None, 0, n),
        "CMP2": ("COMPONENTS", 0.0006, 0, None, 0, n),
        "CMP3": ("COMPONENTS", 0.0010, 0, None, 160, n),  # IPO mid-2022
        "RES1": ("RESOURCES", 0.0003, 42, None, 0, n),
        "RES2": ("RESOURCES", 0.0002, 63, None, 0, n),
        "RES3": ("RESOURCES", 0.0001, 63, None, 0, 400),  # delists mid-quarter
    }
    shares_rows, cons_rows = [], []
    for t, (sleeve, drift, div_every, split_day, s0, s1) in spec.items():
        m = s1 - s0
        steps = 1.0 + drift + rng.normal(0, 0.015, m)
        steps[0] = 1.0
        close = 80.0 * np.cumprod(steps)
        div = np.zeros(m)
        if div_every:
            div[div_every::div_every] = 0.30
        split = np.ones(m)
        if split_day is not None and s0 <= split_day < s1:
            split[split_day - s0] = 2.0
            close[split_day - s0:] = close[split_day - s0:] / 2.0
        make_frame(dates[s0:s1], close, div=div, split=split).to_csv(
            os.path.join(data_dir, f"{t}.csv"), index=False)
        cons_rows.append({"ticker": t, "company_name": t, "sleeve": sleeve,
                          "category": "synthetic",
                          "add_date": dates[s0].date(), "notes": ""})
        shares_rows.append({"ticker": t,
                            "shares_outstanding":
                                float(rng.uniform(1, 8)) * 1e9})

    cons_path = os.path.join(tmp, "constituents.csv")
    shares_path = os.path.join(tmp, "shares.csv")
    pd.DataFrame(cons_rows).to_csv(cons_path, index=False)
    pd.DataFrame(shares_rows).to_csv(shares_path, index=False)

    rc = ci.main(["--data-dir", data_dir, "--constituents", cons_path,
                  "--shares", shares_path, "--output-dir", out_dir,
                  "--base-date", "2022-01-03", "--cap", "0.5"])
    check("e2e: engine exits 0", rc == 0)

    lv = pd.read_csv(os.path.join(out_dir, "index_levels.csv"),
                     parse_dates=["date"]).set_index("date")
    expected = {f"CTIF-{x}-{v}" for x in ["B", "C", "R", "X", "AGG"]
                for v in ["PR", "TR"]}
    check("e2e: all ten series present", expected == set(lv.columns),
          str(sorted(lv.columns)))
    check("e2e: levels finite and positive",
          bool(np.isfinite(lv.values).all() and (lv.values > 0).all()))
    check("e2e: TR >= PR everywhere for every series",
          all(bool((lv[f"CTIF-{x}-TR"] >= lv[f"CTIF-{x}-PR"] - 1e-9).all())
              for x in ["B", "C", "R", "X", "AGG"]))

    reports = sorted(f for f in os.listdir(out_dir)
                     if f.startswith("rebalance_report"))
    check("e2e: initial constitution plus quarterly reports written",
          len(reports) >= 8, str(reports))
    first = pd.read_csv(os.path.join(out_dir, reports[0]))
    later = pd.read_csv(os.path.join(out_dir, reports[-1]))
    check("e2e: mid-2022 IPO excluded at base, present at final rebalance",
          "CMP3" not in set(first["ticker"])
          and "CMP3" in set(later["ticker"]))
    quality = open(os.path.join(out_dir, "quality_log.txt")).read()
    check("e2e: delisting detected and weight redistributed",
          "DELIST RES3" in quality and "DROP CTIF-R" in quality)
    wts_r = pd.read_csv(os.path.join(out_dir, "weights_resources.csv"),
                        parse_dates=["date"]).set_index("date")
    check("e2e: sleeve weights sum to 1 daily",
          np.allclose(wts_r.sum(axis=1).dropna().values, 1.0, atol=1e-9))
    comp_w = pd.read_csv(os.path.join(out_dir, "weights_composite.csv"),
                         parse_dates=["date"]).set_index("date")
    eff_mar23 = ci.snap_to_calendar(ci.nth_friday(2023, 3, 3), lv.index)
    check("e2e: composite reset to 1/3 at a real quarterly effective date",
          np.allclose(comp_w.loc[eff_mar23].values, 1.0 / 3.0, atol=1e-12))
    max_w = float(later["target_weight"].max())
    check("e2e: cap respected in rebalance targets", max_w <= 0.5 + 1e-9,
          f"max target {max_w:.4f}")
    shutil.rmtree(tmp)


# ----------------------------------------------------------------------------
# Calendar helpers and fetch parsing
# ----------------------------------------------------------------------------

def test_calendar_helpers():
    check("nth_friday: third Friday of March 2026 is the 20th",
          ci.nth_friday(2026, 3, 3) == pd.Timestamp("2026-03-20").date())
    check("nth_friday: second Friday of June 2026 is the 12th",
          ci.nth_friday(2026, 6, 2) == pd.Timestamp("2026-06-12").date())
    cal = pd.DatetimeIndex(
        [d for d in pd.bdate_range("2026-03-01", "2026-03-31")
         if d != pd.Timestamp("2026-03-20")])
    check("snap: holiday third Friday snaps to prior trading day (Mar 19)",
          ci.snap_to_calendar(ci.nth_friday(2026, 3, 3), cal)
          == pd.Timestamp("2026-03-19"))


def test_fetch_parsers():
    # Alpaca raw daily bars (t, o, h, l, c, v). One plain day, one dividend
    # ex-date, and one 2:1 forward-split ex-date.
    raw = [
        {"t": "2026-07-08T04:00:00Z", "o": 10, "h": 11, "l": 9, "c": 10.0, "v": 1000},
        {"t": "2026-07-09T04:00:00Z", "o": 10, "h": 11, "l": 9, "c": 10.5, "v": 1200},
        {"t": "2026-07-10T04:00:00Z", "o": 5,  "h": 6,  "l": 4, "c": 5.20, "v": 2400},
    ]
    # adjustment=all bars supply adjusted_close (arbitrary but monotone here)
    adj = [
        {"t": "2026-07-08T04:00:00Z", "c": 5.00},
        {"t": "2026-07-09T04:00:00Z", "c": 5.25},
        {"t": "2026-07-10T04:00:00Z", "c": 5.20},
    ]
    divs = {("TST", "2026-07-09"): 0.10}
    splits = {("TST", "2026-07-10"): 2.0}  # new_rate/old_rate = 2/1
    df = az.assemble_frame("TST", raw, adj, divs, splits)
    check("alpaca assemble: schema columns present",
          all(c in df.columns for c in
              ("date", "open", "high", "low", "close", "adjusted_close",
               "volume", "dividend_amount", "split_coefficient")))
    check("alpaca assemble: sorted ascending by date, close mapped",
          list(df["close"]) == [10.0, 10.5, 5.20])
    idx = df.set_index("date")
    cell = lambda d, c: float(idx.loc[pd.Timestamp(d), c])
    check("alpaca assemble: dividend attached to ex-date only",
          approx(cell("2026-07-09", "dividend_amount"), 0.10)
          and approx(cell("2026-07-08", "dividend_amount"), 0.0))
    check("alpaca assemble: split_coefficient attached to ex-date, else 1",
          approx(cell("2026-07-10", "split_coefficient"), 2.0)
          and approx(cell("2026-07-08", "split_coefficient"), 1.0))
    check("alpaca assemble: adjusted_close mapped from adj feed",
          approx(cell("2026-07-08", "adjusted_close"), 5.00))
    # Split ex-date should be a zero-return price event under raw construction:
    # (close * split) / prev_close - 1 = (5.20 * 2) / 10.5 - 1 ~ -0.0095, i.e.
    # a real small move, not a spurious -50% from the raw halving.
    pr, tr, sa = ci.build_ticker_series(df.set_index("date"), "TST", [])
    check("alpaca assemble: split day is not a spurious -50% move",
          pr.iloc[-1] > -0.10)
    try:
        az.assemble_frame("TST", [], [], {}, {})
        check("alpaca assemble: empty bars raises", False)
    except ValueError:
        check("alpaca assemble: empty bars raises", True)


def main():
    tests = [
        test_capping,
        test_split_adjusted_close,
        test_dividend_pr_tr,
        test_split_day_return,
        test_reconciliation_gate,
        test_buy_and_hold_equivalence,
        test_rebalance_continuity,
        test_delisting,
        test_composite_reset,
        test_calendar_helpers,
        test_fetch_parsers,
        test_end_to_end,
    ]
    for t in tests:
        try:
            t()
        except Exception as exc:
            check(f"{t.__name__} raised no unexpected exception", False,
                  repr(exc))
    n_pass = sum(1 for _, ok in RESULTS if ok)
    n_all = len(RESULTS)
    print(f"\n{n_pass}/{n_all} checks passed")
    return 0 if n_pass == n_all else 1


if __name__ == "__main__":
    sys.exit(main())
