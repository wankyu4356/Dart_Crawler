# -*- coding: utf-8 -*-
"""M4 — 재무정보 수집 (확장).

`fnlttSinglAcntAll.json` (CFS 우선, OFS fallback) 의 응답에서 IS/BS/CF
라인을 모두 스캔해 PE 분석에 필요한 KPI 를 추출:

  IS: 매출액 / 매출원가 / 매출총이익 / 판관비 / 영업이익 / 당기순이익
  CF: 감가상각비(D) / 무형자산상각비(A) → D&A → EBITDA 파생
  BS: 자산총계 / 부채총계 / 자본총계
  파생: GPM% / OPM% / EBITDAM% / NPM%

분기 보고서(11013/11012/11014) 도 동일 로직으로 thstrm_amount 또는
thstrm_add_amount(누적) 우선 사용.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional

from .config import REPRT_CODE
from . import dart_api as api


# ── 계정명 정규화 → key ─────────────────────────────────────────────────
# 공백 제거 후 비교 (DART 응답에 공백/괄호 표기가 다양함)
IS_ACCOUNT_MAP: Dict[str, str] = {
    "매출액":           "revenue",
    "수익(매출액)":      "revenue",
    "매출":             "revenue",
    "영업수익":          "revenue",
    "매출액(영업수익)":   "revenue",

    "매출원가":          "cost_of_sales",
    "용역원가":          "cost_of_sales",
    "영업비용":          "cost_of_sales",  # IS에 매출원가 없을 때 fallback (성격별 분류)

    "매출총이익":        "gross_profit",
    "매출총이익(손실)":   "gross_profit",
    "매출총손익":        "gross_profit",

    "판매비와관리비":     "sga",
    "판매비및관리비":     "sga",
    "판관비":            "sga",

    "영업이익":          "op_income",
    "영업이익(손실)":     "op_income",
    "영업손익":          "op_income",
    "영업손실(이익)":     "op_income",

    "당기순이익":         "net_income",
    "당기순이익(손실)":    "net_income",
    "당기순손익":         "net_income",
    "분기순이익":         "net_income",
    "반기순이익":         "net_income",
    "연결당기순이익":      "net_income",
    "연결당기순손익":      "net_income",
}

BS_ACCOUNT_MAP: Dict[str, str] = {
    "자산총계":          "total_assets",
    "부채총계":          "total_liabilities",
    "자본총계":          "total_equity",
}

# ── CF/IS 에서 D&A 추출 ────────────────────────────────────────────────
# [1] XBRL account_id 매칭 — 가장 신뢰성 높음
# D&A 합계를 한 라인으로 제공하는 표준 ID (있으면 그대로 da 로 사용)
DA_ID_TOTAL = {
    "ifrs-full_DepreciationAndAmortisationExpense",
    "ifrs-full_DepreciationAmortisationAndImpairmentLossReversalOfImpairmentLossRecognisedInProfitOrLoss",
    "dart_DepreciationAndAmortisation",
}
DA_TOTAL_ID_PREFIXES = (
    "ifrs-full_DepreciationAndAmortisation",
    "dart_DepreciationAndAmortisation",
)

# 유형자산 감가상각 전용 ID
DEP_IDS = {
    "ifrs-full_DepreciationPropertyPlantAndEquipment",
    "ifrs-full_DepreciationExpense",
    "dart_DepreciationPropertyPlantAndEquipment",
}
DEP_ID_PREFIXES = (
    "ifrs-full_Depreciation",     # DepreciationPropertyPlantAndEquipment 등
    "dart_Depreciation",
    "entity_Depreciation",
)

# 무형자산 상각 전용 ID
AMORT_IDS = {
    "ifrs-full_AmortisationIntangibleAssetsOtherThanGoodwill",
    "ifrs-full_AmortisationExpense",
    "dart_AmortisationOfIntangibleAssets",
}
AMORT_ID_PREFIXES = (
    "ifrs-full_Amortisation",
    "ifrs-full_AmortizationOf",
    "dart_Amortisation",
    "dart_Amortization",
    "entity_Amortisation",
)

# 제외 (사용권/리스 자산 상각 — 운영리스에 가까워 전통적 D&A에서 분리)
EXCLUDE_IDS = {
    "ifrs-full_DepreciationRightOfUseAssets",
}
EXCLUDE_ID_KEYWORDS = ("RightOfUse", "LeasedAssets", "LeaseAssets")

# [2] account_nm 패턴 매칭 (id 가 비표준이거나 없을 때도 함께 작동)
DEP_EXPLICIT_PATS = [
    "감가상각비에대한조정",
    "유형자산감가상각비",
    "유형자산의감가상각비",
    "유무형자산감가상각비",
    "유형자산및무형자산상각비",
    "감가상각비및무형자산상각비",
    "감가상각비",       # 단독
    "감가상각비용",
]
DEP_GENERAL = "감가상각"
DEP_EXCL_WORDS = ["무형", "사용권", "리스"]
AMORT_PATS = [
    "무형자산상각비에대한조정",
    "무형자산상각비",
    "무형자산의상각비",
    "무형자산상각",
    "무형자산및영업권상각",
    "영업권및무형자산상각",
    "무형자산및영업권의상각",
]


def _id_has_any(aid: str, prefixes: tuple) -> bool:
    return bool(aid) and any(aid.startswith(p) for p in prefixes)


def _id_is_excluded(aid: str) -> bool:
    if not aid:
        return False
    if aid in EXCLUDE_IDS:
        return True
    return any(kw in aid for kw in EXCLUDE_ID_KEYWORDS)


# ── 표시 라벨 / 분류 ────────────────────────────────────────────────────
KEY_LABEL: Dict[str, str] = {
    "revenue":           "매출액",
    "cost_of_sales":     "매출원가",
    "gross_profit":      "매출총이익",
    "gpm":               "GPM",
    "sga":               "판관비",
    "op_income":         "영업이익",
    "opm":               "OPM",
    "dep":               "감가상각비 (D)",
    "amort":             "무형상각비 (A)",
    "da":                "D&A 합계",
    "ebitda":            "EBITDA",
    "ebitdam":           "EBITDAM",
    "net_income":        "당기순이익",
    "npm":               "NPM",
    "total_assets":      "자산총계",
    "total_liabilities": "부채총계",
    "total_equity":      "자본총계",
}

# 화면 표시 순서
PERFORMANCE_KEYS = [
    "revenue", "cost_of_sales", "gross_profit", "gpm",
    "sga", "op_income", "opm",
    "dep", "amort", "da", "ebitda", "ebitdam",
    "net_income", "npm",
]
BALANCE_KEYS = ["total_assets", "total_liabilities", "total_equity"]
PCT_KEYS = {"gpm", "opm", "ebitdam", "npm"}


# ── DTO ──────────────────────────────────────────────────────────────────
@dataclass
class YearFin:
    year: int
    reprt_code: str
    reprt_label: str
    fs_div: str = ""
    currency: str = "KRW"
    values: Dict[str, Optional[float]] = field(default_factory=dict)


@dataclass
class FinancialsBundle:
    annual: List[YearFin] = field(default_factory=list)
    latest_quarter: Optional[YearFin] = None
    indicators: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # fnlttSinglAcntAll 응답 raw rows — Excel 상세 시트용 (계정별 전체 시계열)
    # 각 row 에 `_call_year`, `_reprt_label` 메타 필드 부여
    raw_rows: List[Dict[str, Any]] = field(default_factory=list)


# ── 파싱 / 추출 ──────────────────────────────────────────────────────────
def _to_num(s: Any) -> Optional[float]:
    if s is None or s == "" or s == "-":
        return None
    try:
        return float(str(s).replace(",", ""))
    except ValueError:
        return None


def _empty_values() -> Dict[str, Optional[float]]:
    return {k: None for k in (PERFORMANCE_KEYS + BALANCE_KEYS)}


def _set_first(d: Dict[str, Any], key: str, value: Any) -> None:
    if key not in d or d[key] is None:
        d[key] = value


def _extract_year_values(
    rows: List[Dict[str, Any]], period: str,
) -> Dict[str, Optional[float]]:
    """fnlttSinglAcntAll 의 list 응답에서 한 기간(thstrm/frmtrm/bfefrmtrm) 값 추출.

    D&A 추출 규칙:
      1) XBRL account_id 매칭 우선
         - DA_ID_TOTAL → 그대로 da 에 할당 (가장 신뢰)
         - DEP_IDS / AMORT_IDS → 각각 dep / amort 에 할당 (보고서당 최대 1건)
         - EXCLUDE_IDS 는 스킵
      2) id 매칭 결과가 있으면 같은 카테고리의 name 패턴 탐색은 생략 (중복 방지)
      3) name 패턴 매칭 시에는 **절댓값 최대 1건**만 채택 (합계 우선)
    """
    import os
    debug = os.environ.get("DART_QR_FIN_DEBUG") == "1"

    field_main = {
        "thstrm":     ["thstrm_amount", "thstrm_add_amount"],
        "frmtrm":     ["frmtrm_amount", "frmtrm_add_amount"],
        "bfefrmtrm":  ["bfefrmtrm_amount"],
    }[period]

    out = _empty_values()

    # D&A 후보 수집
    da_total_candidates: List[tuple[str, float]] = []
    dep_id_candidates:   List[tuple[str, float]] = []
    amort_id_candidates: List[tuple[str, float]] = []
    dep_nm_candidates:   List[tuple[str, float]] = []  # account_id 없을 때
    amort_nm_candidates: List[tuple[str, float]] = []

    for row in rows:
        nm_raw = (row.get("account_nm") or "")
        nm = nm_raw.replace(" ", "").replace("\u3000", "")
        aid = (row.get("account_id") or "").strip()
        sj = (row.get("sj_div") or "").upper()
        v: Optional[float] = None
        for f in field_main:
            v = _to_num(row.get(f))
            if v is not None:
                break
        if v is None:
            continue

        if sj in ("IS", "CIS"):
            key = IS_ACCOUNT_MAP.get(nm)
            if key:
                _set_first(out, key, v)
        elif sj == "BS":
            key = BS_ACCOUNT_MAP.get(nm)
            if key:
                _set_first(out, key, v)

        # D&A 는 CF(간접법 조정) 또는 IS(성격별) 어디서든 나올 수 있음
        if sj in ("CF", "IS", "CIS"):
            av = abs(v)
            matched_by_id = False
            # id 명시 제외 (사용권자산 등)
            if _id_is_excluded(aid):
                if debug:
                    print(f"  [SKIP-EXCLUDE] aid={aid} '{nm_raw}'", flush=True)
                continue
            # 이름 기반 사용권 제외 (id 없는 경우 대비)
            if ("사용권" in nm or "리스자산" in nm) and ("상각" in nm or "감가" in nm):
                if debug:
                    print(f"  [SKIP-ROU] '{nm_raw}'", flush=True)
                continue

            # id prefix/set 매칭 (prefix 우선, set 은 fallback)
            if _id_has_any(aid, DA_TOTAL_ID_PREFIXES) or aid in DA_ID_TOTAL:
                da_total_candidates.append((nm_raw, av))
                matched_by_id = True
            elif _id_has_any(aid, DEP_ID_PREFIXES) or aid in DEP_IDS:
                dep_id_candidates.append((nm_raw, av))
                matched_by_id = True
            elif _id_has_any(aid, AMORT_ID_PREFIXES) or aid in AMORT_IDS:
                amort_id_candidates.append((nm_raw, av))
                matched_by_id = True

            # id 매칭 여부와 무관하게 name 패턴도 보조로 스캔
            # (id set/prefix 에 없는 비표준 id 를 가진 회사 대응)
            if not matched_by_id:
                if any(p in nm for p in DEP_EXPLICIT_PATS):
                    dep_nm_candidates.append((nm_raw, av))
                elif DEP_GENERAL in nm and not any(ex in nm for ex in DEP_EXCL_WORDS):
                    dep_nm_candidates.append((nm_raw, av))
                if any(p in nm for p in AMORT_PATS):
                    amort_nm_candidates.append((nm_raw, av))

    # D&A 결정 (우선순위: id-total > id(dep+amort) > name(dep+amort))
    if da_total_candidates:
        # 여러 개면 가장 큰 값 (통상 합계 라인)
        nm, v = max(da_total_candidates, key=lambda x: x[1])
        out["da"] = v
        if debug:
            print(f"  [DA] id-total '{nm}' = {v:,.0f}", flush=True)
    else:
        # id 매칭 우선, 없으면 name 매칭
        if dep_id_candidates:
            nm, v = max(dep_id_candidates, key=lambda x: x[1])
            out["dep"] = v
            if debug:
                print(f"  [DEP] id '{nm}' = {v:,.0f}", flush=True)
        elif dep_nm_candidates:
            nm, v = max(dep_nm_candidates, key=lambda x: x[1])
            out["dep"] = v
            if debug:
                print(f"  [DEP] name '{nm}' = {v:,.0f}", flush=True)

        if amort_id_candidates:
            nm, v = max(amort_id_candidates, key=lambda x: x[1])
            out["amort"] = v
            if debug:
                print(f"  [AMORT] id '{nm}' = {v:,.0f}", flush=True)
        elif amort_nm_candidates:
            nm, v = max(amort_nm_candidates, key=lambda x: x[1])
            out["amort"] = v
            if debug:
                print(f"  [AMORT] name '{nm}' = {v:,.0f}", flush=True)

        if out["dep"] is not None or out["amort"] is not None:
            out["da"] = (out["dep"] or 0.0) + (out["amort"] or 0.0)

    # 파생: gross_profit
    if out["gross_profit"] is None and out["revenue"] is not None and out["cost_of_sales"] is not None:
        out["gross_profit"] = out["revenue"] - out["cost_of_sales"]
    # 파생: EBITDA = OP + D&A
    if out["op_income"] is not None and out["da"] is not None:
        out["ebitda"] = out["op_income"] + out["da"]

    # 마진
    rev = out["revenue"]
    if rev:
        def pct(x: Optional[float]) -> Optional[float]:
            return (x / rev * 100.0) if x is not None else None
        out["gpm"]      = pct(out.get("gross_profit"))
        out["opm"]      = pct(out.get("op_income"))
        out["ebitdam"]  = pct(out.get("ebitda"))
        out["npm"]      = pct(out.get("net_income"))

    return out


# ── fetch ────────────────────────────────────────────────────────────────
def _fetch_full_with_fallback(
    corp_code: str, bsns_year: str, reprt_code: str,
) -> tuple[List[Dict[str, Any]], str]:
    """CFS + OFS 양쪽을 모두 시도해서 한쪽에만 있는 라인(특히 D&A)까지 커버.
    반환값 (combined_rows, fs_used). fs_used 는 CFS 를 우선 표기.

    중복 방지: (sj_div, account_id, account_nm) 키로 dedup.
    둘 다 있으면 CFS 우선.
    """
    by_key: Dict[tuple, Dict[str, Any]] = {}
    fs_seen: List[str] = []
    for fs in ("CFS", "OFS"):
        rows = api.fnltt_singl_acnt_all(corp_code, bsns_year, reprt_code, fs_div=fs)
        if not rows:
            continue
        fs_seen.append(fs)
        for r in rows:
            key = (
                (r.get("sj_div") or "").upper(),
                (r.get("account_id") or "").strip(),
                (r.get("account_nm") or "").strip(),
            )
            # CFS 우선: 이미 키가 있으면 덮어쓰지 않음
            if key not in by_key:
                by_key[key] = r
    if not by_key:
        return [], ""
    fs_used = fs_seen[0] if fs_seen else ""
    return list(by_key.values()), fs_used


def fetch_annual_financials(
    corp_code: str,
    years_back: int = 4,
    ref_year: Optional[int] = None,
    raw_out: Optional[List[Dict[str, Any]]] = None,
) -> List[YearFin]:
    if ref_year is None:
        ref_year = date.today().year
    annual: List[YearFin] = []
    seen: set[int] = set()
    for y in range(ref_year, ref_year - years_back - 2, -1):
        if len(annual) >= years_back:
            break
        rows, fs_used = _fetch_full_with_fallback(corp_code, str(y), REPRT_CODE["FY"])
        if not rows:
            continue
        currency = next((r.get("currency") for r in rows if r.get("currency")), "KRW")
        # raw 수집 (Excel 상세 시트용)
        if raw_out is not None:
            for r in rows:
                rr = dict(r)
                rr["_call_year"] = y
                rr["_reprt_label"] = "사업보고서"
                rr["_fs_div"] = fs_used
                raw_out.append(rr)
        for period, year_offset in [("thstrm", 0), ("frmtrm", 1), ("bfefrmtrm", 2)]:
            yr = y - year_offset
            if yr in seen:
                continue
            vals = _extract_year_values(rows, period)
            if any(v is not None for v in vals.values()):
                annual.append(YearFin(
                    year=yr,
                    reprt_code=REPRT_CODE["FY"],
                    reprt_label="사업보고서",
                    fs_div=fs_used,
                    currency=currency,
                    values=vals,
                ))
                seen.add(yr)
    annual.sort(key=lambda f: f.year, reverse=True)
    return annual[:years_back]


QUARTER_PRIORITY = [
    ("Q3", "3분기보고서"),
    ("H1", "반기보고서"),
    ("Q1", "1분기보고서"),
]


def fetch_latest_quarterly(
    corp_code: str,
    ref_year: Optional[int] = None,
    raw_out: Optional[List[Dict[str, Any]]] = None,
) -> Optional[YearFin]:
    if ref_year is None:
        ref_year = date.today().year
    for y in (ref_year, ref_year - 1):
        for key, label in QUARTER_PRIORITY:
            rows, fs_used = _fetch_full_with_fallback(corp_code, str(y), REPRT_CODE[key])
            if not rows:
                continue
            # raw 수집
            if raw_out is not None:
                for r in rows:
                    rr = dict(r)
                    rr["_call_year"] = y
                    rr["_reprt_label"] = label
                    rr["_fs_div"] = fs_used
                    raw_out.append(rr)
            vals = _extract_year_values(rows, "thstrm")
            if any(v is not None for v in vals.values()):
                currency = next((r.get("currency") for r in rows if r.get("currency")), "KRW")
                return YearFin(
                    year=y,
                    reprt_code=REPRT_CODE[key],
                    reprt_label=label,
                    fs_div=fs_used,
                    currency=currency,
                    values=vals,
                )
    return None


INDICATOR_GROUPS = {
    "수익성": "M210000",
    "안정성": "M220000",
    "성장성": "M230000",
    "활동성": "M240000",
}


def fetch_indicators(
    corp_code: str, bsns_year: str, reprt_code: str,
) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for label, code in INDICATOR_GROUPS.items():
        items = api.fnltt_singl_indx(corp_code, bsns_year, reprt_code, code)
        if items:
            out[label] = items
    return out


def fetch_all(
    corp_code: str,
    years_back: int = 4,
    ref_year: Optional[int] = None,
) -> FinancialsBundle:
    raw_rows: List[Dict[str, Any]] = []
    annual = fetch_annual_financials(
        corp_code, years_back=years_back, ref_year=ref_year, raw_out=raw_rows,
    )
    latest_q = fetch_latest_quarterly(
        corp_code, ref_year=ref_year, raw_out=raw_rows,
    )

    indicators: Dict[str, Dict[str, Any]] = {}
    if annual:
        most = annual[0]
        ind = fetch_indicators(corp_code, str(most.year), most.reprt_code)
        if ind:
            indicators[f"{most.year} 사업보고서"] = ind
    if latest_q:
        ind = fetch_indicators(corp_code, str(latest_q.year), latest_q.reprt_code)
        if ind:
            indicators[f"{latest_q.year} {latest_q.reprt_label}"] = ind

    return FinancialsBundle(
        annual=annual, latest_quarter=latest_q,
        indicators=indicators, raw_rows=raw_rows,
    )


# ── 포맷 헬퍼 ────────────────────────────────────────────────────────────
def format_krw(value: Optional[float]) -> str:
    if value is None:
        return "-"
    absv = abs(value)
    sign = "-" if value < 0 else ""
    if absv >= 1e12:
        return f"{sign}{absv/1e12:,.2f}조"
    if absv >= 1e8:
        return f"{sign}{absv/1e8:,.0f}억"
    if absv >= 1e4:
        return f"{sign}{absv/1e4:,.0f}만"
    return f"{sign}{absv:,.0f}"


def format_pct(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return f"{value:+.1f}%" if value < 0 else f"{value:.1f}%"


def format_value(key: str, value: Optional[float]) -> str:
    if value is None:
        return "-"
    if key in PCT_KEYS:
        return f"{value:.1f}%"
    return format_krw(value)


def yoy(curr: Optional[float], prev: Optional[float]) -> Optional[float]:
    if curr is None or prev is None or prev == 0:
        return None
    return (curr - prev) / abs(prev) * 100.0
