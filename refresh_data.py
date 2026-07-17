"""
refresh_data.py  -  daily data refresh for the CIF dashboard

Fetches current prices, computes the index, and writes a compact snapshot
into data_snapshot/ that the Streamlit app reads. Designed to run
unattended (GitHub Actions) once a day after the US close, so the deployed
dashboard loads instantly with no API key needed at view time and without
burning quota on every visitor.

The snapshot is small (a few CSVs) and is committed to the repo. Raw price
downloads go to the throwaway cache (ctif_cache/, gitignored); only the
computed snapshot is committed.

Locally:   python refresh_data.py
In CI:     the workflow sets ALPHAVANTAGE_API_KEY from repo secrets.
"""

import json
import os

import pandas as pd

import ctif_run as cif

SNAP = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "data_snapshot")


def main():
    key = os.environ.get("ALPHAVANTAGE_API_KEY")
    if key:
        cif.API_KEY = key
    if not cif.API_KEY or cif.API_KEY == "PUT_YOUR_ALPHAVANTAGE_KEY_HERE":
        raise SystemExit("No API key. Set ALPHAVANTAGE_API_KEY.")

    os.makedirs(SNAP, exist_ok=True)
    print("Fetching prices...")
    px, shares = cif.load_all()
    missing = [t for t, _ in cif.CONSTITUENTS if t not in px]
    if missing:
        print(f"WARNING: no data for {missing}; skipped.")

    print("Computing index...")
    levels, weights = cif.compute_index(px, shares, return_weights=True)
    bench = cif.benchmark_tr(px, levels.index)
    adj = pd.DataFrame({t: px[t]["adj_close"].reindex(levels.index).ffill()
                        for t, _ in cif.CONSTITUENTS if t in px})

    levels.to_csv(os.path.join(SNAP, "index_levels.csv"))
    bench.to_csv(os.path.join(SNAP, "bench.csv"))
    adj.to_csv(os.path.join(SNAP, "adj.csv"))
    for sleeve, w in weights.items():
        w.to_csv(os.path.join(SNAP, f"weights_{sleeve.lower()}.csv"))
    meta = {
        "asof": str(levels.index[-1].date()),
        "sleeve_of": {t: s for t, s in cif.CONSTITUENTS if t in px},
        "n_constituents": int(len(adj.columns)),
    }
    with open(os.path.join(SNAP, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)
    print(f"Snapshot written to {SNAP}, as of {meta['asof']}, "
          f"{meta['n_constituents']} names.")


if __name__ == "__main__":
    main()
