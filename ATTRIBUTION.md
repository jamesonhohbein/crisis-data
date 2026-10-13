# Attribution

The outputs in `data/out/` are derived works. Changes made to every source:
units normalized to kg / L / each, prices converted to USD with zone-specific
exchange rates, markets matched to OCHA COD-AB admin pcodes, and rows from
different sources merged by priority. License status per source is tracked in
`sources.yaml`; the publish gate refuses unverified sources.

- **WFP Food Prices** (Sudan, Yemen), World Food Programme, via the Humanitarian
  Data Exchange (HDX): https://data.humdata.org/dataset/wfp-food-prices-for-sudan,
  https://data.humdata.org/dataset/wfp-food-prices-for-yemen. License CC BY-IGO 3.0.
  Upstream sources named by HDX: FPMA, SIFSIA, MoPIC via FAO GIEWS, FSIS, WFP
  sub-offices, FSTS.
- **FEWS NET Data Warehouse**, Famine Early Warning Systems Network:
  https://fdw.fews.net/. Rows are sub-sourced (FAMIS Sudan, WFP, FAO/FSTS Yemen);
  the original data source of each row is kept in `sub_source`. Terms:
  https://help.fews.net/fdp/data-and-information-use-and-attribution-policy.
  FEWS NET does not endorse this use. License: unverified.
- **REACH Joint Market Monitoring Initiative (JMMI)**, REACH / IMPACT Initiatives
  with the Sudan Cash Working Group and the Yemen Cash and Markets Working Group:
  https://www.impact-initiatives.org/. License: unverified (site terms reserve
  reproduction rights).
- **COD-AB administrative boundaries**, OCHA, via HDX:
  https://data.humdata.org/dataset/cod-ab-sdn, https://data.humdata.org/dataset/cod-ab-yem.
  License CC BY-IGO 3.0.
- **Yemen areas of control** (2026-04-08), ACAPS Yemen Analysis Hub, via HDX:
  https://data.humdata.org/dataset/yemen-areas-of-control. License CC BY. Used
  for `ref/yemen_currency_zones.csv`.
