# -*- coding: utf-8 -*-
"""M-unlisted-A — 감사보고서(외부감사 공시) 검색 + 본문 텍스트 추출.

비상장(외감) 법인은 사업보고서가 없어 `hyslrSttus` / `fnlttSinglAcntAll`
등이 대부분 비어 있다. 대신 **연 1회 감사보고서**(`pblntf_ty='F'`)를 제출하므로
이걸 찾아 문서 본문을 파싱해 재무/지배구조를 복구한다.

  • `find_latest_audit_reports(corp_code, n)` — 최근 N년치 감사보고서 접수번호
  • `fetch_audit_body(rcept_no, cap)` — document.xml ZIP → 본문 텍스트 (cap)
"""
from __future__ import annotations
from datetime import date, timedelta
from typing import Any, Dict, List

from . import dart_api as api
from .disclosures import fetch_body


def find_latest_audit_reports(
    corp_code: str,
    n: int = 3,
    lookback_years: int = 6,
) -> List[Dict[str, Any]]:
    """최근 N건의 감사보고서(외부감사 관련) raw 공시 dict.

    필터:
      - pblntf_ty == 'F'   (외부감사 관련)
      - report_nm 에 '감사보고서' 포함
      - 추천 우선순위: '연결 감사보고서' > '감사보고서' > 기타
    """
    today = date.today()
    bgn = (today - timedelta(days=365 * lookback_years)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    rows = api.iter_all_disclosures(corp_code, bgn, end)

    hits = []
    for r in rows:
        if (r.get("pblntf_ty") or "").upper() != "F":
            continue
        nm = r.get("report_nm") or ""
        if "감사보고서" in nm:
            hits.append(r)

    # 우선순위 정렬: 연결 > 별도, 접수일 최신순
    def _rank(r: Dict[str, Any]) -> tuple:
        nm = r.get("report_nm") or ""
        prio = 0
        if "연결" in nm:
            prio = 2
        elif "감사보고서" in nm:
            prio = 1
        return (-prio, -int(r.get("rcept_dt") or "0"))
    hits.sort(key=_rank)

    # 연도별 중복 제거 (최신 1개 유지)
    seen_years: set[str] = set()
    out: List[Dict[str, Any]] = []
    for h in hits:
        y = (h.get("rcept_dt") or "")[:4]
        if y in seen_years:
            continue
        seen_years.add(y)
        out.append(h)
        if len(out) >= n:
            break
    return out


def fetch_audit_body(rcept_no: str, cap: int = 80000) -> str:
    """disclosures.fetch_body 재사용 — ZIP → 모든 XML 텍스트 합본, cap."""
    return fetch_body(rcept_no, cap=cap)
