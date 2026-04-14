# -*- coding: utf-8 -*-
"""M4 — 재무정보 수집 (확장).

`fnlttSinglAcntAll.json` (CFS 우선, OFS fallback) 의 응답에서 IS/BS/CF
라인을 모두 스캔해 PE 분석에 필요한 KPI 를 추출:

  IS: 매출액 / 매출원가 / 매출총이익 / 판관비 / 영업이익 / 당기순이익
  CF: 감가상각비(D) / 무형자산상각비(A) → D&A → EBITDA 파생
  BS: 자산총계 / 부채총계 / 자본총계
  파생: GPM% / OPM% / EBITDAM% / NPM%

분기 보고서(11013/11012/11014) 도 동일 로직으로 thstrm_amount 또는
thstrm_add_amount(누적) 우선 사용.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional

from .config import REPRT_CODE
from . import dart_api as api


# ── 계정명 정규화 → key ─────────────────────────────────────────────────
# 공백 제거 후 비교 (DART 응답에 공백/괄호 표기가 다양함)
IS_ACCOUNT_MAP: Dict[str, str] = {
    "매출액":           "revenue",
    "수익(매출액)":      "revenue",
    "매출":             "revenue",
    "영업수익":          "revenue",
    "매출액(영업수익)":   "revenue",

    "매출원가":          "cost_of_sales",
    "용역원가":          "cost_of_sales",
    "영업비용":          "cost_of_sales",  # IS에 매출원가 없을 때 fallback (성격별 분류)

    "매출총이익":        "gross_profit",
    "매출총이익(손실)":   "gross_profit",
    "매출총손익":        "gross_profit",

    "판매비와관리비":     "sga",
    "판매비및관리비":     "sga",
    "판관비":            "sga",

    "영업이익":          "op_income",
    "영업이익(손실)":     "op_income",
    "영업손익":          "op_income",
    "영업손실(이익)":     "op_income",

    "당기순이익":         "net_income",
    "당기순이익(손실)":    "net_income",
    "당기순손익":         "net_income",
    "분기순이익":         "net_income",
    "반기순이익":         "net_income",
    "연결당기순이익":      "net_income",
    "연결당기순손익":      "net_income",
}

BS_ACCOUNT_MAP: Dict[str, str] = {
    "자산총계":          "total_assets",
    "부채총계":          "total_liabilities",
    "자본총계":          "total_equity",
}

# CF 에서 D&A 추출 패턴 (dart_fill_v10 검증 패턴 차용)
DEP_EXPLICIT_PATS = [
    "감가상각비에대한조정",
    "유형자산감가상각비",
    "유형자산상각비",
    "유형자산의감가상각비",
    "유무형자산감가상각비",
    "유형자산및무형자산상각비",
]
DEP_GENERAL_EXCL = ["무형", "사용권", "리스"]
AMORT_PATS = [
    "무형자산상각비에대한조정",
    "무형자산상각비",
    "무형자산의상각비",
    "무형자산상각",
    "무형자산및영업권상각",
    "영업권및무형자산상각",
]


# ── 표시 라벨 / 분류 ────────────────────────────────────────────────────
KEY_LABEL: Dict[str, str] = {
    "revenue":           "매출액",
    "cost_of_sales":     "매출원가",
    "gross_profit":      "매출총이익",
    "gpm":               "GPM",
    "sga":               "판관비",
    "op_income":         "영업이익",
    "opm":               "OPM",
    "dep":               "감가상각비 (D)",
    "amort":             "무형상각비 (A)",
    "da":                "D&A 합계",
    "ebitda":            "EBITDA",
    "ebitdam":           "EBITDAM",
    "net_income":        "당기순이익",
    "npm":               "NPM",
    "total_assets":      "자산총계",
    "total_liabilities": "부채총계",
    "total_equity":      "자본총계",
}

# 화면 표시 순서
PERFORMANCE_KEYS = [
    "revenue", "cost_of_sales", "gross_profit", "gpm",
    "sga", "op_income", "opm",
    "dep", "amort", "da", "ebitda", "ebitdam",
    "net_income", "npm",
]
BALANCE_KEYS = ["total_assets", "total_liabilities", "total_equity"]
PCT_KEYS = {"gpm", "opm", "ebitdam", "npm"}


# ── DTO ──────────────────────────────────────────────────────────────────
@dataclass
class YearFin:
    year: int
    reprt_code: str
    reprt_label: str
    fs_div: str = ""
    currency: str = "KRW"
    values: Dict[str, Optional[float]] = field(default_factory=dict)


@dataclass
class FinancialsBundle:
    annual: List[YearFin] = field(default_factory=list)
    latest_quarter: Optional[YearFin] = None
    indicators: Dict[str, Dict[str, Any]] = field(default_factory=dict)


# ── 파싱 / 추출 ──────────────────────────────────────────────────────────
def _to_num(s: Any) -> Optional[float]:
    if s is None or s == "" or s == "-":
        return None
    try:
        return float(str(s).replace(",", ""))
    except ValueError:
        return None


def _empty_values() -> Dict[str, Optional[float]]:
    return {k: None for k in (PERFORMANCE_KEYS + BALANCE_KEYS)}


def _set_first(d: Dict[str, Any], key: str, value: Any) -> None:
    if key not in d or d[key] is None:
        d[key] = value


def _extract_year_values(
    rows: List[Dict[str, Any]], period: str,
) -> Dict[str, Optional[float]]:
    """fnlttSinglAcntAll 의 list 응답에서 한 기간(thstrm/frmtrm/bfefrmtrm) 값 추출."""
    field_main = {
        "thstrm":     ["thstrm_amount", "thstrm_add_amount"],
        "frmtrm":     ["frmtrm_amount", "frmtrm_add_amount"],
        "bfefrmtrm":  ["bfefrmtrm_amount"],
    }[period]

    out = _empty_values()
    dep_total: Optional[float] = None
    amort_total: Optional[float] = None

    for row in rows:
        nm_raw = (row.get("account_nm") or "")
        nm = nm_raw.replace(" ", "").replace("\u3000", "")
        sj = (row.get("sj_div") or "").upper()
        v: Optional[float] = None
        for f in field_main:
            v = _to_num(row.get(f))
            if v is not None:
                break
        if v is None:
            continue

        if sj in ("IS", "CIS"):
            key = IS_ACCOUNT_MAP.get(nm)
            if key:
                _set_first(out, key, v)
        elif sj == "BS":
            key = BS_ACCOUNT_MAP.get(nm)
            if key:
                _set_first(out, key, v)
        elif sj == "CF":
            # CF 라인은 부호가 음수/양수 혼재 — 절댓값으로 누적
            av = abs(v)
            if any(p in nm for p in DEP_EXPLICIT_PATS):
                dep_total = (dep_total or 0.0) + av
            elif "감가상각" in nm and not any(ex in nm for ex in DEP_GENERAL_EXCL):
                if dep_total is None:
                    dep_total = av
            if any(p in nm for p in AMORT_PATS):
                amort_total = (amort_total or 0.0) + av

    out["dep"] = dep_total
    out["amort"] = amort_total

    # 파생: gross_profit
    if out["gross_profit"] is None and out["revenue"] is not None and out["cost_of_sales"] is not None:
        out["gross_profit"] = out["revenue"] - out["cost_of_sales"]
    # 파생: D&A
    if out["dep"] is not None or out["amort"] is not None:
        out["da"] = (out["dep"] or 0.0) + (out["amort"] or 0.0)
    # 파생: EBITDA = OP + D&A
    if out["op_income"] is not None and out["da"] is not None:
        out["ebitda"] = out["op_income"] + out["da"]

    # 마진
    rev = out["revenue"]
    if rev:
        def pct(x: Optional[float]) -> Optional[float]:
            return (x / rev * 100.0) if x is not None else None
        out["gpm"]      = pct(out.get("gross_profit"))
        out["opm"]      = pct(out.get("op_income"))
        out["ebitdam"]  = pct(out.get("ebitda"))
        out["npm"]      = pct(out.get("net_income"))

    return out


# ── fetch ────────────────────────────────────────────────────────────────
def _fetch_full_with_fallback(
    corp_code: str, bsns_year: str, reprt_code: str,
) -> tuple[List[Dict[str, Any]], str]:
    """CFS → OFS 순으로 fnlttSinglAcntAll 호출. (rows, fs_used)."""
    for fs in ("CFS", "OFS"):
        rows = api.fnltt_singl_acnt_all(corp_code, bsns_year, reprt_code, fs_div=fs)
        if rows:
            return rows, fs
    return [], ""


def fetch_annual_financials(
    corp_code: str,
    years_back: int = 4,
    ref_year: Optional[int] = None,
) -> List[YearFin]:
    if ref_year is None:
        ref_year = date.today().year
    annual: List[YearFin] = []
    seen: set[int] = set()
    for y in range(ref_year, ref_year - years_back - 2, -1):
        if len(annual) >= years_back:
            break
        rows, fs_used = _fetch_full_with_fallback(corp_code, str(y), REPRT_CODE["FY"])
        if not rows:
            continue
        currency = next((r.get("currency") for r in rows if r.get("currency")), "KRW")
        for period, year_offset in [("thstrm", 0), ("frmtrm", 1), ("bfefrmtrm", 2)]:
            yr = y - year_offset
            if yr in seen:
                continue
            vals = _extract_year_values(rows, period)
            if any(v is not None for v in vals.values()):
                annual.append(YearFin(
                    year=yr,
                    reprt_code=REPRT_CODE["FY"],
                    reprt_label="사업보고서",
                    fs_div=fs_used,
                    currency=currency,
                    values=vals,
                ))
                seen.add(yr)
    annual.sort(key=lambda f: f.year, reverse=True)
    return annual[:years_back]


QUARTER_PRIORITY = [
    ("Q3", "3분기보고서"),
    ("H1", "반기보고서"),
    ("Q1", "1분기보고서"),
]


def fetch_latest_quarterly(
    corp_code: str,
    ref_year: Optional[int] = None,
) -> Optional[YearFin]:
    if ref_year is None:
        ref_year = date.today().year
    for y in (ref_year, ref_year - 1):
        for key, label in QUARTER_PRIORITY:
            rows, fs_used = _fetch_full_with_fallback(corp_code, str(y), REPRT_CODE[key])
            if not rows:
                continue
            vals = _extract_year_values(rows, "thstrm")
            if any(v is not None for v in vals.values()):
                currency = next((r.get("currency") for r in rows if r.get("currency")), "KRW")
                return YearFin(
                    year=y,
                    reprt_code=REPRT_CODE[key],
                    reprt_label=label,
                    fs_div=fs_used,
                    currency=currency,
                    values=vals,
                )
    return None


INDICATOR_GROUPS = {
    "수익성": "M210000",
    "안정성": "M220000",
    "성장성": "M230000",
    "활동성": "M240000",
}


def fetch_indicators(
    corp_code: str, bsns_year: str, reprt_code: str,
) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for label, code in INDICATOR_GROUPS.items():
        items = api.fnltt_singl_indx(corp_code, bsns_year, reprt_code, code)
        if items:
            out[label] = items
    return out


def fetch_all(
    corp_code: str,
    years_back: int = 4,
    ref_year: Optional[int] = None,
) -> FinancialsBundle:
    annual = fetch_annual_financials(corp_code, years_back=years_back, ref_year=ref_year)
    latest_q = fetch_latest_quarterly(corp_code, ref_year=ref_year)

    indicators: Dict[str, Dict[str, Any]] = {}
    if annual:
        most = annual[0]
        ind = fetch_indicators(corp_code, str(most.year), most.reprt_code)
        if ind:
            indicators[f"{most.year} 사업보고서"] = ind
    if latest_q:
        ind = fetch_indicators(corp_code, str(latest_q.year), latest_q.reprt_code)
        if ind:
            indicators[f"{latest_q.year} {latest_q.reprt_label}"] = ind

    return FinancialsBundle(annual=annual, latest_quarter=latest_q, indicators=indicators)


# ── 포맷 헬퍼 ────────────────────────────────────────────────────────────
def format_krw(value: Optional[float]) -> str:
    if value is None:
        return "-"
    absv = abs(value)
    sign = "-" if value < 0 else ""
    if absv >= 1e12:
        return f"{sign}{absv/1e12:,.2f}조"
    if absv >= 1e8:
        return f"{sign}{absv/1e8:,.0f}억"
    if absv >= 1e4:
        return f"{sign}{absv/1e4:,.0f}만"
    return f"{sign}{absv:,.0f}"


def format_pct(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return f"{value:+.1f}%" if value < 0 else f"{value:.1f}%"


def format_value(key: str, value: Optional[float]) -> str:
    if value is None:
        return "-"
    if key in PCT_KEYS:
        return f"{value:.1f}%"
    return format_krw(value)


def yoy(curr: Optional[float], prev: Optional[float]) -> Optional[float]:
    if curr is None or prev is None or prev == 0:
        return None
    return (curr - prev) / abs(prev) * 100.0
