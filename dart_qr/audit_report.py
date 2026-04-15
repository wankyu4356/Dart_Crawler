# -*- coding: utf-8 -*-
"""M-unlisted-A — 감사보고서(외부감사 공시) 검색 + 본문 텍스트 추출.

비상장(외감) 법인은 사업보고서가 없어 `hyslrSttus` / `fnlttSinglAcntAll`
등이 대부분 비어 있다. 대신 **연 1회 감사보고서**(`pblntf_ty='F'`)를 제출하므로
이걸 찾아 문서 본문을 파싱해 재무/지배구조를 복구한다.

  • `find_latest_audit_reports(corp_code, n, prefetched_rows)` —
      prefetched_rows 가 있으면 재활용, 없으면 lookback 연도만큼 DART 재조회.
  • `fetch_audit_body(rcept_no, cap)` — document.xml ZIP → 본문 텍스트 (cap)
"""
from __future__ import annotations
from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Optional

from . import dart_api as api
from .disclosures import fetch_body


def find_latest_audit_reports(
    corp_code: str,
    n: int = 3,
    lookback_years: int = 6,
    prefetched_rows: Optional[List[Dict[str, Any]]] = None,
    log: Optional[Callable[[str], None]] = None,
) -> List[Dict[str, Any]]:
    """최근 N건의 감사보고서 raw 공시 dict.

    필터 완화: `report_nm` 에 '감사보고서' 만 포함하면 채택 (pblntf_ty 는
    참고용). 일부 비상장 응답에서 pblntf_ty 가 비정상 반환되는 사례 대응.
    """
    if prefetched_rows is not None:
        rows = prefetched_rows
        if log:
            log(f"    (기수집 공시 {len(rows)}건에서 감사보고서 탐색)")
    else:
        today = date.today()
        bgn = (today - timedelta(days=365 * lookback_years)).strftime("%Y%m%d")
        end = today.strftime("%Y%m%d")
        if log:
            log(f"    DART 공시조회 {bgn}~{end}")
        rows = api.iter_all_disclosures(corp_code, bgn, end)
        if log:
            log(f"    → 전체 {len(rows)}건 수신")

    hits = []
    for r in rows:
        nm = r.get("report_nm") or ""
        if "감사보고서" not in nm:
            continue
        if not r.get("rcept_no"):
            continue
        hits.append(r)

    if log:
        log(f"    → 감사보고서 후보 {len(hits)}건")

    # 우선순위 정렬: 연결 > 별도, 접수일 최신순
    def _rank(r: Dict[str, Any]) -> tuple:
        nm = r.get("report_nm") or ""
        prio = 2 if "연결" in nm else 1
        return (-prio, -int(str(r.get("rcept_dt") or "0").replace("-", "") or "0"))
    hits.sort(key=_rank)

    # 연도별 중복 제거 (최신 1개 유지)
    seen_years: set[str] = set()
    out: List[Dict[str, Any]] = []
    for h in hits:
        y = (str(h.get("rcept_dt") or "").replace("-", ""))[:4]
        if y in seen_years:
            continue
        seen_years.add(y)
        out.append(h)
        if len(out) >= n:
            break
    return out


def disclosures_to_rows(discs) -> List[Dict[str, Any]]:
    """orchestrator 에서 이미 수집한 Disclosure 객체 리스트를 raw dict 형태로."""
    out: List[Dict[str, Any]] = []
    for d in discs:
        out.append({
            "rcept_no": d.rcept_no,
            "rcept_dt": d.rcept_dt,
            "report_nm": d.report_nm,
            "pblntf_ty": d.pblntf_ty,
            "pblntf_detail_ty": d.pblntf_detail_ty,
            "flr_nm": d.flr_nm,
        })
    return out


def fetch_audit_body(rcept_no: str, cap: int = 80000) -> str:
    """disclosures.fetch_body 재사용 — ZIP → 모든 XML 텍스트 합본, cap."""
    return fetch_body(rcept_no, cap=cap)
