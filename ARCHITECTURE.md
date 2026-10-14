# Architecture

crisis-data 001, conflict-market price panel (spec `specs/001-conflict-market-prices.md`).
P1 (fetch, build, tests) and P2 (public bundle, site) are built. Only WFP prices
are published; FEWS NET and REACH JMMI feed coverage counts only. The weekly
timer and alert rule are P3.

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
  T[weekly timer, P3] -.-> F
  B -.->|stale source, P3| AL[claude-telemetry alerting]
```
