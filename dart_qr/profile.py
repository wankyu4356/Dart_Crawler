# -*- coding: utf-8 -*-
"""M3 — Company Profile 수집.

`company.json` 응답에서 임원/CEO/업종/결산월 등 기본 정보를 뽑고,
기간(X년/개월) 역산 계산 유틸을 제공한다.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
from typing import Any, Dict, Tuple

from .config import PBLNTF_TY_LABEL
from . import dart_api as api


# ── 프로파일 DTO ──────────────────────────────────────────────────────────
@dataclass
class Profile:
    corp_code: str
    corp_name: str
    corp_name_eng: str = ""
    stock_code: str = ""
    stock_name: str = ""
    ceo_nm: str = ""
    corp_cls: str = ""       # Y(유가) K(코스닥) N(코넥스) E(기타)
    jurir_no: str = ""       # 법인등록번호
    bizr_no: str = ""        # 사업자등록번호
    adres: str = ""
    hm_url: str = ""
    ir_url: str = ""
    phn_no: str = ""
    fax_no: str = ""
    induty_code: str = ""
    est_dt: str = ""         # 설립일 YYYYMMDD
    acc_mt: str = ""         # 결산월 MM

    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def corp_cls_label(self) -> str:
        return {
            "Y": "코스피",
            "K": "코스닥",
            "N": "코넥스",
            "E": "비상장",
        }.get(self.corp_cls, self.corp_cls or "-")

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["corp_cls_label"] = self.corp_cls_label
        d.pop("raw", None)
        return d


# ── 조회 ───────────────────────────────────────────────────────────────
def fetch_profile(corp_code: str, corp_name_hint: str = "") -> Profile:
    """`company.json` → Profile. 실패 시 최소 정보만 담긴 Profile 반환."""
    raw = api.company(corp_code)
    if not raw:
        return Profile(corp_code=corp_code, corp_name=corp_name_hint)
    return Profile(
        corp_code=corp_code,
        corp_name=raw.get("corp_name") or corp_name_hint,
        corp_name_eng=raw.get("corp_name_eng", ""),
        stock_code=raw.get("stock_code", "") or "",
        stock_name=raw.get("stock_name", ""),
        ceo_nm=raw.get("ceo_nm", ""),
        corp_cls=raw.get("corp_cls", ""),
        jurir_no=raw.get("jurir_no", ""),
        bizr_no=raw.get("bizr_no", ""),
        adres=raw.get("adres", ""),
        hm_url=raw.get("hm_url", ""),
        ir_url=raw.get("ir_url", ""),
        phn_no=raw.get("phn_no", ""),
        fax_no=raw.get("fax_no", ""),
        induty_code=raw.get("induty_code", ""),
        est_dt=raw.get("est_dt", ""),
        acc_mt=raw.get("acc_mt", ""),
        raw=raw,
    )


# ── 기간 계산 ────────────────────────────────────────────────────────────
def period_from_value(value: int, unit: str, ref: date | None = None) -> Tuple[str, str, str]:
    """X년 또는 X개월 → (bgn_de, end_de, 라벨) YYYYMMDD.

    unit: '년' | '개월' (영문 'years'|'months' 도 허용)
    """
    if ref is None:
        ref = date.today()
    u = (unit or "").strip().lower()
    if u in ("년", "y", "year", "years"):
        days = int(value) * 365
        label = f"최근 {value}년"
    elif u in ("개월", "월", "m", "month", "months"):
        days = int(value) * 30
        label = f"최근 {value}개월"
    else:
        raise ValueError(f"알 수 없는 기간 단위: {unit}")
    bgn = ref - timedelta(days=days)
    return bgn.strftime("%Y%m%d"), ref.strftime("%Y%m%d"), label


# ── 공시 분류 헬퍼 (disclosures 모듈과 공유) ─────────────────────────────
def classify_pblntf(pblntf_ty: str) -> str:
    return PBLNTF_TY_LABEL.get((pblntf_ty or "").upper(), pblntf_ty or "-")
