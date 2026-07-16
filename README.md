# CIF: Castellan Technology Infrastructure Family

A proprietary index family measuring the AI buildout supply chain: the
companies that construct, equip, connect, power, and house data centers,
deliberately excluding the AI compute layer itself. Three cap-weighted
sleeves (Builders, Components, Resources), an equal-sleeve composite, and a
diagnostic aggregate composite, each in price return (PR) and total return
(TR) versions.

The rules live in `methodology.md`. Nothing about the index changes without
a `CHANGELOG.md` entry.

## Two ways to run

**A. One-file quick look (recommended first).** `ctif_run.py` fetches real
prices, caches them locally, computes the index, and shows matplotlib charts
including a 1-year comparison against SPY, QQQ, SMH, and XLU. Best for seeing
results fast in PyCharm.

**B. Full pipeline.** The `src/` modules are the reference implementation,
with a 39-check validation suite. Use these for anything that will be shown
to others.

## Setup in PyCharm

1. Open this folder as a PyCharm project.
2. Create a virtual environment when prompted (or Settings > Project >
   Python Interpreter > Add > Virtualenv).
3. Install dependencies: PyCharm will offer to install from
   `requirements.txt`, or run `pip install -r requirements.txt`.
4. Get a free or premium Alpha Vantage API key from
   https://www.alphavantage.co/support/#api-key

## Run option A: one-file runner

Open `ctif_run.py`, set `API_KEY` at the top, then right-click > Run.
First run downloads and caches ~57 tickers (about a minute on premium);
later runs read the cache and are instant. Three charts appear plus a
printed 1-year return table, and `ctif_levels.csv` is written.

## Run option B: full pipeline

```
python src/validate_math.py
python src/fetch_index_data.py --constituents constituents.csv --output-dir data/raw --outputsize full
python src/fetch_shares.py --constituents constituents.csv --output shares.csv
python src/fetch_index_data.py --tickers SPY QQQ SMH XLU RACK DTCR --output-dir data/benchmarks --outputsize full
python src/compute_index.py --data-dir data/raw --constituents constituents.csv --shares shares.csv --output-dir output --base-date 2022-01-03
python src/make_dashboard.py --output-dir output --dashboard output/dashboard.html --data-dir data/raw --constituents constituents.csv --benchmarks-dir data/benchmarks
python src/weekly_report.py --levels output/index_levels.csv --output output/weekly_report.md
python src/make_charts.py --levels output/index_levels.csv --output-dir output/charts
```

Set your key first: `export ALPHAVANTAGE_API_KEY=your_key` (macOS/Linux) or
`set ALPHAVANTAGE_API_KEY=your_key` (Windows), or pass `--api-key`.

The fetch step verifies every ticker and reports any symbol that fails
(rename, delisting) before the index computes. Always read
`output/quality_log.txt` after a run.

## Offline demo (no API key)

Exercises the entire pipeline on clearly labeled synthetic data:

```
python src/make_synthetic_demo.py --output-dir demo_synthetic
python src/compute_index.py --data-dir demo_synthetic/raw --constituents demo_synthetic/constituents.csv --shares demo_synthetic/shares.csv --output-dir demo_synthetic/output --base-date 2022-01-03
python src/make_dashboard.py --output-dir demo_synthetic/output --dashboard demo_synthetic/output/dashboard.html --data-dir demo_synthetic/raw --constituents demo_synthetic/constituents.csv --benchmarks-dir demo_synthetic/benchmarks --synthetic-banner
```

## Important: back-cast disclosure

History before your first production run is a back-cast. The universe is
selected with hindsight (2026's known buildout winners), so it will tend to
look like it beat the market by a wide margin. That gap is mostly selection,
not skill. The sleeve-versus-sector-benchmark comparisons (Components vs SMH,
Resources vs XLU) are more informative than the headline CTIF-vs-SPY line.

## What not to commit

`.gitignore` already excludes the price cache, downloaded data, generated
outputs, and your virtual environment. Do not commit your API key: it lives
in `ctif_run.py` locally, so if you ever hardcode it, do not push that
change. Prefer setting it as an environment variable.

## Files

```
ctif_run.py            one-file runner: fetch, cache, compute, charts
constituents.csv       53-name universe with sleeve assignments
watchlist.csv          near-miss names and the neocloud satellite list
methodology.md         the rulebook
CHANGELOG.md           dated log of every discretionary decision
requirements.txt       dependencies
src/compute_index.py     calculation engine
src/validate_math.py     39-check validation suite
src/fetch_index_data.py  Alpha Vantage price fetcher
src/fetch_shares.py      shares outstanding fetcher
src/make_dashboard.py    interactive HTML dashboard
src/weekly_report.py     one-page markdown summary
src/make_charts.py       black and white chart pack
src/make_synthetic_demo.py  synthetic universe for offline demos
```
