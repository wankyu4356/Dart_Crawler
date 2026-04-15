# -*- coding: utf-8 -*-
"""M-unlisted-C — 비상장 법인 전용 파이프라인.

기본 흐름:
  1) fnlttSinglAcntAll 우선 시도 (외감 중 XBRL 제출한 곳은 응답 있음)
  2) 빈 응답이거나 연도가 부족하면 감사보고서 본문 → Claude 로 재무 추출
  3) 지배구조 역시 표준 API 시도 후 빈 응답이면 감사보고서 주석 파싱

반환 타입은 상장 파이프라인과 동일 (`FinancialsBundle`, `ShareholderBundle`) —
orchestrator / excel / html 재사용.
"""
from __future__ import annotations
from typing import Any, Callable, Dict, List, Optional

from . import audit_report as ar
from . import financials as fin_mod
from . import shareholders as sh_mod
from . import llm as llm_mod
from .financials import (
    BALANCE_KEYS, FinancialsBundle, PERFORMANCE_KEYS, YearFin,
    _empty_values,
)
from .shareholders import ShareholderBundle


LogFn = Callable[[str], None]


# ── LLM 결과 → YearFin 변환 ─────────────────────────────────────────────
def _yearfin_from_llm(d: Dict[str, Any]) -> Optional[YearFin]:
    """LLM 이 반환한 dict 1건을 YearFin 으로. year 가 없거나 모두 null 이면 None."""
    year = d.get("year")
    if not year:
        return None
    try:
        year = int(year)
    except (TypeError, ValueError):
        return None

    vals = _empty_values()
    # 직접 키 복사
    for k in ("revenue", "cost_of_sales", "gross_profit", "sga", "op_income",
              "dep", "amort", "net_income",
              "total_assets", "total_liabilities", "total_equity"):
        v = d.get(k)
        if isinstance(v, (int, float)):
            vals[k] = float(v)

    # 기본 파생 (gross_profit, da, ebitda, 마진)
    if vals["gross_profit"] is None and vals["revenue"] is not None and vals["cost_of_sales"] is not None:
        vals["gross_profit"] = vals["revenue"] - vals["cost_of_sales"]
    if vals["dep"] is not None or vals["amort"] is not None:
        vals["da"] = (vals["dep"] or 0.0) + (vals["amort"] or 0.0)
    if vals["op_income"] is not None and vals["da"] is not None:
        vals["ebitda"] = vals["op_income"] + vals["da"]

    rev = vals["revenue"]
    if rev:
        def pct(x):
            return (x / rev * 100.0) if x is not None else None
        vals["gpm"]     = pct(vals.get("gross_profit"))
        vals["opm"]     = pct(vals.get("op_income"))
        vals["ebitdam"] = pct(vals.get("ebitda"))
        vals["npm"]     = pct(vals.get("net_income"))

    if not any(v is not None for v in vals.values()):
        return None

    return YearFin(
        year=year,
        reprt_code="AUDIT",
        reprt_label="감사보고서",
        fs_div=(d.get("fs_div") or "CFS")[:3],
        currency="KRW",
        values=vals,
    )


# ── 메인 API ─────────────────────────────────────────────────────────────
def fetch_financials(
    corp_code: str,
    years_back: int = 4,
    client: Any = None,
    log: LogFn = print,
    disclosures=None,
) -> FinancialsBundle:
    """비상장 재무 번들.

    1) 표준 fnlttSinglAcntAll 시도 → annual 이 years_back 이상이면 바로 반환
    2) 부족하면 감사보고서 찾아 LLM 파싱 → 병합

    `disclosures` 가 주어지면 그 안에서 감사보고서를 먼저 찾음 (재호출 없이).
    못 찾거나 못 채우면 6년치 DART 재조회로 보강.
    """
    # 1) 표준 API 시도 (외감 중 일부 응답)
    std = fin_mod.fetch_all(corp_code, years_back=years_back)
    if len(std.annual) >= min(years_back, 2):
        log(f"  → 표준 API로 재무 확보 ({len(std.annual)}년)")
        return std

    log(f"  → 표준 API 응답 부족 ({len(std.annual)}년). 감사보고서 파싱으로 보강")
    prefetched = ar.disclosures_to_rows(disclosures) if disclosures else None
    reports = ar.find_latest_audit_reports(
        corp_code, n=3, prefetched_rows=prefetched, log=log,
    )
    # 수집된 범위에서 못 찾으면 6년치 재조회
    if not reports and prefetched is not None:
        log(f"    기수집 범위에 감사보고서 없음. DART 6년치 재조회")
        reports = ar.find_latest_audit_reports(corp_code, n=3, log=log)
    if not reports:
        log(f"  → 감사보고서 없음. 표준 결과 그대로 반환")
        return std

    merged_by_year: Dict[int, YearFin] = {y.year: y for y in std.annual}
    for r in reports:
        rcept_no = r.get("rcept_no") or ""
        if not rcept_no:
            continue
        log(f"    감사보고서 다운로드: {rcept_no} ({r.get('report_nm','')})")
        body = ar.fetch_audit_body(rcept_no)
        if not body:
            log(f"    본문 비어있음. 스킵")
            continue
        log(f"    본문 {len(body):,}자 → Claude 재무 추출 중")
        try:
            parsed = llm_mod.extract_financials_from_audit(body, client=client)
        except Exception as exc:  # noqa: BLE001
            log(f"    LLM 오류: {exc}")
            continue
        for d in parsed or []:
            yf = _yearfin_from_llm(d)
            if yf is None:
                continue
            if yf.year in merged_by_year:
                continue  # 이미 표준 API/이전 보고서에서 확보
            merged_by_year[yf.year] = yf

    annual = sorted(merged_by_year.values(), key=lambda y: y.year, reverse=True)[:years_back]
    log(f"  → 최종 {len(annual)}개년 재무 확보")
    return FinancialsBundle(annual=annual, latest_quarter=std.latest_quarter,
                            indicators=std.indicators)


def fetch_governance(
    corp_code: str,
    bgn_de: Optional[str] = None,
    end_de: Optional[str] = None,
    client: Any = None,
    log: LogFn = print,
    disclosures=None,
) -> ShareholderBundle:
    """비상장 지배구조 번들.

    1) 표준 API (hyslrSttus 등) 시도 — 외감인 경우 일부 응답
    2) 비어있거나 부족하면 최신 감사보고서 주석을 Claude 로 파싱
    """
    std = sh_mod.fetch_all(corp_code, bgn_de=bgn_de, end_de=end_de)
    has_data = bool(std.major or std.executives or std.dividends
                    or std.audit_opinion or std.minority)
    if has_data:
        log(f"  → 표준 API로 지배구조 확보")
        return std

    log(f"  → 표준 API 비어있음. 감사보고서 주석에서 지배구조 추출")
    prefetched = ar.disclosures_to_rows(disclosures) if disclosures else None
    reports = ar.find_latest_audit_reports(
        corp_code, n=1, prefetched_rows=prefetched, log=log,
    )
    if not reports and prefetched is not None:
        reports = ar.find_latest_audit_reports(corp_code, n=1, log=log)
    if not reports:
        return std
    rcept_no = reports[0].get("rcept_no") or ""
    if not rcept_no:
        return std
    body = ar.fetch_audit_body(rcept_no)
    if not body:
        return std

    log(f"    본문 {len(body):,}자 → Claude 지배구조 추출")
    try:
        parsed = llm_mod.extract_governance_from_audit(body, client=client)
    except Exception as exc:  # noqa: BLE001
        log(f"    LLM 오류: {exc}")
        return std

    bundle = ShareholderBundle()
    bundle.major             = list(parsed.get("major") or [])
    bundle.executives        = list(parsed.get("executives") or [])
    bundle.dividends         = list(parsed.get("dividends") or [])
    bundle.audit_opinion     = list(parsed.get("audit_opinion") or [])
    bundle.major_change      = []
    bundle.minority          = []
    bundle.major_stock       = []
    bundle.executive_stock   = []
    bundle.other_corp_invest = []
    try:
        bundle.source_year = int(parsed.get("source_year") or 0) or None
    except (TypeError, ValueError):
        bundle.source_year = None
    log(f"    → 최대주주 {len(bundle.major)} · 임원 {len(bundle.executives)} · "
        f"배당 {len(bundle.dividends)}")
    return bundle
