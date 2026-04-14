# -*- coding: utf-8 -*-
"""M4 — 재무정보 수집.

`fnlttSinglAcnt.json` 의 당기/전기/전전기 응답을 활용해 **최근 3~5년
연간 핵심계정** + **가장 최신 분기/반기** 값을 뽑는다.

주요 계정만 축약:
  - 매출액 / 영업이익 / 당기순이익 (IS)
  - 자산총계 / 부채총계 / 자본총계 (BS)

`fnlttSinglIndx.json` 로 수익성/안정성/성장성/활동성 지표도 병합.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional

from .config import REPRT_CODE
from . import dart_api as api


# ── 계정명 → 표준키 매핑 ─────────────────────────────────────────────────
ACCOUNT_MAP = {
    "매출액": "revenue",
    "수익(매출액)": "revenue",
    "영업수익": "revenue",
    "영업이익": "op_income",
    "영업이익(손실)": "op_income",
    "당기순이익": "net_income",
    "당기순이익(손실)": "net_income",
    "자산총계": "total_assets",
    "부채총계": "total_liabilities",
    "자본총계": "total_equity",
}

KEY_LABEL = {
    "revenue":           "매출액",
    "op_income":         "영업이익",
    "net_income":        "당기순이익",
    "total_assets":      "자산총계",
    "total_liabilities": "부채총계",
    "total_equity":      "자본총계",
}


@dataclass
class YearFin:
    year: int
    reprt_code: str         # 11011=사업 11012=반기 11013=1Q 11014=3Q
    reprt_label: str        # "사업보고서" 등
    fs_div: str = ""        # CFS/OFS (fnlttSinglAcnt 응답에 없음 — 빈 값 허용)
    currency: str = "KRW"
    values: Dict[str, Optional[float]] = field(default_factory=dict)


@dataclass
class FinancialsBundle:
    annual: List[YearFin] = field(default_factory=list)   # 최근 → 과거순
    latest_quarter: Optional[YearFin] = None              # 가장 최신 Q/반기
    indicators: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # indicators[연도|분기] = {idx_code: {"name": ..., "value": ...}}


# ── 파싱 헬퍼 ────────────────────────────────────────────────────────────
def _to_num(s: Any) -> Optional[float]:
    if s is None or s == "" or s == "-":
        return None
    try:
        return float(str(s).replace(",", ""))
    except ValueError:
        return None


def _extract_values(rows: List[Dict[str, Any]], period: str) -> Dict[str, Optional[float]]:
    """
    period:
      - 'thstrm'     : 당기금액 (분기보고서의 경우 3개월 금액)
      - 'thstrm_add' : 당기 누적
      - 'frmtrm'     : 전기금액 (연간 비교용)
      - 'bfefrmtrm'  : 전전기금액 (사업보고서에서만)
    """
    out: Dict[str, Optional[float]] = {k: None for k in KEY_LABEL}
    for row in rows:
        nm = (row.get("account_nm") or "").replace(" ", "")
        key = ACCOUNT_MAP.get(nm)
        if not key:
            continue
        field_name = {
            "thstrm":     "thstrm_amount",
            "thstrm_add": "thstrm_add_amount",
            "frmtrm":     "frmtrm_amount",
            "bfefrmtrm":  "bfefrmtrm_amount",
        }[period]
        v = _to_num(row.get(field_name))
        if v is not None and out.get(key) is None:
            out[key] = v
    return out


# ── 메인 API ────────────────────────────────────────────────────────────
def fetch_annual_financials(
    corp_code: str,
    years_back: int = 4,
    ref_year: Optional[int] = None,
) -> List[YearFin]:
    """가장 최근 `fnlttSinglAcnt`(사업보고서) 1건을 찾아, 그 응답의 전기/전전기
    필드까지 모아 최대 3개년 확보. `years_back`이 더 크면 이전 연도로 루프."""
    if ref_year is None:
        ref_year = date.today().year
    annual: List[YearFin] = []
    seen: set[int] = set()

    # 최근 사업보고서부터 역순으로 탐색
    for y in range(ref_year, ref_year - years_back - 2, -1):
        if len(annual) >= years_back:
            break
        rows = api.fnltt_singl_acnt(corp_code, str(y), REPRT_CODE["FY"])
        if not rows:
            continue
        thstrm_vals = _extract_values(rows, "thstrm")
        frmtrm_vals = _extract_values(rows, "frmtrm")
        bfefrmtrm_vals = _extract_values(rows, "bfefrmtrm")
        currency = next((r.get("currency") for r in rows if r.get("currency")), "KRW")

        if y not in seen and any(v is not None for v in thstrm_vals.values()):
            annual.append(YearFin(
                year=y, reprt_code=REPRT_CODE["FY"], reprt_label="사업보고서",
                currency=currency, values=thstrm_vals,
            ))
            seen.add(y)
        if (y - 1) not in seen and any(v is not None for v in frmtrm_vals.values()):
            annual.append(YearFin(
                year=y - 1, reprt_code=REPRT_CODE["FY"], reprt_label="사업보고서",
                currency=currency, values=frmtrm_vals,
            ))
            seen.add(y - 1)
        if (y - 2) not in seen and any(v is not None for v in bfefrmtrm_vals.values()):
            annual.append(YearFin(
                year=y - 2, reprt_code=REPRT_CODE["FY"], reprt_label="사업보고서",
                currency=currency, values=bfefrmtrm_vals,
            ))
            seen.add(y - 2)

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
    """당해년도 → 전년도 순으로 Q3→H1→Q1 순서로 탐색, 가장 최신 1건."""
    if ref_year is None:
        ref_year = date.today().year
    for y in (ref_year, ref_year - 1):
        for key, label in QUARTER_PRIORITY:
            rows = api.fnltt_singl_acnt(corp_code, str(y), REPRT_CODE[key])
            if not rows:
                continue
            vals = _extract_values(rows, "thstrm_add") or _extract_values(rows, "thstrm")
            if any(v is not None for v in vals.values()):
                currency = next((r.get("currency") for r in rows if r.get("currency")), "KRW")
                return YearFin(
                    year=y,
                    reprt_code=REPRT_CODE[key],
                    reprt_label=label,
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
    corp_code: str,
    bsns_year: str,
    reprt_code: str,
) -> Dict[str, List[Dict[str, Any]]]:
    """4개 그룹별로 지표 리스트 반환 (2023 3Q 이후부터 제공)."""
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
    """M4 최종: 연간 + 최신 분기 + 지표."""
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


def yoy(curr: Optional[float], prev: Optional[float]) -> Optional[float]:
    if curr is None or prev is None or prev == 0:
        return None
    return (curr - prev) / abs(prev) * 100.0
