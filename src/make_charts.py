"""
make_charts.py

Chart pack from index_levels.csv. House style: black and white, Arial,
minimal ink. Falls back to Helvetica or the matplotlib default when Arial
is unavailable and logs the substitution.

Usage
  python src/make_charts.py --levels output/index_levels.csv \
      --output-dir output/charts
"""

import argparse
import logging
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import pandas as pd

log = logging.getLogger("ctif.charts")

STYLES = {"CTIF-X": ("black", "-", 1.8),
          "CTIF-B": ("black", "--", 1.1),
          "CTIF-C": ("black", ":", 1.1),
          "CTIF-R": ("black", "-.", 1.1)}
NAMES = {"CTIF-X": "Composite", "CTIF-B": "Builders",
         "CTIF-C": "Components", "CTIF-R": "Resources"}


def set_font():
    available = {f.name for f in font_manager.fontManager.ttflist}
    for candidate in ("Arial", "Helvetica", "Liberation Sans", "DejaVu Sans"):
        if candidate in available:
            plt.rcParams["font.family"] = candidate
            if candidate != "Arial":
                log.warning("Arial unavailable, using %s", candidate)
            return
    log.warning("No preferred font found, using matplotlib default")


def style_axes(ax):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.grid(axis="y", color="0.85", linewidth=0.6)
    ax.set_axisbelow(True)


def chart_levels(levels, out_dir, variant):
    fig, ax = plt.subplots(figsize=(9, 5))
    for code, (color, ls, lw) in STYLES.items():
        col = f"{code}-{variant}"
        if col in levels.columns:
            s = levels[col].dropna()
            ax.plot(s.index, s.values, color=color, linestyle=ls,
                    linewidth=lw, label=NAMES[code])
    ax.set_title(f"CTIF index family, {variant} versions "
                 f"(base 100.00 = 2022-01-03)")
    ax.set_ylabel("Index level")
    ax.legend(frameon=False)
    style_axes(ax)
    path = os.path.join(out_dir, f"ctif_levels_{variant.lower()}.png")
    fig.tight_layout()
    fig.savefig(path, dpi=300)
    plt.close(fig)
    log.info("Wrote %s", path)


def chart_drawdown(levels, out_dir):
    fig, ax = plt.subplots(figsize=(9, 4))
    for code, (color, ls, lw) in STYLES.items():
        col = f"{code}-TR"
        if col in levels.columns:
            s = levels[col].dropna()
            dd = s / s.cummax() - 1.0
            ax.plot(dd.index, dd.values, color=color, linestyle=ls,
                    linewidth=lw, label=NAMES[code])
    ax.set_title("Drawdown from running peak, TR versions")
    ax.set_ylabel("Drawdown")
    ax.yaxis.set_major_formatter(
        matplotlib.ticker.PercentFormatter(xmax=1.0))
    ax.legend(frameon=False, loc="lower left")
    style_axes(ax)
    path = os.path.join(out_dir, "ctif_drawdown.png")
    fig.tight_layout()
    fig.savefig(path, dpi=300)
    plt.close(fig)
    log.info("Wrote %s", path)


def chart_sleeve_ratios(levels, out_dir):
    """Relative strength ratios between sleeves, a capex regime indicator."""
    pairs = [("CTIF-B", "CTIF-C"), ("CTIF-R", "CTIF-C"), ("CTIF-B", "CTIF-R")]
    fig, ax = plt.subplots(figsize=(9, 4))
    linestyles = ["-", "--", ":"]
    for (a, b), ls in zip(pairs, linestyles):
        ca, cb = f"{a}-TR", f"{b}-TR"
        if ca in levels.columns and cb in levels.columns:
            ratio = (levels[ca] / levels[cb]).dropna()
            ratio = 100.0 * ratio / ratio.iloc[0]
            ax.plot(ratio.index, ratio.values, color="black", linestyle=ls,
                    linewidth=1.1, label=f"{NAMES[a]} / {NAMES[b]}")
    ax.set_title("Sleeve relative strength, rebased to 100")
    ax.set_ylabel("Ratio")
    ax.legend(frameon=False)
    style_axes(ax)
    path = os.path.join(out_dir, "ctif_sleeve_ratios.png")
    fig.tight_layout()
    fig.savefig(path, dpi=300)
    plt.close(fig)
    log.info("Wrote %s", path)


def main(argv=None):
    ap = argparse.ArgumentParser(description="CTIF chart pack")
    ap.add_argument("--levels", required=True)
    ap.add_argument("--output-dir", required=True)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    os.makedirs(args.output_dir, exist_ok=True)
    set_font()
    levels = pd.read_csv(args.levels, parse_dates=["date"]).set_index("date")
    chart_levels(levels, args.output_dir, "TR")
    chart_levels(levels, args.output_dir, "PR")
    chart_drawdown(levels, args.output_dir)
    chart_sleeve_ratios(levels, args.output_dir)
    log.info("Chart pack complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
