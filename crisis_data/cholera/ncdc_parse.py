"""Deterministic parser for NCDC cholera sitrep PDFs (Table 7, weekly and cumulative by state).

The PDFs are untrusted. pdfplumber reads glyph positions only; nothing in them is executed.
The table text is set non-upright, so pdfplumber's word grouping stacks columns; this module
rebuilds lines and tokens from characters, dedupes overprinted glyphs, and assigns tokens to
columns by the x position of the header words of the same table (one layout family, positions
read per document).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber

TITLE = "Summary table for Weekly"
INT = re.compile(r"^\d{1,3}(,\d{3})*$|^\d+$")
PCT = re.compile(r"^-?\d+(\.\d+)?%$")
PAREN_PCT = re.compile(r"^\(\d+(\.\d+)?%\)$")
ARROWS = {"▼", "▲"}


@dataclass
class Tok:
    text: str
    x0: float
    x1: float
    top: float

    @property
    def xc(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass
class Row:
    sn: int | None
    name: str
    status: str
    top: float
    week_cases: int | None = None
    week_deaths: int | None = None
    cum_cases: int | None = None
    cum_deaths: int | None = None
    cfr: str | None = None
    n_active: int | None = None
    raw: list[str] = field(default_factory=list)


@dataclass
class Issue:
    path: Path
    page: int
    year: int | None
    week: int | None
    states: list[Row]
    national: Row | None
    problems: list[str]


def _num(s: str) -> int:
    return int(s.replace(",", ""))


def _tokens(page) -> list[Tok]:
    """Characters -> tokens. Overprinted duplicates (same glyph within 0.6 pt) are dropped."""
    seen, chars = set(), []
    for c in page.chars:
        if not c["text"].strip():
            chars.append(c)
            continue
        key = (c["text"], round(c["x0"] / 0.6), round(c["top"] / 0.6))
        if key in seen:
            continue
        seen.add(key)
        chars.append(c)
    lines: list[list[dict]] = []
    for c in sorted(chars, key=lambda c: (c["top"], c["x0"])):
        if lines and abs(lines[-1][0]["top"] - c["top"]) <= 1.0:
            lines[-1].append(c)
        else:
            lines.append([c])
    toks: list[Tok] = []
    for ln in lines:
        ln.sort(key=lambda c: c["x0"])
        cur: list[dict] = []
        for c in ln:
            if not c["text"].strip():
                if cur:
                    toks.append(_mk(cur))
                cur = []
                continue
            if cur and (c["x0"] - cur[-1]["x1"] > 1.2 or c["text"] in ARROWS or cur[-1]["text"] in ARROWS):
                toks.append(_mk(cur))
                cur = []
            cur.append(c)
        if cur:
            toks.append(_mk(cur))
    return toks


def _mk(cs: list[dict]) -> Tok:
    return Tok("".join(c["text"] for c in cs), cs[0]["x0"], cs[-1]["x1"], min(c["top"] for c in cs))


def _find(toks: list[Tok], text: str) -> list[Tok]:
    return [t for t in toks if t.text == text]


def _columns(head: list[Tok]) -> dict[str, float]:
    """x centres of the columns, from the header tokens of this table."""
    cfr = _find(head, "CFR")
    if not cfr:
        raise ValueError("no CFR header")
    cfr_x = cfr[0].xc
    cases = sorted(_find(head, "Cases") + _find(head, "Cases,"), key=lambda t: t.xc)
    deaths = sorted(_find(head, "Deaths"), key=lambda t: t.xc)
    cum_cases = max((t for t in cases if t.xc < cfr_x), key=lambda t: t.xc)
    cum_deaths = max((t for t in deaths if t.xc < cfr_x), key=lambda t: t.xc)
    wk_cases = max((t for t in cases if t.xc < cum_cases.xc - 60), key=lambda t: t.top)
    wk_deaths = max((t for t in deaths if t.xc < cum_cases.xc - 60), key=lambda t: t.top)
    status = [t for t in head if t.text.startswith("outbreak") or t.text.startswith("status")]
    return {"status": status[0].xc if status else wk_cases.xc - 50, "wk_cases": wk_cases.xc,
            "wk_deaths": wk_deaths.xc, "cum_cases": cum_cases.xc, "cum_deaths": cum_deaths.xc, "cfr": cfr_x}


def _merge_tests(toks: list[Tok]) -> list[Tok]:
    """'12' '(50%)' -> one test token; '▼' '13%' -> one change token."""
    out: list[Tok] = []
    for t in toks:
        if out and re.fullmatch(r"[\d,]+", t.text) and re.fullmatch(r"[\d,]+", out[-1].text) \
                and t.x0 - out[-1].x1 < 3.0:
            # wide kerning inside one number ('1' '44' -> '144')
            p = out.pop()
            out.append(Tok(p.text + t.text, p.x0, t.x1, p.top))
        elif out and PAREN_PCT.match(t.text) and INT.match(out[-1].text):
            p = out.pop()
            out.append(Tok(p.text + " " + t.text, p.x0, t.x1, p.top))
        elif out and out[-1].text in ARROWS and PCT.match(t.text):
            p = out.pop()
            out.append(Tok(p.text + " " + t.text, p.x0, t.x1, p.top))
        else:
            out.append(t)
    return out


def _fill(row: Row, vals: list[Tok], col: dict[str, float], problems: list[str]) -> None:
    vals = _merge_tests(sorted(vals, key=lambda t: t.x0))
    row.raw = [t.text for t in vals]
    cfr = [t for t in vals if PCT.match(t.text) and abs(t.xc - col["cfr"]) < 15]
    if cfr:
        row.cfr = cfr[0].text
    for t in vals:
        if not (INT.match(t.text) or t.text == "-"):
            continue
        cands = ["wk_cases", "wk_deaths", "cum_cases", "cum_deaths"]
        if row.name == "National":
            cands.append("status")
        k = min(cands, key=lambda c: abs(t.xc - col[c]))
        if abs(t.xc - col[k]) > 25:
            problems.append(f"{row.name}: token {t.text!r} at x {t.xc:.0f} fits no column")
            continue
        v = 0 if t.text == "-" else _num(t.text)
        attr = {"status": "n_active", "wk_cases": "week_cases", "wk_deaths": "week_deaths"}.get(k, k)
        if getattr(row, attr) is not None:
            problems.append(f"{row.name}: two values for {attr}: {getattr(row, attr)} and {v}")
        setattr(row, attr, v)


def parse(path: Path) -> Issue:
    problems: list[str] = []
    with pdfplumber.open(path) as pdf:
        pages = list(pdf.pages)
        for pno, page in enumerate(pages, start=1):
            toks = _tokens(page)
            title = [t for t in toks if t.text == "Summary"]
            title = [t for t in title if any(u.text == "Weekly" and abs(u.top - t.top) < 2 for u in toks)]
            if not title:
                continue
            ttop = title[0].top
            ylabel = [t for t in toks if abs(t.top - ttop) < 2 and re.fullmatch(r"20\d\d", t.text)]
            year = int(ylabel[0].text) if ylabel else None
            if not [t for t in toks if t.text == "National" and t.top > ttop] and pno < len(pages):
                # title at the foot of one page, table on the next (2024 W30)
                pno, toks, ttop = pno + 1, _tokens(pages[pno]), 0.0
            return _table(path, pno, year, toks, ttop, problems)
    return Issue(path, 0, None, None, [], None, ["no Table 7 page"])


def _table(path: Path, pno: int, year: int | None, toks: list[Tok], ttop: float, problems: list[str]) -> Issue:
    nat = [t for t in toks if t.text == "National" and t.top > ttop]
    if not nat:
        problems.append("no National row")
        return Issue(path, pno, year, None, [], None, problems)
    ntop = nat[0].top
    body = [t for t in toks if ttop + 3 < t.top < ntop + 4]
    # state rows: a serial number at the far left followed by a name
    sn = [t for t in body if INT.match(t.text) and t.x1 < 75 and t.top > ttop + 30]
    sn.sort(key=lambda t: t.top)
    first = sn[0].top if sn else ntop
    head = [t for t in body if t.top < first - 2]
    wk = re.search(r"Week\s*:?\s*\(?\s*Week\s*(\d+)\s*\)|\(\s*Week\s*(\d+)\s*\)",
                   " ".join(t.text for t in sorted(head, key=lambda t: (round(t.top), t.x0))))
    week = int(wk.group(1) or wk.group(2)) if wk else None
    col = _columns(head)
    rows: list[Row] = []
    name_x1 = col["status"] - 20
    for s in sn:
        name = [t for t in body if abs(t.top - s.top) < 2.5 and s.x1 < t.x0 and t.x1 < name_x1]
        status = [t for t in body if abs(t.top - s.top) < 2.5 and t.text == "Active"]
        rows.append(Row(int(s.text), " ".join(t.text for t in sorted(name, key=lambda t: t.x0)),
                        "Active" if status else "", s.top))
    nrow = Row(None, "National", "", ntop)
    anchors = rows + [nrow]
    assigned: dict[int, list[Tok]] = {i: [] for i in range(len(anchors))}
    for t in body:
        if t.x0 < name_x1 or t.text == "Active":
            continue
        i = min(range(len(anchors)), key=lambda i: abs(anchors[i].top - t.top))
        if abs(anchors[i].top - t.top) <= 3.5:
            assigned[i].append(t)
    for i, r in enumerate(anchors):
        _fill(r, assigned[i], col, problems)
    keep = []
    for r in rows:
        if r.name in ("#N/A", ""):
            problems.append(f"row {r.sn}: placeholder row {r.name!r} dropped")
        else:
            keep.append(r)
    if "#N/A" in nrow.raw:
        problems.append("National row carries #N/A")
    return Issue(path, pno, year, week, keep, nrow, problems)
