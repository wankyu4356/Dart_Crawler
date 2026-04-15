# -*- coding: utf-8 -*-
"""M-biz — Business Profile (회사 개요) 수집.

최근 사업보고서(상장) 또는 감사보고서(비상장) 본문에서
  • 회사 개요 (무엇을 하는 회사인가)
  • 주요 제품/서비스
  • 사업부별 매출·영업이익률
  • 주요 매출처·매입처
  • 투자 포인트
를 LLM 으로 구조화 추출.
"""
from __future__ import annotations
from typing import Any, Callable, Dict, List, Optional

from . import audit_report as ar
from . import llm as llm_mod
from .disclosures import Disclosure, fetch_body


LogFn = Callable[[str], None]


# 상장사 공시 중 사업 내용이 풍부한 보고서 우선순위
REPORT_PRIORITY = [
    ("사업보고서", 4),
    ("반기보고서", 2),
    ("분기보고서", 1),
]


def _report_priority(report_nm: str) -> int:
    for name, p in REPORT_PRIORITY:
        if name in report_nm:
            return p
    return 0


def pick_source_report(
    discs: List[Disclosure],
    is_listed: bool,
    corp_code: str,
    log: Optional[LogFn] = None,
) -> Optional[Dict[str, Any]]:
    """상장: 최신 사업보고서 > 반기 > 분기. 비상장: 최신 감사보고서.

    반환: {"rcept_no","rcept_dt","report_nm"} dict 또는 None.
    """
    if is_listed:
        cands = [d for d in discs if _report_priority(d.report_nm) > 0]
        if not cands:
            if log:
                log("  (정기보고서 없음)")
            return None
        cands.sort(key=lambda d: (-_report_priority(d.report_nm),
                                  -int(str(d.rcept_dt or "0").replace("-", ""))))
        c = cands[0]
        return {"rcept_no": c.rcept_no, "rcept_dt": c.rcept_dt,
                "report_nm": c.report_nm}
    else:
        pre = ar.disclosures_to_rows(discs) if discs else None
        reports = ar.find_latest_audit_reports(
            corp_code, n=1, prefetched_rows=pre, log=log,
        )
        if not reports and pre is not None:
            reports = ar.find_latest_audit_reports(corp_code, n=1, log=log)
        return reports[0] if reports else None


# "II. 사업의 내용" 섹션 경계 마커
_SECTION_START = [
    "II. 사업의 내용",
    "Ⅱ. 사업의 내용",
    "II.사업의 내용",
    "2. 사업의 내용",
    "제2부 사업의 내용",
    "사업의 내용",
]
_SECTION_END = [
    "III. 재무에 관한 사항",
    "Ⅲ. 재무에 관한 사항",
    "III.재무에 관한 사항",
    "3. 재무에 관한 사항",
    "III. 재무제표 등",
    "재무제표",
]


def slice_business_section(full_body: str, fallback_head: int = 60000) -> str:
    """본문에서 II. 사업의 내용 ~ III. 재무 사이 텍스트만 추출.

    사업보고서는 섹션이 잘 정의돼 있음. 감사보고서는 마커가 없을 수 있어
    fallback 으로 앞쪽 fallback_head 자를 사용.
    """
    if not full_body:
        return ""
    # 가장 먼저 나타나는 start marker 찾기
    start_idx = -1
    for m in _SECTION_START:
        idx = full_body.find(m)
        if idx >= 0 and (start_idx < 0 or idx < start_idx):
            start_idx = idx
    if start_idx < 0:
        # 마커 없음 → 본문 앞부분 (감사보고서 케이스)
        return full_body[:fallback_head]

    # start 이후 가장 먼저 나타나는 end marker
    end_idx = len(full_body)
    for m in _SECTION_END:
        idx = full_body.find(m, start_idx + 10)
        if idx > start_idx and idx < end_idx:
            end_idx = idx

    section = full_body[start_idx:end_idx]
    # LLM 입력 cap (재무/주주 파싱과 별도의 cap)
    if len(section) > 70000:
        section = section[:70000] + "\n...[섹션 cap]"
    return section


def fetch_business_profile(
    discs: List[Disclosure],
    corp_code: str,
    is_listed: bool,
    client: Any = None,
    model: Optional[str] = None,
    log: LogFn = print,
) -> Optional[Dict[str, Any]]:
    """오케스트레이션: 보고서 선택 → 본문 다운로드 → 섹션 슬라이싱 → LLM 추출.

    결과 dict 에 선택한 보고서 정보(report_nm, rcept_no, rcept_dt)도 포함.
    """
    src = pick_source_report(discs, is_listed, corp_code, log=log)
    if src is None:
        log("  → 회사 개요 추출할 보고서 없음")
        return None
    log(f"  보고서: {src['report_nm']} ({src['rcept_no']})")

    # 사업보고서는 매우 큰 경우가 있어 cap 큰 값으로
    body = fetch_body(src["rcept_no"], cap=200000)
    if not body:
        log("  → 본문 다운로드 실패")
        return None
    log(f"  본문 {len(body):,}자")

    section = slice_business_section(body)
    if not section:
        log("  → 사업 섹션 슬라이싱 실패")
        return None
    log(f"  사업 섹션 {len(section):,}자 → Claude 추출")

    kwargs: Dict[str, Any] = {}
    if model:
        kwargs["model"] = model
    parsed = llm_mod.extract_business_overview(section, client=client, **kwargs)
    if not parsed:
        log("  → LLM 추출 실패")
        return None

    # 소스 보고서 메타 병합
    parsed["_source_report_nm"] = src.get("report_nm", "")
    parsed["_source_rcept_no"] = src.get("rcept_no", "")
    parsed["_source_rcept_dt"] = src.get("rcept_dt", "")
    return parsed
