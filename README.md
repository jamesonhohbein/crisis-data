# crisis-data

A monthly market-price panel for Sudan and Yemen from 2023, built from three
public sources, plus a coverage table showing where and when each source
stopped reporting. Spec: `specs/001-conflict-market-prices.md`.

Phase P1 (this tree): fetch, build and tests, local only. Nothing is published.

## Run

```sh
uv sync
uv run crisis-data fetch          # all sources, into data/raw/<source>/<YYYY-MM-DD>/
uv run crisis-data build          # data/raw -> data/out
uv run crisis-data publish --dry-run   # license gate only; sends nothing
uv run pytest                     # T1-T8 (T1, T6 need a fetched snapshot)
```

A full fetch takes about 35 minutes, mostly FEWS NET (about 100 partition
requests, 1 s apart) and the IMPACT repository listing (10 s crawl delay).
Both fetchers resume an interrupted snapshot of the same date.

## Sources

| Source | What | License status |
|---|---|---|
| WFP VAM via HDX | market prices and market exchange rates, CSV | verified (CC BY-IGO 3.0) |
| FEWS NET Data Warehouse | market prices (status Collected) and exchange rates, CSV per country x month | unverified |
| REACH JMMI (IMPACT repository) | district median prices, XLSX per round | unverified |
| OCHA COD-AB via HDX | admin1/admin2 boundaries for matching | verified (CC BY-IGO) |
| ACAPS areas of control via HDX | Yemen district control, for currency zones | verified (CC BY) |

Details, quoted terms and measured gaps: `sources.yaml`. Attribution:
`ATTRIBUTION.md`.

## Outputs (`data/out/`)

- `panel_long`: one row per source observation, with admin pcodes, standard
  unit, local price, exchange rate and its provenance (`fx_source`), USD per
  standard unit, and `raw_ref` pointing at the exact raw file and row or cell.
- `panel_merged`: one row per place x commodity x month. Place is the admin2
  pcode, or the admin1 pcode for area averages. Source priority JMMI > WFP >
  FEWS NET; `disagree` when sources differ by more than 20% in USD.
- `coverage`, `coverage_summary`: source x country x admin1 x month counts of
  observed market-commodity pairs, last month seen, and a lost-since-2024 flag.
- `build_report.json`: row counts, every drop by reason, match rates, FX
  provenance counts, the Yemen zone check, JMMI files used and skipped.

## Crosswalks (`ref/`, committed, hand-maintained)

- `commodities.csv`: source item to staple; unmapped items are dropped and
  counted.
- `units.csv`: source unit to kg, L or each with a quantity factor.
- `yemen_currency_zones.csv`: district control (ACAPS 2026-04-08) to rial
  zone, with a source citation per row.
- `yemen_market_zones.csv`: per-market zone overrides where the district map
  cannot place a market, each with its evidence.
- `markets_review.csv`: markets that did not match COD-AB; fill
  `resolved_*` columns by hand and rebuild.

## Caveats

- Yemen has two rials about 3x apart. Every Yemen row carries
  `currency_zone`, and FX never crosses zones. Zones are time-invariant (one
  2026 control snapshot).
- Sudan FX is thin: WFP reports market rates for Khartoum and Port Sudan
  only, so most Sudan rows use a national median. Aug and Sep 2025 have no
  Sudan rate from any source; those rows have no USD price.
- FEWS NET Yemen prices are mostly governorate averages; they match admin1
  only.
- Raw downloads are untrusted data, parsed with csv, json and openpyxl
  (read-only, cached values) and never executed.
