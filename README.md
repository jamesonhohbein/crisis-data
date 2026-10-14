# crisis-data

A monthly market-price panel for Sudan and Yemen from 2023, plus a coverage map
showing where and when each source stopped reporting.

**Site, map and downloads: https://jamesonhohbein.github.io/crisis-data/**

Spec: `specs/001-conflict-market-prices.md`. Built from three sources; only WFP
prices are published (CC BY-IGO). FEWS NET and REACH JMMI terms forbid
redistribution without consent, so they contribute observation counts to the
coverage map and nothing else. Code MIT (`LICENSE`), data CC BY 4.0
(`LICENSE-DATA`, `ATTRIBUTION.md`).

## Run

```sh
uv sync
uv run crisis-data fetch          # all sources, into data/raw/<source>/<YYYY-MM-DD>/
uv run crisis-data build          # data/raw -> data/out
uv run crisis-data publish --dry-run   # gate, build + audit data/out/public; sends nothing
uv run crisis-data publish        # same, then push the bundle to the gh-pages branch
uv run pytest                     # T1-T8, T11 (T1, T6 need a fetched snapshot)
CRISIS_DATA_T9_URL=https://jamesonhohbein.github.io/crisis-data/ \
  NODE_PATH=<dir with playwright> uv run pytest tests/test_p2.py -k t9   # T9, live site
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

## Published bundle (`data/out/public/`, deployed to `gh-pages`)

- `panel_public`: WFP rows of `panel_long`, USD recomputed from WFP exchange
  rates only. Rows with no WFP rate keep `price_usd` empty.
- `coverage`, `coverage_summary`: all three sources, counts and months only.
- `yemen_zone_counts`: staple observations per source x currency zone x month.
- `admin1.geojson` (simplified COD-AB), `SHA256SUMS`, the map and method note.

## Local outputs (`data/out/`, never published)

- `panel_long`: one row per source observation, with admin pcodes, standard
  unit, local price, exchange rate and its provenance (`fx_source`), USD per
  standard unit, and `raw_ref` pointing at the exact raw file and row or cell.
- `panel_merged`: one row per place x commodity x month. Place is the admin2
  pcode, or the admin1 pcode for area averages. Source priority JMMI > WFP >
  FEWS NET; `disagree` when sources differ by more than 20% in USD.
- `coverage`, `coverage_summary`: source x country x admin1 x month counts of
  observed market-commodity pairs, last month seen, and a lost-since-2024 flag.
- `fx_rates`: every exchange rate the build could use, all sources.
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
