# CTIF Changelog

Every discretionary decision gets a dated entry. No exceptions.

## 2026-07-20

- Migrated the entire data layer from Alpha Vantage to Alpaca (Market Data API).
  - New src/alpaca_source.py: raw + adjusted daily bars and /v1/corporate-actions,
    assembled into the unchanged engine schema (close, adjusted_close,
    dividend_amount, split_coefficient, OHLCV). Dual-source reconciliation in 8.1
    is preserved; the index math and validation suite are untouched.
  - Rewrote src/fetch_index_data.py and ctif_run.py fetch layer for Alpaca.
    Default feed is IEX (free); --feed sip / FEED="sip" for paid full coverage.
  - Shares outstanding is now a maintained input (shares.csv) rather than an API
    pull, since Alpaca has no fundamentals endpoint. src/fetch_shares.py became a
    no-network template/validator; populate shares from Bloomberg at each
    quarterly reference date (methodology section 10 updated).
  - Credentials are now ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY across
    refresh_data.py, streamlit_app.py, and the GitHub Actions workflow.
  - Fixed dark-mode invisibility of the Composite line in streamlit_app.py
    (Composite recolored from #111111 to #bfbfbf) and added line thickness to the
    dispersion and momentum signal charts.

## 2026-07-13  v1.0 initial construction
- Methodology v1.0 drafted: three sleeves plus composite, PR and TR versions,
  10 percent single-name cap, quarterly rebalance (ref 2nd Friday, effective
  3rd Friday close), base 100.00 on 2022-01-03.
- Initial universe: 53 core names (14 Builders, 20 Components, 19 Resources),
  25 watchlist names including the neocloud satellite list.
- Standing exclusions adopted: hyperscalers, model builders, NVDA/AMD
  accelerator layer, AI and EDA software, semicap and foundry, neoclouds.
- Engine validated: 39/39 checks in validate_math.py.
- Live date: not yet set. To be recorded at first production run.

## 2026-07-13  v1.0 delivery layer
- Added make_dashboard.py: single-file offline HTML dashboard (overview,
  weights, latest rebalance with cap flags, stats, quality log, methodology
  notes and back-cast disclosure).
- Added make_synthetic_demo.py: reproducible 53-name synthetic universe
  mirroring real structure for end-to-end pipeline demonstration.

## 2026-07-15  dashboard v2
- Interactive charts (crosshair readout, clickable legends, 1M/3M/6M/YTD/1Y/Max
  windows, rebase-to-window-start, log scale, PR/TR switch).
- Risk tab: rolling 63-day annualized volatility and sleeve correlations.
- Contributors tab: per-constituent arithmetic contribution (MTD/QTD/YTD).
- Rebalance archive: every report browsable with cap flags.
- Benchmark overlay support from --benchmarks-dir (AV format CSVs).
- Behavior verified headlessly in jsdom: rendering plus all interactions,
  zero runtime errors.
