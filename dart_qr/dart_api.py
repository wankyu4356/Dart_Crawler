# -*- coding: utf-8 -*-
"""M2 — DART OpenAPI 공통 GET 래퍼 + 엔드포인트 thin wrapper.

설계 원칙:
  • 실패는 예외 대신 **빈 리스트**로 흡수 → 상위 로직 흐름 단순화.
  • `status="013"`(조회 데이터 없음)은 정상 상황. 로그 없이 []를 돌려줌.
  • 일부 엔드포인트는 특정 파라미터 조합에서만 유효 — 각 wrapper가
    필요한 파라미터를 캡슐화하고, 호출자는 의미 있는 인자만 넘긴다.
"""
from __future__ import annotations
import time
from typing import Any, Dict, List, Optional

import requests

from .config import (
    DART_API_KEY,
    DART_BASE_URL,
    MAX_RETRIES,
    REQUEST_DELAY,
    REQUEST_TIMEOUT,
)


# ── 저수준 GET ───────────────────────────────────────────────────────────
_session: Optional[requests.Session] = None


def _sess() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
    return _session


def get_json(endpoint: str, **params) -> Dict[str, Any]:
    """DART `/api/<endpoint>` 호출 → JSON dict 반환.

    예외/에러도 항상 dict 반환 (`status`, `message`, `list`).
    네트워크 장애 시 최대 MAX_RETRIES 번 재시도 (exp backoff).
    """
    url = f"{DART_BASE_URL}/{endpoint}"
    full = {**params, "crtfc_key": DART_API_KEY}
    last_err = ""
    for attempt in range(MAX_RETRIES):
        try:
            r = _sess().get(url, params=full, timeout=REQUEST_TIMEOUT)
            time.sleep(REQUEST_DELAY)
            try:
                data = r.json()
            except ValueError:
                return {"status": "ERR", "message": "비 JSON 응답", "list": []}
            return data
        except requests.RequestException as exc:
            last_err = str(exc)
            time.sleep(0.5 * (2 ** attempt))
    return {"status": "ERR", "message": last_err, "list": []}


def get_list(endpoint: str, **params) -> List[Dict[str, Any]]:
    """편의: `list` 필드만 추출 (status != 000 이면 빈 리스트)."""
    d = get_json(endpoint, **params)
    if d.get("status") == "000":
        lst = d.get("list")
        if isinstance(lst, list):
            return lst
    return []


def get_binary(endpoint: str, **params) -> Optional[bytes]:
    """document.xml / fnlttXbrl.xml 등 ZIP 바이너리."""
    url = f"{DART_BASE_URL}/{endpoint}"
    full = {**params, "crtfc_key": DART_API_KEY}
    for attempt in range(MAX_RETRIES):
        try:
            r = _sess().get(url, params=full, timeout=REQUEST_TIMEOUT)
            time.sleep(REQUEST_DELAY)
            ctype = r.headers.get("Content-Type", "")
            # 에러 응답은 XML/JSON으로 옴
            if r.status_code == 200 and "xml" not in ctype and "json" not in ctype:
                return r.content
            # application/x-msdownload / octet-stream 등
            if r.status_code == 200 and r.content[:2] == b"PK":
                return r.content
            return None
        except requests.RequestException:
            time.sleep(0.5 * (2 ** attempt))
    return None


# ── 엔드포인트 thin wrapper ──────────────────────────────────────────────
# 각 wrapper는 DART API 문서의 path 이름을 그대로 함수명으로 쓴다.

def company(corp_code: str) -> Dict[str, Any]:
    """기업개황. 단건 dict (status=000이면 그대로, 아니면 {})."""
    d = get_json("company.json", corp_code=corp_code)
    return d if d.get("status") == "000" else {}


def list_disclosures(
    corp_code: str,
    bgn_de: str,
    end_de: str,
    page_no: int = 1,
    page_count: int = 100,
    last_reprt_at: str = "N",
) -> Dict[str, Any]:
    """공시 목록 (페이징). raw 응답 dict 그대로."""
    return get_json(
        "list.json",
        corp_code=corp_code,
        bgn_de=bgn_de,
        end_de=end_de,
        page_no=str(page_no),
        page_count=str(page_count),
        last_reprt_at=last_reprt_at,
    )


def iter_all_disclosures(
    corp_code: str,
    bgn_de: str,
    end_de: str,
    page_count: int = 100,
    max_pages: int = 50,
) -> List[Dict[str, Any]]:
    """페이징을 순회해 전체 공시 list를 합쳐 반환."""
    out: List[Dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        d = list_disclosures(
            corp_code, bgn_de, end_de, page_no=page, page_count=page_count
        )
        if d.get("status") != "000":
            break
        chunk = d.get("list") or []
        out.extend(chunk)
        total_page = int(d.get("total_page") or 1)
        if page >= total_page:
            break
    return out


def document_zip(rcept_no: str) -> Optional[bytes]:
    """공시 원본 ZIP 바이너리. 실패 시 None."""
    return get_binary("document.xml", rcept_no=rcept_no)


# ── 재무 ─────────────────────────────────────────────────────────────────
def fnltt_singl_acnt(corp_code: str, bsns_year: str, reprt_code: str):
    return get_list(
        "fnlttSinglAcnt.json",
        corp_code=corp_code, bsns_year=bsns_year, reprt_code=reprt_code,
    )


def fnltt_singl_indx(corp_code: str, bsns_year: str, reprt_code: str, idx_cl_code: str):
    return get_list(
        "fnlttSinglIndx.json",
        corp_code=corp_code, bsns_year=bsns_year,
        reprt_code=reprt_code, idx_cl_code=idx_cl_code,
    )


# ── 주주 ─────────────────────────────────────────────────────────────────
def major_holders(corp_code: str, bsns_year: str, reprt_code: str):
    return get_list(
        "hyslrSttus.json",
        corp_code=corp_code, bsns_year=bsns_year, reprt_code=reprt_code,
    )


def major_holder_changes(corp_code: str, bsns_year: str, reprt_code: str):
    return get_list(
        "hyslrChgSttus.json",
        corp_code=corp_code, bsns_year=bsns_year, reprt_code=reprt_code,
    )


def minority_holders(corp_code: str, bsns_year: str, reprt_code: str):
    return get_list(
        "mrhlSttus.json",
        corp_code=corp_code, bsns_year=bsns_year, reprt_code=reprt_code,
    )


def major_stock_report(corp_code: str):
    """대량보유 5% rule (기간 파라미터 없음, 누적)."""
    return get_list("majorstock.json", corp_code=corp_code)


def executive_stock_report(corp_code: str):
    """임원·주요주주 소유보고 (기간 파라미터 없음)."""
    return get_list("elestock.json", corp_code=corp_code)


# ── 경영진·배당·자회사·감사 ────────────────────────────────────────────
def executives(corp_code: str, bsns_year: str, reprt_code: str):
    return get_list(
        "exctvSttus.json",
        corp_code=corp_code, bsns_year=bsns_year, reprt_code=reprt_code,
    )


def dividends(corp_code: str, bsns_year: str, reprt_code: str):
    return get_list(
        "alotMatter.json",
        corp_code=corp_code, bsns_year=bsns_year, reprt_code=reprt_code,
    )


def other_corp_investment(corp_code: str, bsns_year: str, reprt_code: str):
    return get_list(
        "otrCprInvstmntSttus.json",
        corp_code=corp_code, bsns_year=bsns_year, reprt_code=reprt_code,
    )


def audit_opinion(corp_code: str, bsns_year: str, reprt_code: str):
    return get_list(
        "accnutAdtorNmNdAdtOpinion.json",
        corp_code=corp_code, bsns_year=bsns_year, reprt_code=reprt_code,
    )
