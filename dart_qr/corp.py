# -*- coding: utf-8 -*-
"""M1 — 기업 고유번호(corp_code) 조회.

DART `corpCode.xml` 을 1회 다운로드해 프로세스 메모리에 캐시하고, 회사명으로
`(corp_code, corp_name, stock_code)` 를 찾는다. `dart_fill_v10.py` 의
`get_corp_list` / `search_corp` 패턴을 이식해 단독 모듈로 분리한 것.
"""
from __future__ import annotations
import io
import zipfile
from dataclasses import dataclass
from typing import Callable, List, Optional
from xml.etree import ElementTree as ET

import requests

from .config import DART_API_KEY, DART_BASE_URL, REQUEST_TIMEOUT


@dataclass(frozen=True)
class Corp:
    corp_code: str
    corp_name: str
    stock_code: str

    @property
    def is_listed(self) -> bool:
        return bool(self.stock_code and self.stock_code.strip())


_CORP_CACHE: Optional[List[Corp]] = None


def load_corp_index(log: Optional[Callable[[str], None]] = None) -> List[Corp]:
    """corpCode.xml(ZIP) → [Corp,…] 캐시 후 반환."""
    global _CORP_CACHE
    if _CORP_CACHE is not None:
        return _CORP_CACHE
    if log:
        log("DART 기업 목록 다운로드 중...")
    resp = requests.get(
        f"{DART_BASE_URL}/corpCode.xml",
        params={"crtfc_key": DART_API_KEY},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        xml_bytes = zf.read("CORPCODE.xml")
    root = ET.fromstring(xml_bytes)
    items: List[Corp] = []
    for el in root.findall("list"):
        items.append(
            Corp(
                corp_code=(el.findtext("corp_code") or "").strip(),
                corp_name=(el.findtext("corp_name") or "").strip(),
                stock_code=(el.findtext("stock_code") or "").strip(),
            )
        )
    _CORP_CACHE = items
    if log:
        log(f"  → {len(items):,}개 로드 완료")
    return items


def search_corp(
    name: str,
    log: Optional[Callable[[str], None]] = None,
    prefer_listed: bool = True,
) -> Corp:
    """회사명으로 단 1건 반환.

    규칙:
      1) 정확 일치 우선. 없으면 부분일치.
      2) prefer_listed=True 면 상장사(`stock_code` 존재)를 우선.
      3) 같은 조건이면 회사명이 짧은 것 우선 (모회사 선택 확률↑).
    """
    corps = load_corp_index(log)
    if not name or not name.strip():
        raise ValueError("회사명이 비어있습니다.")
    key = name.strip()
    exact = [c for c in corps if c.corp_name == key]
    partial = [c for c in corps if key in c.corp_name]
    pool = exact if exact else partial
    if not pool:
        raise ValueError(
            f"'{name}' 을(를) DART에서 찾을 수 없습니다. "
            "정확한 회사명을 확인하세요."
        )
    if prefer_listed:
        listed = [c for c in pool if c.is_listed]
        pool = listed or pool
    pool_sorted = sorted(pool, key=lambda c: (len(c.corp_name), c.corp_name))
    chosen = pool_sorted[0]
    if log:
        log(f"  → 선택: {chosen.corp_name} ({chosen.corp_code})"
            + (f" [종목 {chosen.stock_code}]" if chosen.is_listed else ""))
        # 동명이인(여러 후보) 이 있으면 상위 5건 같이 안내 — 원하는 곳이 아니면
        # 사용자가 회사명을 더 명확히 입력하도록.
        if len(pool) > 1:
            alts = pool_sorted[1:6]
            log(f"  (동명/유사 {len(pool)-1}건 존재 — 상위 {len(alts)}건:)")
            for c in alts:
                tag = f"[종목 {c.stock_code}]" if c.is_listed else "[비상장]"
                log(f"    • {c.corp_name} ({c.corp_code}) {tag}")
    return chosen


def search_corp_candidates(name: str, limit: int = 10) -> List[Corp]:
    """GUI 선택 UI용 — 이름 부분일치 상위 N건."""
    corps = load_corp_index()
    key = name.strip()
    if not key:
        return []
    hits = [c for c in corps if key in c.corp_name]
    hits.sort(key=lambda c: (not c.is_listed, len(c.corp_name)))
    return hits[:limit]
