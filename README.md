# crisis-data

A monthly market-price panel for Sudan and Yemen from 2023, plus a coverage map
showing where and when each source stopped reporting. Also: subnational cholera
for Nigeria and Sudan, 2024-25, continuing the JHU dataset (spec 002).

**Site, map and downloads: https://jamesonhohbein.github.io/crisis-data/**
(cholera: https://jamesonhohbein.github.io/crisis-data/cholera/)

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
uv run crisis-data refresh        # weekly job: fetch, build, publish, freshness metrics
uv run pytest                     # T1-T8, T11, T12 (T1, T6 need a fetched snapshot)
CRISIS_DATA_T9_URL=https://jamesonhohbein.github.io/crisis-data/ \
  NODE_PATH=<dir with playwright> uv run pytest tests/test_p2.py -k t9   # T9, live site
```

A full fetch takes about 35 minutes, mostly FEWS NET (about 100 partition
requests, 1 s apart) and the IMPACT repository listing (10 s crawl delay).
Both fetchers resume an interrupted snapshot of the same date.

## Subnational cholera (spec 002)

```sh
uv run crisis-data cholera fetch     # NCDC PDFs, Sudan layer, WHO adm0, JHU, COD-AB, South Sudan sample
uv run crisis-data cholera build     # data/raw/cholera -> data/out/cholera
uv run crisis-data cholera publish   # same as `crisis-data publish`; the bundle includes cholera/
CRISIS_DATA_C9_URL=https://jamesonhohbein.github.io/crisis-data/cholera/ \
  NODE_PATH=<dir with playwright> uv run pytest tests/test_cholera.py -k c9   # C9, live site
```

- `cholera_admin1`: JHU columns and dtypes (read from the OSF release at build
  time) plus `country`, `admin1_pcode`, `obs_type` (weekly or cumulative),
  `source`, `source_doc`, `vintage`, `method`, `current`, `source_week`.
  Nigeria: 24 NCDC sitreps, 2024 W4 to 2025 W9, weekly and year-to-date rows
  per state. Sudan: the ArcGIS state layer, 2024 W30 to 2025 W45, weekly cases.
- `checks`: per country and week, state sum vs printed national row, cumulative
  continuity, WHO national comparison. Disagreements are shown, not corrected.
- `ref/cholera_handcheck.csv`: the C6 hand check (20 rows against the PDF page
  or the live layer).
- South Sudan is excluded: its sitreps have no county or state table
  (`sources.yaml`, `ssd_sitreps`). The series have ended, so nothing refreshes;
  publish carries the last build in `data/out/cholera/`.

## Weekly refresh (P3)

`systemd/crisis-data.timer` runs `crisis-data refresh` Sundays 02:30. Install:
`ln -s $PWD/systemd/crisis-data.{service,timer} ~/.config/systemd/user/ &&
systemctl --user daemon-reload && systemctl --user enable --now crisis-data.timer`.
A snapshot identical to the previous one is dropped, so an unchanged week
publishes nothing. Any failed step exits non-zero and publishes nothing. It
writes `~/.cache/borg/metrics/crisis_data.prom` (per-source fetch and
new-row timestamps, last good run) from `data/state/freshness.json`; the rules
are in `~/Software/claude-telemetry/alerting/97-crisis-data.yaml`.

## Sources

| Source | What | License status |
|---|---|---|
| WFP VAM via HDX | market prices and market exchange rates, CSV | verified (CC BY-IGO 3.0) |
| FEWS NET Data Warehouse | market prices (status Collected) and exchange rates, CSV per country x month | unverified |
| REACH JMMI (IMPACT repository) | district median prices, XLSX per round | unverified |
| OCHA COD-AB via HDX | admin1/admin2 boundaries for matching | verified (CC BY-IGO) |
| ACAPS areas of control via HDX | Yemen district control, for currency zones | verified (CC BY) |
| NCDC cholera sitreps (002) | Nigeria state tables, PDF | facts-attribution (no license stated, approved 2026-10-14) |
| Sudan cholera ArcGIS layer (002) | Sudan state weekly cases | facts-attribution (no license stated, approved 2026-10-14) |
| WHO cholera dashboard adm0 (002) | national weekly reference | verified (CC BY-IGO 3.0) |
| JHU cholera dataset, OSF 2ncf7 (002) | schema and 2010-2022 Nigeria | verified (CC BY 4.0) |

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
