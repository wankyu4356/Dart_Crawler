# -*- coding: utf-8 -*-
"""M6 — 공시 목록 조회 + 본문 추출.

  • `list.json` 페이징 순회로 기간 내 전체 공시 수집.
  • 중요 공시(`pblntf_ty in {A,B,D}`)는 `document.xml` 을 다운로드해
    ZIP 해제 후 텍스트만 추출. Claude 입력용으로 길이 cap.
"""
from __future__ import annotations
import html
import io
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree as ET

from .config import DART_VIEWER_URL, IMPORTANT_PBLNTF_TY, PBLNTF_TY_LABEL
from . import dart_api as api


# ── DTO ──────────────────────────────────────────────────────────────────
@dataclass
class Disclosure:
    rcept_no: str
    rcept_dt: str                 # YYYYMMDD
    corp_name: str
    report_nm: str
    flr_nm: str = ""              # 공시 제출인
    pblntf_ty: str = ""
    pblntf_detail_ty: str = ""
    rm: str = ""
    # LLM 단계에서 채워질 필드
    body: str = ""
    summary: str = ""
    key_points: List[str] = field(default_factory=list)
    implication: str = ""
    llm_status: str = "pending"   # pending | skipped | ok | error

    @property
    def ty_label(self) -> str:
        return PBLNTF_TY_LABEL.get(self.pblntf_ty, self.pblntf_ty or "-")

    @property
    def is_important(self) -> bool:
        return (self.pblntf_ty or "").upper() in IMPORTANT_PBLNTF_TY

    @property
    def viewer_url(self) -> str:
        return DART_VIEWER_URL.format(rcept_no=self.rcept_no)


# ── 목록 조회 ────────────────────────────────────────────────────────────
def _infer_pblntf_ty_from_report_nm(report_nm: str) -> str:
    """DART API 가 pblntf_ty 를 빈 문자열로 반환하는 케이스 대응.

    report_nm 에서 공시유형 prefix 를 추론해 A/B/D/F 중 하나 반환.
    매칭 안 되면 빈 문자열 (중요공시 아님).
    """
    nm = (report_nm or "").replace(" ", "")
    # 정기공시 (A)
    if any(k in nm for k in ("사업보고서", "반기보고서", "분기보고서")):
        return "A"
    # 외부감사 (F) — 감사보고서는 비상장사에서 주력
    if "감사보고서" in nm:
        return "F"
    # 지분공시 (D)
    if any(k in nm for k in (
        "주식등의대량보유", "임원·주요주주특정증권",
        "임원ㆍ주요주주특정증권", "최대주주변경",
        "최대주주등소유주식변동",
    )):
        return "D"
    # 주요사항 (B) — 유상증자·전환사채·합병·분할·감자·자사주·매각·취득 등
    if any(k in nm for k in (
        "주요사항보고서", "유상증자", "무상증자", "전환사채", "신주인수권부사채",
        "합병", "분할", "감자결정", "자기주식", "매각결정", "영업양도",
        "영업양수", "타법인주식", "해산", "회생", "파산",
    )):
        return "B"
    return ""


def fetch_list(corp_code: str, bgn_de: str, end_de: str) -> List[Disclosure]:
    rows = api.iter_all_disclosures(corp_code, bgn_de, end_de)
    out: List[Disclosure] = []
    for r in rows:
        raw_ty = (r.get("pblntf_ty") or "").strip()
        report_nm = r.get("report_nm", "")
        # DART API 가 pblntf_ty 를 빈 문자열로 반환하는 케이스 → report_nm 에서 추론
        if not raw_ty:
            raw_ty = _infer_pblntf_ty_from_report_nm(report_nm)
        out.append(Disclosure(
            rcept_no=str(r.get("rcept_no", "")).strip(),
            rcept_dt=str(r.get("rcept_dt", "")).strip(),
            corp_name=r.get("corp_name", ""),
            report_nm=report_nm,
            flr_nm=r.get("flr_nm", ""),
            pblntf_ty=raw_ty,
            pblntf_detail_ty=r.get("pblntf_detail_ty", ""),
            rm=r.get("rm", ""),
        ))
    # 최신 접수일 우선 정렬
    out.sort(key=lambda d: (d.rcept_dt, d.rcept_no), reverse=True)
    return out


# ── 본문 추출 ────────────────────────────────────────────────────────────
_WS_RE = re.compile(r"\s+")


def _xml_text(xml_bytes: bytes) -> str:
    """XML 에서 의미 있는 텍스트만 공백으로 병합."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        # 단순 fallback — 태그 제거
        raw = xml_bytes.decode("utf-8", errors="ignore")
        raw = re.sub(r"<[^>]+>", " ", raw)
        return _WS_RE.sub(" ", raw).strip()
    pieces: List[str] = []
    for el in root.iter():
        if el.text and el.text.strip():
            pieces.append(el.text.strip())
        if el.tail and el.tail.strip():
            pieces.append(el.tail.strip())
    return _WS_RE.sub(" ", " ".join(pieces)).strip()


def fetch_body(rcept_no: str, cap: int = 30000) -> str:
    """`document.xml` → ZIP 해제 → XML/HTML 본문 텍스트 합본. 실패 시 ''.

    일부 공시(특히 정정본)는 ZIP 안에 XML 없이 HTML/htm 만 있는 경우가 있어
    양쪽 모두 지원한다.
    """
    data = api.document_zip(rcept_no)
    if not data:
        return ""
    texts: List[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for name in zf.namelist():
                lower = name.lower()
                if lower.endswith(".xml"):
                    try:
                        t = _xml_text(zf.read(name))
                    except Exception:
                        continue
                elif lower.endswith((".html", ".htm")):
                    try:
                        raw = zf.read(name).decode("utf-8", errors="ignore")
                    except Exception:
                        continue
                    # script/style 제거
                    raw = re.sub(r"<script[\s\S]*?</script>", " ", raw,
                                 flags=re.I)
                    raw = re.sub(r"<style[\s\S]*?</style>", " ", raw,
                                 flags=re.I)
                    raw = re.sub(r"<br\s*/?>", "\n", raw, flags=re.I)
                    raw = re.sub(r"</p\s*>", "\n", raw, flags=re.I)
                    raw = re.sub(r"<[^>]+>", " ", raw)
                    t = html.unescape(raw)
                    t = _WS_RE.sub(" ", t).strip()
                else:
                    continue
                if t:
                    texts.append(t)
        text = "\n\n".join(texts)
    except zipfile.BadZipFile:
        return ""
    if cap and len(text) > cap:
        text = text[:cap] + "\n...[본문 cap]"
    return text


def fill_bodies_for_important(
    discs: List[Disclosure],
    limit: Optional[int] = None,
    cap: int = 30000,
    log=None,
) -> None:
    """중요 공시 대상으로 본문 채우기 (in-place).

    `limit` 지정 시 상위 N건만 처리 (최신순).
    """
    important = [d for d in discs if d.is_important]
    if limit is not None:
        important = important[:limit]
    for i, d in enumerate(important, 1):
        if log:
            log(f"  [{i}/{len(important)}] {d.rcept_dt} {d.report_nm[:40]}")
        d.body = fetch_body(d.rcept_no, cap=cap)
