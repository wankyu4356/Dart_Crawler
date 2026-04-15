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
    """본문에서 II. 사업의 내용 ~ III. 재무 사이 **본문 구간** 만 선택.

    사업보고서 document.xml 은 보통 구조가:
      표지 / 목차 [II. 사업의 내용 ← 여기가 먼저 매칭됨!]
      I. 회사의 개요
      II. 사업의 내용 [실제 본문. 수만 자]
      III. 재무에 관한 사항

    첫 번째 매칭을 쓰면 "목차" 만 캡처해 빈 섹션이 됨. 따라서 모든 매칭 쌍
    (start, end) 을 구해 **가장 긴 구간** 을 본문으로 선택.

    감사보고서는 섹션 마커가 없거나 다르므로 마커 못 찾으면 앞 60k 폴백.
    """
    if not full_body:
        return ""

    start_marks = [
        "II. 사업의 내용",
        "Ⅱ. 사업의 내용",
        "II.사업의 내용",
        "Ⅱ.사업의 내용",
        "2. 사업의 내용",
        "제2부 사업의 내용",
        "사업의 내용",
    ]
    end_marks = [
        "III. 재무에 관한 사항",
        "Ⅲ. 재무에 관한 사항",
        "III.재무에 관한 사항",
        "Ⅲ.재무에 관한 사항",
        "3. 재무에 관한 사항",
        "III. 재무제표 등",
        "Ⅲ. 재무제표 등",
        "재무에 관한 사항",
    ]

    # start 모든 인덱스 수집
    starts: List[int] = []
    for m in start_marks:
        idx = 0
        while True:
            i = full_body.find(m, idx)
            if i < 0:
                break
            starts.append(i)
            idx = i + 1
    if not starts:
        return full_body[:fallback_head]
    starts = sorted(set(starts))

    # 각 start 마커별로 다음 end 마커까지 길이 측정 → 가장 긴 것 선택
    best_span: Optional[tuple[int, int]] = None
    best_len = 0
    for s in starts:
        next_end = len(full_body)
        for m in end_marks:
            i = full_body.find(m, s + 10)
            if i > s and i < next_end:
                next_end = i
        length = next_end - s
        if length > best_len:
            best_len = length
            best_span = (s, next_end)

    if best_span is None or best_len < 300:
        # 본문 매칭 실패 (목차밖에 없음) → 폴백
        return full_body[:fallback_head]

    s, e = best_span
    section = full_body[s:e]
    if len(section) > 80000:
        section = section[:80000] + "\n...[섹션 cap]"
    return section


def slice_da_relevant(body: str, cap: int = 50000) -> str:
    """D&A 가 등장할 만한 구간만 병합 슬라이싱.

      • 현금흐름표 / 영업활동으로인한현금흐름
      • 감가상각비 / 무형자산상각비 / 상각비
      • 비용의 성격별 분류 주석

    각 마커 주변 앞 500 / 뒤 4000자 수집 후 겹치면 병합. cap 이하로 자름.
    """
    if not body:
        return ""
    markers = [
        "현금흐름표", "현 금 흐 름 표",
        "영업활동으로인한현금흐름", "영업활동 현금흐름", "영업활동현금흐름",
        "감가상각비", "무형자산상각비", "상각비용",
        "비용의 성격별", "비용의성격별", "성격별 분류", "성격별분류",
        "유형자산 및 무형자산", "유형자산감가상각",
    ]
    spans: List[tuple[int, int]] = []
    for m in markers:
        idx = 0
        while True:
            i = body.find(m, idx)
            if i < 0:
                break
            a = max(0, i - 500)
            b = min(len(body), i + 4000)
            spans.append((a, b))
            idx = i + len(m)
    if not spans:
        return body[:cap]
    # merge
    spans.sort()
    merged: List[tuple[int, int]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    parts = [body[s:e] for s, e in merged]
    text = "\n---\n".join(parts)
    if len(text) > cap:
        text = text[:cap] + "\n...[cap]"
    return text


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
