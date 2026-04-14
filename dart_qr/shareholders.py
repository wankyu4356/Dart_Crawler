# -*- coding: utf-8 -*-
"""M5 — 주주현황 수집.

수집 대상 (사업보고서 기반 + 수시공시 기반 혼합):
  1) 최대주주 현황         hyslrSttus
  2) 최대주주 변동 현황     hyslrChgSttus
  3) 소액주주 현황          mrhlSttus
  4) 대량보유 5% rule       majorstock  (누적, 기간 필터 직접)
  5) 임원·주요주주 소유     elestock    (누적, 기간 필터 직접)
  6) 임원 현황             exctvSttus
  7) 배당                 alotMatter
  8) 타법인출자            otrCprInvstmntSttus
  9) 감사의견              accnutAdtorNmNdAdtOpinion
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional

from .config import REPRT_CODE
from . import dart_api as api


@dataclass
class ShareholderBundle:
    major: List[Dict[str, Any]] = field(default_factory=list)
    major_change: List[Dict[str, Any]] = field(default_factory=list)
    minority: List[Dict[str, Any]] = field(default_factory=list)
    major_stock: List[Dict[str, Any]] = field(default_factory=list)   # 5% rule
    executive_stock: List[Dict[str, Any]] = field(default_factory=list)
    executives: List[Dict[str, Any]] = field(default_factory=list)
    dividends: List[Dict[str, Any]] = field(default_factory=list)
    other_corp_invest: List[Dict[str, Any]] = field(default_factory=list)
    audit_opinion: List[Dict[str, Any]] = field(default_factory=list)
    source_year: Optional[int] = None    # 사용된 최신 사업보고서 연도


# ── 최신 사업보고서 연도 자동 탐색 ────────────────────────────────────────
def find_latest_business_year(
    corp_code: str,
    ref_year: Optional[int] = None,
    max_back: int = 3,
) -> Optional[int]:
    """가장 최근 사업보고서(`11011`)가 존재하는 연도를 찾는다.

    `hyslrSttus` 를 프로빙 endpoint로 사용 — 본 용도에 적합.
    """
    if ref_year is None:
        ref_year = date.today().year
    for y in range(ref_year, ref_year - max_back - 1, -1):
        rows = api.major_holders(corp_code, str(y), REPRT_CODE["FY"])
        if rows:
            return y
    return None


# ── 전체 수집 ─────────────────────────────────────────────────────────
def fetch_all(
    corp_code: str,
    bgn_de: Optional[str] = None,
    end_de: Optional[str] = None,
    ref_year: Optional[int] = None,
) -> ShareholderBundle:
    """주주/지배구조 관련 섹션 일괄 수집.

    - `bgn_de`/`end_de` (YYYYMMDD) 지정 시 대량보유/임원소유는 접수일 필터.
    - 사업보고서 기반 9종은 자동으로 최신 연도 1건을 가져온다.
    """
    bundle = ShareholderBundle()
    yr = find_latest_business_year(corp_code, ref_year=ref_year)
    bundle.source_year = yr
    if yr is not None:
        y, r = str(yr), REPRT_CODE["FY"]
        bundle.major             = api.major_holders(corp_code, y, r)
        bundle.major_change      = api.major_holder_changes(corp_code, y, r)
        bundle.minority          = api.minority_holders(corp_code, y, r)
        bundle.executives        = api.executives(corp_code, y, r)
        bundle.dividends         = api.dividends(corp_code, y, r)
        bundle.other_corp_invest = api.other_corp_investment(corp_code, y, r)
        bundle.audit_opinion     = api.audit_opinion(corp_code, y, r)

    # 5% rule / 임원보유 — 전체 누적 응답에서 기간 필터
    ms = api.major_stock_report(corp_code)
    es = api.executive_stock_report(corp_code)
    if bgn_de and end_de:
        ms = [r for r in ms if _in_range(r.get("rcept_dt"), bgn_de, end_de)]
        es = [r for r in es if _in_range(
            (r.get("rcept_dt") or "").replace("-", ""), bgn_de, end_de)]
    bundle.major_stock = ms
    bundle.executive_stock = es
    return bundle


def _in_range(rcept_dt: Any, bgn: str, end: str) -> bool:
    s = (str(rcept_dt) if rcept_dt else "").replace("-", "")
    if not s:
        return False
    return bgn <= s <= end


# ── 요약 지표 ────────────────────────────────────────────────────────────
def top_holder_summary(major: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """최대주주 리스트에서 '본인' 또는 최상단 항목의 기말 지분율을 요약."""
    if not major:
        return None
    head = next(
        (r for r in major if (r.get("relate") or "").strip() == "본인"),
        major[0],
    )
    return {
        "name":       head.get("nm", ""),
        "relate":     head.get("relate", ""),
        "stock_knd":  head.get("stock_knd", ""),
        "bsis_rate":  _f(head.get("bsis_posesn_stock_qota_rt")),
        "trmend_rate": _f(head.get("trmend_posesn_stock_qota_rt")),
    }


def _f(s: Any) -> Optional[float]:
    if s in (None, "", "-"):
        return None
    try:
        return float(str(s).replace(",", ""))
    except ValueError:
        return None
