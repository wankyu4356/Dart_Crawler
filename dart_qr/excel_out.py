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

from .config import CONTACT_EMAIL, CONTACT_NAME
from .disclosures import Disclosure
from .financials import (
    BALANCE_KEYS, FinancialsBundle, KEY_LABEL, PCT_KEYS, PERFORMANCE_KEYS,
    _to_num, format_value, yoy,
)
from .profile import Profile
from .shareholders import ShareholderBundle, top_holder_summary


HEADER_FONT = Font(bold=True, color="FFFFFF")
HEADER_FILL = PatternFill("solid", fgColor="305496")
SUBHEADER_FILL = PatternFill("solid", fgColor="D9E1F2")
RATIO_FILL = PatternFill("solid", fgColor="F3F6FB")   # 비율 행 옅은 음영
RATIO_FONT = Font(italic=True, color="1F3864")
LINK_FONT = Font(color="0563C1", underline="single")   # 하이퍼링크 스타일
WRAP = Alignment(wrap_text=True, vertical="top")


def _apply_hyperlink(cell, url: str, display: str = None) -> None:
    """openpyxl 셀에 하이퍼링크 적용."""
    if not url:
        return
    cell.value = display if display else url
    cell.hyperlink = url
    cell.font = LINK_FONT

# 셀 표시 포맷 — 값은 raw 숫자(원 단위)로 저장하고 표시만 포맷
KRW_FMT = '#,##0'           # 1,234,567,890
# 12.3% (기본) / -12.3% (빨강) / -
PCT_FMT = '0.0"%";[Red]\\-0.0"%";"-"'


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

    # 문의 메모
    last_row = 5 + len(rows) + 2
    cell = ws.cell(row=last_row, column=1,
                   value=f"문의·제안: {CONTACT_EMAIL}")
    cell.font = Font(italic=True, color="607D8B")
    cell = ws.cell(row=last_row + 1, column=1,
                   value=f"생성기: {CONTACT_NAME}")
    cell.font = Font(italic=True, color="B0BEC5", size=9)

    _autofit(ws)


def _write_financials_sheet(wb: Workbook, sheet_name: str,
                             annual_list, latest_q) -> None:
    from openpyxl.utils import get_column_letter

    if not annual_list and not latest_q:
        return

    ws = wb.create_sheet(sheet_name)
    # 연도 헤더에 연결/별도 구분 (한국어) 표시
    FS_KR = {"CFS": "연결", "OFS": "별도"}
    headers = ["계정"]
    for y in annual_list:
        tag = f" · {FS_KR.get(y.fs_div, y.fs_div)}" if y.fs_div in ("CFS", "OFS") else ""
        headers.append(f"{y.year} ({y.reprt_label}{tag})")
    if latest_q:
        q = latest_q
        tag = f" · {FS_KR.get(q.fs_div, q.fs_div)}" if q.fs_div in ("CFS", "OFS") else ""
        headers.append(f"{q.year} {q.reprt_label}{tag}")
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
                for y in annual_list:
                    row.append(y.values.get(key))
                if latest_q:
                    row.append(latest_q.values.get(key))
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
    if len(annual_list) >= 2:
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
            for i in range(len(annual_list)):
                col_curr = get_column_letter(2 + i)
                col_prev = get_column_letter(2 + i + 1)
                if i + 1 >= len(annual_list):
                    break  # 마지막 연도는 전년 없음 → 빈 셀
                cell = ws.cell(row=yoy_row_idx, column=2 + i)
                cell.value = (
                    f'=IFERROR(({col_curr}{src_row}/{col_prev}{src_row}-1)*100,"")'
                )
                cell.number_format = PCT_FMT
                cell.alignment = Alignment(horizontal="right")
            # 나머지 셀 (마지막 연도 + 분기) 포맷만
            for c in range(2 + max(len(annual_list) - 1, 0), n_cols + 1):
                ws.cell(row=yoy_row_idx, column=c).number_format = PCT_FMT

    # 혼합 모드 각주
    ofs_years = [y.year for y in annual_list if y.fs_div == "OFS"]
    cfs_years = [y.year for y in annual_list if y.fs_div == "CFS"]
    if ofs_years and cfs_years:
        ws.append([])
        note = (f"※ {', '.join(str(y) for y in ofs_years)}년은 별도기준 "
                f"(연결감사보고서 미제출). 다른 연도는 연결기준과 비교에 유의.")
        ws.append([note])
        ws.cell(row=ws.max_row, column=1).font = Font(italic=True, color="B71C1C")

    _autofit(ws, max_width=28)


def _write_financials(wb: Workbook, fin: FinancialsBundle) -> None:
    """재무 시트 최대 3벌 생성: 재무(hybrid) / 재무_연결 / 재무_별도."""
    # 1) 기본 hybrid "재무" (기존 호환 유지)
    _write_financials_sheet(wb, "재무", fin.annual, fin.latest_quarter)
    # 2) 연결 전용
    if fin.annual_cfs or fin.latest_quarter_cfs:
        _write_financials_sheet(wb, "재무_연결", fin.annual_cfs, fin.latest_quarter_cfs)
    # 3) 별도 전용
    if fin.annual_ofs or fin.latest_quarter_ofs:
        _write_financials_sheet(wb, "재무_별도", fin.annual_ofs, fin.latest_quarter_ofs)


SJ_ORDER = ["BS", "IS", "CIS", "CF", "SCE"]
SJ_LABEL = {
    "BS":  "재무상태표",
    "IS":  "손익계산서",
    "CIS": "포괄손익계산서",
    "CF":  "현금흐름표",
    "SCE": "자본변동표",
}


def _write_fin_detail(wb: Workbook, fin: FinancialsBundle) -> None:
    """fnlttSinglAcntAll 의 raw rows 를 계정별 시계열로 피벗해 한 시트에 기록."""
    if not fin.raw_rows:
        return

    # 피벗: (sj_div, account_id, account_nm, ord) -> {year: amount}
    pivot: Dict[tuple, Dict[int, float]] = {}
    meta: Dict[tuple, Dict[str, str]] = {}      # account_detail / currency / sj_nm
    years_seen: set = set()

    for r in fin.raw_rows:
        sj = (r.get("sj_div") or "").upper()
        aid = (r.get("account_id") or "").strip()
        anm = (r.get("account_nm") or "").strip()
        ord_s = r.get("ord") or "99999"
        try:
            ordn = int(str(ord_s).replace(",", ""))
        except ValueError:
            ordn = 99999
        call_year = r.get("_call_year")
        if not isinstance(call_year, int):
            continue
        key = (sj, aid, anm, ordn)
        meta.setdefault(key, {
            "sj_nm":          r.get("sj_nm", ""),
            "account_detail": r.get("account_detail", ""),
            "currency":       r.get("currency", ""),
        })
        for period, yoff in [("thstrm", 0), ("frmtrm", 1), ("bfefrmtrm", 2)]:
            year = call_year - yoff
            amt = _to_num(r.get(f"{period}_amount"))
            if amt is None:
                continue
            pivot.setdefault(key, {}).setdefault(year, amt)
            years_seen.add(year)

    if not years_seen:
        return
    years = sorted(years_seen, reverse=True)   # 최신 → 과거

    ws = wb.create_sheet("재무제표_상세")
    headers = ["구분", "계정ID", "계정명", "계정상세", *[f"{y}" for y in years], "통화"]
    ws.append(headers)
    _style_header(ws, 1, len(headers))

    for sj in SJ_ORDER:
        sj_keys = sorted([k for k in pivot if k[0] == sj], key=lambda k: k[3])
        if not sj_keys:
            continue
        # 섹션 헤더 행
        ws.append([SJ_LABEL.get(sj, sj)])
        ridx = ws.max_row
        for c in range(1, len(headers) + 1):
            cell = ws.cell(row=ridx, column=c)
            cell.font = Font(bold=True, color="305496")
            cell.fill = SUBHEADER_FILL

        for key in sj_keys:
            sj_, aid, anm, ordn = key
            m = meta.get(key, {})
            row_vals: List[Any] = [
                SJ_LABEL.get(sj_, sj_), aid, anm, m.get("account_detail", ""),
            ]
            for y in years:
                v = pivot[key].get(y)
                row_vals.append(v)
            row_vals.append(m.get("currency", ""))
            ws.append(row_vals)
            r_idx = ws.max_row
            # 연도 컬럼 포맷
            for c in range(5, 5 + len(years)):
                cell = ws.cell(row=r_idx, column=c)
                cell.number_format = KRW_FMT
                cell.alignment = Alignment(horizontal="right")

    # 상단 고정 + 열 너비
    ws.freeze_panes = "E2"
    ws.column_dimensions["A"].width = 12
    ws.column_dimensions["B"].width = 36
    ws.column_dimensions["C"].width = 32
    ws.column_dimensions["D"].width = 22
    for i in range(len(years)):
        ws.column_dimensions[get_column_letter(5 + i)].width = 18
    ws.column_dimensions[get_column_letter(5 + len(years))].width = 8


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
    # 제목 컬럼(5) 을 하이퍼링크로 만들고, DART 링크 컬럼(9) 에도 "열기" 링크
    for d in discs:
        ws.append([
            d.rcept_dt, d.ty_label, d.pblntf_detail_ty, d.flr_nm,
            d.report_nm, d.summary,
            d.implication, d.llm_status, "",
        ])
        r = ws.max_row
        # 제목 → 하이퍼링크
        _apply_hyperlink(ws.cell(row=r, column=5), d.viewer_url, d.report_nm)
        # DART 링크 열 → "열기"
        _apply_hyperlink(ws.cell(row=r, column=9), d.viewer_url, "열기 ↗")
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
            d.rcept_dt, "", d.ty_label,
            d.summary,
            "\n".join(f"• {kp}" for kp in d.key_points),
            d.implication,
            "",
        ])
        r = ws.max_row
        _apply_hyperlink(ws.cell(row=r, column=2), d.viewer_url, d.report_nm)
        _apply_hyperlink(ws.cell(row=r, column=7), d.viewer_url, "열기 ↗")
    for row in ws.iter_rows(min_row=2, min_col=4, max_col=6):
        for cell in row:
            cell.alignment = WRAP
    _autofit(ws, max_width=60)


def _write_business(wb: Workbook, biz: Optional[dict]) -> None:
    if not biz:
        return
    ws = wb.create_sheet("사업개요", 1)
    ws["A1"] = "회사 개요 (Business Profile)"
    ws["A1"].font = Font(bold=True, size=14)
    src = biz.get("_source_report_nm") or ""
    src_rcept = biz.get("_source_rcept_no") or ""
    if src:
        ws["A2"] = "기준 보고서:"
        ws["A2"].font = Font(italic=True, color="607D8B")
        if src_rcept:
            url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={src_rcept}"
            _apply_hyperlink(ws.cell(row=2, column=2), url, src)
        else:
            ws["B2"] = src
            ws["B2"].font = Font(italic=True, color="607D8B")

    r = 4
    summary = (biz.get("business_summary") or "").strip()
    if summary:
        ws.cell(row=r, column=1, value="요약").font = Font(bold=True, color="305496")
        r += 1
        c = ws.cell(row=r, column=1, value=summary)
        c.alignment = WRAP
        ws.row_dimensions[r].height = max(40, min(200, 18 * (len(summary) // 50 + 1)))
        r += 2

    products = biz.get("products") or []
    if products:
        ws.cell(row=r, column=1, value="주요 제품/서비스").font = Font(bold=True, color="305496")
        r += 1
        for p in products[:30]:
            ws.cell(row=r, column=1, value=f"• {p}")
            r += 1
        r += 1

    segments = biz.get("segments") or []
    if segments:
        ws.cell(row=r, column=1, value="사업부별 매출·영업이익률").font = Font(bold=True, color="305496")
        r += 1
        headers = ["사업부/제품군", "매출", "매출 단위/연도", "영업이익률(%)", "설명"]
        for i, h in enumerate(headers, start=1):
            cell = ws.cell(row=r, column=i, value=h)
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
        r += 1
        for s in segments[:30]:
            ws.cell(row=r, column=1, value=s.get("name", ""))
            rev = s.get("revenue")
            ws.cell(row=r, column=2, value=rev if isinstance(rev, (int, float)) else None)
            ws.cell(row=r, column=2).number_format = KRW_FMT
            ws.cell(row=r, column=3, value=s.get("revenue_note", ""))
            opm = s.get("op_margin_pct")
            ws.cell(row=r, column=4, value=opm if isinstance(opm, (int, float)) else None)
            ws.cell(row=r, column=4).number_format = PCT_FMT
            ws.cell(row=r, column=5, value=s.get("description", "")).alignment = WRAP
            r += 1
        r += 1

    customers = biz.get("major_customers") or []
    suppliers = biz.get("major_suppliers") or []
    if customers or suppliers:
        ws.cell(row=r, column=1, value="주요 매출처").font = Font(bold=True, color="305496")
        ws.cell(row=r, column=3, value="주요 매입처").font = Font(bold=True, color="305496")
        r += 1
        max_n = max(len(customers), len(suppliers))
        for i in range(min(20, max_n)):
            if i < len(customers):
                ws.cell(row=r, column=1, value=f"• {customers[i]}")
            if i < len(suppliers):
                ws.cell(row=r, column=3, value=f"• {suppliers[i]}")
            r += 1
        r += 1

    insights = biz.get("key_insights") or []
    if insights:
        ws.cell(row=r, column=1, value="투자 포인트").font = Font(bold=True, color="305496")
        r += 1
        for x in insights:
            cell = ws.cell(row=r, column=1, value=f"▶ {x}")
            cell.alignment = WRAP
            cell.fill = PatternFill("solid", fgColor="FFF8EF")
            r += 1

    # 열 너비
    ws.column_dimensions["A"].width = 40
    ws.column_dimensions["B"].width = 22
    ws.column_dimensions["C"].width = 28
    ws.column_dimensions["D"].width = 16
    ws.column_dimensions["E"].width = 50


# ── 최종 엔트리 ──────────────────────────────────────────────────────────
def write_excel(
    path: str,
    profile: Profile,
    fin: FinancialsBundle,
    shareholders: ShareholderBundle,
    disclosures: List[Disclosure],
    period_label: str,
    exec_summary: Optional[str] = None,
    business: Optional[dict] = None,
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

    _write_business(wb, business)
    _write_financials(wb, fin)
    _write_fin_detail(wb, fin)
    _write_indicators(wb, fin)
    _write_shareholders(wb, shareholders)
    _write_disclosure_list(wb, disclosures)
    _write_important_details(wb, disclosures)

    wb.save(path)
