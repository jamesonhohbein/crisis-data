# Architecture

crisis-data has two products sharing one publish step and one Pages site.

- 001, conflict-market price panel (`specs/001-conflict-market-prices.md`).
  P1 (fetch, build, tests), P2 (public bundle, site) and P3 (weekly refresh,
  alerts) are built. Only WFP prices are published; FEWS NET and REACH JMMI feed
  coverage counts only.
- 002, subnational cholera backfill 2024-25 (`specs/002-cholera-backfill.md`).
  P1 and P2 are built: Nigeria (NCDC sitreps) and Sudan (ArcGIS state layer) in
  the JHU schema, on `/cholera/`. South Sudan is excluded (no tables), so there
  is no cholera refresh: publish carries the last cholera build in
  `data/out/cholera/` into every bundle, including the weekly 001 refresh.

```mermaid
flowchart LR
  WFP[WFP VAM CSV] --> F[fetch]
  FEWS[FEWS NET API] --> F
  JMMI[REACH JMMI XLSX] --> F
  F --> RAW[(data/raw + sha256)]
  RAW --> B[build: units, FX, markets, commodities]
  REF[ref/ crosswalks + COD-AB] --> B
  B --> P[(data/out: panel_long, panel_merged, coverage, fx_rates)]
  P --> PUB[publish: license gate, WFP-only panel with WFP rates, coverage counts, audit]
  NCDC[NCDC sitrep PDFs] --> CF[cholera fetch]
  SDN[Sudan ArcGIS state layer] --> CF
  WHO[WHO adm0 weekly + HDX] --> CF
  JHU[JHU 2010-2023 OSF] --> CF
  CF --> CRAW[(data/raw/cholera + sha256)]
  CRAW --> CP[parse: Table 7 by glyph position, no LLM]
  CP --> CB[build: JHU schema, COD-AB pcodes, vintages, checks]
  CRAW --> CB
  CB --> CO[(data/out/cholera: cholera_admin1, checks, who_adm0_weekly)]
  CO --> PUB
  PUB --> BUN[(data/out/public: site + data + SHA256SUMS, cholera/ subtree)]
  BUN --> GH[gh-pages branch: prices page + /cholera/]
  T[crisis-data.timer, Sun 02:30] --> R[refresh: fetch, settle snapshot, build, publish]
  R --> F
  R --> M[(crisis_data.prom: per-source last new rows, run last ok)]
  M --> AL[claude-telemetry alerting: Crisis data folder]
```
