# -*- coding: utf-8 -*-
"""M-fin4-A — Chart.js 용 데이터 빌더.

html_out.py 에서 호출. 각 함수는 Python dict/list 를 반환하고, html_out 이
`json.dumps(...)` 로 <script> 블록에 임베드한다. 순수 함수라 unit test 용이.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional

from .financials import FinancialsBundle, YearFin
from .shareholders import ShareholderBundle


def _f(x: Any) -> Optional[float]:
    if x in (None, "", "-"):
        return None
    try:
        return float(str(x).replace(",", ""))
    except ValueError:
        return None


# ── 재무 차트 데이터 ────────────────────────────────────────────────────
def _label_of(y: YearFin) -> str:
    return f"{y.year}"


def _annual_then_quarter(fin: FinancialsBundle) -> List[YearFin]:
    """그래프 X축 순서: 오래된 연도 → 최신 연도 → 최신 분기."""
    yrs = sorted(fin.annual, key=lambda f: f.year)
    if fin.latest_quarter is not None:
        yrs.append(fin.latest_quarter)
    return yrs


def build_performance_chart(fin: FinancialsBundle) -> Dict[str, Any]:
    """매출/영업이익/EBITDA/당기순이익 grouped bar (억원 단위)."""
    periods = _annual_then_quarter(fin)
    labels = []
    for y in periods:
        if y.reprt_code == "11011":
            labels.append(_label_of(y))
        else:
            labels.append(f"{y.year} {y.reprt_label}")

    def series(key: str) -> List[Optional[float]]:
        return [
            (y.values.get(key) / 1e8) if y.values.get(key) is not None else None
            for y in periods
        ]

    return {
        "labels": labels,
        "datasets": [
            {"label": "매출액",    "data": series("revenue"),    "backgroundColor": "#1a237e"},
            {"label": "영업이익",  "data": series("op_income"),  "backgroundColor": "#3949ab"},
            {"label": "EBITDA",   "data": series("ebitda"),     "backgroundColor": "#5c6bc0"},
            {"label": "당기순이익", "data": series("net_income"), "backgroundColor": "#7e57c2"},
        ],
        "unit": "억원",
    }


def build_margin_chart(fin: FinancialsBundle) -> Dict[str, Any]:
    """GPM / OPM / EBITDAM / NPM line (%)."""
    periods = _annual_then_quarter(fin)
    labels = []
    for y in periods:
        if y.reprt_code == "11011":
            labels.append(_label_of(y))
        else:
            labels.append(f"{y.year} {y.reprt_label}")

    def series(key: str) -> List[Optional[float]]:
        return [y.values.get(key) for y in periods]

    return {
        "labels": labels,
        "datasets": [
            {"label": "GPM",     "data": series("gpm"),     "borderColor": "#1a237e", "backgroundColor": "rgba(26,35,126,0.08)"},
            {"label": "OPM",     "data": series("opm"),     "borderColor": "#43a047", "backgroundColor": "rgba(67,160,71,0.08)"},
            {"label": "EBITDAM", "data": series("ebitdam"), "borderColor": "#ff6b35", "backgroundColor": "rgba(255,107,53,0.08)"},
            {"label": "NPM",     "data": series("npm"),     "borderColor": "#c62828", "backgroundColor": "rgba(198,40,40,0.08)"},
        ],
        "unit": "%",
    }


def build_bs_chart(fin: FinancialsBundle) -> Dict[str, Any]:
    """BS stacked bar: 부채 + 자본 = 자산 (조원 단위)."""
    periods = _annual_then_quarter(fin)
    labels = []
    for y in periods:
        if y.reprt_code == "11011":
            labels.append(_label_of(y))
        else:
            labels.append(f"{y.year} {y.reprt_label}")

    def series(key: str) -> List[Optional[float]]:
        return [
            (y.values.get(key) / 1e12) if y.values.get(key) is not None else None
            for y in periods
        ]

    return {
        "labels": labels,
        # stacked 순서: 자본이 아래(베이스), 부채가 위 (재무상태 관습)
        "datasets": [
            {"label": "자본", "data": series("total_equity"),       "backgroundColor": "#26a69a"},
            {"label": "부채", "data": series("total_liabilities"), "backgroundColor": "#ef5350"},
        ],
        "unit": "조원",
    }


# ── 지배구조 도넛 ──────────────────────────────────────────────────────
def build_ownership_chart(
    sh: ShareholderBundle,
    top_n: int = 4,
) -> Optional[Dict[str, Any]]:
    """지분율 도넛:
      • 최대주주 상위 top_n 명 개별 표시 (trmend_posesn_stock_qota_rt)
      • 나머지 특수관계인 합산 → "기타 특수관계인"
      • 소액주주 (mrhlSttus hold_stock_rate)
      • 그 외 잔여 지분 → "일반/자기주식 등"
    """
    if not sh.major:
        return None

    # 최대주주·특수관계인 리스트 (지분율 있는 것만)
    SKIP_NAMES = {"계", "소계", "합계", "총계", "보통주계", "우선주계"}
    parties: List[tuple[str, float]] = []
    for r in sh.major:
        rate = _f(r.get("trmend_posesn_stock_qota_rt"))
        if rate is None or rate <= 0:
            continue
        name = (r.get("nm") or "").strip() or "-"
        relate = (r.get("relate") or "").strip()
        # 합계/소계 행 제외
        if name in SKIP_NAMES:
            continue
        if not relate and name.endswith("계"):
            continue
        disp = f"{name}" if not relate or relate == "본인" else f"{name} ({relate})"
        parties.append((disp, rate))
    if not parties:
        return None
    parties.sort(key=lambda x: x[1], reverse=True)

    top = parties[:top_n]
    rest = parties[top_n:]
    rest_sum = sum(v for _, v in rest)
    major_total = sum(v for _, v in parties)

    labels: List[str] = [name for name, _ in top]
    data: List[float] = [round(v, 3) for _, v in top]
    colors = ["#1a237e", "#3949ab", "#5c6bc0", "#7e57c2", "#9575cd", "#b39ddb"]

    if rest_sum > 0:
        labels.append("기타 특수관계인")
        data.append(round(rest_sum, 3))

    # 소액주주
    minority_rate: Optional[float] = None
    if sh.minority:
        # "소액주주" 행의 hold_stock_rate
        for r in sh.minority:
            if "소액" in (r.get("se") or ""):
                minority_rate = _f(r.get("hold_stock_rate"))
                break
        if minority_rate is None:
            # fallback: 첫 번째 행
            minority_rate = _f(sh.minority[0].get("hold_stock_rate"))
    if minority_rate and minority_rate > 0:
        labels.append("소액주주")
        data.append(round(minority_rate, 3))

    # 잔여 = 100 - (major_total + minority_rate)
    used = major_total + (minority_rate or 0)
    remainder = round(100.0 - used, 3)
    if remainder > 0.1:
        labels.append("기타/자기주식")
        data.append(remainder)

    bg = []
    extra = ["#ff8a65", "#26a69a", "#bdbdbd"]
    for i in range(len(labels)):
        if i < len(top):
            bg.append(colors[i % len(colors)])
        else:
            bg.append(extra[(i - len(top)) % len(extra)])

    return {
        "labels": labels,
        "datasets": [{
            "data": data,
            "backgroundColor": bg,
            "borderWidth": 2,
            "borderColor": "#ffffff",
        }],
    }


# ── KPI 상단 박스용 요약 ────────────────────────────────────────────────
def build_kpi_cards(fin: FinancialsBundle) -> List[Dict[str, Any]]:
    """가장 최신 연간 재무 기준 KPI 박스 3~4개.
    YoY 증감은 전년 대비."""
    if not fin.annual:
        return []
    curr = fin.annual[0]
    prev = fin.annual[1] if len(fin.annual) >= 2 else None

    def _yoy(key: str) -> Optional[float]:
        if not prev:
            return None
        a = curr.values.get(key)
        b = prev.values.get(key)
        if a is None or b is None or b == 0:
            return None
        return (a - b) / abs(b) * 100.0

    cards = []
    if curr.values.get("revenue") is not None:
        cards.append({
            "label": "매출액",
            "value_raw": curr.values["revenue"],
            "yoy": _yoy("revenue"),
            "period": f"{curr.year} 사업연도",
        })
    if curr.values.get("op_income") is not None:
        cards.append({
            "label": "영업이익",
            "value_raw": curr.values["op_income"],
            "margin": curr.values.get("opm"),
            "yoy": _yoy("op_income"),
            "period": f"{curr.year}",
        })
    if curr.values.get("ebitda") is not None:
        cards.append({
            "label": "EBITDA",
            "value_raw": curr.values["ebitda"],
            "margin": curr.values.get("ebitdam"),
            "yoy": _yoy("ebitda"),
            "period": f"{curr.year}",
        })
    if curr.values.get("net_income") is not None:
        cards.append({
            "label": "당기순이익",
            "value_raw": curr.values["net_income"],
            "margin": curr.values.get("npm"),
            "yoy": _yoy("net_income"),
            "period": f"{curr.year}",
        })
    return cards
