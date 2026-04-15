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
  --bg:#f1f3f8;
  --surface:#ffffff;
  --primary:#10174a;
  --primary-2:#283593;
  --primary-3:#5c6bc0;
  --accent:#ff6b35;
  --accent-2:#ffd180;
  --pos:#2e7d32;
  --neg:#c62828;
  --ink:#0f172a;
  --ink-2:#334155;
  --muted:#64748b;
  --border:#e2e8f0;
  --soft:#f8fafc;
  --radius:14px;
  --shadow:0 1px 2px rgba(15,23,42,.04), 0 1px 3px rgba(15,23,42,.06);
  --shadow-lg:0 10px 30px rgba(16,23,74,.12);
  --shadow-xl:0 20px 50px rgba(16,23,74,.18);
  --hero-grad: radial-gradient(1200px 400px at 85% -30%, rgba(255,107,53,.25), transparent 55%),
               linear-gradient(135deg, #0b1240 0%, #1a237e 45%, #283593 100%);
}

*{box-sizing:border-box;}
html,body{margin:0;padding:0;}
body{
  font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI','Pretendard',
              'Noto Sans KR','Malgun Gothic',Arial,sans-serif;
  background:var(--bg); color:var(--ink);
  line-height:1.6;
  font-feature-settings:"tnum","cv11";
  -webkit-font-smoothing:antialiased;
  -moz-osx-font-smoothing:grayscale;
}
.wrap{max-width:1200px; margin:0 auto; padding:32px 24px 80px;}

/* HERO */
.hero{
  background: var(--hero-grad);
  color:#fff; padding:40px 44px 32px; border-radius:20px;
  box-shadow:var(--shadow-xl); position:relative; overflow:hidden;
  border: 1px solid rgba(255,255,255,0.06);
}
.hero::before{
  content:""; position:absolute; right:-120px; bottom:-120px;
  width:360px; height:360px; border-radius:50%;
  background:radial-gradient(circle, rgba(92,107,192,.35), transparent 70%);
  pointer-events:none;
}
.hero::after{
  content:""; position:absolute; left:-60px; top:-60px;
  width:220px; height:220px; border-radius:50%;
  background:radial-gradient(circle, rgba(255,209,128,.12), transparent 70%);
  pointer-events:none;
}
.hero h1{
  margin:0 0 4px 0; font-size:34px; font-weight:700;
  letter-spacing:-.02em; line-height:1.2; position:relative; z-index:1;
}
.hero .sub{
  font-size:14px; opacity:.65; margin-left:12px; font-weight:400;
  letter-spacing:.02em;
}
.hero .meta{
  display:flex; flex-wrap:wrap; gap:8px 22px;
  font-size:13px; opacity:.9; margin-top:14px; position:relative; z-index:1;
}
.hero .meta span{
  padding:4px 10px; background:rgba(255,255,255,.08);
  border-radius:8px; backdrop-filter:blur(6px);
  border:1px solid rgba(255,255,255,.1);
}
.hero .meta span b{font-weight:600; letter-spacing:-.01em;}
.hero .period-badge{
  display:inline-flex; align-items:center; gap:6px;
  background:linear-gradient(135deg,rgba(255,107,53,.25),rgba(255,107,53,.12));
  padding:6px 14px; border-radius:999px;
  font-size:12px; margin-top:14px;
  border:1px solid rgba(255,107,53,.3);
  position:relative; z-index:1;
  font-weight:500;
}
.hero .period-badge::before{
  content:"●"; color:var(--accent-2); font-size:8px;
}

/* KPI row */
.kpis{
  display:grid;
  grid-template-columns:repeat(auto-fit, minmax(230px, 1fr));
  gap:16px; margin:22px 0 10px;
}
.kpi{
  background:var(--surface); border-radius:var(--radius);
  padding:20px 22px; box-shadow:var(--shadow);
  border:1px solid var(--border);
  position:relative; overflow:hidden;
  transition: transform .15s ease, box-shadow .15s ease;
}
.kpi::before{
  content:""; position:absolute; left:0; top:0; bottom:0; width:4px;
  background:linear-gradient(180deg, var(--primary), var(--primary-3));
}
.kpi:hover{ transform: translateY(-2px); box-shadow:var(--shadow-lg); }
.kpi.kpi-op::before{ background:linear-gradient(180deg,#3949ab,#7986cb); }
.kpi.kpi-ebitda::before{ background:linear-gradient(180deg,#ff6b35,#ffa726); }
.kpi.kpi-net::before{ background:linear-gradient(180deg,#7e57c2,#b39ddb); }
.kpi .label{
  font-size:11px; color:var(--muted);
  text-transform:uppercase; letter-spacing:.1em; font-weight:600;
}
.kpi .value{
  font-size:28px; font-weight:700; color:var(--ink);
  margin-top:8px; letter-spacing:-.02em; line-height:1.1;
  font-variant-numeric:tabular-nums;
}
.kpi .sub{font-size:12px; color:var(--muted); margin-top:8px;}
.kpi .yoy{font-weight:700; margin-left:4px;}
.kpi .yoy.pos{color:var(--pos);}
.kpi .yoy.neg{color:var(--neg);}
.kpi .margin-pill{
  display:inline-block;
  background:linear-gradient(135deg,#eef2fb,#e8eafc);
  color:var(--primary);
  font-size:11px; padding:3px 10px; border-radius:999px;
  font-weight:600; margin-left:8px;
  border:1px solid rgba(26,35,126,.1);
  vertical-align:middle;
}

/* Section card */
section.card{
  background:var(--surface); border-radius:var(--radius);
  padding:28px 32px; margin:18px 0;
  box-shadow:var(--shadow);
  border:1px solid var(--border);
}
section.card > h2{
  margin:0 0 18px 0; padding-bottom:14px;
  border-bottom:1px solid var(--border);
  font-size:20px; font-weight:700; letter-spacing:-.02em;
  display:flex; align-items:center; gap:12px;
  color:var(--ink);
}
section.card h2 .secnum{
  display:inline-flex; align-items:center; justify-content:center;
  background:linear-gradient(135deg, var(--primary), var(--primary-2));
  color:#fff;
  width:28px; height:28px; border-radius:8px;
  font-size:14px; font-weight:700;
  box-shadow:0 2px 6px rgba(26,35,126,.25);
}
section.card h2 .hint{
  font-size:12px; color:var(--muted); font-weight:400;
  margin-left:auto;
  background:var(--soft); padding:4px 10px;
  border-radius:8px; border:1px solid var(--border);
}
section.card h3{
  margin-top:22px; margin-bottom:10px;
  font-size:14px; font-weight:600; color:var(--ink-2);
  letter-spacing:-.01em;
}
section.card h3::before{
  content:""; display:inline-block; width:3px; height:14px;
  background:var(--primary); margin-right:8px; vertical-align:middle;
  border-radius:2px;
}
.muted{color:var(--muted);} .small{font-size:12px;}

/* Business Profile */
.biz .biz-summary{
  background:linear-gradient(135deg, #eef2fb 0%, #f5f0ff 100%);
  border-left:4px solid var(--primary);
  padding:16px 20px; border-radius:10px; font-size:14.5px;
  line-height:1.7; color:var(--ink);
  box-shadow: inset 0 0 0 1px rgba(26,35,126,.08);
}
.chips{display:flex; flex-wrap:wrap; gap:8px; margin:6px 0 14px;}
.chip{
  display:inline-flex; align-items:center; gap:6px;
  background:linear-gradient(135deg,#eef2fb,#e3e8fb);
  color:var(--primary);
  padding:6px 14px; border-radius:999px; font-size:12px; font-weight:600;
  border:1px solid rgba(26,35,126,.12);
  box-shadow: 0 1px 2px rgba(26,35,126,.06);
  transition: transform .12s ease;
}
.chip:hover{ transform: translateY(-1px); }
.chip::before{
  content:""; width:6px; height:6px; border-radius:50%;
  background:var(--primary); display:inline-block;
}
table.seg td, table.seg th{font-size:13px;}
table.seg td:nth-child(2), table.seg td:nth-child(3){
  text-align:right; font-variant-numeric:tabular-nums;
}
.two-col{
  display:grid; grid-template-columns:1fr 1fr; gap:20px;
  margin:12px 0;
}
@media (max-width:720px){ .two-col{grid-template-columns:1fr;} }

ul.counterparty{list-style:none; padding:0; margin:4px 0 0 0;}
ul.counterparty li{
  padding:10px 14px; margin-bottom:8px;
  background:var(--soft);
  border:1px solid var(--border); border-left:3px solid var(--primary-3);
  border-radius:8px; font-size:13px; line-height:1.5;
}
ul.counterparty .counterparty-meta{
  display:inline-block; margin-top:4px;
  color:var(--ink-2); font-size:12px;
}

ul.insights{margin:4px 0 0 0; padding:0; list-style:none;}
ul.insights li{
  margin:6px 0; padding:10px 14px;
  background:linear-gradient(135deg,#fff8ef 0%, #fff3e0 100%);
  border-left:4px solid var(--accent);
  border-radius:8px; font-size:13.5px; line-height:1.5;
  box-shadow: 0 1px 2px rgba(255,107,53,.08);
  position:relative;
}
ul.insights li::before{
  content:"▸"; color:var(--accent); font-weight:bold;
  margin-right:6px;
}
.insight-tag{
  display:inline-block;
  background:linear-gradient(135deg,#fff2e0,#ffe0b2);
  color:#c62828;
  padding:3px 10px; border-radius:6px;
  font-size:11px; font-weight:700; letter-spacing:.02em;
  margin-right:10px;
  border:1px solid rgba(255,107,53,.3);
  vertical-align:middle;
  white-space:nowrap;
}
.insight-body{ vertical-align:middle; }

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
  padding:9px 12px;
  border-bottom:1px solid var(--border);
  text-align:left; vertical-align:middle;
}
table thead th{
  background:linear-gradient(180deg, #f8fafc, #eff2f7);
  font-weight:600; color:var(--ink-2);
  font-size:12px; text-transform:uppercase; letter-spacing:.03em;
  position:sticky; top:0;
  border-bottom:2px solid var(--border);
}
table tbody tr:nth-child(even){background:var(--soft);}
table tbody tr:hover{background:#eef2fb !important;}
table.fin td, table.fin th{text-align:right; font-variant-numeric:tabular-nums;}
table.fin th:first-child, table.fin td:first-child{text-align:left;}
table.fin tr.section-head td{
  background:linear-gradient(90deg,#eef2fb 0%, #f5f7fa 100%) !important;
  font-weight:700; color:var(--primary);
  padding:10px 12px; font-size:12px;
  text-transform:uppercase; letter-spacing:.04em;
  border-top:2px solid var(--primary);
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

/* FS Toggle (연결 / 별도) */
.fs-toggle{
  display:inline-flex; gap:4px;
  background:var(--soft);
  padding:4px;
  border-radius:10px;
  border:1px solid var(--border);
  margin-bottom:14px;
}
.fs-toggle .tab{
  background:transparent; border:none;
  padding:8px 18px; font-size:13px; font-weight:600;
  color:var(--muted);
  border-radius:8px; cursor:pointer;
  transition:background .15s, color .15s;
  letter-spacing:-.01em;
}
.fs-toggle .tab:hover{ color:var(--ink); }
.fs-toggle .tab.active{
  background:linear-gradient(135deg, var(--primary), var(--primary-2));
  color:#fff;
  box-shadow:0 2px 6px rgba(26,35,126,.25);
}
.fs-toggle .tab.disabled{
  color:#b0bec5; cursor:not-allowed;
}
.fs-toggle .tab.disabled:hover{ color:#b0bec5; background:transparent; }
.fs-toggle .tab .muted{color:inherit; opacity:.7;}
.fs-toggle .tab.active .muted{color:#fff; opacity:.85;}

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

    # 매출처 / 매입처 2컬럼 — 수치 있으면 함께 표기, 없으면 이름만
    def _fmt_counterparty_item(item):
        """item 이 dict 면 name + share_pct + amount, 문자열이면 그대로."""
        if isinstance(item, str):
            return _esc(item)
        if not isinstance(item, dict):
            return _esc(str(item))
        name = _esc(item.get("name", ""))
        parts = []
        sp = item.get("share_pct")
        if isinstance(sp, (int, float)):
            parts.append(f"<b>{sp:.1f}%</b>")
        amt = item.get("amount")
        if isinstance(amt, (int, float)):
            # 조/억/만 단위 포맷
            a = abs(amt)
            if a >= 1e12:
                parts.append(f"{amt/1e12:,.2f}조")
            elif a >= 1e8:
                parts.append(f"{amt/1e8:,.0f}억")
            elif a >= 1e4:
                parts.append(f"{amt/1e4:,.0f}만")
            else:
                parts.append(f"{amt:,.0f}원")
        note = item.get("amount_note") or ""
        if note:
            parts.append(f"<span class='muted small'>{_esc(note)}</span>")
        desc = item.get("description") or ""
        badge = ""
        if parts:
            badge = " · ".join(parts)
            badge = f"<span class='counterparty-meta'>{badge}</span>"
        desc_html = f" <span class='muted small'>— {_esc(desc)}</span>" if desc else ""
        return f"<b>{name}</b>{desc_html}<br>{badge}" if badge else f"<b>{name}</b>{desc_html}"

    def _has_data(items):
        if not items:
            return False
        return any(
            (isinstance(x, str) and x.strip()) or
            (isinstance(x, dict) and (x.get("name") or "").strip())
            for x in items
        )

    cs_html = ""
    if _has_data(customers) or _has_data(suppliers):
        def _list_block(items):
            if not _has_data(items):
                return "<p class='muted small'>(공시에 구체적 기재 없음)</p>"
            return ("<ul class='counterparty'>" +
                    "".join(f"<li>{_fmt_counterparty_item(x)}</li>" for x in items[:15]) +
                    "</ul>")
        cs_html = f"""
  <div class="two-col">
    <div>
      <h3>주요 매출처 (고객)</h3>
      {_list_block(customers)}
    </div>
    <div>
      <h3>주요 매입처 (공급)</h3>
      {_list_block(suppliers)}
    </div>
  </div>"""

    # 투자 포인트 — "내용 (분류키워드)" 렌더. ":" 구분자도 대응.
    # 분류 키워드 휴리스틱: 5~12자 이내 + 특정 키워드 포함이면 분류로 판단
    KEYWORD_HINTS = (
        "드라이버", "리스크", "포인트", "강점", "약점",
        "경쟁", "밸류", "재무", "규제", "성장", "기회", "위협",
        "역풍", "모멘텀", "트렌드", "시너지",
    )

    def _looks_like_tag(s: str) -> bool:
        s = s.strip()
        if not (1 <= len(s) <= 18):
            return False
        return any(k in s for k in KEYWORD_HINTS)

    def _tag_chip(tag: str, body: str) -> str:
        """태그 pill 을 **앞**에 + 괄호 없이 + 본문 뒤."""
        return (f"<span class='insight-tag'>{_esc(tag)}</span>"
                f"<span class='insight-body'>{_esc(body)}</span>")

    def _render_insight(text: str) -> str:
        import re as _re
        raw = str(text).strip()
        # 1) "내용 (태그)" 형태 — 괄호 제거하며 태그 pill 앞으로
        m = _re.match(r"^(.*?)\s*\(([^()]+)\)\s*$", raw)
        if m and m.group(2).strip():
            return _tag_chip(m.group(2).strip(), m.group(1).strip())
        # 2) "X: Y" 형태 — 태그쪽 판별
        if ":" in raw:
            left, right = [p.strip() for p in raw.split(":", 1)]
            if left and right:
                if _looks_like_tag(left) and not _looks_like_tag(right):
                    return _tag_chip(left, right)
                if _looks_like_tag(right) and not _looks_like_tag(left):
                    return _tag_chip(right, left)
                # 판별 어려우면 짧은 쪽을 태그
                if len(left) <= len(right):
                    return _tag_chip(left, right)
                return _tag_chip(right, left)
        return f"<span class='insight-body'>{_esc(raw)}</span>"

    ins_html = ""
    if insights:
        lis = "".join(f"<li>{_render_insight(x)}</li>" for x in insights)
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


def _fin_table_for(annual: List, latest_q) -> str:
    """확장 KPI 표 (Performance + BS + YoY) — 왼쪽(과거) → 오른쪽(최신) 순."""
    # 과거 → 최신 순으로 정렬 (사용자 요청)
    annual = sorted(annual, key=lambda y: y.year)
    cols = [f"{y.year} ({y.reprt_label})" for y in annual]
    if latest_q:
        cols.append(f"{latest_q.year} {latest_q.reprt_label}")
    head = "".join(f"<th>{_esc(c)}</th>" for c in cols)

    def _cell_for(key: str, v):
        txt = _esc(format_value(key, v))
        cls = ""
        if key in PCT_KEYS and isinstance(v, (int, float)) and v < 0:
            cls = ' class="neg"'
        return f"<td{cls}>{txt}</td>"

    def _row_for(key: str) -> str:
        label = KEY_LABEL.get(key, key)
        cells = []
        for y in annual:
            cells.append(_cell_for(key, y.values.get(key)))
        if latest_q:
            cells.append(_cell_for(key, latest_q.values.get(key)))
        tr_cls = ' class="ratio"' if key in PCT_KEYS else ""
        return f"<tr{tr_cls}><th>{_esc(label)}</th>{''.join(cells)}</tr>"

    perf_rows = "".join(_row_for(k) for k in PERFORMANCE_KEYS)
    bs_rows   = "".join(_row_for(k) for k in BALANCE_KEYS)

    yoy_html = ""
    if len(annual) >= 2:
        def _yoy_row(key: str, label: str) -> str:
            # annual 은 과거→최신 순. 첫 열은 비교 불가, 이후 열은 직전 연도 대비.
            cells = []
            for i, y in enumerate(annual):
                if i == 0:
                    cells.append("<td>-</td>")
                    continue
                v = yoy(y.values.get(key), annual[i - 1].values.get(key))
                if v is None:
                    cells.append("<td>-</td>")
                else:
                    cls = "pos" if v >= 0 else "neg"
                    cells.append(f'<td class="{cls}">{v:+.1f}%</td>')
            if latest_q:
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


def _fin_table(fin: FinancialsBundle) -> str:
    """기존 호환 — best(hybrid) 표 렌더."""
    return _fin_table_for(fin.annual, fin.latest_quarter)


def _financials_section(
    cnt: SectionCounter, fin: FinancialsBundle,
    chart_payload: Optional[Dict[str, Any]] = None,
) -> str:
    n = cnt.next()
    has_cfs = bool(fin.annual_cfs or fin.latest_quarter_cfs)
    has_ofs = bool(fin.annual_ofs or fin.latest_quarter_ofs)

    if not fin.annual and not has_cfs and not has_ofs:
        return f'<section class="card fin-card"><h2><span class="secnum">{n}</span> 재무 하이라이트</h2>' \
               f'<p class="muted">수집된 재무 데이터가 없습니다.</p></section>'

    cp = chart_payload or {}

    def _render_view(fs: str, annual, latest_q, cp_fs: Dict[str, Any]) -> str:
        """한 FS (cfs / ofs) 의 차트 3개 + 상세 표."""
        if not annual and not latest_q:
            return (f'<div class="fs-view" data-fs="{fs}" style="display:none">'
                    f'<p class="muted">데이터 없음</p></div>')
        perf_unit = (cp_fs.get("performance") or {}).get("unit", "억원")
        bs_unit   = (cp_fs.get("bs")          or {}).get("unit", "조원")
        table_html = _fin_table_for(annual, latest_q)
        return f"""
<div class="fs-view" data-fs="{fs}">
  <div class="chart-grid">
    <div class="chart-box wide">
      <h4>손익 추이 <span class="unit">단위: {_esc(perf_unit)}</span></h4>
      <canvas id="chart-perf-{fs}"></canvas>
    </div>
    <div class="chart-box">
      <h4>마진 추이 <span class="unit">단위: %</span></h4>
      <canvas id="chart-margin-{fs}"></canvas>
    </div>
    <div class="chart-box">
      <h4>재무상태 (자산 = 자본 + 부채) <span class="unit">단위: {_esc(bs_unit)}</span></h4>
      <canvas id="chart-bs-{fs}"></canvas>
    </div>
  </div>
  <h3>상세 재무 표</h3>
  {table_html}
</div>"""

    view_cfs = _render_view("cfs", fin.annual_cfs, fin.latest_quarter_cfs,
                            cp.get("cfs") or {}) if has_cfs else (
        '<div class="fs-view" data-fs="cfs" style="display:none">'
        '<p class="muted">연결재무제표 데이터 없음</p></div>')
    view_ofs = _render_view("ofs", fin.annual_ofs, fin.latest_quarter_ofs,
                            cp.get("ofs") or {}) if has_ofs else (
        '<div class="fs-view" data-fs="ofs" style="display:none">'
        '<p class="muted">별도재무제표 데이터 없음</p></div>')

    # 토글 버튼 (기본 CFS 활성)
    default_fs = "cfs" if has_cfs else "ofs"
    tab_cfs_cls = "tab active" if default_fs == "cfs" else "tab"
    tab_ofs_cls = "tab active" if default_fs == "ofs" else "tab"
    if not has_cfs:
        tab_cfs_cls += " disabled"
    if not has_ofs:
        tab_ofs_cls += " disabled"
    cfs_suffix = f"({len(fin.annual_cfs)}개년)" if has_cfs else "(없음)"
    ofs_suffix = f"({len(fin.annual_ofs)}개년)" if has_ofs else "(없음)"

    toggle_html = (
        f'<div class="fs-toggle" role="tablist">'
        f'<button class="{tab_cfs_cls}" data-fs="cfs" '
        f'{"" if has_cfs else "disabled"}>연결 (CFS) <span class="muted small">{cfs_suffix}</span></button>'
        f'<button class="{tab_ofs_cls}" data-fs="ofs" '
        f'{"" if has_ofs else "disabled"}>별도 (OFS) <span class="muted small">{ofs_suffix}</span></button>'
        f'</div>'
    )

    # 기본 뷰가 CFS 가 아니면 display 반전
    if default_fs == "ofs":
        view_cfs = view_cfs.replace('data-fs="cfs"', 'data-fs="cfs" style="display:none"', 1)
        view_ofs = view_ofs.replace('style="display:none"', "", 1) if 'style="display:none"' in view_ofs else view_ofs

    hint = ""
    cfs_cnt = len(fin.annual_cfs)
    ofs_cnt = len(fin.annual_ofs)
    if cfs_cnt and ofs_cnt:
        hint = f'<span class="hint">연결 {cfs_cnt}개년 · 별도 {ofs_cnt}개년 수집</span>'
    elif cfs_cnt:
        hint = f'<span class="hint">연결재무제표 기준 {cfs_cnt}개년</span>'
    elif ofs_cnt:
        hint = f'<span class="hint">별도재무제표 기준 {ofs_cnt}개년</span>'

    return f"""
<section class="card fin-card">
  <h2><span class="secnum">{n}</span> 재무 하이라이트{hint}</h2>
  {toggle_html}
  {view_cfs}
  {view_ofs}
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

  function fmtPct(v) {
    if (v == null || isNaN(v)) return '-';
    return v.toFixed(1) + '%';
  }

  // 0% 미만 영역을 연한 분홍 (마진 차트 전용 plugin)
  var negativeZoneBg = {
    id: 'negativeZoneBg',
    beforeDatasetsDraw: function(chart) {
      var area = chart.chartArea;
      var yScale = chart.scales.y;
      if (!area || !yScale) return;
      var y0 = yScale.getPixelForValue(0);
      if (y0 >= area.bottom) return;
      var ctx = chart.ctx;
      ctx.save();
      ctx.fillStyle = 'rgba(244, 143, 177, 0.18)';
      var top = Math.max(y0, area.top);
      ctx.fillRect(area.left, top, area.right - area.left, area.bottom - top);
      ctx.strokeStyle = 'rgba(198, 40, 40, 0.35)';
      ctx.setLineDash([4, 4]);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(area.left, y0); ctx.lineTo(area.right, y0);
      ctx.stroke(); ctx.restore();
    }
  };

  function initFsCharts(fs) {
    var payload = CD[fs];
    if (!payload) return;

    // 1) 손익 bar — 조원이면 소수점 2자리, 억원은 콤마
    var p = payload.performance;
    var elP = document.getElementById('chart-perf-' + fs);
    if (p && elP) {
      var pUnit = p.unit || '억원';
      var pFrac = (pUnit === '조원') ? 2 : 0;
      new Chart(elP, {
        type: 'bar',
        data: { labels: p.labels, datasets: p.datasets },
        options: Object.assign({}, COMMON, {
          scales: {
            y: {
              ticks: {
                callback: function(v){
                  return v.toLocaleString('ko-KR',
                    {minimumFractionDigits: pFrac, maximumFractionDigits: pFrac}) + pUnit;
                },
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
                    : v.toLocaleString('ko-KR',
                        {minimumFractionDigits: pFrac, maximumFractionDigits: 2}) + pUnit);
                }
              }
            })
          })
        })
      });
    }

    // 2) 마진 line
    var m = payload.margin;
    var elM = document.getElementById('chart-margin-' + fs);
    if (m && elM) {
      m.datasets.forEach(function(ds){
        ds.tension = 0.3; ds.fill = false;
        ds.pointRadius = 4; ds.pointHoverRadius = 6; ds.borderWidth = 2;
      });
      new Chart(elM, {
        type: 'line',
        data: { labels: m.labels, datasets: m.datasets },
        plugins: [negativeZoneBg],
        options: Object.assign({}, COMMON, {
          scales: {
            y: { ticks: { callback: function(v){ return v.toFixed(0) + '%'; }, font:{size:11} }, grid: { color: '#eceff4' } },
            x: { grid: { display: false }, ticks: {font:{size:11}} }
          },
          plugins: Object.assign({}, COMMON.plugins, {
            tooltip: Object.assign({}, COMMON.plugins.tooltip, {
              callbacks: { label: function(ctx){ return ctx.dataset.label + ': ' + fmtPct(ctx.parsed.y); } }
            })
          })
        })
      });
    }

    // 3) BS stacked — 조원 단위는 소수점 2자리, 억원 단위는 콤마
    var b = payload.bs;
    var elB = document.getElementById('chart-bs-' + fs);
    if (b && elB) {
      var bUnit = b.unit || '조원';
      var bFrac = (bUnit === '조원') ? 2 : 0;   // 조원이면 소수점 2자리
      new Chart(elB, {
        type: 'bar',
        data: { labels: b.labels, datasets: b.datasets },
        options: Object.assign({}, COMMON, {
          scales: {
            y: {
              stacked: true,
              ticks: {
                callback: function(v){
                  return v.toLocaleString('ko-KR',
                    {minimumFractionDigits: bFrac, maximumFractionDigits: bFrac}) + bUnit;
                },
                font:{size:11}
              },
              grid: { color: '#eceff4' }
            },
            x: { stacked: true, grid: { display: false }, ticks: {font:{size:11}} }
          },
          plugins: Object.assign({}, COMMON.plugins, {
            tooltip: Object.assign({}, COMMON.plugins.tooltip, {
              callbacks: {
                label: function(ctx){
                  return ctx.dataset.label + ': ' + ctx.parsed.y.toLocaleString('ko-KR',
                    {minimumFractionDigits: bFrac, maximumFractionDigits: bFrac}) + bUnit;
                },
                footer: function(items){
                  var sum = items.reduce(function(s, i){ return s + i.parsed.y; }, 0);
                  return '자산총계 ≈ ' + sum.toLocaleString('ko-KR',
                    {minimumFractionDigits: bFrac, maximumFractionDigits: bFrac}) + bUnit;
                }
              }
            })
          })
        })
      });
    }
  }

  // 모든 FS 차트 초기화 (hidden view 도 그대로 그려두면 토글 시 즉시 표시)
  ['cfs', 'ofs'].forEach(initFsCharts);

  // 지분 도넛 (공통)
  var o = CD.ownership;
  var elO = document.getElementById('chart-ownership');
  if (o && elO) {
    new Chart(elO, {
      type: 'doughnut',
      data: { labels: o.labels, datasets: o.datasets },
      options: Object.assign({}, COMMON, {
        cutout: '55%',
        plugins: Object.assign({}, COMMON.plugins, {
          legend: { position: 'right', labels: { usePointStyle: true, boxWidth: 8, font:{size:11} } },
          tooltip: Object.assign({}, COMMON.plugins.tooltip, {
            callbacks: { label: function(ctx){ return ctx.label + ': ' + ctx.parsed.toFixed(2) + '%'; } }
          })
        })
      })
    });
  }

  // FS 토글 버튼 핸들러
  document.querySelectorAll('.fs-toggle .tab').forEach(function(btn){
    btn.addEventListener('click', function(){
      if (btn.hasAttribute('disabled') || btn.classList.contains('disabled')) return;
      var fs = btn.dataset.fs;
      var section = btn.closest('.fin-card');
      if (!section) return;
      section.querySelectorAll('.tab').forEach(function(b){
        b.classList.toggle('active', b.dataset.fs === fs);
      });
      section.querySelectorAll('.fs-view').forEach(function(v){
        v.style.display = v.dataset.fs === fs ? '' : 'none';
      });
    });
  });
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
    footnotes: Optional[dict] = None,
) -> None:
    cnt = SectionCounter()
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Chart.js 데이터 — CFS/OFS 각각, ownership 은 공통
    from types import SimpleNamespace
    def _fs_payload(annual, latest_q):
        if not annual and not latest_q:
            return None
        view = SimpleNamespace(annual=annual, latest_quarter=latest_q)
        return {
            "performance": cd.build_performance_chart(view),
            "margin":      cd.build_margin_chart(view),
            "bs":          cd.build_bs_chart(view),
        }

    chart_payload: Dict[str, Any] = {
        "cfs":       _fs_payload(fin.annual_cfs, fin.latest_quarter_cfs),
        "ofs":       _fs_payload(fin.annual_ofs, fin.latest_quarter_ofs),
        "ownership": cd.build_ownership_chart(shareholders),
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
