# Architecture

crisis-data 001, conflict-market price panel (spec `specs/001-conflict-market-prices.md`).
P1 (fetch, build, tests) is built; publish runs the license gate in `--dry-run`
only, and the weekly timer and alert rule are P3.

```mermaid
flowchart LR
  WFP[WFP VAM CSV] --> F[fetch]
  FEWS[FEWS NET API] --> F
  JMMI[REACH JMMI XLSX] --> F
  F --> RAW[(data/raw + sha256)]
  RAW --> B[build: units, FX, markets, commodities]
  REF[ref/ crosswalks + COD-AB] --> B
  B --> P[(panel_long, panel_merged, coverage)]
  P --> PUB[publish + license gate]
  PUB --> GH[GitHub repo + Pages site]
  T[weekly timer] --> F
  B -->|stale source| AL[claude-telemetry alerting]
```
