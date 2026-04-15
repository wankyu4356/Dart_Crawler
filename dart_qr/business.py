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
import re
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

# 정정/변경 접두어 — 본문이 부분 변경본만 담겨 LLM 파싱이 어려움 → 후순위
AMEND_PREFIX_RE = re.compile(
    r"^\s*\[(?:첨부정정|기재정정|정정|기재|정정제출요구|정정명령부과|"
    r"첨부추가|변경등록|연장결정|발행조건확정)\]"
)


def _is_amended(report_nm: str) -> bool:
    return bool(AMEND_PREFIX_RE.match(report_nm or ""))


def _base_report_name(report_nm: str) -> str:
    """정정 접두어 제거한 기본 보고서명."""
    return AMEND_PREFIX_RE.sub("", report_nm or "").strip()


def _report_priority(report_nm: str) -> int:
    base = _base_report_name(report_nm)
    for name, p in REPORT_PRIORITY:
        if name in base:
            return p
    return 0


def pick_source_reports(
    discs: List[Disclosure],
    is_listed: bool,
    corp_code: str,
    log: Optional[LogFn] = None,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """상장/비상장 공통 — 본문 소스로 쓸 보고서 **후보 리스트** (우선순위순).

    정렬 기준:
      1) 보고서 우선순위 (사업보고서 4 > 반기 2 > 분기 1)
      2) **원본 우선** — 정정본([첨부정정] 등) 은 후순위
      3) 최신 접수일자
    """
    out: List[Dict[str, Any]] = []
    if is_listed:
        cands = [d for d in discs if _report_priority(d.report_nm) > 0]
        # 연결감사보고서 등 pblntf_ty='F' + 이름에 '감사보고서' 포함
        audit_cands = [d for d in discs
                       if (d.pblntf_ty or "") == "F"
                       and "감사보고서" in (d.report_nm or "")]
        if not cands and not audit_cands:
            if log:
                log("  (정기보고서 / 감사보고서 없음)")
            return []
        def _fy_num(d):
            m = re.search(r"\((\d{4})[.\-/년]?\s*\d{1,2}", d.report_nm or "")
            return int(m.group(1)) if m else 0

        # 1) 보고서 종류 우선 (사업 > 반기 > 분기)
        # 2) 결산연도 최신 우선
        # 3) 같은 연도 안에서 원본 > 정정본
        # 4) 접수일 최신
        cands.sort(key=lambda d: (
            -_report_priority(d.report_nm),
            -_fy_num(d),
            1 if _is_amended(d.report_nm) else 0,
            -int(str(d.rcept_dt or "0").replace("-", "") or "0"),
        ))
        # 연결감사보고서 > 별도감사보고서, 최신 접수일 우선
        audit_cands.sort(key=lambda d: (
            0 if "연결" in (d.report_nm or "") else 1,
            -_fy_num(d),
            1 if _is_amended(d.report_nm) else 0,
            -int(str(d.rcept_dt or "0").replace("-", "") or "0"),
        ))
        # 정기보고서 → 감사보고서 순
        cands = cands + audit_cands
        # 같은 결산년도·기본보고서명 조합은 최대 2건(원본+정정)까지만
        seen: Dict[tuple, int] = {}
        for d in cands:
            base = _base_report_name(d.report_nm)
            # 결산연도 추출 (2024.12 / 2024.06 등)
            m = re.search(r"\((\d{4})[.\-/년]?\s*\d{1,2}", d.report_nm or "")
            fy = m.group(1) if m else ""
            key = (base, fy)
            seen[key] = seen.get(key, 0) + 1
            if seen[key] > 2:
                continue
            out.append({
                "rcept_no": d.rcept_no, "rcept_dt": d.rcept_dt,
                "report_nm": d.report_nm,
                "_amended": _is_amended(d.report_nm),
            })
            if len(out) >= limit:
                break
        return out
    else:
        pre = ar.disclosures_to_rows(discs) if discs else None
        reports = ar.find_latest_audit_reports(
            corp_code, n=limit, prefetched_rows=pre, log=log,
        )
        if not reports and pre is not None:
            reports = ar.find_latest_audit_reports(corp_code, n=limit, log=log)
        return [dict(r) for r in (reports or [])]


def pick_source_report(
    discs: List[Disclosure],
    is_listed: bool,
    corp_code: str,
    log: Optional[LogFn] = None,
) -> Optional[Dict[str, Any]]:
    """기존 호환용 — 가장 상위 1건만 반환."""
    lst = pick_source_reports(discs, is_listed, corp_code, log=log, limit=1)
    return lst[0] if lst else None


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


def slice_da_relevant(body: str, cap: int = 120000) -> str:
    """D&A 관련 구간 병합 슬라이싱.

    우선순위:
      1) 현금흐름표 본문 (간접법 조정 항목)
      2) 현금흐름표 주석 / 유형자산·무형자산 주석
      3) 비용의 성격별 분류 주석

    각 마커 주변 -500 / +5000자 수집 후 span 병합.
    """
    if not body:
        return ""
    markers = [
        # 1) 현금흐름표 본문
        "현금흐름표", "현 금 흐 름 표", "Statement of Cash Flows",
        "영업활동으로인한현금흐름", "영업활동 현금흐름", "영업활동현금흐름",
        "영업활동으로부터의 현금흐름",
        # 2) 현금흐름표 주석 + 유형자산/무형자산 주석
        "현금흐름표에 대한 주석", "현금흐름표에대한주석",
        "유형자산", "무형자산", "유 형 자 산", "무 형 자 산",
        # 3) D&A 라인 단어
        "감가상각비", "감가상각", "무형자산상각비", "상각비용", "상각비",
        # 4) 비용 성격별 분류
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
            b = min(len(body), i + 5000)
            spans.append((a, b))
            idx = i + len(m)
    if not spans:
        return body[:cap]
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


def slice_footnotes_section(full_body: str, cap: int = 80000) -> str:
    """본문에서 주석(Notes) 관련 구간 집중 슬라이싱.

    키 주석 섹션의 전형적 제목들 주변 ±대량 컨텍스트 수집 후 merge.
    """
    if not full_body:
        return ""
    markers = [
        "주석", "Notes",
        "특수관계자", "관계회사 거래", "특수관계자와의 거래",
        "우발부채", "우발채무", "지급보증", "계류 중인 소송",
        "중요한 계약", "장기차입금", "사채", "회사채",
        "리스", "이연법인세",
        "보고기간 후 사건", "후속사건",
    ]
    spans: List[tuple[int, int]] = []
    for m in markers:
        idx = 0
        while True:
            i = full_body.find(m, idx)
            if i < 0:
                break
            a = max(0, i - 300)
            b = min(len(full_body), i + 6000)
            spans.append((a, b))
            idx = i + len(m)
    if not spans:
        return full_body[:cap]
    spans.sort()
    merged: List[tuple[int, int]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    text = "\n---\n".join(full_body[s:e] for s, e in merged)
    if len(text) > cap:
        text = text[:cap] + "\n...[cap]"
    return text


def fetch_footnotes(
    discs: List[Disclosure],
    corp_code: str,
    is_listed: bool,
    client: Any = None,
    model: Optional[str] = None,
    log: LogFn = print,
) -> Optional[Dict[str, Any]]:
    """최신 사업/감사보고서에서 주요 주석 추출."""
    candidates = pick_source_reports(discs, is_listed, corp_code, log=log, limit=3)
    if not candidates:
        log("  → 주석 추출할 보고서 없음")
        return None

    kwargs: Dict[str, Any] = {}
    if model:
        kwargs["model"] = model

    for idx, src in enumerate(candidates, 1):
        log(f"  [{idx}/{len(candidates)}] 보고서: {src.get('report_nm','')}")
        body_full = fetch_body(src.get("rcept_no", ""), cap=400000)
        if not body_full or len(body_full) < 1000:
            log(f"    본문 부족 → 다음")
            continue
        section = slice_footnotes_section(body_full, cap=70000)
        if len(section) < 1000:
            log(f"    주석 섹션 부족 → 다음")
            continue
        log(f"    본문 {len(body_full):,}자 → 주석 {len(section):,}자 추출")
        parsed = llm_mod.extract_footnotes_from_body(section, client=client, **kwargs)
        if not parsed or not any(v for v in parsed.values() if isinstance(v, list)):
            log(f"    LLM 결과 비어있음 → 다음")
            continue
        parsed["_source_report_nm"] = src.get("report_nm", "")
        parsed["_source_rcept_no"]  = src.get("rcept_no", "")
        return parsed
    return None


def fetch_business_profile(
    discs: List[Disclosure],
    corp_code: str,
    is_listed: bool,
    client: Any = None,
    model: Optional[str] = None,
    log: LogFn = print,
) -> Optional[Dict[str, Any]]:
    """보고서 후보를 우선순위대로 순회 — 본문 확보 + 섹션 슬라이싱 실질 성공까지."""
    candidates = pick_source_reports(discs, is_listed, corp_code, log=log, limit=5)
    if not candidates:
        log("  → 회사 개요 추출할 보고서 없음")
        return None

    kwargs: Dict[str, Any] = {}
    if model:
        kwargs["model"] = model

    for idx, src in enumerate(candidates, 1):
        tag = " (정정본)" if src.get("_amended") else ""
        log(f"  [{idx}/{len(candidates)}] 보고서{tag}: {src.get('report_nm','')} "
            f"({src.get('rcept_no','')})")
        body = fetch_body(src.get("rcept_no", ""), cap=240000)
        if not body or len(body) < 500:
            log(f"    본문 부족({len(body) if body else 0}자) → 다음 후보 시도")
            continue
        log(f"    본문 {len(body):,}자")

        section = slice_business_section(body)
        if not section or len(section) < 300:
            log(f"    사업 섹션 부족({len(section) if section else 0}자) → 다음 후보 시도")
            continue
        log(f"    사업 섹션 {len(section):,}자 → Claude 추출")

        parsed = llm_mod.extract_business_overview(section, client=client, **kwargs)
        if not parsed:
            log(f"    LLM 응답 비어있음 → 다음 후보 시도")
            continue

        parsed["_source_report_nm"] = src.get("report_nm", "")
        parsed["_source_rcept_no"]  = src.get("rcept_no", "")
        parsed["_source_rcept_dt"]  = src.get("rcept_dt", "")
        return parsed

    log("  → 모든 후보 시도 실패")
    return None
