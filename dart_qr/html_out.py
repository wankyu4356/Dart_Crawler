# -*- coding: utf-8 -*-
"""M9 (v2) — PE IC 리포트 수준의 HTML 리포트.

디자인 원칙
  • CSS Variables 기반 통일 디자인 시스템
  • Chart.js CDN (UMD) — 인터랙티브 툴팁/범례/애니메이션
  • KPI 박스 · 카드 그리드 · 지배구조 도넛 · 손익 콤보 · BS stacked
  • Executive Summary 가 비면 섹션 자체 생략 + 번호 자동 재할당
  • 인쇄(print) 시에도 레이아웃 유지

렌더 순서 (동적 번호):
  1. HERO 헤더
  2. KPI 박스 (매출/영업이익/EBITDA/당기순이익)
  3. Executive Summary   (있을 때만)
  4. 재무 하이라이트      (손익 bar + 마진 line + BS stacked)
  5. 지배구조            (도넛 + 표)
  6. 이슈 타임라인
  7. 핵심 이슈 카드      (주요사항 중 LLM 분석 완료분)
"""
from __future__ import annotations
import html
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from . import chart_data as cd
from .config import CONTACT_EMAIL, CONTACT_NAME
from .disclosures import Disclosure
from .financials import (
    BALANCE_KEYS, FinancialsBundle, KEY_LABEL, PCT_KEYS, PERFORMANCE_KEYS,
    format_krw, format_value, yoy,
)
from .profile import Profile
from .shareholders import ShareholderBundle, top_holder_summary


# ── util ────────────────────────────────────────────────────────────────
def _esc(s: Any) -> str:
    return html.escape(str(s) if s is not None else "")


def _fmt_date(s: str) -> str:
    s = (s or "").replace("-", "")
    if len(s) == 8:
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s or "-"


def _md_to_html(text: str) -> str:
    if not text:
        return ""
    lines = text.splitlines()
    out: List[str] = []
    in_ul = False
    for raw in lines:
        ln = raw.rstrip()
        if not ln.strip():
            if in_ul:
                out.append("</ul>")
                in_ul = False
            continue
        m_h = None
        for k in (4, 3, 2, 1):
            if ln.startswith("#" * k + " "):
                m_h = k
                break
        if m_h:
            if in_ul:
                out.append("</ul>")
                in_ul = False
            out.append(f"<h{m_h+2}>{_inline(ln[m_h+1:].strip())}</h{m_h+2}>")
            continue
        if ln.lstrip().startswith(("- ", "* ")):
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{_inline(ln.lstrip()[2:].strip())}</li>")
            continue
        if in_ul:
            out.append("</ul>")
            in_ul = False
        out.append(f"<p>{_inline(ln.strip())}</p>")
    if in_ul:
        out.append("</ul>")
    return "\n".join(out)


def _inline(s: str) -> str:
    s = html.escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"`([^`]+?)`", r"<code>\1</code>", s)
    return s


class SectionCounter:
    """섹션 번호를 렌더링 시 동적으로 매김.
    Exec Summary 가 생략돼도 이후 번호가 끊기지 않도록."""
    _NUMERALS = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩"]

    def __init__(self) -> None:
        self._n = 0

    def next(self) -> str:
        self._n += 1
        if self._n <= len(self._NUMERALS):
            return self._NUMERALS[self._n - 1]
        return str(self._n)


# ── 공시 유형 뱃지 ─────────────────────────────────────────────────────
BADGE_COLORS = {
    "A": ("정기", "#1E88E5"),
    "B": ("주요", "#E53935"),
    "C": ("발행", "#8E24AA"),
    "D": ("지분", "#43A047"),
    "E": ("기타", "#757575"),
    "F": ("감사", "#FB8C00"),
    "G": ("펀드", "#00897B"),
    "H": ("유동화", "#6D4C41"),
    "I": ("거래소", "#546E7A"),
    "J": ("공정위", "#5E35B1"),
}


def _badge(ty: str) -> str:
    label, color = BADGE_COLORS.get(ty, (ty or "-", "#999"))
    return (
        f'<span class="badge" style="background:{color}">{_esc(label)}</span>'
    )


# ── CSS 디자인 시스템 ──────────────────────────────────────────────────
CSS = """
:root{
  --bg:#f4f5f7;
  --surface:#ffffff;
  --primary:#1a237e;
  --primary-2:#3949ab;
  --primary-3:#5c6bc0;
  --accent:#ff6b35;
  --pos:#2e7d32;
  --neg:#c62828;
  --ink:#1c1f24;
  --ink-2:#37474f;
  --muted:#78909c;
  --border:#eceff4;
  --radius:12px;
  --shadow:0 1px 3px rgba(0,0,0,.06), 0 0 0 1px rgba(0,0,0,.02);
  --shadow-lg:0 8px 24px rgba(26,35,126,.08);
}

*{box-sizing:border-box;}
html,body{margin:0;padding:0;}
body{
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans KR',
              'Malgun Gothic',Arial,sans-serif;
  background:var(--bg); color:var(--ink); line-height:1.55;
  font-feature-settings:"tnum";
}
.wrap{max-width:1180px; margin:0 auto; padding:28px 24px 60px;}

/* HERO */
.hero{
  background:linear-gradient(135deg,var(--primary) 0%, var(--primary-2) 70%, var(--primary-3) 100%);
  color:#fff; padding:36px 36px 28px; border-radius:16px;
  box-shadow:var(--shadow-lg); position:relative; overflow:hidden;
}
.hero::after{
  content:""; position:absolute; right:-80px; top:-80px;
  width:260px; height:260px; border-radius:50%;
  background:radial-gradient(circle, rgba(255,107,53,.25), transparent 70%);
  pointer-events:none;
}
.hero h1{margin:0 0 4px 0; font-size:30px; letter-spacing:-.01em;}
.hero .sub{font-size:14px; opacity:.72; margin-left:10px; font-weight:400;}
.hero .meta{
  display:flex; flex-wrap:wrap; gap:10px 20px;
  font-size:13px; opacity:.92; margin-top:12px;
}
.hero .meta span b{font-weight:600;}
.hero .period-badge{
  display:inline-block; background:rgba(255,255,255,.14);
  padding:3px 10px; border-radius:999px;
  font-size:12px; margin-top:10px; backdrop-filter:blur(4px);
}

/* KPI row */
.kpis{
  display:grid;
  grid-template-columns:repeat(auto-fit, minmax(220px, 1fr));
  gap:14px; margin:18px 0 6px;
}
.kpi{
  background:var(--surface); border-radius:var(--radius);
  padding:16px 18px; box-shadow:var(--shadow);
  border-left:4px solid var(--primary);
}
.kpi.kpi-op{border-left-color:var(--primary-2);}
.kpi.kpi-ebitda{border-left-color:var(--accent);}
.kpi.kpi-net{border-left-color:#7e57c2;}
.kpi .label{
  font-size:11px; color:var(--muted);
  text-transform:uppercase; letter-spacing:.08em; font-weight:600;
}
.kpi .value{
  font-size:24px; font-weight:700; color:var(--ink);
  margin-top:6px; letter-spacing:-.01em;
  font-variant-numeric:tabular-nums;
}
.kpi .sub{font-size:12px; color:var(--muted); margin-top:4px;}
.kpi .yoy{font-weight:600; margin-left:4px;}
.kpi .yoy.pos{color:var(--pos);}
.kpi .yoy.neg{color:var(--neg);}
.kpi .margin-pill{
  display:inline-block; background:#eef2fb; color:var(--primary);
  font-size:11px; padding:2px 8px; border-radius:999px;
  font-weight:600; margin-left:4px;
}

/* Section card */
section.card{
  background:var(--surface); border-radius:var(--radius);
  padding:22px 26px; margin:18px 0;
  box-shadow:var(--shadow);
}
section.card > h2{
  margin:0 0 14px 0; padding-bottom:10px;
  border-bottom:2px solid var(--border);
  font-size:18px; letter-spacing:-.01em;
  display:flex; align-items:center; gap:10px;
}
section.card h2 .secnum{
  display:inline-block; color:var(--primary);
  font-size:15px; font-weight:700;
  min-width:22px;
}
section.card h2 .hint{
  font-size:12px; color:var(--muted); font-weight:400; margin-left:auto;
}
section.card h3{margin-top:18px; margin-bottom:8px; font-size:14px; color:var(--ink-2);}
.muted{color:var(--muted);} .small{font-size:12px;}

/* Business Profile */
.biz .biz-summary{
  background:#f3f6ff; border-left:4px solid var(--primary);
  padding:12px 16px; border-radius:8px; font-size:14.5px;
  line-height:1.6;
}
.chips{display:flex; flex-wrap:wrap; gap:6px; margin:4px 0 10px;}
.chip{
  display:inline-block; background:#eef2fb; color:var(--primary);
  padding:4px 10px; border-radius:999px; font-size:12px; font-weight:500;
}
table.seg td, table.seg th{font-size:13px;}
table.seg td:nth-child(2), table.seg td:nth-child(3){
  text-align:right; font-variant-numeric:tabular-nums;
}
.two-col{
  display:grid; grid-template-columns:1fr 1fr; gap:18px;
  margin:10px 0;
}
@media (max-width:720px){ .two-col{grid-template-columns:1fr;} }
ul.insights{margin:4px 0 0 18px; padding:0;}
ul.insights li{
  margin:4px 0; padding:4px 8px; background:#fff8ef;
  border-left:3px solid var(--accent); border-radius:4px;
}

/* Exec body */
.exec{font-size:14.5px;}
.exec h2{border:none; font-size:16px; margin-top:14px; padding:0;}
.exec h3{font-size:14px;}
.exec ul{padding-left:20px;}
.exec li{margin:2px 0;}
.exec code{background:#f5f7fa; padding:1px 6px; border-radius:4px; font-size:13px;}

/* Tables */
table{
  width:100%; border-collapse:separate; border-spacing:0;
  font-size:13px;
}
table th,table td{
  padding:7px 10px; border-bottom:1px solid var(--border);
  text-align:left; vertical-align:middle;
}
table thead th{
  background:#f5f7fa; font-weight:600;
  position:sticky; top:0;
}
table tbody tr:hover{background:#fafbfe;}
table.fin td, table.fin th{text-align:right; font-variant-numeric:tabular-nums;}
table.fin th:first-child, table.fin td:first-child{text-align:left;}
table.fin tr.section-head td{
  background:#eef2fb; font-weight:600; color:var(--primary);
  padding:6px 10px;
}
table.fin tr.yoy td{color:var(--ink-2); font-weight:600;}
table.fin tr.yoy{background:#fafbfe;}
table.fin tr.ratio td, table.fin tr.ratio th{
  font-style: italic;
  background: #f7f9fc;
  color: var(--primary);
}
/* 음수 비율은 빨강 강조 (ratio 보다 우선) */
table.fin tr.ratio td.neg{
  color: var(--neg) !important;
  background: #fff0f3 !important;
  font-weight: 600;
}
table.fin .pos{color:var(--pos);} table.fin .neg{color:var(--neg);}

/* Charts grid */
.chart-grid{
  display:grid;
  grid-template-columns:1fr 1fr;
  gap:18px; margin:8px 0 4px;
}
.chart-box{
  background:#fafbfe; border:1px solid var(--border);
  border-radius:10px; padding:14px 16px;
  min-height:300px;
}
.chart-box h4{
  margin:0 0 10px 0; font-size:13px; color:var(--ink-2);
  display:flex; justify-content:space-between; align-items:center;
}
.chart-box h4 .unit{color:var(--muted); font-size:11px; font-weight:400;}
.chart-box canvas{width:100% !important; max-height:280px;}
.chart-box.wide{grid-column:1 / -1;}
@media (max-width: 900px){ .chart-grid{grid-template-columns:1fr;} }

/* Ownership */
.ownership{
  display:grid; grid-template-columns: 1fr 1.5fr; gap:18px;
  align-items:start;
}
@media (max-width: 900px){ .ownership{grid-template-columns:1fr;} }
.top-holder{
  background:#f3f6ff; border-left:4px solid var(--primary);
  padding:10px 14px; border-radius:8px; margin-bottom:10px;
}
.top-holder b{color:var(--primary);}

/* Badge */
.badge{
  display:inline-block; background:#999; color:#fff;
  padding:2px 9px; border-radius:999px;
  font-size:11px; font-weight:600; margin-right:6px;
}

/* Timeline */
.timeline{border-left:2px solid var(--border); padding-left:16px; margin-top:6px;}
.tl-item{margin:12px 0; position:relative;}
.tl-item::before{
  content:""; position:absolute; left:-22px; top:6px;
  width:10px; height:10px; border-radius:50%; background:var(--primary);
  box-shadow:0 0 0 3px #fff;
}
.tl-item .date{font-size:12px; color:var(--muted); margin-bottom:2px;}
.tl-item .ttl{
  font-weight:600; color:var(--primary);
  text-decoration:none; font-size:14px;
}
.tl-item .ttl:hover{text-decoration:underline;}
.tl-item .flr{margin-left:8px;}
.llmblk{
  background:#fafbfe; border:1px solid var(--border); border-radius:8px;
  padding:10px 14px; margin-top:6px;
}
.llmblk .summary{margin:2px 0 6px 0; font-size:13px;}
.llmblk ul.kp{margin:0 0 6px 20px; padding:0; font-size:13px; color:var(--ink-2);}
.llmblk .implication{
  margin:0; font-size:13px; color:var(--neg);
  background:#fff8f8; padding:7px 10px; border-radius:6px;
  border-left:3px solid var(--neg);
}

/* Key issues grid */
.issues{display:grid; grid-template-columns:repeat(auto-fit, minmax(320px, 1fr)); gap:14px;}
.issue{
  background:var(--surface); border:1px solid var(--border);
  border-radius:10px; padding:14px 16px;
  transition:box-shadow .15s;
}
.issue:hover{box-shadow:var(--shadow-lg);}
.issue header{display:flex; justify-content:space-between; margin-bottom:6px; font-size:13px;}
.issue header time{color:var(--muted);}
.issue header a{color:var(--primary); text-decoration:none; font-weight:600;}
.issue .summary{font-size:13px; margin:4px 0;}
.issue .implication{
  background:#fff8f8; color:var(--neg); padding:7px 10px;
  border-radius:6px; font-size:13px; border-left:3px solid var(--neg);
}

footer{
  text-align:center; color:var(--muted);
  font-size:12px; margin-top:30px;
}
footer .contact{margin-top:4px;}
footer .contact a{color:var(--primary); text-decoration:none; font-weight:600;}
footer .contact a:hover{text-decoration:underline;}

@media print{
  body{background:#fff;}
  section.card{box-shadow:none; border:1px solid var(--border);}
  .hero{background:var(--primary) !important;}
}
"""


# ── 섹션 렌더 (skeleton — 이후 단계에서 채움) ────────────────────────────
def _header(profile: Profile, period_label: str) -> str:
    return f"""
<header class="hero">
  <h1>{_esc(profile.corp_name)}<span class="sub">{_esc(profile.corp_name_eng)}</span></h1>
  <div class="meta">
    <span>종목코드 <b>{_esc(profile.stock_code or '-')}</b></span>
    <span>법인구분 <b>{_esc(profile.corp_cls_label)}</b></span>
    <span>대표 <b>{_esc(profile.ceo_nm or '-')}</b></span>
    <span>결산월 <b>{_esc(profile.acc_mt or '-')}</b></span>
    <span>설립 <b>{_esc(_fmt_date(profile.est_dt))}</b></span>
  </div>
  <span class="period-badge">조회기간 · {_esc(period_label)}</span>
</header>
"""


def _kpi_row(fin: FinancialsBundle) -> str:
    cards = cd.build_kpi_cards(fin)
    if not cards:
        return ""
    CLS_MAP = {
        "매출액":    "",
        "영업이익":   " kpi-op",
        "EBITDA":   " kpi-ebitda",
        "당기순이익": " kpi-net",
    }
    html_cards: List[str] = []
    for c in cards:
        cls = CLS_MAP.get(c["label"], "")
        value = format_krw(c["value_raw"])
        margin_badge = ""
        if c.get("margin") is not None:
            margin_badge = f'<span class="margin-pill">마진 {c["margin"]:.1f}%</span>'
        yoy_html = ""
        if c.get("yoy") is not None:
            direction = "pos" if c["yoy"] >= 0 else "neg"
            arrow = "▲" if c["yoy"] >= 0 else "▼"
            yoy_html = f'<span class="yoy {direction}">{arrow} {abs(c["yoy"]):.1f}%</span>'
        html_cards.append(f"""
  <div class="kpi{cls}">
    <div class="label">{_esc(c['label'])}</div>
    <div class="value">{_esc(value)}{margin_badge}</div>
    <div class="sub">{_esc(c.get('period',''))} · YoY {yoy_html if yoy_html else '<span class="muted">-</span>'}</div>
  </div>""")
    return f'<div class="kpis">{"".join(html_cards)}</div>'


def _business_section(cnt: SectionCounter, biz: Optional[dict]) -> str:
    """Business Profile 섹션: 요약/제품/사업부/매출처·매입처/투자포인트."""
    if not biz:
        return ""
    summary = (biz.get("business_summary") or "").strip()
    products = biz.get("products") or []
    segments = biz.get("segments") or []
    customers = biz.get("major_customers") or []
    suppliers = biz.get("major_suppliers") or []
    insights = biz.get("key_insights") or []
    source_nm = biz.get("_source_report_nm") or ""

    if not any([summary, products, segments, customers, suppliers, insights]):
        return ""

    n = cnt.next()
    hint = f'<span class="hint">기준: {_esc(source_nm)}</span>' if source_nm else ""

    # 주요 제품/서비스 — chip grid
    chips_html = ""
    if products:
        chips = "".join(f'<span class="chip">{_esc(p)}</span>' for p in products[:20])
        chips_html = f'<h3>주요 제품/서비스</h3><div class="chips">{chips}</div>'

    # 사업부별 매출·영업이익률 — 표
    seg_html = ""
    if segments:
        def _fmt_rev(v, note):
            if v is None:
                return _esc(note or "-")
            # 원 단위로 들어온 금액을 조/억으로 포맷
            absv = abs(v)
            if absv >= 1e12:
                return f"{v/1e12:,.2f}조"
            if absv >= 1e8:
                return f"{v/1e8:,.0f}억"
            if absv >= 1e4:
                return f"{v/1e4:,.0f}만"
            return f"{v:,.0f}"

        rows = []
        for s in segments[:20]:
            name = _esc(s.get("name", "-"))
            rev = _fmt_rev(s.get("revenue"), s.get("revenue_note"))
            opm = s.get("op_margin_pct")
            opm_str = f"{opm:.1f}%" if isinstance(opm, (int, float)) else "-"
            desc = _esc(s.get("description", ""))
            rows.append(
                f"<tr><th>{name}</th><td>{_esc(rev)}</td>"
                f"<td>{opm_str}</td><td>{desc}</td></tr>"
            )
        seg_html = (
            "<h3>사업부별 매출·영업이익률</h3>"
            "<table class='seg'><thead>"
            "<tr><th>사업부/제품군</th><th>매출</th>"
            "<th>영업이익률</th><th>설명</th></tr>"
            "</thead><tbody>" + "".join(rows) + "</tbody></table>"
        )

    # 매출처 / 매입처 2컬럼
    cs_html = ""
    if customers or suppliers:
        def _ul(items):
            if not items:
                return "<p class='muted'>(정보 없음)</p>"
            return "<ul>" + "".join(f"<li>{_esc(x)}</li>" for x in items[:15]) + "</ul>"
        cs_html = f"""
  <div class="two-col">
    <div>
      <h3>주요 매출처 (고객)</h3>
      {_ul(customers)}
    </div>
    <div>
      <h3>주요 매입처 (공급)</h3>
      {_ul(suppliers)}
    </div>
  </div>"""

    # 투자 포인트
    ins_html = ""
    if insights:
        lis = "".join(f"<li>{_esc(x)}</li>" for x in insights)
        ins_html = f'<h3>투자 포인트</h3><ul class="insights">{lis}</ul>'

    sum_html = (
        f'<p class="biz-summary">{_esc(summary)}</p>' if summary else ""
    )

    return f"""
<section class="card biz">
  <h2><span class="secnum">{n}</span> 회사 개요 {hint}</h2>
  {sum_html}
  {chips_html}
  {seg_html}
  {cs_html}
  {ins_html}
</section>
"""


def _exec_section(cnt: SectionCounter, exec_summary: Optional[str]) -> str:
    if not exec_summary or not exec_summary.strip():
        return ""
    n = cnt.next()
    return f"""
<section class="card">
  <h2><span class="secnum">{n}</span> Executive Summary
      <span class="hint">Claude 기반 경영진 요약</span></h2>
  <div class="exec">{_md_to_html(exec_summary)}</div>
</section>
"""


def _fin_table(fin: FinancialsBundle) -> str:
    """확장 KPI 표 (Performance + BS + YoY)."""
    cols = [f"{y.year} ({y.reprt_label})" for y in fin.annual]
    if fin.latest_quarter:
        q = fin.latest_quarter
        cols.append(f"{q.year} {q.reprt_label}")
    head = "".join(f"<th>{_esc(c)}</th>" for c in cols)

    def _cell_for(key: str, v):
        """값 하나를 <td> 로 렌더. 비율 음수는 빨간색 class."""
        txt = _esc(format_value(key, v))
        cls = ""
        if key in PCT_KEYS and isinstance(v, (int, float)) and v < 0:
            cls = ' class="neg"'
        return f"<td{cls}>{txt}</td>"

    def _row_for(key: str) -> str:
        label = KEY_LABEL.get(key, key)
        cells = []
        for y in fin.annual:
            cells.append(_cell_for(key, y.values.get(key)))
        if fin.latest_quarter:
            cells.append(_cell_for(key, fin.latest_quarter.values.get(key)))
        # 비율 행은 이탤릭 + 옅은 음영
        tr_cls = ' class="ratio"' if key in PCT_KEYS else ""
        return f"<tr{tr_cls}><th>{_esc(label)}</th>{''.join(cells)}</tr>"

    perf_rows = "".join(_row_for(k) for k in PERFORMANCE_KEYS)
    bs_rows   = "".join(_row_for(k) for k in BALANCE_KEYS)

    # YoY (매출/영업이익/순이익)
    yoy_html = ""
    if len(fin.annual) >= 2:
        def _yoy_row(key: str, label: str) -> str:
            cells = []
            for i, y in enumerate(fin.annual):
                if i + 1 >= len(fin.annual):
                    cells.append("<td>-</td>")
                    continue
                v = yoy(y.values.get(key), fin.annual[i + 1].values.get(key))
                if v is None:
                    cells.append("<td>-</td>")
                else:
                    cls = "pos" if v >= 0 else "neg"
                    cells.append(f'<td class="{cls}">{v:+.1f}%</td>')
            if fin.latest_quarter:
                cells.append("<td>-</td>")
            return f'<tr class="yoy"><th>{_esc(label)}</th>{"".join(cells)}</tr>'
        yoy_html = (
            f'<tr class="section-head"><td colspan="{len(cols)+1}">YoY 성장률</td></tr>'
            + _yoy_row("revenue",    "매출 YoY")
            + _yoy_row("op_income",  "영업이익 YoY")
            + _yoy_row("net_income", "순이익 YoY")
        )

    n_cols = len(cols) + 1
    return f"""
<table class="fin">
  <thead><tr><th>계정</th>{head}</tr></thead>
  <tbody>
    <tr class="section-head"><td colspan="{n_cols}">Performance (손익)</td></tr>
    {perf_rows}
    <tr class="section-head"><td colspan="{n_cols}">Balance Sheet</td></tr>
    {bs_rows}
    {yoy_html}
  </tbody>
</table>
"""


def _financials_section(
    cnt: SectionCounter, fin: FinancialsBundle,
    chart_payload: Optional[Dict[str, Any]] = None,
) -> str:
    n = cnt.next()
    if not fin.annual and not fin.latest_quarter:
        return f'<section class="card"><h2><span class="secnum">{n}</span> 재무 하이라이트</h2>' \
               f'<p class="muted">수집된 재무 데이터가 없습니다.</p></section>'
    fs_hint = ""
    if fin.annual and fin.annual[0].fs_div:
        fs_hint = f'<span class="hint">기준: {fin.annual[0].fs_div} (연결 우선)</span>'

    # 동적 단위: chart_payload 에서 꺼내오거나 기본값
    cp = chart_payload or {}
    perf_unit = (cp.get("performance") or {}).get("unit", "억원")
    bs_unit   = (cp.get("bs")          or {}).get("unit", "조원")

    return f"""
<section class="card">
  <h2><span class="secnum">{n}</span> 재무 하이라이트{fs_hint}</h2>

  <div class="chart-grid">
    <div class="chart-box wide">
      <h4>손익 추이 <span class="unit">단위: {_esc(perf_unit)}</span></h4>
      <canvas id="chart-performance"></canvas>
    </div>
    <div class="chart-box">
      <h4>마진 추이 <span class="unit">단위: %</span></h4>
      <canvas id="chart-margin"></canvas>
    </div>
    <div class="chart-box">
      <h4>재무상태 (자산 = 자본 + 부채) <span class="unit">단위: {_esc(bs_unit)}</span></h4>
      <canvas id="chart-bs"></canvas>
    </div>
  </div>

  <h3>상세 재무 표</h3>
  {_fin_table(fin)}
</section>
"""


def _table_from_items(
    items: List[Dict[str, Any]], cols: List[tuple[str, str]], limit: int = 30,
) -> str:
    if not items:
        return "<p class='muted'>(데이터 없음)</p>"
    head = "".join(f"<th>{_esc(label)}</th>" for _, label in cols)
    body = ""
    for it in items[:limit]:
        tds = "".join(f"<td>{_esc(it.get(key, ''))}</td>" for key, _ in cols)
        body += f"<tr>{tds}</tr>"
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _governance_section(cnt: SectionCounter, sh: ShareholderBundle) -> str:
    n = cnt.next()
    if not any([sh.major, sh.major_change, sh.minority, sh.executives,
                sh.dividends, sh.audit_opinion]):
        return f'<section class="card"><h2><span class="secnum">{n}</span> 지배구조</h2>' \
               f'<p class="muted">수집된 지배구조 데이터가 없습니다.</p></section>'

    th = top_holder_summary(sh.major)
    top_line = ""
    if th and th.get("trmend_rate") is not None:
        top_line = (
            f'<div class="top-holder">'
            f'최대주주 <b>{_esc(th["name"])}</b> '
            f'({_esc(th["relate"])}) · 기말지분율 '
            f'<b>{th["trmend_rate"]:.2f}%</b>'
            f'</div>'
        )

    # 지배구조 표들
    major_tbl = _table_from_items(sh.major, [
        ("nm", "성명"), ("relate", "관계"), ("stock_knd", "주식종류"),
        ("trmend_posesn_stock_co", "보유주식수"),
        ("trmend_posesn_stock_qota_rt", "기말지분율(%)"),
    ])
    exec_tbl = _table_from_items(sh.executives, [
        ("nm", "성명"), ("ofcps", "직위"), ("chrg_job", "담당"),
        ("rgist_exctv_at", "등기여부"), ("hffc_pd", "재직기간"),
    ])
    div_tbl = _table_from_items(sh.dividends, [
        ("se", "구분"), ("stock_knd", "주식종류"),
        ("thstrm", "당기"), ("frmtrm", "전기"), ("lwfr", "전전기"),
    ])
    src = f'<span class="hint">기준 {sh.source_year} 사업보고서</span>' if sh.source_year else ""

    # 도넛 차트 왼쪽 + 최대주주 표 오른쪽 grid
    chart_or_note = (
        '<canvas id="chart-ownership"></canvas>'
        if sh.major
        else '<p class="muted">지분 도넛 차트 생성 불가 (데이터 없음)</p>'
    )

    return f"""
<section class="card">
  <h2><span class="secnum">{n}</span> 지배구조 {src}</h2>
  {top_line}

  <div class="ownership">
    <div class="chart-box" style="min-height:320px;">
      <h4>지분율 분포</h4>
      {chart_or_note}
    </div>
    <div>
      <h3>최대주주 및 특수관계인</h3>
      {major_tbl}
    </div>
  </div>

  <h3>경영진</h3>
  {exec_tbl}

  <h3>배당 이력</h3>
  {div_tbl}
</section>
"""


def _timeline_section(cnt: SectionCounter, discs: List[Disclosure]) -> str:
    n = cnt.next()
    if not discs:
        return (
            f'<section class="card"><h2><span class="secnum">{n}</span> 이슈 타임라인</h2>'
            f'<p class="muted">기간 내 공시가 없습니다.</p></section>'
        )
    items: List[str] = []
    for d in discs:
        extra = ""
        if d.llm_status == "ok":
            kp = "".join(f"<li>{_esc(k)}</li>" for k in d.key_points)
            kp_block = f"<ul class='kp'>{kp}</ul>" if kp else ""
            extra = f"""
      <div class='llmblk'>
        <p class='summary'>{_esc(d.summary)}</p>
        {kp_block}
        <p class='implication'><b>Implication.</b> {_esc(d.implication)}</p>
      </div>"""
        items.append(f"""
  <div class="tl-item">
    <div class="date">{_esc(_fmt_date(d.rcept_dt))}</div>
    <div class="body">
      {_badge(d.pblntf_ty)}
      <a class="ttl" href="{_esc(d.viewer_url)}" target="_blank" rel="noopener">{_esc(d.report_nm)}</a>
      <span class="flr muted small">by {_esc(d.flr_nm)}</span>
      {extra}
    </div>
  </div>""")

    analyzed = sum(1 for d in discs if d.llm_status == "ok")
    hint = (
        f'<span class="hint">전체 {len(discs)}건 · LLM 분석 {analyzed}건</span>'
    )
    return f"""
<section class="card">
  <h2><span class="secnum">{n}</span> 이슈 타임라인 {hint}</h2>
  <div class="timeline">{''.join(items)}</div>
</section>
"""


def _key_issues_section(cnt: SectionCounter, discs: List[Disclosure],
                        max_items: int = 10) -> str:
    key = [d for d in discs if d.pblntf_ty == "B" and d.llm_status == "ok"][:max_items]
    if not key:
        return ""
    n = cnt.next()
    cards = "".join(f"""
  <article class="issue">
    <header>
      <time>{_esc(_fmt_date(d.rcept_dt))}</time>
      {_badge(d.pblntf_ty)}
      <a href="{_esc(d.viewer_url)}" target="_blank" rel="noopener">{_esc(d.report_nm)}</a>
    </header>
    <p class="summary">{_esc(d.summary)}</p>
    <p class="implication"><b>Implication.</b> {_esc(d.implication)}</p>
  </article>""" for d in key)
    return f"""
<section class="card">
  <h2><span class="secnum">{n}</span> 핵심 이슈 카드
      <span class="hint">주요사항 중 LLM 분석 완료 {len(key)}건</span></h2>
  <div class="issues">{cards}</div>
</section>
"""


# ── 메인 엔트리 ─────────────────────────────────────────────────────────
CHART_JS_CDN = "https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"


# 브라우저에서 실행될 초기화 스크립트. JS 문자열 그대로 주입.
CHART_INIT_JS = r"""
(function() {
  if (typeof Chart === 'undefined') return;
  var CD = window.CHART_DATA || {};

  var COMMON = {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { position: 'bottom', labels: { usePointStyle: true, boxWidth: 8, font: {size: 11} } },
      tooltip: {
        backgroundColor: 'rgba(26,35,126,0.95)',
        titleFont: { weight: 'bold' },
        padding: 10,
        cornerRadius: 8,
      }
    }
  };

  function fmtKRW(v) {
    if (v == null || isNaN(v)) return '-';
    var a = Math.abs(v), s = v < 0 ? '-' : '';
    if (a >= 1e4) return s + (a/1e4).toLocaleString('ko-KR', {maximumFractionDigits:2}) + '조';
    if (a >= 1)   return s + a.toLocaleString('ko-KR', {maximumFractionDigits:0}) + '억';
    return s + a.toLocaleString('ko-KR');
  }
  function fmtPct(v) {
    if (v == null || isNaN(v)) return '-';
    return v.toFixed(1) + '%';
  }

  // ── 1. 손익 bar (동적 단위)
  var p = CD.performance;
  if (p && document.getElementById('chart-performance')) {
    var pUnit = p.unit || '억원';
    new Chart(document.getElementById('chart-performance'), {
      type: 'bar',
      data: { labels: p.labels, datasets: p.datasets },
      options: Object.assign({}, COMMON, {
        scales: {
          y: {
            ticks: {
              callback: function(v){ return v.toLocaleString('ko-KR') + pUnit; },
              font:{size:11}
            },
            grid: { color: '#eceff4' }
          },
          x: { grid: { display: false }, ticks: {font:{size:11}} }
        },
        plugins: Object.assign({}, COMMON.plugins, {
          tooltip: Object.assign({}, COMMON.plugins.tooltip, {
            callbacks: {
              label: function(ctx){
                var v = ctx.parsed.y;
                return ctx.dataset.label + ': ' + (v == null ? '-'
                  : v.toLocaleString('ko-KR', {maximumFractionDigits:2}) + pUnit);
              }
            }
          })
        })
      })
    });
  }

  // 0% 미만 영역을 연한 분홍으로 음영 (마진 차트 전용)
  var negativeZoneBg = {
    id: 'negativeZoneBg',
    beforeDatasetsDraw: function(chart) {
      var area = chart.chartArea;
      var yScale = chart.scales.y;
      if (!area || !yScale) return;
      var y0 = yScale.getPixelForValue(0);
      if (y0 >= area.bottom) return;  // 전 구간 음수면 전체 음영
      var ctx = chart.ctx;
      ctx.save();
      ctx.fillStyle = 'rgba(244, 143, 177, 0.18)';  // 연한 분홍
      var top = Math.max(y0, area.top);
      ctx.fillRect(area.left, top, area.right - area.left, area.bottom - top);
      // 0% 라인 점선
      ctx.strokeStyle = 'rgba(198, 40, 40, 0.35)';
      ctx.setLineDash([4, 4]);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(area.left, y0);
      ctx.lineTo(area.right, y0);
      ctx.stroke();
      ctx.restore();
    }
  };

  // ── 2. 마진 line
  var m = CD.margin;
  if (m && document.getElementById('chart-margin')) {
    m.datasets.forEach(function(ds){
      ds.tension = 0.3;
      ds.fill = false;
      ds.pointRadius = 4;
      ds.pointHoverRadius = 6;
      ds.borderWidth = 2;
    });
    new Chart(document.getElementById('chart-margin'), {
      type: 'line',
      data: { labels: m.labels, datasets: m.datasets },
      plugins: [negativeZoneBg],
      options: Object.assign({}, COMMON, {
        scales: {
          y: {
            ticks: { callback: function(v){ return v.toFixed(0) + '%'; }, font:{size:11} },
            grid: { color: '#eceff4' }
          },
          x: { grid: { display: false }, ticks: {font:{size:11}} }
        },
        plugins: Object.assign({}, COMMON.plugins, {
          tooltip: Object.assign({}, COMMON.plugins.tooltip, {
            callbacks: {
              label: function(ctx){
                return ctx.dataset.label + ': ' + fmtPct(ctx.parsed.y);
              }
            }
          })
        })
      })
    });
  }

  // ── 3. BS stacked (동적 단위)
  var b = CD.bs;
  if (b && document.getElementById('chart-bs')) {
    var bUnit = b.unit || '조원';
    new Chart(document.getElementById('chart-bs'), {
      type: 'bar',
      data: { labels: b.labels, datasets: b.datasets },
      options: Object.assign({}, COMMON, {
        scales: {
          y: {
            stacked: true,
            ticks: { callback: function(v){ return v.toLocaleString('ko-KR') + bUnit; }, font:{size:11} },
            grid: { color: '#eceff4' }
          },
          x: { stacked: true, grid: { display: false }, ticks: {font:{size:11}} }
        },
        plugins: Object.assign({}, COMMON.plugins, {
          tooltip: Object.assign({}, COMMON.plugins.tooltip, {
            callbacks: {
              label: function(ctx){
                return ctx.dataset.label + ': ' + ctx.parsed.y.toFixed(2) + bUnit;
              },
              footer: function(items){
                var sum = items.reduce(function(s, i){ return s + i.parsed.y; }, 0);
                return '자산총계 ≈ ' + sum.toFixed(2) + bUnit;
              }
            }
          })
        })
      })
    });
  }

  // ── 4. 지분 도넛
  var o = CD.ownership;
  if (o && document.getElementById('chart-ownership')) {
    new Chart(document.getElementById('chart-ownership'), {
      type: 'doughnut',
      data: { labels: o.labels, datasets: o.datasets },
      options: Object.assign({}, COMMON, {
        cutout: '55%',
        plugins: Object.assign({}, COMMON.plugins, {
          legend: { position: 'right', labels: { usePointStyle: true, boxWidth: 8, font:{size:11} } },
          tooltip: Object.assign({}, COMMON.plugins.tooltip, {
            callbacks: {
              label: function(ctx){
                return ctx.label + ': ' + ctx.parsed.toFixed(2) + '%';
              }
            }
          })
        })
      })
    });
  }
})();
"""


def write_html(
    path: str,
    profile: Profile,
    fin: FinancialsBundle,
    shareholders: ShareholderBundle,
    disclosures: List[Disclosure],
    period_label: str,
    exec_summary: Optional[str] = None,
    business: Optional[dict] = None,
) -> None:
    cnt = SectionCounter()
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Chart.js 데이터 — 단계 3에서 실제 스크립트 주입
    chart_payload: Dict[str, Any] = {
        "performance": cd.build_performance_chart(fin) if fin.annual or fin.latest_quarter else None,
        "margin":      cd.build_margin_chart(fin)      if fin.annual or fin.latest_quarter else None,
        "bs":          cd.build_bs_chart(fin)          if fin.annual or fin.latest_quarter else None,
        "ownership":   cd.build_ownership_chart(shareholders),
    }

    body = (
        _header(profile, period_label)
        + _kpi_row(fin)
        + _business_section(cnt, business)
        + _exec_section(cnt, exec_summary)
        + _financials_section(cnt, fin, chart_payload)
        + _governance_section(cnt, shareholders)
        + _timeline_section(cnt, disclosures)
        + _key_issues_section(cnt, disclosures)
    )

    html_doc = f"""<!doctype html>
<html lang="ko"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>완규의 딸깍공장 — {_esc(profile.corp_name)}</title>
<script src="{CHART_JS_CDN}"></script>
<style>{CSS}</style>
</head><body>
<div class="wrap">
{body}
<footer>
  <div>Generated {_esc(generated)} · {_esc(CONTACT_NAME)}</div>
  <div class="contact">문의/개선 제안:
    <a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a>
  </div>
</footer>
</div>
<script>
window.CHART_DATA = {json.dumps(chart_payload, ensure_ascii=False)};
</script>
<script>
{CHART_INIT_JS}
</script>
</body></html>
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(html_doc)
