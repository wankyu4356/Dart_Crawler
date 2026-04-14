# -*- coding: utf-8 -*-
"""M9 — HTML 리포트 생성.

레이아웃:
  ① 헤더 (회사명·티커·업종·CEO·결산월·조회기간)
  ② Executive Summary (Claude 생성 마크다운)
  ③ 재무 하이라이트 (표 + YoY)
  ④ 지배구조 (최대주주 + 임원 + 배당)
  ⑤ 공시 타임라인 (유형 뱃지 + Implication 강조)
  ⑥ 핵심 이슈 카드 (B=주요사항 상위 10건)
"""
from __future__ import annotations
import html
from datetime import datetime
from typing import Any, Dict, List, Optional

from .disclosures import Disclosure
from .financials import FinancialsBundle, KEY_LABEL, format_krw, yoy
from .profile import Profile
from .shareholders import ShareholderBundle, top_holder_summary


# ── util ────────────────────────────────────────────────────────────────
def _esc(s: Any) -> str:
    return html.escape(str(s) if s is not None else "")


def _md_to_html(text: str) -> str:
    """아주 단순한 마크다운 → HTML 변환 (제목·굵게·리스트·단락)."""
    if not text:
        return ""
    lines = text.splitlines()
    out = []
    in_ul = False
    for raw in lines:
        ln = raw.rstrip()
        if not ln.strip():
            if in_ul:
                out.append("</ul>")
                in_ul = False
            out.append("")
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
            out.append(f"<h{m_h+1}>{_inline(ln[m_h+1:].strip())}</h{m_h+1}>")
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
    # **bold**
    import re
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    return s


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
        f'<span style="display:inline-block;background:{color};color:#fff;'
        f'padding:2px 8px;border-radius:10px;font-size:11px;'
        f'font-weight:600;margin-right:6px;">{_esc(label)}</span>'
    )


def _fmt_date(s: str) -> str:
    s = (s or "").replace("-", "")
    if len(s) == 8:
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s or "-"


# ── 섹션 렌더 ────────────────────────────────────────────────────────────
def _header(profile: Profile, period_label: str) -> str:
    return f"""
<header class="hero">
  <h1>{_esc(profile.corp_name)}
      <span class="sub">{_esc(profile.corp_name_eng)}</span></h1>
  <div class="meta">
    <span>종목코드 <b>{_esc(profile.stock_code or '-')}</b></span>
    <span>법인구분 <b>{_esc(profile.corp_cls_label)}</b></span>
    <span>대표 <b>{_esc(profile.ceo_nm or '-')}</b></span>
    <span>결산월 <b>{_esc(profile.acc_mt or '-')}</b></span>
    <span>설립 <b>{_esc(_fmt_date(profile.est_dt))}</b></span>
    <span>조회기간 <b>{_esc(period_label)}</b></span>
  </div>
</header>
"""


def _exec_section(exec_summary: Optional[str]) -> str:
    if not exec_summary:
        return ""
    return f"""
<section class="card">
  <h2>① Executive Summary</h2>
  <div class="exec">{_md_to_html(exec_summary)}</div>
</section>
"""


def _financials_section(fin: FinancialsBundle) -> str:
    if not fin.annual and not fin.latest_quarter:
        return ""
    cols = [f"{y.year} ({y.reprt_label})" for y in fin.annual]
    if fin.latest_quarter:
        q = fin.latest_quarter
        cols.append(f"{q.year} {q.reprt_label}")
    head = "".join(f"<th>{_esc(c)}</th>" for c in cols)

    rows_html = []
    for key, label in KEY_LABEL.items():
        cells = []
        for y in fin.annual:
            cells.append(f"<td>{_esc(format_krw(y.values.get(key)))}</td>")
        if fin.latest_quarter:
            cells.append(f"<td>{_esc(format_krw(fin.latest_quarter.values.get(key)))}</td>")
        rows_html.append(f"<tr><th>{_esc(label)}</th>{''.join(cells)}</tr>")

    # 매출 YoY
    if len(fin.annual) >= 2:
        yoy_cells = []
        for i, y in enumerate(fin.annual):
            if i + 1 >= len(fin.annual):
                yoy_cells.append("<td>-</td>")
                continue
            v = yoy(y.values.get("revenue"), fin.annual[i + 1].values.get("revenue"))
            if v is None:
                yoy_cells.append("<td>-</td>")
            else:
                color = "#2e7d32" if v >= 0 else "#c62828"
                yoy_cells.append(f'<td style="color:{color};font-weight:600;">{v:+.1f}%</td>')
        if fin.latest_quarter:
            yoy_cells.append("<td>-</td>")
        rows_html.append(
            f"<tr><th>매출 YoY</th>{''.join(yoy_cells)}</tr>"
        )

    # 지표
    ind_html = ""
    if fin.indicators:
        blocks = []
        for period, groups in fin.indicators.items():
            inner = []
            for group_label, items in groups.items():
                lis = "".join(
                    f"<li><span>{_esc(it.get('idx_nm',''))}</span>"
                    f"<b>{_esc(it.get('idx_val',''))}</b></li>"
                    for it in items
                )
                inner.append(f"<div class='indgrp'><h4>{_esc(group_label)}</h4><ul>{lis}</ul></div>")
            blocks.append(
                f"<div class='indperiod'><h3>{_esc(period)}</h3>"
                f"<div class='indrow'>{''.join(inner)}</div></div>"
            )
        ind_html = f"<div class='indicators'>{''.join(blocks)}</div>"

    return f"""
<section class="card">
  <h2>② 재무 하이라이트</h2>
  <table class="fin">
    <thead><tr><th>계정</th>{head}</tr></thead>
    <tbody>{''.join(rows_html)}</tbody>
  </table>
  {ind_html}
</section>
"""


def _governance_section(sh: ShareholderBundle) -> str:
    if not any([sh.major, sh.major_change, sh.executives, sh.dividends, sh.audit_opinion]):
        return ""
    th = top_holder_summary(sh.major)
    top_line = ""
    if th:
        top_line = (
            f"<p class='top-holder'>최대주주 <b>{_esc(th['name'])}</b> "
            f"({_esc(th['relate'])}) — 기말지분율 "
            f"<b>{th['trmend_rate']}%</b></p>"
            if th["trmend_rate"] is not None else ""
        )

    def _table(items: List[Dict[str, Any]], cols: List[tuple[str, str]]) -> str:
        if not items:
            return "<p class='muted'>(데이터 없음)</p>"
        head = "".join(f"<th>{_esc(label)}</th>" for _, label in cols)
        body = ""
        for it in items[:30]:
            tds = "".join(f"<td>{_esc(it.get(key, ''))}</td>" for key, _ in cols)
            body += f"<tr>{tds}</tr>"
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"

    major_tbl = _table(sh.major, [
        ("nm", "성명"), ("relate", "관계"), ("stock_knd", "주식종류"),
        ("trmend_posesn_stock_co", "보유주식수"),
        ("trmend_posesn_stock_qota_rt", "기말지분율(%)"),
    ])
    exec_tbl = _table(sh.executives, [
        ("nm", "성명"), ("ofcps", "직위"), ("chrg_job", "담당"),
        ("rgist_exctv_at", "등기"), ("hffc_pd", "재직기간"),
    ])
    div_tbl = _table(sh.dividends, [
        ("se", "구분"), ("stock_knd", "주식종류"),
        ("thstrm", "당기"), ("frmtrm", "전기"), ("lwfr", "전전기"),
    ])
    audit_tbl = _table(sh.audit_opinion, [
        ("bsns_year", "사업연도"), ("adtor", "감사인"),
        ("adt_opinion", "감사의견"), ("emphs_matter", "강조사항"),
    ])

    src = f"(기준: {sh.source_year} 사업보고서)" if sh.source_year else ""
    return f"""
<section class="card">
  <h2>③ 지배구조 <span class="muted small">{_esc(src)}</span></h2>
  {top_line}
  <h3>최대주주 및 특수관계인</h3>
  {major_tbl}
  <h3>경영진</h3>
  {exec_tbl}
  <h3>배당</h3>
  {div_tbl}
  <h3>감사의견</h3>
  {audit_tbl}
</section>
"""


def _timeline_section(discs: List[Disclosure]) -> str:
    if not discs:
        return "<section class='card'><h2>④ 이슈 타임라인</h2><p class='muted'>기간 내 공시 없음</p></section>"
    items = []
    for d in discs:
        extra = ""
        if d.llm_status == "ok":
            kp = "".join(f"<li>{_esc(k)}</li>" for k in d.key_points)
            extra = (
                f"<div class='llmblk'>"
                f"<p class='summary'>{_esc(d.summary)}</p>"
                f"{'<ul class=kp>'+kp+'</ul>' if kp else ''}"
                f"<p class='implication'><b>Implication:</b> {_esc(d.implication)}</p>"
                f"</div>"
            )
        items.append(f"""
  <div class="item">
    <div class="date">{_esc(_fmt_date(d.rcept_dt))}</div>
    <div class="body">
      {_badge(d.pblntf_ty)}
      <a class="ttl" href="{_esc(d.viewer_url)}" target="_blank">{_esc(d.report_nm)}</a>
      <span class="flr muted small">by {_esc(d.flr_nm)}</span>
      {extra}
    </div>
  </div>
""")
    return f"""
<section class="card">
  <h2>④ 이슈 타임라인 <span class="muted small">({len(discs)}건)</span></h2>
  <div class="timeline">
    {''.join(items)}
  </div>
</section>
"""


def _key_issues_section(discs: List[Disclosure], max_items: int = 10) -> str:
    """주요사항 + Implication이 있는 건 상위 N건 카드 레이아웃."""
    key = [d for d in discs
           if d.pblntf_ty == "B" and d.llm_status == "ok"][:max_items]
    if not key:
        return ""
    cards = []
    for d in key:
        cards.append(f"""
  <article class="issue">
    <header>
      <time>{_esc(_fmt_date(d.rcept_dt))}</time>
      <a href="{_esc(d.viewer_url)}" target="_blank">{_esc(d.report_nm)}</a>
    </header>
    <p class="summary">{_esc(d.summary)}</p>
    <p class="implication"><b>Implication:</b> {_esc(d.implication)}</p>
  </article>
""")
    return f"""
<section class="card">
  <h2>⑤ 핵심 이슈 카드 (주요사항 중심)</h2>
  <div class="issues">{''.join(cards)}</div>
</section>
"""


# ── CSS ──────────────────────────────────────────────────────────────────
CSS = """
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans KR',
     Arial,sans-serif;max-width:1100px;margin:0 auto;padding:24px;
     background:#f4f5f7;color:#1c1f24;line-height:1.55;}
header.hero{background:linear-gradient(120deg,#1a237e,#3949ab);
     color:#fff;padding:28px 32px;border-radius:14px;margin-bottom:20px;}
header.hero h1{margin:0 0 6px 0;font-size:28px;}
header.hero .sub{font-size:14px;opacity:.7;margin-left:10px;font-weight:400;}
header.hero .meta{display:flex;flex-wrap:wrap;gap:18px;font-size:13px;opacity:.92;}
section.card{background:#fff;border-radius:12px;padding:20px 24px;margin:16px 0;
     box-shadow:0 1px 3px rgba(0,0,0,.06);}
section.card h2{margin-top:0;border-bottom:2px solid #eceff4;padding-bottom:8px;}
section.card h3{margin-top:20px;font-size:15px;color:#37474f;}
.muted{color:#78909c;} .small{font-size:12px;}
.top-holder{background:#f3f6ff;border-left:4px solid #3949ab;
     padding:8px 12px;border-radius:6px;}
.exec p,.exec li{font-size:14px;}
table{width:100%;border-collapse:collapse;margin:8px 0;font-size:13px;}
table th,table td{padding:6px 10px;border-bottom:1px solid #eceff4;text-align:left;}
table thead th{background:#f5f7fa;font-weight:600;}
table.fin td,table.fin th{text-align:right;}
table.fin th:first-child,table.fin td:first-child{text-align:left;}
.indicators{margin-top:14px;}
.indperiod{margin-bottom:10px;}
.indrow{display:flex;flex-wrap:wrap;gap:14px;}
.indgrp{flex:1 1 200px;background:#f7f9fc;padding:10px 14px;border-radius:8px;}
.indgrp h4{margin:0 0 6px 0;font-size:13px;color:#455a64;}
.indgrp ul{margin:0;padding:0;list-style:none;}
.indgrp li{display:flex;justify-content:space-between;font-size:13px;padding:2px 0;}
.timeline{border-left:2px solid #e0e0e0;padding-left:14px;}
.timeline .item{margin:10px 0;}
.timeline .date{font-size:12px;color:#607d8b;margin-bottom:2px;}
.timeline .ttl{font-weight:600;color:#1a237e;text-decoration:none;}
.timeline .ttl:hover{text-decoration:underline;}
.timeline .flr{margin-left:8px;}
.llmblk{background:#fafbfe;border:1px solid #eceff4;border-radius:8px;
     padding:8px 12px;margin-top:6px;}
.llmblk .summary{margin:2px 0 6px 0;font-size:13px;}
.llmblk ul.kp{margin:0 0 6px 18px;padding:0;font-size:13px;color:#37474f;}
.llmblk .implication{margin:0;font-size:13px;color:#b71c1c;
     background:#fff8f8;padding:6px 8px;border-radius:6px;}
.issues{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px;}
.issue{background:#fafbfe;border:1px solid #eceff4;border-radius:10px;padding:12px 14px;}
.issue header{display:flex;justify-content:space-between;margin-bottom:6px;font-size:13px;}
.issue header time{color:#607d8b;}
.issue header a{color:#1a237e;text-decoration:none;font-weight:600;}
.issue .summary{font-size:13px;margin:4px 0;}
.issue .implication{background:#fff8f8;color:#b71c1c;padding:6px 8px;
     border-radius:6px;font-size:13px;}
footer{text-align:center;color:#90a4ae;font-size:12px;margin-top:30px;}
"""


def write_html(
    path: str,
    profile: Profile,
    fin: FinancialsBundle,
    shareholders: ShareholderBundle,
    disclosures: List[Disclosure],
    period_label: str,
    exec_summary: Optional[str] = None,
) -> None:
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    body = (
        _header(profile, period_label)
        + _exec_section(exec_summary)
        + _financials_section(fin)
        + _governance_section(shareholders)
        + _timeline_section(disclosures)
        + _key_issues_section(disclosures)
    )
    html_doc = f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<title>DART QuickReport — {_esc(profile.corp_name)}</title>
<style>{CSS}</style></head><body>
{body}
<footer>Generated {_esc(generated)} · DART QuickReport</footer>
</body></html>
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(html_doc)
