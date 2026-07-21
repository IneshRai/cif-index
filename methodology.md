# CTIF Index Family Methodology
## Castellan Technology Infrastructure Family
### Version 1.0 (draft for internal review)

---

## 1. Overview and objective

The CTIF family measures the performance of US-listed companies supplying the physical buildout of AI computing capacity: the firms that construct, equip, connect, power, and house data centers. The family deliberately excludes the AI layer itself (model builders, hyperscalers, AI software, and the merchant GPU vendors) so that the index isolates the "AI inputs" supply chain rather than the AI trade.

The family consists of three sub-indexes and one composite, each published in a price return (PR) and total return (TR) version, for eight daily series in total:

| Code | Name | Description |
|---|---|---|
| CTIF-B | CTIF Builders | Construction, electrical and mechanical contracting, power and cooling equipment for the data center shell |
| CTIF-C | CTIF Components | Silicon, storage, optics, interconnect, and systems that ship into the racks |
| CTIF-R | CTIF Resources | Power generation, utilities, nuclear fuel, onsite power, and data center landlords |
| CTIF-X | CTIF Composite | Equal-sleeve blend of B, C, and R |

Each series carries a PR or TR suffix, for example CTIF-B-TR.

The primary intended uses are: (1) monitoring the AI buildout theme as a market segment, (2) attribution of strategy performance to the three legs of the buildout, and (3) a reference series for potential future overlay work. The index is a measurement tool. It is not an investment product and no live capital tracks it.

## 2. Governance

The index is maintained by the Castellan quant research group. All discretionary decisions (constituent additions and deletions, sleeve reassignments, corporate action treatments not covered by standing rules) are recorded in `CHANGELOG.md` with a date, the decision, and a one-line rationale. Nothing about the index may change without a changelog entry. Methodology changes require a version increment of this document.

## 3. Universe and eligibility

A security is eligible for the index if, at a quarterly review reference date, it meets all of the following:

1. **Listing.** Common stock, tracking stock, ADR, or REIT share with primary or full ordinary listing on NYSE, NYSE American, or Nasdaq. USD pricing.
2. **Size.** Float or full market capitalization of at least USD 2.0 billion for additions. Existing constituents are deleted if capitalization falls below USD 1.5 billion at a review (buffer prevents churn).
3. **Liquidity.** Three-month median daily traded value of at least USD 10 million.
4. **Seasoning.** At least 63 trading days of price history as of the reference date. Newly listed companies, spin-offs, and re-listings therefore enter no earlier than the first quarterly review after roughly three months of trading. Seasoning governs entrants at quarterly reviews; it does not apply to the initial constitution at the base date, where membership requires only that the security is listed. The committee may fast-track a very large spin-off (precedent: GE Vernova) with a changelog entry.
5. **Revenue linkage.** Meaningful revenue, backlog, or booked orders tied to data center and AI infrastructure capital spending, assessed from company segment disclosures. As a working standard, roughly 25 percent or more of revenue linked to the theme, or clear category leadership in a product line whose marginal buyer is a data center operator. This is a judgment criterion, applied by the committee and documented per name in `constituents.csv`.

**Standing exclusions.** The following are excluded regardless of revenue linkage, because including them turns the index into the AI trade it is meant to sit adjacent to:

- Hyperscalers and model builders (for example MSFT, GOOGL, AMZN, META, ORCL).
- The merchant AI accelerator layer (NVDA, AMD) and CPU/IP vendors whose value is dominated by the compute layer (ARM, QCOM in this context).
- AI and EDA software (for example CDNS, SNPS) and all pure software.
- Semiconductor capital equipment and foundry (for example AMAT, LRCX, KLAC, ASML, TSM). This is a v1 scope decision: semicap is a different cycle with existing benchmarks (SMH), and including it dilutes the buildout signature. Revisit at an annual review if desired.
- Neoclouds and GPU landlords (for example CRWV, NBIS) and miner conversions. These blur into being AI companies and carry crypto-adjacent volatility. They are tracked on the watchlist as a possible satellite series outside the composite.

## 4. Sleeve assignment

Every constituent belongs to exactly one sleeve, determined by its primary revenue driver, so the composite contains no double counting. Where a company spans sleeves (example: GEV sells both generation equipment and grid equipment), the committee assigns the sleeve matching the larger relevant business and records the rationale. Sleeve reassignments happen only at quarterly reviews with a changelog entry.

## 5. Review schedule

- **Quarterly reviews** align with rebalances (Section 7). At each review the committee re-affirms eligibility of all constituents, applies the deletion buffer, and considers watchlist promotions.
- An **annual deep review** each June re-examines the standing exclusions, sleeve definitions, and the parameters in this document (cap level, thresholds).
- **Between reviews** the constituent list changes only through corporate actions (Section 9). There are no discretionary intra-quarter additions.

## 6. Weighting

Within each sleeve, constituents are weighted by capped market capitalization:

1. Compute each member's market capitalization proxy at the reference date: split-adjusted closing price multiplied by shares outstanding (Section 10 describes the shares source and its known approximation).
2. Set initial weights proportional to capitalization.
3. Apply a **10 percent single-name cap** using the standard iterative procedure: any weight above 10 percent is set to 10 percent, the excess is redistributed pro rata across uncapped names, and the procedure repeats until no name exceeds the cap. Feasibility requires at least 10 members per sleeve, which all sleeves satisfy.

There are no group or sector caps within sleeves in v1. The composite level provides the diversification across economic segments.

## 7. Rebalancing schedule and mechanics

- **Frequency.** Quarterly.
- **Reference date.** Close of the second Friday of March, June, September, and December (or the prior trading day if that Friday is a holiday). All selection decisions and weight calculations use data as of the reference date. This gives five trading days of notice and guarantees no lookahead.
- **Effective date.** Close of the third Friday of the same month (or the prior trading day if a holiday). New membership and target weights are seeded at the effective date close and govern index returns from the next trading day forward.
- Between effective dates, membership and share counts are held fixed. Weights drift with prices, which is mathematically identical to holding a buy-and-hold portfolio of the constituents. There is no intra-quarter reweighting.

## 8. Index calculation

### 8.1 Constituent daily returns

For each constituent and trading day t, from raw close C, split coefficient k (shares multiply by k, price divides by k, k = 1 on normal days), and cash dividend per share d going ex on day t:

- Price return factor: `PR_i(t) = (C_t * k_t) / C_{t-1}`
- Total return factor: `TR_i(t) = (C_t * k_t + d_t) / C_{t-1}`

The engine independently computes the total return factor from the vendor's dividend-and-split adjusted close, `TR'_i(t) = AdjC_t / AdjC_{t-1}`, and reconciles the two. On days with no dividend and no split the price and total return are identical and the adjusted-close computation is used directly. Any reconciliation gap above 10 basis points on an event day is logged for manual review. This dual computation is the primary data-quality gate.

### 8.2 Sub-index levels

Let w_i(t) be the weight of member i at the start of day t. The daily index returns are weighted averages of constituent returns:

- `R_PR(t) = sum_i w_i(t) * (PR_i(t) - 1)`
- `R_TR(t) = sum_i w_i(t) * (TR_i(t) - 1)`

Levels chain multiplicatively from the base value: `L(t) = L(t-1) * (1 + R(t))`, with L(base date) = 100.00.

**Weight path.** Both the PR and TR versions of a sleeve use the same weight path, and that path evolves with price returns:

`w_i(t+1) = w_i(t) * PR_i(t) / sum_j w_j(t) * PR_j(t)`

with weights reset to the capped targets at each effective date close. This is the standard index-provider convention: the TR index represents the PR basket with all cash dividends reinvested pro rata across the entire basket on the ex-date, which leaves relative weights unchanged versus the price-only basket. Consequence: TR minus PR spread equals the compounded index dividend yield, and TR is always at or above PR from any common starting point.

### 8.3 Composite

CTIF-X holds the three sleeves at one-third each, reset at every quarterly effective date, drifting between resets. Composite daily returns are the sleeve-weight-averaged sleeve returns, with the composite weight path drifting on sleeve **price** returns for both the PR and TR versions (same convention as 8.2, one level up). Composite levels chain from 100.00 at the base date.

Equal thirds is the v1 design choice: it expresses "the buildout" rather than "whatever happens to be biggest" (an aggregate cap-weighted composite would be roughly two-thirds Components because of one or two mega-caps), and it makes sleeve attribution legible. The engine also outputs an aggregate cap-weighted composite as a supplementary diagnostic series so the two definitions can be compared.

## 9. Corporate actions

| Event | Treatment |
|---|---|
| Cash dividend (regular or special) | PR unaffected. TR reinvests per Section 8.2. |
| Stock split or reverse split | Neutral by construction via the split coefficient. |
| Merger or acquisition of a constituent (constituent is acquired) | The name remains through its final trading day at market prices. On the first day it no longer trades, its final weight is redistributed pro rata across remaining sleeve members (economically: proceeds reinvested in the sleeve). Logged automatically. |
| Constituent is the acquirer | No index action. Eligibility re-checked at the next review. |
| Spin-off by a constituent | The parent's price adjustment flows through vendor data. The spun entity is not automatically added; it enters the eligible universe under the seasoning rule. If the vendor's adjusted series and the raw series disagree materially on the ex-date, the reconciliation gate flags it and the committee documents the treatment in the changelog. |
| Delisting for distress | Same mechanics as acquisition: final traded price, then pro rata redistribution. If the final print is stale or halted, the committee may set a terminal value with a changelog entry. |
| Trading halt (temporary) | Price carried forward, zero return, logged. |
| Ticker change | Data series remapped, changelog entry, no economic effect. |

## 10. Data sources and known approximations

- **Prices, dividends, splits.** Alpaca Market Data API. Raw daily bars (`/v2/stocks/{symbol}/bars`, `adjustment=raw`) supply the raw close and OHLCV; the same endpoint with `adjustment=all` supplies the split- and dividend-adjusted close; the corporate-actions endpoint (`/v1/corporate-actions`) supplies per-event dividend amounts and split ratios. Raw close, adjusted close, dividend amount, and split coefficient are all stored so both return constructions in 8.1 can be computed and reconciled. The default feed is IEX (free); the SIP feed (full consolidated volume) requires a paid Alpaca subscription. Alpaca stock history extends back several years and corporate-actions coverage begins April 2020, both of which comfortably precede the 2022-01-03 base date.
- **Shares outstanding.** Maintained input (`shares.csv`), not an API field: Alpaca does not provide fundamentals. Shares are captured from Bloomberg at each quarterly reference date and held constant from that date forward (point-in-time from first production quarter), which is standard index practice and equivalent to the prior vendor-sourced approach. 
- **Back-cast approximation (disclosed).** For history before the first production quarter, current shares outstanding are held constant, and market caps use the split-adjusted price series so splits do not distort the proxy. This ignores historical buybacks and issuance. The distortion is small for mature payers and largest for heavy issuers; it affects relative weights at historical rebalances, not the return math. Documented here rather than hidden.
- **Calendar.** The trading calendar is the union of constituent trading dates. Nominal rebalance dates falling on holidays snap to the prior trading day.
- **Quality gates run on every calculation:** per-ticker reconciliation of the two TR constructions on event days; detection of interior missing days (carried forward, warned); detection of series that end early (treated as delistings, warned); zero or negative price detection; membership count per sleeve per quarter.

## 11. Base date, live date, and back-cast disclosure

- **Base date:** January 3, 2022, at 100.00 for all eight series. This start captures a full pre-ChatGPT year, the 2022 drawdown, and the subsequent buildout cycle.
- **Live date:** the date of the first production calculation run at Castellan (stamped in the output metadata). Everything before the live date is a **back-cast**.
- **Disclosure that travels with every output:** the back-cast selects the universe with full hindsight. The constituents are companies known in 2026 to have been the winners of the buildout. Back-cast levels and statistics therefore describe how the theme traded, and are useful for attribution and regime analysis. They are not an estimate of returns anyone could have earned, and they must never be presented as such. (Live illustration for internal reference: the MarketVector index underlying the VanEck RACK ETF showed a +51 percent back-filled quarter immediately before the fund's June 2026 launch, and the fund itself was slightly negative in its first weeks of live trading.)

New listings enter the back-cast only under the same seasoning rule that governs live operation (GEV, SNDK, CEG, TLN, OKLO, SMR, ALAB, CRDO all enter at their first eligible historical rebalance), so the back-cast mechanics match live mechanics exactly. The hindsight is in the selection, not the machinery, and the disclosure above is the honest statement of that.

## 12. Outputs

Every calculation run writes:

- `index_levels.csv`: one row per trading day, eight CTIF series plus the supplementary aggregate composite.
- `weights_<sleeve>.csv`: daily drifted weights per member (audit trail).
- `rebalance_report_<date>.csv`: membership, market cap proxies, uncapped and capped target weights for each rebalance.
- `stats_summary.csv`: annualized return, volatility, max drawdown, TR minus PR spread, per series, split by back-cast and live periods once a live period exists.
- `quality_log.txt`: every warning the gates produced.
- `run_metadata.json`: run timestamp, code version, data as-of date, base and live dates.

## 13. Parameters (single source of truth)

| Parameter | v1 value |
|---|---|
| Single-name cap | 10 percent |
| Rebalance months | Mar, Jun, Sep, Dec |
| Reference date | 2nd Friday close |
| Effective date | 3rd Friday close |
| Addition size floor | USD 2.0 billion |
| Deletion size floor | USD 1.5 billion |
| Liquidity floor | USD 10 million median daily value, 3 months |
| Seasoning | 63 trading days |
| Base date / value | 2022-01-03 / 100.00 |
| Composite sleeve weights | 1/3, 1/3, 1/3, quarterly reset |
| Reconciliation tolerance | 10 bps on event days |

Changing any parameter requires a methodology version increment and a changelog entry.
