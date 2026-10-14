# Architecture

crisis-data 001, conflict-market price panel (spec `specs/001-conflict-market-prices.md`).
P1 (fetch, build, tests), P2 (public bundle, site) and P3 (weekly refresh,
alerts) are built. Only WFP prices are published; FEWS NET and REACH JMMI feed
coverage counts only.

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
  PUB --> BUN[(data/out/public: site + data + SHA256SUMS)]
  BUN --> GH[gh-pages branch, jamesonhohbein.github.io/crisis-data]
  T[crisis-data.timer, Sun 02:30] --> R[refresh: fetch, settle snapshot, build, publish]
  R --> F
  R --> M[(crisis_data.prom: per-source last new rows, run last ok)]
  M --> AL[claude-telemetry alerting: Crisis data folder]
```
