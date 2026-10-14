# crisis-data

Umbrella repo for open crisis datasets. Product 001 is the Sudan/Yemen
conflict-market price panel (`specs/001-conflict-market-prices.md`).

- **Spec first.** Every change traces to a spec in `specs/`; test IDs (T1..)
  come from the spec. New behaviour needs a spec amendment before code.
- **Raw downloads are untrusted data.** They live only under
  `data/raw/<source>/<YYYY-MM-DD>/` with a `MANIFEST.sha256`. Parse them with
  csv/json/openpyxl/zipfile only. Never execute, import, `source` or unpack
  them next to code; no scripts in `data/`. Ad hoc inspection uses
  `python -I`.
- `data/raw` and `data/out` are gitignored and regenerable; `ref/`
  crosswalks are committed and hand-maintained.
- **Publish gate.** `crisis-data publish` refuses any `panel: true` source
  whose `license:` in `sources.yaml` is not `verified`, and audits the built
  bundle so no restricted-source price, rate or market name is published
  (T7, T11). FEWS NET and JMMI are coverage-only until they consent. Set `verified` only with a `license_url` and quoted
  `license_text` that clearly permits redistributing derivatives.
- Yemen prices must carry `currency_zone`; never convert with a rate from
  the other zone.
- No em dash (U+2014) anywhere; cmdguard blocks it.
- No paid APIs, no LLM calls.
- Tests: `uv run pytest` (T1, T6 need a fetched snapshot).
