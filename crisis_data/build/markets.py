"""Match source markets to OCHA COD-AB admin1/admin2.

Order of evidence:
  1. A hand resolution in ref/markets_review.csv (resolved_admin2_pcode or
     resolved_admin1_pcode filled in) always wins.
  2. Source-supplied pcodes that exist in COD-AB.
  3. Coordinates: point in admin2 polygon; a point just outside every polygon
     (coast, rounding) takes the nearest admin2 within NEAREST_MAX_KM.
  4. Fuzzy name: admin1 name against COD admin1, then admin2 name within that
     admin1. Below NAME_THRESHOLD goes to the review file unmatched.

Area averages (FEWS NET Yemen "X (Governorate average)", "Hadramaut Coast (Area
average)") are not places: their coordinates are placeholders (Lahj's falls in
Dhamar, Al Maharah's is 16,52). They match admin1 by name only and keep
admin2 empty (match_method "area_average").

Every market that is unmatched, below threshold, or whose coordinate match
lands in a different admin1 than the source's own admin1 name is written to
ref/markets_review.csv so a person can resolve it.
"""

from __future__ import annotations

import re
import unicodedata

import geopandas as gpd
import pandas as pd
from rapidfuzz import fuzz, process
from shapely.geometry import Point

from crisis_data.paths import REF

NAME_THRESHOLD = 85
NEAREST_MAX_KM = 15.0
REVIEW_PATH = REF / "markets_review.csv"
REVIEW_COLS = [
    "source", "country", "market_id_src", "market_name_src", "admin1_src", "admin2_src", "lat", "lon",
    "reason", "auto_admin1_pcode", "auto_admin2_pcode", "score", "resolved_admin1_pcode",
    "resolved_admin2_pcode", "note",
]

AREA_AVG = re.compile(r"\s*\((governorate|area) average\)\s*$", re.I)
# FEWS NET sub-governorate area names that fuzzy matching cannot place.
AREA_ADMIN1 = {"hadramaut valley": "Hadramawt", "hadramaut coast": "Hadramawt", "mareb": "Ma'rib",
               "amanat al asimah": "Sana'a City", "al dhale e": "Ad Dali'", "al baidha": "Al Bayda",
               "al hudaydah": "Al Hodeidah", "al mahweet": "Al Mahwit", "sa ada": "Sa'dah"}

_STOP = re.compile(r"\b(al|el|ad|ar|as|ash|at|az|governorate|state|city|district|rural|town|locality)\b")


def norm(s: str | None) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[`'’\-_/().,]", " ", s)
    s = _STOP.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def cod_frames(codab: dict) -> dict[str, dict[str, gpd.GeoDataFrame]]:
    out = {}
    for iso3, layers in codab.items():
        out[iso3] = {lvl: gpd.GeoDataFrame.from_features(gj["features"], crs="EPSG:4326") for lvl, gj in layers.items()}
    return out


def read_review() -> pd.DataFrame:
    if REVIEW_PATH.exists():
        df = pd.read_csv(REVIEW_PATH, dtype=str, keep_default_na=False)
        for c in REVIEW_COLS:
            if c not in df.columns:
                df[c] = ""
        return df[REVIEW_COLS]
    return pd.DataFrame(columns=REVIEW_COLS)


def _fuzzy(name: str, choices: dict[str, str]) -> tuple[str | None, float]:
    if not name or not choices:
        return None, 0.0
    hit = process.extractOne(norm(name), {k: norm(v) for k, v in choices.items()}, scorer=fuzz.token_sort_ratio)
    if not hit:
        return None, 0.0
    _, score, key = hit
    return key, float(score)


def match_markets(markets: pd.DataFrame, codab: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """markets: unique rows of source,country,market_id_src,market_name_src,admin1_src,admin2_src,
    admin1_pcode_src,admin2_pcode_src,lat,lon. Returns (matched, review_rows)."""
    cod = cod_frames(codab)
    review = read_review()
    resolved = {
        (r.source, r.country, r.market_id_src): r
        for r in review.itertuples()
        if r.resolved_admin1_pcode or r.resolved_admin2_pcode
    }
    out, rev = [], []
    for iso3, grp in markets.groupby("country", sort=True):
        a1, a2 = cod[iso3]["admin1"], cod[iso3]["admin2"]
        a1_names = dict(zip(a1.adm1_pcode, a1.adm1_name))
        a2_parent = dict(zip(a2.adm2_pcode, a2.adm1_pcode))
        a2_proj = a2.to_crs("EPSG:3857") if len(a2) else a2
        for m in grp.itertuples(index=False):
            key = (m.source, m.country, str(m.market_id_src))
            p1 = p2 = None
            method, score, reason = "", None, ""
            if key in resolved:
                r = resolved[key]
                p2 = r.resolved_admin2_pcode or None
                p1 = r.resolved_admin1_pcode or (a2_parent.get(p2) if p2 else None)
                method = "review"
            elif m.market_name_src and AREA_AVG.search(str(m.market_name_src)):
                area = AREA_AVG.sub("", str(m.market_name_src))
                area = AREA_ADMIN1.get(re.sub(r"[^a-z ]", " ", area.lower()).strip(), area)
                p1, s1 = _fuzzy(area, a1_names)
                method, score = "area_average", s1
                if s1 < NAME_THRESHOLD:
                    reason, p1, method = f"area average admin1 name score {s1:.0f}", None, ""
            elif m.admin2_pcode_src and m.admin2_pcode_src in a2_parent:
                p2, p1, method = m.admin2_pcode_src, a2_parent[m.admin2_pcode_src], "pcode"
            elif pd.notna(m.lat) and pd.notna(m.lon):
                pt = Point(float(m.lon), float(m.lat))
                hit = a2[a2.contains(pt)]
                if len(hit):
                    p2, method = hit.iloc[0].adm2_pcode, "coords"
                else:
                    ptp = gpd.GeoSeries([pt], crs="EPSG:4326").to_crs("EPSG:3857").iloc[0]
                    d = a2_proj.distance(ptp)
                    i = d.idxmin()
                    km = float(d.loc[i]) / 1000.0
                    if km <= NEAREST_MAX_KM:
                        p2, method, score = a2.loc[i, "adm2_pcode"], "coords_nearest", round(km, 2)
                    else:
                        reason = f"coords {km:.0f} km from nearest admin2"
                p1 = a2_parent.get(p2) if p2 else None
                if p1 and m.admin1_src:
                    _, s = _fuzzy(m.admin1_src, {p1: a1_names[p1]})
                    if s < 60:
                        best, bs = _fuzzy(m.admin1_src, a1_names)
                        if best != p1 and bs >= NAME_THRESHOLD:
                            reason = f"coords admin1 {a1_names[p1]} != source admin1 {m.admin1_src}"
            else:
                if m.admin1_pcode_src and m.admin1_pcode_src in a1_names:
                    p1, s1 = m.admin1_pcode_src, 100.0
                else:
                    p1, s1 = _fuzzy(m.admin1_src, a1_names)
                    if s1 < NAME_THRESHOLD:
                        reason, p1 = f"admin1 name score {s1:.0f}", None
                if p1:
                    sub = a2[a2.adm1_pcode == p1]
                    p2c, s2 = _fuzzy(m.admin2_src or m.market_name_src, dict(zip(sub.adm2_pcode, sub.adm2_name)))
                    if s2 >= NAME_THRESHOLD:
                        p2, method, score = p2c, "name", s2
                    else:
                        method, score = "name_admin1_only", s1
                        reason = f"admin2 name score {s2:.0f} (best {p2c})"
            out.append(dict(source=m.source, country=m.country, market_id_src=m.market_id_src,
                            admin1_pcode=p1, admin2_pcode=p2, match_method=method or "none", match_score=score))
            if reason and key not in resolved:
                rev.append(dict(source=m.source, country=m.country, market_id_src=str(m.market_id_src),
                                market_name_src=m.market_name_src, admin1_src=m.admin1_src, admin2_src=m.admin2_src,
                                lat="" if pd.isna(m.lat) else f"{m.lat}", lon="" if pd.isna(m.lon) else f"{m.lon}",
                                reason=reason, auto_admin1_pcode=p1 or "", auto_admin2_pcode=p2 or "",
                                score="" if score is None else f"{score}", resolved_admin1_pcode="",
                                resolved_admin2_pcode="", note=""))
    matched = pd.DataFrame(out)
    # keep earlier hand resolutions; refresh unresolved rows from this build
    keep = review[(review.resolved_admin1_pcode != "") | (review.resolved_admin2_pcode != "")]
    new = pd.DataFrame(rev, columns=REVIEW_COLS)
    merged = pd.concat([keep, new], ignore_index=True)
    merged = merged.drop_duplicates(["source", "country", "market_id_src"], keep="first")
    merged = merged.sort_values(["country", "source", "market_id_src"], kind="stable").reset_index(drop=True)
    return matched, merged


def write_review(review: pd.DataFrame) -> None:
    review.to_csv(REVIEW_PATH, index=False, lineterminator="\n")
