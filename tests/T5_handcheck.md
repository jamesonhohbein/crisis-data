# T5 hand check: 20 random panel rows against the raw source

Build of raw snapshots 2026-10-12 (wfp, fewsnet, jmmi, codab). Sample:
`panel_long.parquet.sample(20, random_state=20261012)`, sorted by source and
country. Each raw row was opened at its `raw_ref` (CSV line number, or XLSX
sheet, row and column) with openpyxl/csv under `python -I`, and price, unit and
month compared by eye with the panel row.

Unit for JMMI: Sudan per the unit row of the analysis tables (Sorghum, Sugar,
Ful masri: 1 kg); Yemen `calc_price_*` per the REACH Kobo form (kg, L). Month
for JMMI is the round month in the file name; FEWS NET and WFP use the row's
own date.

| # | source | country | market | item (raw) | raw ref | raw price, unit, month | panel price, unit, month | match |
|---|---|---|---|---|---|---|---|---|
| 1 | fewsnet | SDN | Ed Daein | Salt | marketpricefacts/sdn/2024-05.csv L72 | [withheld] SDG, kg, 2024-05-31 | [withheld], kg, 2024-05 | yes |
| 2 | fewsnet | SDN | Al-Fasher | Wheat Grain | marketpricefacts/sdn/2024-06.csv L40 | [withheld] SDG, kg, 2024-06-30 | [withheld], kg, 2024-06 | yes |
| 3 | fewsnet | YEM | Sa'ada (Governorate average) | Sorghum | marketpricefacts/yem/2023-01.csv L1190 | [withheld] YER, kg, 2023-01-31 | [withheld], kg, 2023-01 | yes |
| 4 | fewsnet | YEM | Ta'izz (Governorate average) | Gasoline | marketpricefacts/yem/2026-01.csv L406 | [withheld] YER, L, 2026-01-31 | [withheld], L, 2026-01 | yes |
| 5 | fewsnet | YEM | Hadramaut Coast (Area average) | Diesel | marketpricefacts/yem/2026-03.csv L305 | [withheld] YER, L, 2026-03-31 | [withheld], L, 2026-03 | yes |
| 6 | fewsnet | YEM | Amran (Governorate average) | Gasoline | marketpricefacts/yem/2023-06.csv L623 | [withheld] YER, L, 2023-06-30 | [withheld], L, 2023-06 | yes |
| 7 | fewsnet | YEM | Abyan (Governorate average) | Refined Vegetable Oil | marketpricefacts/yem/2024-11.csv L27 | [withheld] YER, L, 2024-11-30 | [withheld], L, 2024-11 | yes |
| 8 | fewsnet | YEM | Hajja (Governorate average) | Diesel | marketpricefacts/yem/2024-05.csv L698 | [withheld] YER, L, 2024-05-31 | [withheld], L, 2024-05 | yes |
| 9 | fewsnet | YEM | Al Ghaidha | Refined Vegetable Oil | marketpricefacts/yem/2024-05.csv L213 | [withheld] YER, L, 2024-05-31 | [withheld], L, 2024-05 | yes |
| 10 | fewsnet | YEM | Al Ghaidha | Wheat Grain | marketpricefacts/yem/2024-08.csv L221 | [withheld] YER, kg, 2024-08-31 | [withheld], kg, 2024-08 | yes |
| 11 | jmmi | SDN | Shia'ria (SD05148) | Ful masri price | November-2025 tables, Median price items (districts) R20C9 | [withheld] SDG, kg, Nov 2025 | [withheld], kg, 2025-11 | yes |
| 12 | jmmi | SDN | Al Galabat Al Gharbyah - Kassab (SD12078) | Sugar price | November-2025 tables, Median price items (districts) R58C21 | [withheld] SDG, kg, Nov 2025 | [withheld], kg, 2025-11 | yes |
| 13 | jmmi | SDN | Ad Diwaim (SD09044) | Sorghum price | February-2025 tables, Median price items (districts) R23C5 | [withheld] SDG, kg, Feb 2025 | [withheld], kg, 2025-02 | yes |
| 14 | jmmi | YEM | Salh (YE1519) | calc_price_lentil | round 67 September-2023, District Medians-Single R40C12 | [withheld] YER, kg, Sep 2023 | [withheld], kg, 2023-09 | yes |
| 15 | jmmi | YEM | Al Mukha (YE1505) | calc_price_rice | round 75 May-2024, District Medians-Single R10C9 | [withheld] YER, kg, May 2024 | [withheld], kg, 2024-05 | yes |
| 16 | jmmi | YEM | Kuhlan Ash Sharaf (YE1709) | calc_price_vegetable_oil | round 60 February-2023, District Medians-Single R19C24 | [withheld] YER, L, Feb 2023 | [withheld], L, 2023-02 | yes |
| 17 | jmmi | YEM | Dar Sad (YE2401) | calc_price_diesel | round 74 Apr-2024, District Medians-Single R34C19 | [withheld] YER, L, Apr 2024 | [withheld], L, 2024-04 | yes |
| 18 | wfp | SDN | El Gedarif | Sorghum | wfp_food_prices_sdn.csv L17972 | 1275 SDG, 3 KG, 2024-09-15 | 1275, 3 KG (factor 3), 2024-09 | yes |
| 19 | wfp | SDN | Wad Medani | Salt (iodised) | wfp_food_prices_sdn.csv L20816 | 1213 SDG, KG, 2025-11-15 | 1213, KG, 2025-11 | yes |
| 20 | wfp | YEM | Dhamar City | Beans (kidney red) | wfp_food_prices_yem.csv L50201 | 1200 YER, KG, 2023-09-15 | 1200, KG, 2023-09 | yes |

Result: 20 of 20 match on price, unit and month.

In this public copy the FEWS NET and REACH JMMI prices are withheld
(`[withheld]`): their terms do not permit redistribution without consent. The
check compared the actual values at each `raw ref`.
