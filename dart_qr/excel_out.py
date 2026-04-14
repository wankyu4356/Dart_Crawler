# -*- coding: utf-8 -*-
"""M8 — Excel 리포트 저장 (openpyxl).

시트 구성:
  Profile / 재무 / 재무지표 / 주주_최대 / 주주_변동 / 주주_대량보유 /
  주주_임원소유 / 주주_소액 / 임원 / 배당 / 타법인출자 / 감사의견 /
  공시리스트 / 주요공시상세
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .disclosures import Disclosure
from .financials import FinancialsBundle, KEY_LABEL, format_krw, yoy
from .profile import Profile
from .shareholders import ShareholderBundle, top_holder_summary


HEADER_FONT = Font(bold=True, color="FFFFFF")
HEADER_FILL = PatternFill("solid", fgColor="305496")
SUBHEADER_FILL = PatternFill("solid", fgColor="D9E1F2")
WRAP = Alignment(wrap_text=True, vertical="top")


def _style_header(ws, row: int, ncols: int) -> None:
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row, column=c)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center")


def _autofit(ws, max_width: int = 50) -> None:
    for col in ws.columns:
        letter = get_column_letter(col[0].column)
        width = 10
        for cell in col:
            v = cell.value
            if v is None:
                continue
            # 한글 폭 보정: 한글 1.8, ASCII 1.0
            s = str(v)
            w = sum(1.8 if ord(ch) > 127 else 1.0 for ch in s[:80])
            if w > width:
                width = w
        ws.column_dimensions[letter].width = min(max_width, width + 2)


# ── 섹션별 writer ────────────────────────────────────────────────────────
def _write_profile(ws, profile: Profile, period_label: str) -> None:
    ws.title = "Profile"
    ws["A1"] = "DART QuickReport — Company Profile"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A3"] = "조회기간"; ws["B3"] = period_label
    rows = [
        ("회사명(국문)",  profile.corp_name),
        ("회사명(영문)",  profile.corp_name_eng),
        ("종목코드",      profile.stock_code),
        ("법인구분",      profile.corp_cls_label),
        ("대표자",        profile.ceo_nm),
        ("법인등록번호",  profile.jurir_no),
        ("사업자등록번호", profile.bizr_no),
        ("업종코드",      profile.induty_code),
        ("설립일",        profile.est_dt),
        ("결산월",        profile.acc_mt),
        ("주소",          profile.adres),
        ("전화",          profile.phn_no),
        ("홈페이지",      profile.hm_url),
        ("IR",            profile.ir_url),
    ]
    for i, (k, v) in enumerate(rows, start=5):
        ws.cell(row=i, column=1, value=k).font = Font(bold=True)
        ws.cell(row=i, column=2, value=v)
    _autofit(ws)


def _write_financials(wb: Workbook, fin: FinancialsBundle) -> None:
    ws = wb.create_sheet("재무")
    headers = ["계정"] + [f"{y.year} ({y.reprt_label})" for y in fin.annual]
    if fin.latest_quarter:
        q = fin.latest_quarter
        headers.append(f"{q.year} {q.reprt_label}")
    ws.append(headers)
    _style_header(ws, 1, len(headers))

    for key, label in KEY_LABEL.items():
        row = [label]
        for y in fin.annual:
            v = y.values.get(key)
            row.append(format_krw(v))
        if fin.latest_quarter:
            row.append(format_krw(fin.latest_quarter.values.get(key)))
        ws.append(row)

    # YoY 행 (매출 기준). annual은 최신→과거 순이므로, 각 연도의 이전 연도는
    # annual[i+1]. 마지막 연도는 비교 불가 → "-".
    if len(fin.annual) >= 2:
        yoy_row = ["매출 YoY (%)"]
        for i, y in enumerate(fin.annual):
            if i + 1 >= len(fin.annual):
                yoy_row.append("-")
                continue
            curr_rev = y.values.get("revenue")
            prev_rev = fin.annual[i + 1].values.get("revenue")
            v = yoy(curr_rev, prev_rev)
            yoy_row.append(f"{v:+.1f}%" if v is not None else "-")
        if fin.latest_quarter:
            yoy_row.append("-")
        ws.append(yoy_row)

    _autofit(ws, max_width=28)


def _write_indicators(wb: Workbook, fin: FinancialsBundle) -> None:
    if not fin.indicators:
        return
    ws = wb.create_sheet("재무지표")
    ws.append(["구분", "분류", "지표명", "지표값"])
    _style_header(ws, 1, 4)
    for period, groups in fin.indicators.items():
        for group_label, items in groups.items():
            for it in items:
                ws.append([
                    period, group_label,
                    it.get("idx_nm", ""), it.get("idx_val", ""),
                ])
    _autofit(ws, max_width=28)


def _write_table(wb: Workbook, sheet_name: str, rows: List[Dict[str, Any]]) -> None:
    ws = wb.create_sheet(sheet_name)
    if not rows:
        ws.append(["(데이터 없음)"])
        return
    headers = list(rows[0].keys())
    ws.append(headers)
    _style_header(ws, 1, len(headers))
    for r in rows:
        ws.append([r.get(h, "") for h in headers])
    _autofit(ws, max_width=40)


def _write_shareholders(wb: Workbook, sh: ShareholderBundle) -> None:
    _write_table(wb, "주주_최대",       sh.major)
    _write_table(wb, "주주_변동",       sh.major_change)
    _write_table(wb, "주주_대량보유",   sh.major_stock)
    _write_table(wb, "주주_임원소유",   sh.executive_stock)
    _write_table(wb, "주주_소액",       sh.minority)
    _write_table(wb, "임원",            sh.executives)
    _write_table(wb, "배당",            sh.dividends)
    _write_table(wb, "타법인출자",      sh.other_corp_invest)
    _write_table(wb, "감사의견",        sh.audit_opinion)


def _write_disclosure_list(wb: Workbook, discs: List[Disclosure]) -> None:
    ws = wb.create_sheet("공시리스트")
    headers = ["접수일", "유형", "세부유형", "제출인", "제목", "요약",
               "Implication", "상태", "DART 링크"]
    ws.append(headers)
    _style_header(ws, 1, len(headers))
    for d in discs:
        ws.append([
            d.rcept_dt, d.ty_label, d.pblntf_detail_ty, d.flr_nm,
            d.report_nm, d.summary,
            d.implication, d.llm_status, d.viewer_url,
        ])
    # 요약/implication 줄바꿈
    for row in ws.iter_rows(min_row=2, min_col=6, max_col=7):
        for cell in row:
            cell.alignment = WRAP
    _autofit(ws, max_width=60)


def _write_important_details(wb: Workbook, discs: List[Disclosure]) -> None:
    ws = wb.create_sheet("주요공시상세")
    ws.append(["접수일", "제목", "유형", "요약", "핵심 포인트", "Implication", "링크"])
    _style_header(ws, 1, 7)
    for d in discs:
        if d.llm_status != "ok":
            continue
        ws.append([
            d.rcept_dt, d.report_nm, d.ty_label,
            d.summary,
            "\n".join(f"• {kp}" for kp in d.key_points),
            d.implication,
            d.viewer_url,
        ])
    for row in ws.iter_rows(min_row=2, min_col=4, max_col=6):
        for cell in row:
            cell.alignment = WRAP
    _autofit(ws, max_width=60)


# ── 최종 엔트리 ──────────────────────────────────────────────────────────
def write_excel(
    path: str,
    profile: Profile,
    fin: FinancialsBundle,
    shareholders: ShareholderBundle,
    disclosures: List[Disclosure],
    period_label: str,
    exec_summary: Optional[str] = None,
) -> None:
    wb = Workbook()
    _write_profile(wb.active, profile, period_label)

    if exec_summary:
        es = wb.create_sheet("Executive Summary", 1)
        es["A1"] = "Executive Summary"
        es["A1"].font = Font(bold=True, size=14)
        es["A3"] = exec_summary
        es["A3"].alignment = WRAP
        es.column_dimensions["A"].width = 100

    _write_financials(wb, fin)
    _write_indicators(wb, fin)
    _write_shareholders(wb, shareholders)
    _write_disclosure_list(wb, disclosures)
    _write_important_details(wb, disclosures)

    wb.save(path)
