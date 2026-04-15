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
from .financials import (
    BALANCE_KEYS, FinancialsBundle, KEY_LABEL, PCT_KEYS, PERFORMANCE_KEYS,
    format_value, yoy,
)
from .profile import Profile
from .shareholders import ShareholderBundle, top_holder_summary


HEADER_FONT = Font(bold=True, color="FFFFFF")
HEADER_FILL = PatternFill("solid", fgColor="305496")
SUBHEADER_FILL = PatternFill("solid", fgColor="D9E1F2")
RATIO_FILL = PatternFill("solid", fgColor="F3F6FB")   # 비율 행 옅은 음영
RATIO_FONT = Font(italic=True, color="1F3864")
WRAP = Alignment(wrap_text=True, vertical="top")

# 셀 표시 포맷 — 값은 raw 숫자(원 단위)로 저장하고 표시만 포맷
KRW_FMT = '#,##0'           # 1,234,567,890
PCT_FMT = '0.0"%";\\-0.0"%";"-"'  # 12.3% / -12.3% / -


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
    ws["A1"] = "완규의 딸깍공장 — Company Profile"
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
    from openpyxl.utils import get_column_letter

    ws = wb.create_sheet("재무")
    headers = ["계정"] + [f"{y.year} ({y.reprt_label})" for y in fin.annual]
    if fin.latest_quarter:
        q = fin.latest_quarter
        headers.append(f"{q.year} {q.reprt_label}")
    ws.append(headers)
    _style_header(ws, 1, len(headers))
    n_cols = len(headers)

    # key → 행번호 저장 (margin/YoY 수식에서 참조)
    row_of: Dict[str, int] = {}

    # 마진 키 → (분자 key, 분모 key) 매핑. 모두 * 100.
    MARGIN_FORMULA = {
        "gpm":     ("gross_profit", "revenue"),
        "opm":     ("op_income",    "revenue"),
        "ebitdam": ("ebitda",       "revenue"),
        "npm":     ("net_income",   "revenue"),
    }

    def _emit_block(title: str, keys: List[str]) -> None:
        ws.append([title])
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True, color="305496")
        ws.cell(row=ws.max_row, column=1).fill = SUBHEADER_FILL
        for key in keys:
            label = KEY_LABEL.get(key, key)
            row: List[Any] = [label]
            if key in MARGIN_FORMULA:
                # 마진은 placeholder 로 append 후 행번호 기록 → 수식 채움
                for _ in range(n_cols - 1):
                    row.append(None)
                ws.append(row)
                row_of[key] = ws.max_row
                r_idx = ws.max_row
                # 라벨 셀도 이탤릭 + 음영
                lbl_cell = ws.cell(row=r_idx, column=1)
                lbl_cell.font = RATIO_FONT
                lbl_cell.fill = RATIO_FILL
                num_key, den_key = MARGIN_FORMULA[key]
                num_r = row_of.get(num_key)
                den_r = row_of.get(den_key)
                for c in range(2, n_cols + 1):
                    cell = ws.cell(row=r_idx, column=c)
                    if num_r and den_r:
                        col = get_column_letter(c)
                        cell.value = f'=IFERROR({col}{num_r}/{col}{den_r}*100,"")'
                    cell.number_format = PCT_FMT
                    cell.alignment = Alignment(horizontal="right")
                    cell.font = RATIO_FONT
                    cell.fill = RATIO_FILL
            else:
                # 일반 raw 값
                for y in fin.annual:
                    row.append(y.values.get(key))
                if fin.latest_quarter:
                    row.append(fin.latest_quarter.values.get(key))
                ws.append(row)
                row_of[key] = ws.max_row
                for c in range(2, n_cols + 1):
                    cell = ws.cell(row=ws.max_row, column=c)
                    cell.number_format = KRW_FMT
                    cell.alignment = Alignment(horizontal="right")

    _emit_block("◆ Performance (손익)", PERFORMANCE_KEYS)
    ws.append([])
    _emit_block("◆ Balance Sheet", BALANCE_KEYS)

    # YoY 영역 — 수식 기반: =(curr/prev - 1)*100
    # annual 은 최신→과거 순. Excel 컬럼도 최신이 왼쪽(B), 과거가 오른쪽.
    if len(fin.annual) >= 2:
        ws.append([])
        ws.append(["◆ YoY 성장률 (매출/영업이익/순이익)"])
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True, color="305496")
        ws.cell(row=ws.max_row, column=1).fill = SUBHEADER_FILL

        for key, label in [("revenue", "매출 YoY"),
                           ("op_income", "영업이익 YoY"),
                           ("net_income", "순이익 YoY")]:
            src_row = row_of.get(key)
            ws.append([label] + [None] * (n_cols - 1))
            yoy_row_idx = ws.max_row
            if src_row is None:
                continue
            # 각 annual 연도 i 에 대해 i+1 (전년) 대비 수식. 마지막 열(가장 과거)은 비교 불가.
            for i in range(len(fin.annual)):
                col_curr = get_column_letter(2 + i)
                col_prev = get_column_letter(2 + i + 1)
                if i + 1 >= len(fin.annual):
                    break  # 마지막 연도는 전년 없음 → 빈 셀
                cell = ws.cell(row=yoy_row_idx, column=2 + i)
                cell.value = (
                    f'=IFERROR(({col_curr}{src_row}/{col_prev}{src_row}-1)*100,"")'
                )
                cell.number_format = PCT_FMT
                cell.alignment = Alignment(horizontal="right")
            # 나머지 셀 (마지막 연도 + 분기) 포맷만
            for c in range(2 + max(len(fin.annual) - 1, 0), n_cols + 1):
                ws.cell(row=yoy_row_idx, column=c).number_format = PCT_FMT

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
