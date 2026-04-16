# -*- coding: utf-8 -*-
"""M7 — Anthropic Claude 기반 공시 요약 + Implication + Executive Summary.

설계:
  • 시스템 프롬프트는 역할·출력 스키마로 분리 + prompt caching 활성화
    → 다건 호출 시 입력 토큰 비용 급감.
  • 병렬 호출은 `concurrent.futures.ThreadPoolExecutor` (max_workers=4).
  • 출력 파싱은 ```json ...``` 블록 우선, 실패 시 첫 `{ ... }` 스캔.
    모두 실패하면 summary 에 raw text 저장, llm_status='error'.
"""
from __future__ import annotations
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional

from .config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL
from .disclosures import Disclosure


# ── 시스템 프롬프트 (캐시 적용) ──────────────────────────────────────────
SYSTEM_PROMPT = """당신은 사모펀드(PE) 투자심사역입니다. 한국 상장·비상장 기업의
DART 공시를 읽고 투자의사결정 관점에서 실질을 꿰뚫는 코멘트를 작성합니다.
다음 원칙을 엄격히 지킵니다:

1) **사실과 해석 분리**: summary 는 공시 본문에 적힌 사실만. implication 은 PE 관점의 해석.
2) **M&A · 자금조달 · 지배구조 · 리스크 · 기회** 5개 렌즈로 시사점을 판단.
3) **숫자 인용**: 금액/비율/일자는 본문에서 확인되는 경우만. 추정 금지.
4) **간결**: summary 3~4줄, key_points 3~5개, implication 2~4줄.
5) 반드시 **순수 JSON** 으로만 응답. 마크다운/주석 없이."""

SCHEMA_HINT = """아래 JSON 스키마로 응답하세요:
{
  "summary": "3~4줄 한국어 요약",
  "key_points": ["핵심 사실 1", "핵심 사실 2", "핵심 사실 3"],
  "implication": "PE 투자관점 시사점"
}"""


def _build_user_prompt(corp_name: str, title: str, date: str, ty_label: str, body: str) -> str:
    return (
        f"{SCHEMA_HINT}\n\n"
        f"회사: {corp_name}\n"
        f"공시일: {date}\n"
        f"공시유형: {ty_label}\n"
        f"공시 제목: {title}\n"
        f"---- 본문 발췌 ----\n{body}\n"
    )


# ── JSON 파싱 ────────────────────────────────────────────────────────────
_CODEBLOCK_RE = re.compile(r"```(?:json)?\s*(\{[\s\S]*\})\s*```")
_OBJECT_RE = re.compile(r"\{[\s\S]*\}")


def _parse_json(text: str) -> Optional[Dict[str, Any]]:
    """LLM 응답에서 JSON 객체 추출. 실패 시 None.

    다중 fallback:
      1) ```json ... ``` 코드펜스
      2) 본문에 직접 임베드된 {...} (greedy)
      3) 마크다운 prose 안의 가장 긴 {...} 블록 (trailing comma 등 제거 시도)
    """
    if not text:
        return None
    m = _CODEBLOCK_RE.search(text)
    candidate = m.group(1) if m else None
    if candidate is None:
        m2 = _OBJECT_RE.search(text)
        candidate = m2.group(0) if m2 else None
    if candidate is None:
        return None
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        # trailing comma / python-style comments 등 관대한 복구 시도
        try:
            cleaned = re.sub(r",\s*([\]}])", r"\1", candidate)  # trailing comma 제거
            return json.loads(cleaned)
        except json.JSONDecodeError:
            return None


# ── Anthropic 클라이언트 ────────────────────────────────────────────────
_client = None


def get_client(api_key: Optional[str] = None):
    """Anthropic 클라이언트 싱글턴. 키 미지정 시 ANTHROPIC_API_KEY 사용."""
    global _client
    if _client is not None and api_key is None:
        return _client
    try:
        from anthropic import Anthropic
    except ImportError as e:
        raise RuntimeError(
            "anthropic 패키지 설치 필요: pip install anthropic"
        ) from e
    key = api_key or ANTHROPIC_API_KEY
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY 미설정")
    _client = Anthropic(api_key=key)
    return _client


# ── 단건 요약 ────────────────────────────────────────────────────────────
def summarize_disclosure(
    disc: Disclosure,
    client=None,
    model: str = ANTHROPIC_MODEL,
    max_tokens: int = 800,
) -> Disclosure:
    """in-place 로 disc.summary/key_points/implication/llm_status 채움."""
    if not disc.body:
        disc.llm_status = "skipped"
        return disc
    client = client or get_client()
    user = _build_user_prompt(
        corp_name=disc.corp_name,
        title=disc.report_nm,
        date=disc.rcept_dt,
        ty_label=disc.ty_label,
        body=disc.body,
    )
    try:
        resp = _safe_create(
            client,
            model=model,
            max_tokens=max_tokens,
            system=[{
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": user}],
        )
        # resp.content 는 블록 리스트
        parts = []
        for block in resp.content:
            txt = getattr(block, "text", None)
            if txt:
                parts.append(txt)
        raw = "\n".join(parts).strip()
        parsed = _parse_json(raw)
        if parsed is None:
            disc.summary = raw[:400] or "(빈 응답)"
            disc.llm_status = "error"
        else:
            disc.summary = str(parsed.get("summary", "")).strip()
            kp = parsed.get("key_points") or []
            if isinstance(kp, list):
                disc.key_points = [str(x).strip() for x in kp if str(x).strip()]
            disc.implication = str(parsed.get("implication", "")).strip()
            disc.llm_status = "ok"
    except Exception as exc:  # noqa: BLE001
        import traceback
        disc.summary = f"(LLM 오류: {type(exc).__name__}: {exc})"
        disc.llm_status = "error"
    return disc


# ── 병렬 요약 ────────────────────────────────────────────────────────────
def summarize_batch(
    discs: List[Disclosure],
    client=None,
    max_workers: int = 4,
    log: Optional[Callable[[str], None]] = None,
    model: str = ANTHROPIC_MODEL,
) -> None:
    client = client or get_client()
    targets = [d for d in discs if d.body and d.llm_status == "pending"]
    if not targets:
        return
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(summarize_disclosure, d, client, model): d
            for d in targets
        }
        done = 0
        for fut in as_completed(futures):
            d = futures[fut]
            try:
                fut.result()
            except Exception as exc:  # noqa: BLE001
                d.llm_status = "error"
                d.summary = f"(LLM 오류: {exc})"
            done += 1
            if log:
                # error 케이스는 본문 앞부분(에러 메시지)까지 같이 노출
                extra = ""
                if d.llm_status == "error":
                    extra = f" — {(d.summary or '')[:120]}"
                log(f"  LLM {done}/{len(targets)}: {d.rcept_dt} {d.report_nm[:30]} [{d.llm_status}]{extra}")


# ── Executive Summary ───────────────────────────────────────────────────
EXEC_SYSTEM = """당신은 PE 투자심사역입니다. 한 회사의 일정 기간 공시 이슈 요약
모음을 받아, 투자위원회에 제출할 **Executive Summary(경영진 요약)** 를
마크다운으로 작성합니다. 섹션:
1. **한 문장 총평**
2. **핵심 변화** (지배구조·M&A·자금조달·매출/실적 트렌드 중 실제 확인된 것만)
3. **주요 리스크**
4. **기회·투자포인트**
5. **즉시 확인 필요사항** (DD 시 파고들 포인트)
공시에 근거하지 않은 추정은 금지. 숫자는 본문에 있는 것만 인용."""


def build_executive_summary(
    profile_summary: str,
    disclosures: List[Disclosure],
    client=None,
    model: str = ANTHROPIC_MODEL,
    max_tokens: int = 1500,
) -> str:
    client = client or get_client()
    # 본문 요약된 공시만 사용
    pieces = []
    for d in disclosures:
        if d.llm_status != "ok":
            continue
        pieces.append(
            f"- [{d.rcept_dt}] ({d.ty_label}) {d.report_nm}\n"
            f"  요약: {d.summary}\n"
            f"  시사점: {d.implication}"
        )
    if not pieces:
        return "(분석된 공시가 없어 Executive Summary를 생성할 수 없습니다.)"
    body = "\n".join(pieces[:60])  # 과도한 입력 방지

    user = (
        f"회사 기본정보:\n{profile_summary}\n\n"
        f"분석된 공시 이슈 모음:\n{body}"
    )
    try:
        resp = _safe_create(
            client,
            model=model,
            max_tokens=max_tokens,
            system=[{
                "type": "text",
                "text": EXEC_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": user}],
        )
        parts = [getattr(b, "text", "") for b in resp.content]
        return "\n".join(p for p in parts if p).strip()
    except Exception as exc:  # noqa: BLE001
        return f"(Executive Summary 생성 실패: {exc})"


# ── 감사보고서 파싱 프롬프트 (비상장 지원) ────────────────────────────────
AUDIT_FIN_SYSTEM = """당신은 한국 회계 전문가입니다. 비상장 법인의
감사보고서 원문 텍스트를 받아 **재무제표 핵심 계정**을 순수 JSON 으로 추출합니다.

엄격 규칙:
1) 본문에 실제로 표기된 숫자만 사용. 추정 금지.
2) 단위는 원(KRW) 기준으로 통일. 본문이 백만원/천원 단위면 환산.
3) 연도는 감사보고서의 "제N기"에 대응하는 결산연도 (YYYY).
4) 응답은 **JSON 배열만** (마크다운/주석 금지).

JSON 스키마 (각 원소):
{
  "year": 2024,
  "fs_div": "CFS" | "OFS",
  "revenue": number | null,
  "cost_of_sales": number | null,
  "gross_profit": number | null,
  "sga": number | null,
  "op_income": number | null,
  "dep": number | null,
  "amort": number | null,
  "net_income": number | null,
  "total_assets": number | null,
  "total_liabilities": number | null,
  "total_equity": number | null
}
최대 3개년 (당기/전기/전전기). 없는 계정은 null."""


AUDIT_GOV_SYSTEM = """당신은 한국 회계 전문가입니다. 비상장 법인의
감사보고서 주석(Notes) 에서 **지배구조/주주/임원/배당/감사의견** 정보를
구조화된 JSON 으로 추출합니다.

엄격 규칙:
1) 본문에 명시된 사실만. 추정·외부지식 사용 금지.
2) 지분율은 %. 없으면 null.
3) 응답은 **JSON 객체만** (마크다운/주석 금지).

스키마:
{
  "major": [ {"nm":"이름","relate":"본인/특수관계인/법인 등","stock_knd":"보통주",
              "trmend_posesn_stock_co":숫자|null,
              "trmend_posesn_stock_qota_rt":숫자|null} ],
  "executives": [ {"nm":"이름","ofcps":"직위","chrg_job":"담당","rgist_exctv_at":"등기여부","hffc_pd":"재직기간"} ],
  "dividends": [ {"se":"현금배당(주당)/배당성향 등","stock_knd":"보통주",
                  "thstrm":"당기값","frmtrm":"전기값","lwfr":"전전기값"} ],
  "audit_opinion": [ {"bsns_year":"당기/전기","adtor":"감사인","adt_opinion":"적정/한정/부적정/의견거절","emphs_matter":"강조사항"} ],
  "source_year": 2024
}"""


def _parse_json_array(text: str):
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(\[[\s\S]*\])\s*```", text)
    candidate = m.group(1) if m else None
    if candidate is None:
        m2 = re.search(r"\[[\s\S]*\]", text)
        candidate = m2.group(0) if m2 else None
    if candidate is None:
        return None
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def extract_financials_from_audit(
    body: str,
    client=None,
    model: str = ANTHROPIC_MODEL,
    max_tokens: int = 2000,
) -> list:
    """감사보고서 본문 → 재무 dict 배열 (최대 3개년). 실패 시 빈 리스트."""
    if not body:
        return []
    client = client or get_client()
    try:
        resp = _safe_create(
            client,
            model=model,
            max_tokens=max_tokens,
            system=[{
                "type": "text",
                "text": AUDIT_FIN_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{
                "role": "user",
                "content": f"감사보고서 본문(발췌):\n\n{body}",
            }],
        )
        raw = "\n".join(getattr(b, "text", "") for b in resp.content).strip()
        parsed = _parse_json_array(raw)
        if not isinstance(parsed, list):
            return []
        return parsed
    except Exception:  # noqa: BLE001
        return []


# ── 사업 개요 (Business Overview) 추출 ─────────────────────────────────
BUSINESS_SYSTEM = """당신은 PE 투자심사역입니다. 한국 기업의 사업보고서 또는
감사보고서 원문에서 투자 판단에 필요한 **회사 개요**를 구조화 JSON 으로
추출합니다.

엄격 규칙:
1) 본문에 명시된 사실만 사용. 외부지식/추정 금지.
2) 금액은 원 단위로 통일하되, 본문 표기가 "억원" 등으로 다르면 revenue_note
   에 단위/기준연도를 명기.
3) 비율은 %.
4) 응답은 **JSON 객체만** (마크다운/주석 금지).

스키마:
{
  "business_summary": "3~5줄, 이 회사가 무엇을 하는지 + 핵심 BM",
  "products": ["제품/서비스 1", ...],
  "segments": [
    {"name":"사업부/제품군","revenue":숫자|null,"revenue_note":"단위/연도 설명",
     "op_margin_pct":숫자|null,"description":"한 줄 설명"}
  ],
  "major_customers": [
    {"name":"고객사 1", "share_pct":숫자|null, "amount":숫자|null,
     "amount_note":"단위/연도 설명", "description":"한 줄 설명|null"}
  ],
  "major_suppliers": [
    {"name":"공급사 1", "share_pct":숫자|null, "amount":숫자|null,
     "amount_note":"단위/연도 설명", "description":"한 줄 설명|null"}
  ],
  "key_insights": [
    "문장 내용 (분류 키워드)"
  ]
}

매출처/매입처 규칙:
  • 본문에 비중·거래금액 등 수치가 있으면 share_pct (%) 와 amount (원 단위)
    로 채우고 amount_note 에 기준연도/단위 명시. 이름만 나열돼 있고 수치가
    없으면 share_pct/amount/amount_note 는 null.
  • 수치가 전혀 없는 이름은 배제 가능 (공시 의무가 없는 경우 많음).

key_insights 형식:
  • 반드시 "문장 내용 (분류 키워드)" 형태. 분류 키워드 예시:
    성장 드라이버, 리스크, 경쟁 위치, 밸류드라이버, 재무 건전성, 규제 이슈.
  • 예: "라이선스 부문 OPM 35% (밸류드라이버)",
        "원재료 가격 변동 노출 (리스크)"."""


# ── D&A 전용 추출 (최후 fallback) ──────────────────────────────────────
DA_SYSTEM = """당신은 한국 회계 전문가입니다. 한국 기업 공시 본문 발췌에서
**연도별 감가상각비(Depreciation)와 무형자산상각비(Amortisation)** 를
JSON 배열로 추출합니다.

추출 우선순위 (위에서부터 찾고, 있으면 그걸 사용):
  1. **현금흐름표 (Statement of Cash Flows)** — "영업활동현금흐름"
     간접법 조정 항목의 "감가상각비" / "무형자산상각비" 라인 (최우선).
  2. **현금흐름표 주석** — 본문에 현금흐름표가 안 보이거나 감가상각 라인이
     없으면, "현금흐름표에 대한 주석" / "유형자산" / "무형자산" 주석에서
     당기 증가·감소 중 "상각" 또는 "감가상각" 항목.
  3. 비용의 성격별 분류 주석 (2순위 fallback).

핵심 원칙:
1) 본문에 명시된 사실만. 추정·외부지식 금지.
2) 숫자는 **원 단위**로 환산 — 본문이 "백만원" 이면 ×1,000,000,
   "천원" 이면 ×1,000. 단위는 표 제목이나 상단 명시를 확인.
3) **사용권자산 감가상각비 / 리스자산 상각비 / 리스부채 상각 등은 제외**
   (전통적 D&A 와 분리).
4) 본문에 나오는 **모든 연도**를 포함 (당기/전기/전전기 비교표가 있으면 3개년).
5) 응답은 **JSON 배열만** (마크다운/코드펜스 없이).
6) 본문에 정말 데이터가 없으면 `[]` 반환 — 거짓 수치 생성 금지.

스키마:
[
  {"year": 2024, "dep": 숫자|null, "amort": 숫자|null,
   "source": "cash_flow"|"cash_flow_note"|"cost_by_nature"|null},
  ...
]

예시:
  현금흐름표에 "감가상각비 393,500 (백만원)" 당기 →
    [{"year":2024, "dep":393500000000, "amort":null, "source":"cash_flow"}]

dep = 유형자산 감가상각비 (사용권 제외).
amort = 무형자산 상각비.
D&A 합계 라인("감가상각 및 무형자산상각비")만 있으면 dep 에 합계, amort null."""


def _clean_utf8(s: str) -> str:
    """문자열을 UTF-8 round-trip 으로 정규화.

    PyInstaller --windowed + 한글 Windows 환경에서 Anthropic SDK 내부
    인코딩 경로가 일부 문자에 대해 ASCII 로 fallback 하는 경우 대비.
    surrogate / invalid bytes 도 여기서 제거.
    """
    if not isinstance(s, str):
        return s
    try:
        return s.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
    except Exception:
        return s


class _FakeResponseContent:
    """Anthropic SDK 응답과 유사한 최소 인터페이스 (raw HTTP fallback 용)."""
    def __init__(self, text: str):
        self.text = text


class _FakeResponse:
    def __init__(self, text: str):
        self.content = [_FakeResponseContent(text)]


def _raw_http_call(kwargs: Dict[str, Any], api_key: str, max_retries: int = 3):
    """Anthropic REST API 직접 호출 — **stdlib http.client** 로 최저수준 호출.

    requests/urllib3 를 쓰지 않음. 이유:
    - 한글 Windows + PyInstaller 에서 urllib3 가 HTTP 헤더를 latin-1 로
      인코딩하는 과정에서 socket.gethostname() 등 시스템 값 한글 localize
      가 섞여들어가 `\ub97c` (를) 인코딩 실패.
    - http.client 는 우리가 set 한 헤더 바이트만 그대로 보냄. 자동 주입 없음.

    429/5xx: 지수 백오프 (2s, 4s, 8s) 최대 3회 재시도.
    """
    import http.client as _hc
    import ssl as _ssl
    import time as _time
    import socket as _socket

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json; charset=utf-8",
        "accept": "application/json",
        # User-Agent 생략 — http.client 는 자동 추가하지 않아 완전 ASCII 보장
    }
    payload = {
        "model": kwargs["model"],
        "max_tokens": kwargs["max_tokens"],
    }
    sys_blk = kwargs.get("system")
    if sys_blk:
        payload["system"] = sys_blk
    msgs = kwargs.get("messages") or []
    payload["messages"] = msgs

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    ctx = _ssl.create_default_context()

    last_exc = None
    for attempt in range(max_retries):
        conn = None
        try:
            conn = _hc.HTTPSConnection(
                "api.anthropic.com", timeout=120, context=ctx,
            )
            conn.request("POST", "/v1/messages", body=body, headers=headers)
            resp = conn.getresponse()
            status = resp.status
            raw = resp.read().decode("utf-8", errors="replace")
            if status == 200:
                data = json.loads(raw)
                blocks = data.get("content") or []
                text = "\n".join(
                    b.get("text", "") for b in blocks if isinstance(b, dict)
                )
                return _FakeResponse(text)
            # 429/5xx 재시도
            if status == 429 or 500 <= status < 600:
                last_exc = RuntimeError(f"HTTP {status}: {raw[:300]}")
                if attempt < max_retries - 1:
                    _time.sleep(2 ** (attempt + 1))
                    continue
            # 4xx 는 재시도 불가
            raise RuntimeError(f"HTTP {status}: {raw[:500]}")
        except (_hc.HTTPException, _socket.error, _ssl.SSLError, OSError) as exc:
            last_exc = exc
            if attempt < max_retries - 1:
                _time.sleep(2 ** (attempt + 1))
                continue
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
    if last_exc:
        raise last_exc
    raise RuntimeError("raw HTTP call exhausted retries without specific error")


def _safe_create(client, **kwargs):
    """모든 LLM 호출을 raw HTTP 로 처리 (SDK messages.create 호출 안 함).

    SDK 가 client 인스턴스로 받는 api_key 만 추출해서 REST API 직접 호출.
    이전에는 SDK 먼저 시도 후 UnicodeEncodeError 시 fallback 했으나,
    SDK telemetry 헤더의 한글 localize 문제가 재발해서 근본 차단.

    텍스트 모두 UTF-8 round-trip 으로 사전 정화.
    """
    sys_blk = kwargs.get("system")
    if isinstance(sys_blk, list):
        for b in sys_blk:
            if isinstance(b, dict) and isinstance(b.get("text"), str):
                b["text"] = _clean_utf8(b["text"])
    elif isinstance(sys_blk, str):
        kwargs["system"] = _clean_utf8(sys_blk)
    msgs = kwargs.get("messages")
    if isinstance(msgs, list):
        for m in msgs:
            c = m.get("content") if isinstance(m, dict) else None
            if isinstance(c, str):
                m["content"] = _clean_utf8(c)
            elif isinstance(c, list):
                for blk in c:
                    if isinstance(blk, dict) and isinstance(blk.get("text"), str):
                        blk["text"] = _clean_utf8(blk["text"])

    # api key 추출 — client 인스턴스에서 or 환경변수
    key = (
        getattr(client, "api_key", None)
        or ANTHROPIC_API_KEY
        or os.environ.get("ANTHROPIC_API_KEY", "")
    )
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY 미설정 — raw HTTP 호출 불가")
    return _raw_http_call(kwargs, key)


def extract_da_from_body(
    body: str,
    client=None,
    model: str = ANTHROPIC_MODEL,
    max_tokens: int = 1500,
    return_raw: bool = False,
):
    """본문 텍스트 → [{year, dep, amort}, ...]. 실패 시 빈 리스트.
    return_raw=True 이면 (parsed, raw_response_str) 튜플 반환 (디버그용)."""
    if not body:
        return ([], "") if return_raw else []
    client = client or get_client()
    # UTF-8 clean round-trip 으로 ASCII fallback 경로 차단
    body = _clean_utf8(body)
    try:
        resp = _safe_create(
            client,
            model=model,
            max_tokens=max_tokens,
            system=[{
                "type": "text",
                "text": _clean_utf8(DA_SYSTEM),
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{
                "role": "user",
                "content": _clean_utf8(f"본문(발췌):\n\n{body}"),
            }],
        )
        raw = "\n".join(getattr(b, "text", "") for b in resp.content).strip()
        parsed = _parse_json_array(raw)
        if not isinstance(parsed, list):
            parsed = []
        return (parsed, raw) if return_raw else parsed
    except Exception as exc:  # noqa: BLE001
        import traceback
        tb = traceback.format_exc()
        if return_raw:
            return ([], f"__EXCEPTION__ {type(exc).__name__}: {exc}\n{tb}")
        return []


# ── 주석(Footnotes) 요약 — 감사보고서/사업보고서의 주요 주석 섹션 정리 ──
FOOTNOTES_SYSTEM = """당신은 한국 PE 투자심사역입니다. 사업보고서 또는 감사보고서의
**주석(Notes)** 섹션에서 PE 가 투자 판단 시 반드시 읽어야 할 **주요 주석**을
카테고리별로 정리해 JSON 으로 반환합니다.

엄격 규칙:
1) 본문에 명시된 사실만 사용. 추정/일반론 금지.
2) 금액은 원 단위 숫자(amount) 또는 원문 그대로(note) 기재.
3) 없는 항목은 해당 배열을 빈 리스트로.
4) 응답은 **JSON 객체만** (마크다운/주석/코드펜스 없이).

스키마:
{
  "related_party_transactions": [
    {"counterparty":"특수관계자명","relation":"지배/종속/관계 등",
     "nature":"거래 성격 (매출/매입/차입 등)",
     "amount":숫자|null, "note":"요약"}
  ],
  "contingent_liabilities": [
    {"title":"우발부채 제목","amount":숫자|null,"note":"요약/진행상황"}
  ],
  "major_contracts": [
    {"title":"중요 계약","counterparty":"상대","value":숫자|null,
     "term":"계약기간","note":"요약"}
  ],
  "loans_and_borrowings": [
    {"lender":"차입처","balance":숫자|null,"rate":"금리","maturity":"만기",
     "collateral":"담보"}
  ],
  "subsequent_events": [
    {"title":"보고기간 후 사건","note":"요약"}
  ],
  "other_key_footnotes": [
    {"title":"기타 주요 주석","note":"요약"}
  ]
}"""


def extract_footnotes_from_body(
    body: str,
    client=None,
    model: str = ANTHROPIC_MODEL,
    max_tokens: int = 3000,
    return_raw: bool = False,
):
    """사업/감사보고서 본문 → 주요 주석 구조화 JSON.

    return_raw=True 이면 (parsed_or_empty, raw_str) 튜플 반환 (디버그용).
    """
    if not body:
        return ({}, "") if return_raw else {}
    client = client or get_client()
    try:
        resp = _safe_create(
            client,
            model=model,
            max_tokens=max_tokens,
            system=[{
                "type": "text",
                "text": FOOTNOTES_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{
                "role": "user",
                "content": f"주석 섹션 본문(발췌):\n\n{body}",
            }],
        )
        raw = "\n".join(getattr(b, "text", "") for b in resp.content).strip()
        parsed = _parse_json(raw)
        result = parsed if isinstance(parsed, dict) else {}
        return (result, raw) if return_raw else result
    except Exception as exc:  # noqa: BLE001
        if return_raw:
            return ({}, f"__EXCEPTION__ {type(exc).__name__}: {exc}")
        return {}


def extract_business_overview(
    body: str,
    client=None,
    model: str = ANTHROPIC_MODEL,
    max_tokens: int = 2500,
    return_raw: bool = False,
):
    """사업보고서 `II. 사업의 내용` 섹션 혹은 감사보고서 본문에서 비즈니스 정보
    JSON dict 추출.

    return_raw=True 이면 (parsed_or_empty, raw_str) 튜플 반환.
    """
    if not body:
        return ({}, "") if return_raw else {}
    client = client or get_client()
    try:
        resp = _safe_create(
            client,
            model=model,
            max_tokens=max_tokens,
            system=[{
                "type": "text",
                "text": BUSINESS_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{
                "role": "user",
                "content": f"사업 섹션 원문(발췌):\n\n{body}",
            }],
        )
        raw = "\n".join(getattr(b, "text", "") for b in resp.content).strip()
        parsed = _parse_json(raw)
        result = parsed if isinstance(parsed, dict) else {}
        return (result, raw) if return_raw else result
    except Exception as exc:  # noqa: BLE001
        if return_raw:
            return ({}, f"__EXCEPTION__ {type(exc).__name__}: {exc}")
        return {}


def extract_governance_from_audit(
    body: str,
    client=None,
    model: str = ANTHROPIC_MODEL,
    max_tokens: int = 2000,
) -> dict:
    """감사보고서 본문 → 지배구조 dict. 실패 시 빈 dict."""
    if not body:
        return {}
    client = client or get_client()
    try:
        resp = _safe_create(
            client,
            model=model,
            max_tokens=max_tokens,
            system=[{
                "type": "text",
                "text": AUDIT_GOV_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{
                "role": "user",
                "content": f"감사보고서 본문(발췌):\n\n{body}",
            }],
        )
        raw = "\n".join(getattr(b, "text", "") for b in resp.content).strip()
        parsed = _parse_json(raw)
        if not isinstance(parsed, dict):
            return {}
        return parsed
    except Exception:  # noqa: BLE001
        return {}
