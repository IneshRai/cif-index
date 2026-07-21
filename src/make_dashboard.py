"""
make_dashboard.py (v2)

Builds a single self-contained interactive HTML dashboard from the engine's
outputs. No external dependencies, works offline, monochrome house style.

Features
  Interactive charts: crosshair readout, clickable legend, range windows
  (1M/3M/6M/YTD/1Y/Max), rebase-to-window-start, log scale, PR/TR switch.
  Risk tab: rolling 63-day annualized volatility and sleeve correlations.
  Contributors tab: per-constituent contribution attribution (MTD/QTD/YTD).
  Rebalance tab: browsable archive of every rebalance report with cap flags.
  Benchmarks: any engine-schema CSVs in --benchmarks-dir are overlaid
  on the level chart and compared in a table.

Usage (production)
  python src/make_dashboard.py --output-dir output \
      --data-dir data/raw --constituents constituents.csv \
      --benchmarks-dir data/benchmarks --dashboard output/dashboard.html

--data-dir and --constituents enable the Contributors tab.
Add --synthetic-banner for demo runs only.
"""

import argparse
import glob
import html
import json
import logging
import os
import sys

import numpy as np
import pandas as pd

import compute_index as ci
from weekly_report import trailing_return, since_return, next_rebalance

log = logging.getLogger("ctif.dashboard")

SLEEVES = [("CTIF-B", "Builders", "BUILDERS"),
           ("CTIF-C", "Components", "COMPONENTS"),
           ("CTIF-R", "Resources", "RESOURCES")]
LINE = {"CTIF-X": ("#000000", None, 2.6), "CTIF-B": ("#000000", "8 4", 1.5),
        "CTIF-C": ("#444444", "2 3", 1.7), "CTIF-R": ("#000000", "10 3 2 3", 1.5),
        "CTIF-AGG": ("#999999", None, 1.3)}
BENCH_STYLES = [("#666666", "5 5", 1.3), ("#999999", "2 4", 1.5),
                ("#666666", "12 4", 1.2), ("#999999", "8 3 2 3", 1.3),
                ("#666666", "1 3", 1.7)]


def fmt_pct(x, signed=True):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "n/a"
    return f"{x:+.2%}" if signed else f"{x:.2%}"


# ----------------------------------------------------------------------------
# Analytics
# ----------------------------------------------------------------------------

def rolling_vol(levels, window=63):
    out = {}
    for code, label, _ in [("CTIF-X", "Composite", None)] + SLEEVES:
        s = levels[f"{code}-TR"].dropna()
        r = s / s.shift(1) - 1.0
        out[f"vol {label}"] = (r.rolling(window).std(ddof=1)
                               * np.sqrt(252.0)).reindex(levels.index)
    return pd.DataFrame(out, index=levels.index)


def rolling_corr(levels, window=63):
    rets = {}
    for code, label, _ in SLEEVES:
        s = levels[f"{code}-TR"].dropna()
        rets[label] = (s / s.shift(1) - 1.0).reindex(levels.index)
    r = pd.DataFrame(rets)
    out = {}
    for a, b in [("Builders", "Components"), ("Builders", "Resources"),
                 ("Components", "Resources")]:
        out[f"corr {a[0]}/{b[0]}"] = r[a].rolling(window).corr(r[b])
    return pd.DataFrame(out, index=levels.index)


def load_benchmarks(bench_dir, calendar):
    out = {}
    if not bench_dir or not os.path.isdir(bench_dir):
        return out
    for path in sorted(glob.glob(os.path.join(bench_dir, "*.csv"))):
        name = os.path.splitext(os.path.basename(path))[0]
        try:
            df = ci.load_price_file(path)
            tr = (df["adjusted_close"] / df["adjusted_close"].iloc[0]
                  * 100.0).reindex(calendar).ffill()
            if tr.notna().sum() < 30:
                log.warning("Benchmark %s: too little overlap, skipped", name)
                continue
            out[name] = tr
        except Exception as exc:
            log.warning("Benchmark %s skipped: %s", name, exc)
    return out


def contributions(data_dir, constituents, out_dir, levels):
    """Per-constituent arithmetic contribution sums per sleeve and window."""
    cons = pd.read_csv(constituents)
    tickers = list(cons["ticker"].str.strip())
    quality = []
    _, tr, _, _, calendar = ci.load_universe(data_dir, tickers, quality)
    tr = tr.reindex(levels.index)
    asof = levels.index[-1]
    windows = {
        "MTD": pd.Timestamp(asof.year, asof.month, 1),
        "QTD": pd.Timestamp(asof.year, 3 * ((asof.month - 1) // 3) + 1, 1),
        "YTD": pd.Timestamp(asof.year, 1, 1),
    }
    blocks = []
    for code, label, sleeve in SLEEVES:
        wpath = os.path.join(out_dir, f"weights_{sleeve.lower()}.csv")
        w = pd.read_csv(wpath, parse_dates=["date"]).set_index("date")
        w = w.reindex(levels.index)
        cols = [c for c in w.columns if c in tr.columns]
        contrib_day = (w[cols].shift(1) * tr[cols])
        rows = []
        for win, start in windows.items():
            c = contrib_day.loc[start:].sum().sort_values(ascending=False)
            c = c[c != 0.0]
            top = "".join(f"<tr><td>{t}</td><td>{v * 1e4:+,.0f}</td></tr>"
                          for t, v in c.head(5).items())
            bot = "".join(f"<tr><td>{t}</td><td>{v * 1e4:+,.0f}</td></tr>"
                          for t, v in c.tail(5).sort_values().items())
            rows.append(
                f"<div><h4>{win}</h4>"
                f"<table><tr><th>Top</th><th>bps</th></tr>{top}</table>"
                f"<table><tr><th>Bottom</th><th>bps</th></tr>{bot}</table>"
                f"</div>")
        blocks.append(f"<h3>{label}</h3><div class='cols'>"
                      + "".join(rows) + "</div>")
    return ("<p class='sub'>Arithmetic sum of daily weight times total "
            "return, in basis points of the sleeve. Approximate for long "
            "windows (no compounded linking).</p>" + "".join(blocks))


# ----------------------------------------------------------------------------
# Static HTML pieces
# ----------------------------------------------------------------------------

def returns_table(levels, benchmarks):
    asof = levels.index[-1]
    ys = pd.Timestamp(asof.year, 1, 1) - pd.Timedelta(days=1)
    qs = pd.Timestamp(asof.year, 3 * ((asof.month - 1) // 3) + 1, 1) \
        - pd.Timedelta(days=1)
    ms = pd.Timestamp(asof.year, asof.month, 1) - pd.Timedelta(days=1)

    def row(label, tr, pr=None, bench=False):
        ytd_tr = since_return(tr, ys)
        gap = ""
        if pr is not None:
            ytd_pr = since_return(pr, ys)
            gap = fmt_pct(None if None in (ytd_tr, ytd_pr)
                          else ytd_tr - ytd_pr)
        cls = " class='bench'" if bench else ""
        return (f"<tr{cls}><td>{html.escape(label)}</td>"
                f"<td>{tr.iloc[-1]:,.2f}</td>"
                f"<td>{fmt_pct(trailing_return(tr, 5))}</td>"
                f"<td>{fmt_pct(since_return(tr, ms))}</td>"
                f"<td>{fmt_pct(since_return(tr, qs))}</td>"
                f"<td>{fmt_pct(ytd_tr)}</td>"
                f"<td>{fmt_pct(tr.iloc[-1] / tr.dropna().iloc[0] - 1.0)}</td>"
                f"<td>{gap}</td></tr>")

    head = ("<tr><th>Series</th><th>TR level</th><th>1W</th><th>MTD</th>"
            "<th>QTD</th><th>YTD</th><th>Full</th><th>TR-PR YTD</th></tr>")
    rows = []
    labels = {"CTIF-X": "Composite", "CTIF-B": "Builders",
              "CTIF-C": "Components", "CTIF-R": "Resources",
              "CTIF-AGG": "Aggregate cap-weight (diagnostic)"}
    for code, label in labels.items():
        rows.append(row(label, levels[f"{code}-TR"].dropna(),
                        levels[f"{code}-PR"].dropna()))
    for name, tr in benchmarks.items():
        rows.append(row(f"Benchmark: {name}", tr.dropna(), bench=True))
    return f"<table>{head}{''.join(rows)}</table>"


def weight_bars(w, top=12, cap=0.10):
    w = w.sort_values(ascending=False)
    shown, other = w.head(top), float(w.iloc[top:].sum())
    rows = []
    for tkr, val in shown.items():
        capped = " <b>(cap)</b>" if val >= cap - 1e-6 else ""
        rows.append(
            f'<div class="wrow"><span class="wt">{html.escape(str(tkr))}'
            f'</span><span class="wbar"><span class="wfill" '
            f'style="width:{min(val, cap * 1.2) / (cap * 1.2) * 100:.1f}%">'
            f'</span></span><span class="wv">{val:.2%}{capped}</span></div>')
    if other > 0:
        rows.append(f'<div class="wrow"><span class="wt">{len(w) - top} '
                    f'others</span><span class="wbar"></span>'
                    f'<span class="wv">{other:.2%}</span></div>')
    return "".join(rows)


def rebalance_archive(report_paths):
    options, divs = [], []
    for i, path in enumerate(sorted(report_paths, reverse=True)):
        rep = pd.read_csv(path)
        eff = str(rep["effective_date"].iloc[0])
        ref = str(rep["reference_date"].iloc[0])
        options.append(f'<option value="reb{i}">{eff}</option>')
        tables = []
        for code, label, sleeve in SLEEVES:
            g = rep[rep["sleeve"] == sleeve].copy()
            if g.empty:
                continue
            g["uncapped"] = g["mcap_proxy"] / g["mcap_proxy"].sum()
            g = g.sort_values("target_weight", ascending=False)
            body = "".join(
                f"<tr><td>{r.ticker}</td><td>{r.mcap_proxy / 1e9:,.1f}</td>"
                f"<td>{r.uncapped:.2%}</td><td>{r.target_weight:.2%}"
                f"{' <b>(cap)</b>' if r.target_weight >= 0.0999 else ''}"
                f"</td></tr>" for r in g.itertuples())
            tables.append(f"<h3>{label}: {len(g)} members</h3>"
                          "<table><tr><th>Ticker</th><th>Mcap proxy ($bn)"
                          "</th><th>Uncapped</th><th>Capped target</th></tr>"
                          f"{body}</table>")
        divs.append(f'<div id="reb{i}" class="rebpane'
                    f'{" on" if i == 0 else ""}">'
                    f'<p class="sub">Reference {ref}, effective {eff}. '
                    'Targets seeded at the effective close.</p>'
                    + "".join(tables) + "</div>")
    return "".join(options), "".join(divs)


# ----------------------------------------------------------------------------
# Page template
# ----------------------------------------------------------------------------

CSS = """
 body { font-family: Arial, Helvetica, sans-serif; color:#000;
        background:#fff; margin:0; }
 .banner { background:#000; color:#fff; text-align:center; padding:8px 12px;
        font-size:13px; letter-spacing:.04em; position:sticky; top:0;
        z-index:5; }
 .wrap { max-width:1000px; margin:0 auto; padding:18px 20px 60px; }
 h1 { font-size:22px; margin:14px 0 2px; }
 h2 { font-size:16px; margin:26px 0 8px; border-bottom:2px solid #000;
      padding-bottom:4px; }
 h3 { font-size:14px; margin:18px 0 6px; }
 h4 { font-size:12.5px; margin:8px 0 4px; }
 .sub { color:#444; font-size:13px; margin:0 0 12px; }
 .tabs { display:flex; gap:6px; margin:18px 0 6px; flex-wrap:wrap; }
 .tabs button { font:inherit; font-size:13px; padding:7px 13px;
        background:#fff; border:1px solid #000; cursor:pointer; }
 .tabs button.on { background:#000; color:#fff; }
 .pane { display:none; } .pane.on { display:block; }
 table { border-collapse:collapse; width:100%; font-size:12.5px;
        margin:8px 0 14px; }
 th,td { border:1px solid #999; padding:4px 8px; text-align:left; }
 th { background:#efefef; }
 td:nth-child(n+2), th:nth-child(n+2) { text-align:right; }
 tr.bench td { color:#555; font-style:italic; }
 .wrow { display:flex; align-items:center; gap:8px; margin:3px 0;
        font-size:12.5px; }
 .wt { width:90px; } .wv { width:105px; text-align:right; }
 .wbar { flex:1; height:12px; border:1px solid #999; background:#fff; }
 .wfill { display:block; height:100%; background:#000; }
 .cols { display:grid; grid-template-columns:repeat(auto-fit,
        minmax(240px,1fr)); gap:18px; }
 pre { background:#f6f6f6; border:1px solid #ccc; padding:10px;
        font-size:11.5px; overflow-x:auto; }
 .note { border:1px solid #000; padding:10px 12px; font-size:13px;
        margin:10px 0; }
 .controls { display:flex; gap:14px; align-items:center; flex-wrap:wrap;
        font-size:12.5px; border:1px solid #000; padding:8px 10px;
        margin:14px 0; position:sticky; top:34px; background:#fff;
        z-index:4; }
 .controls .grp { display:flex; gap:4px; align-items:center; }
 .controls button { font:inherit; font-size:12px; padding:3px 9px;
        background:#fff; border:1px solid #666; cursor:pointer; }
 .controls button.on { background:#000; color:#fff; border-color:#000; }
 .chart { position:relative; margin:6px 0 18px; }
 .chart svg text.leg { cursor:pointer; }
 .chart svg text.leg.off { fill:#bbb; text-decoration:line-through; }
 #tip { position:fixed; display:none; background:#fff; border:1px solid #000;
        padding:6px 9px; font-size:11.5px; pointer-events:none; z-index:9;
        white-space:nowrap; box-shadow:2px 2px 0 #000; }
 .rebpane { display:none; } .rebpane.on { display:block; }
 select { font:inherit; font-size:13px; padding:4px; }
 footer { color:#666; font-size:11.5px; margin-top:40px; }
"""

JS = r"""
'use strict';
var D = window.CHART_DATA;
var N = D.dates.length;
var CHARTS = [];
var state = { i0: 0, rebase: true, log: false, variant: 'TR', hidden: {} };

function esc(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;'); }
function fmtVal(v, kind){
  if (v === null || v === undefined || isNaN(v)) return 'n/a';
  if (kind === 'pct') return (v * 100).toFixed(2) + '%';
  return v.toLocaleString('en-US', {minimumFractionDigits: 2,
                                    maximumFractionDigits: 2});
}
function niceTicks(lo, hi, n){
  var span = hi - lo; if (span <= 0) return [lo];
  var raw = span / n, mag = Math.pow(10, Math.floor(Math.log(raw)/Math.LN10));
  var step = 10 * mag;
  [1, 2, 2.5, 5, 10].some(function(s){
    if (s * mag >= raw) { step = s * mag; return true; } return false; });
  var out = [], v = Math.ceil(lo / step) * step;
  for (; v <= hi + step * 0.01; v += step) out.push(v);
  return out;
}

function Chart(hostId, cfg){
  this.host = document.getElementById(hostId);
  this.cfg = cfg;
  this.W = 940; this.H = cfg.h || 330;
  this.ml = 64; this.mr = 12; this.mt = 30; this.mb = 30;
  CHARTS.push(this);
  var self = this;
  this.host.addEventListener('mousemove', function(e){ self.hover(e); });
  this.host.addEventListener('mouseleave', function(){ tip.style.display='none';
    var c = self.host.querySelector('.cross'); if (c) c.setAttribute('opacity','0'); });
  this.host.addEventListener('click', function(e){
    var t = e.target.closest ? e.target.closest('.leg') : null;
    if (t) { var n = t.getAttribute('data-n');
      state.hidden[n] = !state.hidden[n]; redrawAll(); } });
  this.render();
}
Chart.prototype.names = function(){
  var v = state.variant;
  return this.cfg.series.map(function(n){ return n.replace('{V}', v); })
    .filter(function(n){ return D.series[n]; });
};
Chart.prototype.windowVals = function(name){
  var raw = D.series[name].v.slice(state.i0);
  if (this.cfg.rebase && state.rebase){
    var base = null;
    for (var i = 0; i < raw.length; i++){
      if (raw[i] !== null){ base = raw[i]; break; } }
    if (base) raw = raw.map(function(x){
      return x === null ? null : 100 * x / base; });
  }
  return raw;
};
Chart.prototype.render = function(){
  var self = this, names = this.names();
  var iw = this.W - this.ml - this.mr, ih = this.H - this.mt - this.mb;
  var vis = names.filter(function(n){ return !state.hidden[n]; });
  var lo = Infinity, hi = -Infinity;
  this.vals = {};
  names.forEach(function(n){ self.vals[n] = self.windowVals(n); });
  vis.forEach(function(n){ self.vals[n].forEach(function(x){
    if (x !== null && isFinite(x)){ if (x<lo) lo=x; if (x>hi) hi=x; } }); });
  if (!isFinite(lo)) { lo = 0; hi = 1; }
  var pad = (hi - lo) * 0.06 || 1; lo -= pad; hi += pad;
  var useLog = this.cfg.rebase && state.log && lo > 0;
  var Ylo = useLog ? Math.log(lo) : lo, Yhi = useLog ? Math.log(hi) : hi;
  var m = this.vals[names[0]].length;
  this.X = function(i){ return self.ml + (m<2?0:iw * i/(m-1)); };
  var Y = function(v){
    var t = useLog ? Math.log(v) : v;
    return self.mt + ih * (1 - (t - Ylo)/(Yhi - Ylo)); };
  this.Yfun = Y;
  var s = '<svg viewBox="0 0 '+this.W+' '+this.H+'" ' +
    'xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;' +
    'background:#fff;border:1px solid #ddd">';
  s += '<text x="'+this.ml+'" y="18" font-size="13.5" font-weight="bold">' +
       esc(this.cfg.title + (this.cfg.rebase && state.rebase ?
       ' (rebased to 100 at window start)' : '')) + '</text>';
  niceTicks(lo, hi, 5).forEach(function(tv){
    var y = Y(tv);
    if (y < self.mt || y > self.H - self.mb) return;
    s += '<line x1="'+self.ml+'" y1="'+y.toFixed(1)+'" x2="' +
      (self.W-self.mr)+'" y2="'+y.toFixed(1)+'" stroke="#e2e2e2"/>';
    s += '<text x="'+(self.ml-6)+'" y="'+(y+4).toFixed(1)+'" font-size="11"' +
      ' text-anchor="end" fill="#444">' +
      (self.cfg.kind==='pct' ? (tv*100).toFixed(self.cfg.dp!==undefined?self.cfg.dp:0)+'%'
       : tv.toLocaleString('en-US',{maximumFractionDigits:0})) + '</text>'; });
  var lastYr = null;
  for (var i = 0; i < m; i++){
    var yr = D.dates[state.i0 + i].slice(0,4);
    if (yr !== lastYr && i > 0){
      var x = this.X(i);
      s += '<line x1="'+x.toFixed(1)+'" y1="'+this.mt+'" x2="'+x.toFixed(1) +
        '" y2="'+(this.H-this.mb)+'" stroke="#f0f0f0"/>';
      s += '<text x="'+x.toFixed(1)+'" y="'+(this.H-this.mb+15) +
        '" font-size="11" text-anchor="middle" fill="#444">'+yr+'</text>';
    }
    lastYr = yr;
  }
  names.forEach(function(n){
    if (state.hidden[n]) return;
    var st = D.series[n], pts = [];
    self.vals[n].forEach(function(v, i){
      if (v !== null && isFinite(v))
        pts.push(self.X(i).toFixed(1) + ',' + Y(v).toFixed(1)); });
    s += '<polyline points="'+pts.join(' ')+'" fill="none" stroke="' +
      st.c + '" stroke-width="'+st.w+'"' +
      (st.d ? ' stroke-dasharray="'+st.d+'"' : '') + '/>'; });
  var lx = this.ml + 6;
  names.forEach(function(n){
    var st = D.series[n], off = state.hidden[n];
    s += '<line x1="'+lx+'" y1="'+(self.mt+9)+'" x2="'+(lx+24)+'" y2="' +
      (self.mt+9)+'" stroke="'+(off?'#ccc':st.c)+'" stroke-width="'+st.w+'"' +
      (st.d ? ' stroke-dasharray="'+st.d+'"' : '') + '/>';
    s += '<text x="'+(lx+28)+'" y="'+(self.mt+13)+'" font-size="12" ' +
      'class="leg'+(off?' off':'')+'" data-n="'+esc(n)+'">' +
      esc(st.label || n)+'</text>';
    lx += 38 + 7.2 * (st.label || n).length; });
  s += '<line class="cross" x1="0" y1="'+this.mt+'" x2="0" y2="' +
    (this.H-this.mb)+'" stroke="#000" stroke-width="0.8" opacity="0"/>';
  s += '</svg>';
  this.host.innerHTML = s;
};
Chart.prototype.hover = function(e){
  var svg = this.host.querySelector('svg');
  var r = svg.getBoundingClientRect();
  if (!r.width) return;
  var xr = (e.clientX - r.left) / r.width * this.W;
  var m = this.vals[this.names()[0]].length;
  var iw = this.W - this.ml - this.mr;
  var i = Math.round((xr - this.ml) / iw * (m - 1));
  if (i < 0 || i >= m) return;
  var cross = svg.querySelector('.cross');
  cross.setAttribute('x1', this.X(i)); cross.setAttribute('x2', this.X(i));
  cross.setAttribute('opacity', '0.5');
  var self = this;
  var rows = this.names().filter(function(n){ return !state.hidden[n]; })
    .map(function(n){ return '<b>' + esc(D.series[n].label || n) + '</b> ' +
      fmtVal(self.vals[n][i], self.cfg.kind); });
  tip.innerHTML = '<b>' + D.dates[state.i0 + i] + '</b><br>' +
    rows.join('<br>');
  tip.style.display = 'block';
  tip.style.left = Math.min(e.clientX + 14,
    window.innerWidth - tip.offsetWidth - 8) + 'px';
  tip.style.top = (e.clientY + 14) + 'px';
};

function redrawAll(){ CHARTS.forEach(function(c){ c.render(); }); }
function setRange(code, btn){
  var lookback = { '1M':21, '3M':63, '6M':126, '1Y':252 };
  if (code === 'MAX') state.i0 = 0;
  else if (code === 'YTD'){
    var yr = D.dates[N-1].slice(0,4); state.i0 = 0;
    for (var i = 0; i < N; i++){
      if (D.dates[i].slice(0,4) === yr){ state.i0 = i; break; } }
  } else state.i0 = Math.max(0, N - 1 - lookback[code]);
  document.querySelectorAll('[data-range]').forEach(function(b){
    b.classList.toggle('on', b === btn); });
  redrawAll();
}
function show(ev, id){
  document.querySelectorAll('.pane').forEach(function(p){
    p.classList.remove('on'); });
  document.querySelectorAll('.tabs button').forEach(function(b){
    b.classList.remove('on'); });
  document.getElementById(id).classList.add('on');
  ev.currentTarget.classList.add('on');
}
var tip = document.createElement('div'); tip.id = 'tip';
document.body.appendChild(tip);
document.getElementById('ctl-rebase').addEventListener('change', function(e){
  state.rebase = e.target.checked; redrawAll(); });
document.getElementById('ctl-log').addEventListener('change', function(e){
  state.log = e.target.checked; redrawAll(); });
document.querySelectorAll('input[name=variant]').forEach(function(rb){
  rb.addEventListener('change', function(e){
    state.variant = e.target.value; redrawAll(); }); });
var rebsel = document.getElementById('rebsel');
if (rebsel) rebsel.addEventListener('change', function(e){
  document.querySelectorAll('.rebpane').forEach(function(p){
    p.classList.remove('on'); });
  document.getElementById(e.target.value).classList.add('on'); });
window.CTIF_CHART_CONFIGS.forEach(function(c){ new Chart(c.el, c); });
"""

PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CTIF Dashboard</title><style>{css}</style></head><body>
{banner}
<div class="wrap">
<h1>CTIF: Castellan Technology Infrastructure Family</h1>
<p class="sub">As of {asof} &middot; Base 100.00 on {base} &middot;
Methodology v{ver} &middot; {ncon} constituents &middot;
Next rebalance (third Friday): {nextreb}</p>

<div class="tabs">
<button class="on" onclick="show(event,'p0')">Overview</button>
<button onclick="show(event,'p1')">Risk</button>
<button onclick="show(event,'p2')">Weights</button>
<button onclick="show(event,'p3')">Contributors</button>
<button onclick="show(event,'p4')">Rebalances</button>
<button onclick="show(event,'p5')">Quality</button>
<button onclick="show(event,'p6')">Methodology</button>
</div>

<div class="controls">
<span class="grp"><b>Window:</b>
<button data-range onclick="setRange('1M',this)">1M</button>
<button data-range onclick="setRange('3M',this)">3M</button>
<button data-range onclick="setRange('6M',this)">6M</button>
<button data-range onclick="setRange('YTD',this)">YTD</button>
<button data-range onclick="setRange('1Y',this)">1Y</button>
<button data-range class="on" onclick="setRange('MAX',this)">Max</button>
</span>
<label><input type="checkbox" id="ctl-rebase" checked>
Rebase to window start</label>
<label><input type="checkbox" id="ctl-log"> Log scale</label>
<span class="grp"><b>Version:</b>
<label><input type="radio" name="variant" value="TR" checked> TR</label>
<label><input type="radio" name="variant" value="PR"> PR</label></span>
<span class="sub" style="margin:0">Click legend entries to toggle series.
Hover for values.</span>
</div>

<div id="p0" class="pane on">
<h2>Performance summary</h2>
{returns}
<h2>Index levels{benchnote}</h2>
<div id="ch-levels"></div>
<h2>Drawdown from running peak (TR)</h2>
<div id="ch-dd"></div>
<h2>Sleeve relative strength</h2>
<div id="ch-ratio"></div>
</div>

<div id="p1" class="pane">
<h2>Rolling 63-day annualized volatility (TR)</h2>
<div id="ch-vol"></div>
<h2>Rolling 63-day sleeve correlations (TR daily returns)</h2>
<div id="ch-corr"></div>
<h2>Series statistics (full history)</h2>
{stats}
</div>

<div id="p2" class="pane">
<h2>Current sleeve weights (drifted, as of {asof})</h2>
<div class="cols">
<div><h3>Builders</h3>{wb}</div>
<div><h3>Components</h3>{wc}</div>
<div><h3>Resources</h3>{wr}</div>
</div>
<h2>Composite sleeve mix over time (quarterly reset sawtooth)</h2>
<div id="ch-mix"></div>
</div>

<div id="p3" class="pane">
<h2>Contribution attribution</h2>
{contrib}
</div>

<div id="p4" class="pane">
<h2>Rebalance archive</h2>
<p class="sub">Every rebalance the engine has produced, most recent first.
Weights at exactly the cap are flagged.</p>
<select id="rebsel">{rebopts}</select>
{rebdivs}
</div>

<div id="p5" class="pane">
<h2>Quality log</h2>
<pre>{quality}</pre>
<h2>Run metadata</h2>
<pre>{meta}</pre>
</div>

<div id="p6" class="pane">
<h2>Key rules</h2>
<div class="note">Three market-cap-weighted sleeves with a 10 percent
single-name cap; equal-thirds composite reset quarterly. Rebalance
reference is the second Friday close, effective the third Friday close, of
March, June, September, and December. Between rebalances share counts are
fixed and weights drift with prices. PR and TR versions share one weight
path; TR reinvests dividends pro rata across the basket, so TR sits at or
above PR at every date. Corporate actions follow the standing rules table
in methodology.md; every discretionary decision requires a changelog
entry.</div>
<h2>Back-cast disclosure</h2>
<div class="note">History before the live date is a back-cast. The universe
is selected with hindsight: these are companies known in 2026 to have been
winners of the buildout. Back-cast levels describe how the theme traded and
are useful for attribution and regime analysis. They are not an estimate of
returns anyone could have earned and must never be presented as such.</div>
</div>

<footer>Generated by make_dashboard.py from compute_index.py outputs.
Production data source: Alpaca daily bars (raw + adjusted) with
dual-construction reconciliation on dividend and split days.</footer>
</div>
<script>
window.CHART_DATA = {data_json};
window.CTIF_CHART_CONFIGS = {charts_json};
{js}
</script>
</body></html>
"""


def series_entry(values, color, dash, width, label=None):
    v = [None if (x is None or not np.isfinite(x)) else round(float(x), 6)
         for x in values]
    return {"v": v, "c": color, "d": dash, "w": width, "label": label}


def main(argv=None):
    ap = argparse.ArgumentParser(description="CTIF interactive dashboard")
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--dashboard", required=True)
    ap.add_argument("--data-dir", help="raw price dir, enables Contributors")
    ap.add_argument("--constituents", help="needed with --data-dir")
    ap.add_argument("--benchmarks-dir", help="optional benchmark CSVs")
    ap.add_argument("--synthetic-banner", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    od = args.output_dir

    levels = pd.read_csv(os.path.join(od, "index_levels.csv"),
                         parse_dates=["date"]).set_index("date")
    stats = pd.read_csv(os.path.join(od, "stats_summary.csv"))
    meta = json.load(open(os.path.join(od, "run_metadata.json")))
    quality = open(os.path.join(od, "quality_log.txt")).read().strip()
    dates = [d.strftime("%Y-%m-%d") for d in levels.index]

    benchmarks = load_benchmarks(args.benchmarks_dir, levels.index)
    series, level_names = {}, []
    for code in ["CTIF-X", "CTIF-B", "CTIF-C", "CTIF-R"]:
        c, d, w = LINE[code]
        for var in ("TR", "PR"):
            series[f"{code}-{var}"] = series_entry(
                levels[f"{code}-{var}"], c, d, w,
                label={"CTIF-X": "Composite", "CTIF-B": "Builders",
                       "CTIF-C": "Components",
                       "CTIF-R": "Resources"}[code])
        level_names.append(f"{code}-{{V}}")
    for i, (name, tr) in enumerate(benchmarks.items()):
        c, d, w = BENCH_STYLES[i % len(BENCH_STYLES)]
        series[f"BM {name}"] = series_entry(tr, c, d, w, label=name)
        level_names.append(f"BM {name}")

    for code, label, _ in [("CTIF-X", "Composite", None)] + SLEEVES:
        s = levels[f"{code}-TR"]
        dd = s / s.cummax() - 1.0
        c, d, w = LINE[code]
        series[f"dd {label}"] = series_entry(dd, c, d, w, label=label)
    for (a, b), (c, d, w) in zip(
            [("CTIF-B", "CTIF-C"), ("CTIF-R", "CTIF-C"),
             ("CTIF-B", "CTIF-R")],
            [("#000000", None, 1.6), ("#555555", "7 4", 1.4),
             ("#000000", "2 3", 1.6)]):
        r = levels[f"{a}-TR"] / levels[f"{b}-TR"]
        series[f"ratio {a[5]}/{b[5]}"] = series_entry(
            100.0 * r / r.dropna().iloc[0], c, d, w,
            label=f"{a[5]} / {b[5]}")

    vol = rolling_vol(levels)
    for col in vol.columns:
        code = {"vol Composite": "CTIF-X", "vol Builders": "CTIF-B",
                "vol Components": "CTIF-C", "vol Resources": "CTIF-R"}[col]
        c, d, w = LINE[code]
        series[col] = series_entry(vol[col], c, d, w,
                                   label=col.replace("vol ", ""))
    corr = rolling_corr(levels)
    for col, (c, d, w) in zip(corr.columns,
                              [("#000000", None, 1.6),
                               ("#555555", "7 4", 1.4),
                               ("#000000", "2 3", 1.6)]):
        series[col] = series_entry(corr[col], c, d, w,
                                   label=col.replace("corr ", ""))

    comp_w = pd.read_csv(os.path.join(od, "weights_composite.csv"),
                         parse_dates=["date"]).set_index("date") \
        .reindex(levels.index)
    for col in comp_w.columns:
        c, d, w = LINE.get(col, ("#000", None, 1.4))
        series[f"mix {col}"] = series_entry(
            comp_w[col], c, d, w,
            label={"CTIF-B": "Builders", "CTIF-C": "Components",
                   "CTIF-R": "Resources"}.get(col, col))

    charts = [
        {"el": "ch-levels", "title": "CTIF index family",
         "series": level_names, "rebase": True, "kind": "level", "h": 360},
        {"el": "ch-dd", "title": "Drawdown",
         "series": [f"dd {l}" for _, l, _ in
                    [("CTIF-X", "Composite", None)] + SLEEVES],
         "rebase": False, "kind": "pct", "h": 260},
        {"el": "ch-ratio", "title": "Sleeve ratios, capex regime indicator",
         "series": ["ratio B/C", "ratio R/C", "ratio B/R"],
         "rebase": True, "kind": "level", "h": 260},
        {"el": "ch-vol", "title": "Rolling 63d annualized volatility",
         "series": list(vol.columns), "rebase": False, "kind": "pct",
         "h": 280},
        {"el": "ch-corr", "title": "Rolling 63d correlation",
         "series": list(corr.columns), "rebase": False, "kind": "pct",
         "h": 280, "dp": 0},
        {"el": "ch-mix", "title": "Composite sleeve mix",
         "series": [f"mix {c}" for c in comp_w.columns],
         "rebase": False, "kind": "pct", "h": 260},
    ]

    wt = {}
    for sleeve in ["builders", "components", "resources"]:
        wt[sleeve] = pd.read_csv(
            os.path.join(od, f"weights_{sleeve}.csv"),
            parse_dates=["date"]).set_index("date").iloc[-1].dropna()

    contrib = ("<p class='sub'>Contributors require --data-dir and "
               "--constituents (per-name returns). Rerun with both to "
               "enable this tab.</p>")
    if args.data_dir and args.constituents:
        log.info("Computing contribution attribution")
        contrib = contributions(args.data_dir, args.constituents, od, levels)

    rebopts, rebdivs = rebalance_archive(
        glob.glob(os.path.join(od, "rebalance_report_*.csv")))

    banner = ""
    if args.synthetic_banner:
        banner = ('<div class="banner">SYNTHETIC DEMONSTRATION DATA. '
                  'Prices are random walks generated by '
                  'make_synthetic_demo.py. Levels and returns have no '
                  'market meaning. This page demonstrates the production '
                  'pipeline end to end.</div>')

    stats_head = ("<tr><th>Series</th><th>Ann. return</th><th>Ann. vol</th>"
                  "<th>Max drawdown</th></tr>")
    stats_rows = "".join(
        f"<tr><td>{r.series}</td><td>{fmt_pct(r.ann_return)}</td>"
        f"<td>{fmt_pct(r.ann_vol, signed=False)}</td>"
        f"<td>{fmt_pct(r.max_drawdown)}</td></tr>"
        for r in stats.itertuples())

    html_out = PAGE.format(
        css=CSS, banner=banner, asof=dates[-1],
        base=meta.get("base_date", ""), ver=meta.get(
            "methodology_version", ""),
        ncon=meta.get("n_constituents", ""),
        nextreb=next_rebalance(levels.index[-1].date()),
        returns=returns_table(levels, benchmarks),
        benchnote=(" with benchmarks" if benchmarks else
                   " (no benchmark files found; fetch benchmarks to "
                   "overlay SPY, QQQ, SMH, XLU, RACK)"),
        stats=f"<table>{stats_head}{stats_rows}</table>",
        wb=weight_bars(wt["builders"]), wc=weight_bars(wt["components"]),
        wr=weight_bars(wt["resources"]), contrib=contrib,
        rebopts=rebopts, rebdivs=rebdivs,
        quality=html.escape(quality) if quality else "No quality events.",
        meta=html.escape(json.dumps(meta, indent=2)),
        data_json=json.dumps({"dates": dates, "series": series},
                             separators=(",", ":")),
        charts_json=json.dumps(charts), js=JS)

    with open(args.dashboard, "w") as f:
        f.write(html_out)
    log.info("Wrote %s (%.0f KB)", args.dashboard,
             os.path.getsize(args.dashboard) / 1024)
    return 0


if __name__ == "__main__":
    sys.exit(main())
