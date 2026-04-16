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
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .config import CONTACT_EMAIL, CONTACT_NAME
from .disclosures import Disclosure
from .financials import (
    BALANCE_KEYS, FinancialsBundle, KEY_LABEL, PCT_KEYS, PERFORMANCE_KEYS,
    _to_num, format_value, yoy,
)
from .profile import Profile
from .shareholders import ShareholderBundle, top_holder_summary


# ── 업종별 테마 팔레트 (headerprimary 만 달라짐; 나머지 UI 는 뉴트럴) ──
# 가시성 우선 — 접근성 고려한 어두운 톤만 사용 (텍스트 #FFF 대비 ≥ 7:1)
INDUSTRY_THEMES = {
    "bank":    "0A3D62",  # 은행 — 딥 네이비 (신뢰)
    "tech":    "1565C0",  # IT/SW — 블루
    "pharma":  "1B5E20",  # 제약/바이오 — 포레스트 그린
    "chem":    "4A148C",  # 화학/소재 — 퍼플
    "auto":    "263238",  # 자동차/운송 — 차콜
    "retail":  "BF360C",  # 소매/유통 — 버미리언
    "energy":  "3E2723",  # 에너지/광업 — 브라운
    "con":     "1A237E",  # 건설 — 인디고
    "media":   "AD1457",  # 미디어/엔터 — 딥 핑크
    "default": "1F3864",  # 기본 — 딥 블루
}


def pick_theme(profile: Profile) -> str:
    """업종 코드·회사명 키워드로 가장 적절한 헤더 테마 컬러 선택."""
    code = (profile.induty_code or "").strip()
    nm = (profile.corp_name or "") + " " + (profile.corp_name_eng or "")
    nm_l = nm.lower()
    # 한국 표준산업분류(KSIC) 앞자리 기준 대분류
    # 64=금융, 65=보험, 66=금융관련 / 21=의약 / 20=화학 / 30=자동차 / 46~47=도소매 /
    # 58~63=출판/방송/IT / 05~09=광업 / 41~42=건설
    prefix2 = code[:2] if len(code) >= 2 else ""
    if prefix2 in ("64", "65", "66") or "은행" in nm or "뱅크" in nm or \
       "bank" in nm_l or "증권" in nm or "securities" in nm_l or \
       "생명" in nm or "화재" in nm or "해상" in nm or "보험" in nm or \
       "카드" in nm or "캐피탈" in nm or \
       "금융" in nm or "finance" in nm_l or "financial" in nm_l:
        return INDUSTRY_THEMES["bank"]
    if prefix2 == "21" or "제약" in nm or "바이오" in nm or "pharma" in nm_l or "bio" in nm_l:
        return INDUSTRY_THEMES["pharma"]
    if prefix2 == "20" or "화학" in nm or "소재" in nm or "chemical" in nm_l:
        return INDUSTRY_THEMES["chem"]
    if prefix2 == "30" or "자동차" in nm or "모빌리티" in nm or "motor" in nm_l:
        return INDUSTRY_THEMES["auto"]
    if prefix2 in ("46", "47") or "유통" in nm or "리테일" in nm or "retail" in nm_l:
        return INDUSTRY_THEMES["retail"]
    if prefix2 in ("58", "59", "60", "61", "62", "63") or \
       "카카오" in nm or "네이버" in nm or "게임" in nm or "소프트" in nm_l or \
       "테크" in nm or "tech" in nm_l or "it" in nm_l:
        return INDUSTRY_THEMES["tech"]
    if prefix2 in ("05", "06", "07", "08", "09") or "에너지" in nm or "정유" in nm:
        return INDUSTRY_THEMES["energy"]
    if prefix2 in ("41", "42") or "건설" in nm or "e&c" in nm_l:
        return INDUSTRY_THEMES["con"]
    if prefix2 in ("59", "90") or "엔터" in nm or "미디어" in nm or "entertainment" in nm_l:
        return INDUSTRY_THEMES["media"]
    return INDUSTRY_THEMES["default"]


# ── IB 리포트 스타일 (모듈 전역. set_theme() 로 헤더 컬러만 동적 변경) ──
_THEME_HEX = "1F3864"  # 모듈 전역 current theme

HEADER_FONT = Font(bold=True, color="FFFFFF", name="Calibri", size=11)
HEADER_FILL = PatternFill("solid", fgColor=_THEME_HEX)        # 테마색
SECTION_FILL = PatternFill("solid", fgColor="E8EDF5")         # 뉴트럴 섹션 밴드
SUBHEADER_FILL = PatternFill("solid", fgColor="E8EDF5")       # 호환
RATIO_FILL = PatternFill("solid", fgColor="F4F7FB")           # 비율 행 뉴트럴
RATIO_FONT = Font(italic=True, color="37474F", name="Calibri", size=11)
SUBTOTAL_FILL = PatternFill("solid", fgColor="EEF2F7")        # 부분합 뉴트럴
SUBTOTAL_FONT = Font(bold=True, color="263238", name="Calibri", size=11)
TOTAL_FILL = PatternFill("solid", fgColor=_THEME_HEX)
TOTAL_FONT = Font(bold=True, color="FFFFFF", name="Calibri", size=11)


def _apply_theme(hex_color: str) -> None:
    """헤더/TOTAL 색상만 테마 hex 로 갱신 (나머지 뉴트럴 유지)."""
    global _THEME_HEX, HEADER_FILL, TOTAL_FILL, _MEDIUM_SIDE, HEADER_BORDER
    _THEME_HEX = hex_color
    HEADER_FILL.fgColor.rgb = hex_color
    TOTAL_FILL.fgColor.rgb = hex_color
    # 보더 테마색도 동기화
    _MEDIUM_SIDE = Side(style="medium", color=hex_color)
    HEADER_BORDER = Border(
        left=_MEDIUM_SIDE, right=_MEDIUM_SIDE,
        top=_MEDIUM_SIDE, bottom=_MEDIUM_SIDE,
    )
CHECK_OK_FILL = PatternFill("solid", fgColor="E8F5E9")
CHECK_FAIL_FILL = PatternFill("solid", fgColor="FFEBEE")
NOTE_FONT = Font(italic=True, color="616161", name="Calibri", size=10)
LINK_FONT = Font(color="0563C1", underline="single", name="Calibri", size=11)
BODY_FONT = Font(name="Calibri", size=11)
HEADER_ALIGN = Alignment(horizontal="center", vertical="center", wrap_text=True)
RIGHT_ALIGN = Alignment(horizontal="right", vertical="center")
LEFT_ALIGN = Alignment(horizontal="left", vertical="center", indent=0)
INDENT_ALIGN = Alignment(horizontal="left", vertical="center", indent=1)
WRAP = Alignment(wrap_text=True, vertical="top")

# 얇은 border
_THIN_SIDE = Side(style="thin", color="BDBDBD")
_MEDIUM_SIDE = Side(style="medium", color="1F3864")
THIN_BORDER = Border(left=_THIN_SIDE, right=_THIN_SIDE, top=_THIN_SIDE, bottom=_THIN_SIDE)
HEADER_BORDER = Border(
    left=_MEDIUM_SIDE, right=_MEDIUM_SIDE,
    top=_MEDIUM_SIDE, bottom=_MEDIUM_SIDE,
)


def _apply_hyperlink(cell, url: str, display: str = None) -> None:
    """openpyxl 셀에 하이퍼링크 적용."""
    if not url:
        return
    cell.value = display if display else url
    cell.hyperlink = url
    cell.font = LINK_FONT

# ── 마크다운 텍스트 → Excel 용 plain text 변환 ────────────────────────
import re as _re

_MD_STRIP_RULES = [
    (_re.compile(r"```[\s\S]*?```"),                ""),      # code fence 블록 제거
    (_re.compile(r"\*\*(.+?)\*\*"),                 r"\1"),   # **bold** → bold
    (_re.compile(r"__(.+?)__"),                     r"\1"),   # __bold__ → bold
    (_re.compile(r"\*(.+?)\*"),                     r"\1"),   # *italic* → italic
    (_re.compile(r"`([^`]+)`"),                     r"\1"),   # `code` → code
    (_re.compile(r"^#{1,6}\s+", _re.MULTILINE),    ""),      # # heading → heading
    (_re.compile(r"^[-*_]{3,}\s*$", _re.MULTILINE),""),      # --- hr → 빈줄
    (_re.compile(r"^\s*[-*]\s+", _re.MULTILINE),   "• "),    # - bullet → • bullet
    (_re.compile(r"^\s*\d+\.\s+", _re.MULTILINE),  ""),      # 1. numbered → plain
]


def _strip_md(text) -> str:
    """LLM 응답 텍스트에서 마크다운 서식 + raw JSON 잔류물을 제거해 Excel 가독성 확보."""
    if not isinstance(text, str) or not text:
        return text or ""
    for pat, repl in _MD_STRIP_RULES:
        text = pat.sub(repl, text)
    # raw JSON 잔류물 제거 (파싱 실패 시 disc.summary 에 남는 패턴)
    # { "key": "value", ... } 형태가 셀 전체를 채우면 내부 value 만 추출 시도
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            import json
            obj = json.loads(stripped)
            if isinstance(obj, dict):
                # summary / key_points / implication 키가 있으면 조합
                parts = []
                for k in ("summary", "key_points", "implication"):
                    v = obj.get(k)
                    if isinstance(v, str) and v.strip():
                        parts.append(v.strip())
                    elif isinstance(v, list):
                        parts.append("\n".join(f"• {x}" for x in v if isinstance(x, str)))
                if parts:
                    text = "\n\n".join(parts)
        except (json.JSONDecodeError, Exception):
            pass
    # 연속 빈줄 압축
    text = _re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _fmt_counterparty(item) -> str:
    """customer/supplier 가 dict 면 name+share+amount 문자열로, str 이면 그대로."""
    if isinstance(item, str):
        return item
    if not isinstance(item, dict):
        return str(item)
    name = item.get("name") or ""
    parts = [name]
    sp = item.get("share_pct")
    if isinstance(sp, (int, float)):
        parts.append(f"({sp:.1f}%)")
    amt = item.get("amount")
    if isinstance(amt, (int, float)):
        a = abs(amt)
        if a >= 1e12:
            parts.append(f"{amt/1e12:,.2f}조")
        elif a >= 1e8:
            parts.append(f"{amt/1e8:,.0f}억")
        else:
            parts.append(f"{amt:,.0f}원")
    note = item.get("amount_note") or item.get("description") or ""
    if note:
        parts.append(f"— {note}")
    return " ".join(p for p in parts if p)


# 셀 표시 포맷 — 값은 raw 숫자(원 단위)로 저장하고 표시만 포맷
KRW_FMT = '#,##0'           # 1,234,567,890
# 12.3% (기본) / -12.3% (빨강) / -
PCT_FMT = '0.0"%";[Red]\\-0.0"%";"-"'


def _style_header(ws, row: int, ncols: int) -> None:
    # 테마 컬러 적용 — 모듈 전역 _THEME_HEX 기반으로 셀마다 새 fill 생성
    theme_fill = PatternFill("solid", fgColor=_THEME_HEX)
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row, column=c)
        cell.font = HEADER_FONT
        cell.fill = theme_fill
        cell.alignment = HEADER_ALIGN
        cell.border = HEADER_BORDER
    ws.row_dimensions[row].height = 28


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
    ws.sheet_view.showGridLines = False

    # 타이틀 배너 (테마색 적용)
    ws["A1"] = profile.corp_name
    ws["A1"].font = Font(bold=True, size=20, color="FFFFFF", name="Calibri")
    ws["A1"].fill = PatternFill("solid", fgColor=_THEME_HEX)
    ws["A1"].alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.merge_cells("A1:D1")
    ws.row_dimensions[1].height = 36

    # 서브 배너
    ws["A2"] = (f"{profile.corp_cls_label}  ·  "
                f"{profile.stock_code or profile.corp_code}  ·  "
                f"CEO {profile.ceo_nm or '-'}")
    ws["A2"].font = Font(italic=True, size=11, color="607D8B", name="Calibri")
    ws["A2"].alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.merge_cells("A2:D2")
    ws.row_dimensions[2].height = 20

    # 조회기간 라벨
    ws["A3"] = "조회기간"
    ws["A3"].font = Font(bold=True, color=_THEME_HEX, name="Calibri", size=10)
    ws["A3"].alignment = INDENT_ALIGN
    ws["B3"] = period_label
    ws["B3"].font = BODY_FONT
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


def _ib_style_cell(cell, *, bold=False, fill=None, font=None, align=None,
                    number_format=None, border=None):
    if font is not None:
        cell.font = font
    elif bold:
        cell.font = Font(bold=True, name="Calibri", size=11)
    else:
        cell.font = BODY_FONT
    if fill is not None:
        cell.fill = fill
    if align is not None:
        cell.alignment = align
    if number_format is not None:
        cell.number_format = number_format
    if border is not None:
        cell.border = border


def _write_financials_sheet(wb: Workbook, sheet_name: str,
                             annual_list, latest_q) -> None:
    """IB 스타일 재무 시트.
      • 섹션 밴드 (Performance / Balance Sheet / YoY)
      • 라벨 들여쓰기 (최상위 합계 vs 구성항목)
      • 주요 합계는 **수식**으로 (예: 매출총이익 = 매출액 - 매출원가)
      • 시트 하단에 **검증 체커** (수식 합계 vs 원본 raw 값 일치 여부)
    """
    if not annual_list and not latest_q:
        return

    ws = wb.create_sheet(sheet_name)
    ws.sheet_view.showGridLines = False

    # 과거 → 최신 순으로 정렬 (왼쪽부터 오래된 → 오른쪽이 최신)
    annual_list = sorted(annual_list, key=lambda y: y.year)

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

    # 마진 (% 수식) — 각 분자/분모 key 로부터 row 참조
    MARGIN_FORMULA = {
        "gpm":     ("gross_profit", "revenue"),
        "opm":     ("op_income",    "revenue"),
        "ebitdam": ("ebitda",       "revenue"),
        "npm":     ("net_income",   "revenue"),
    }
    # 부분합 수식: 키 → (부호 포함 항목 리스트). 가능하면 Python 원값 대신
    # 수식으로 저장해 raw 값 편집 시 자동 재계산.
    SUBTOTAL_FORMULA = {
        "gross_profit": [("+", "revenue"), ("-", "cost_of_sales")],
        "op_income":    [("+", "gross_profit"), ("-", "sga")],
        "da":           [("+", "dep"), ("+", "amort")],
        "ebitda":       [("+", "op_income"), ("+", "da")],
    }
    # 시각적 강조 레벨: "header"=섹션 헤더, "total"=최상위 합계, "subtotal"=중간 합계,
    # "item"=세부 항목(들여쓰기), "ratio"=비율.
    LEVEL_MAP = {
        # Performance
        "revenue":        "total",
        "cost_of_sales":  "item",
        "gross_profit":   "subtotal",
        "gpm":            "ratio",
        "sga":            "item",
        "op_income":      "subtotal",
        "opm":            "ratio",
        "dep":            "item",
        "amort":          "item",
        "da":             "subtotal",
        "ebitda":         "subtotal",
        "ebitdam":        "ratio",
        "net_income":     "total",
        "npm":            "ratio",
        # BS
        "total_assets":       "total",
        "total_liabilities":  "subtotal",
        "total_equity":       "subtotal",
    }

    def _append_section_band(title: str) -> None:
        # 섹션 헤더: 옅은 파란색 밴드를 모든 컬럼에 걸쳐 표시
        ws.append([title] + [None] * (n_cols - 1))
        r = ws.max_row
        for c in range(1, n_cols + 1):
            cell = ws.cell(row=r, column=c)
            cell.font = SUBTOTAL_FONT
            cell.fill = SECTION_FILL
            cell.alignment = LEFT_ALIGN if c == 1 else RIGHT_ALIGN
        ws.row_dimensions[r].height = 22
        # 병합
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=n_cols)

    def _write_value_row(key: str, values_getter) -> None:
        """한 행 렌더 — 라벨 + 각 열의 raw 값. values_getter(yearfin, key) → float|None"""
        label = KEY_LABEL.get(key, key)
        level = LEVEL_MAP.get(key, "item")
        ws.append([label])
        r = ws.max_row
        row_of[key] = r

        lbl_cell = ws.cell(row=r, column=1)
        if level == "ratio":
            lbl_cell.font = RATIO_FONT
            lbl_cell.fill = RATIO_FILL
            lbl_cell.alignment = INDENT_ALIGN
        elif level == "total":
            lbl_cell.font = TOTAL_FONT
            lbl_cell.fill = PatternFill("solid", fgColor=_THEME_HEX)
            lbl_cell.alignment = LEFT_ALIGN
        elif level == "subtotal":
            lbl_cell.font = SUBTOTAL_FONT
            lbl_cell.fill = SUBTOTAL_FILL
            lbl_cell.alignment = LEFT_ALIGN
        else:  # item
            lbl_cell.font = BODY_FONT
            lbl_cell.alignment = INDENT_ALIGN

        # 마진 수식
        if key in MARGIN_FORMULA:
            num_key, den_key = MARGIN_FORMULA[key]
            num_r = row_of.get(num_key)
            den_r = row_of.get(den_key)
            for c in range(2, n_cols + 1):
                cell = ws.cell(row=r, column=c)
                if num_r and den_r:
                    col = get_column_letter(c)
                    cell.value = f'=IFERROR({col}{num_r}/{col}{den_r}*100,"")'
                cell.number_format = PCT_FMT
                cell.alignment = RIGHT_ALIGN
                cell.font = RATIO_FONT
                cell.fill = RATIO_FILL
            return

        # 부분합 수식 (분자/분모 row 들이 이미 있으면 수식, 없으면 raw)
        use_formula = False
        parts = SUBTOTAL_FORMULA.get(key)
        if parts and all(row_of.get(k) is not None for _, k in parts):
            use_formula = True

        for c in range(2, n_cols + 1):
            cell = ws.cell(row=r, column=c)
            col = get_column_letter(c)
            if use_formula:
                formula_parts = []
                for sign, k in parts:
                    formula_parts.append(f"{sign}{col}{row_of[k]}")
                cell.value = "=" + "".join(formula_parts).lstrip("+")
            else:
                # raw 값 가져오기 — yearfin 인덱스 매핑
                idx = c - 2
                if idx < len(annual_list):
                    v = annual_list[idx].values.get(key)
                elif latest_q and idx == len(annual_list):
                    v = latest_q.values.get(key)
                else:
                    v = None
                cell.value = v
            cell.number_format = KRW_FMT
            cell.alignment = RIGHT_ALIGN
            # 합계 강조
            if level == "total":
                cell.font = TOTAL_FONT
                cell.fill = PatternFill("solid", fgColor=_THEME_HEX)
            elif level == "subtotal":
                cell.font = SUBTOTAL_FONT
                cell.fill = SUBTOTAL_FILL

    def _emit_block(title: str, keys: List[str]) -> None:
        _append_section_band(title)
        for key in keys:
            _write_value_row(key, None)

    _emit_block("PERFORMANCE (손익)", PERFORMANCE_KEYS)
    ws.append([])
    _emit_block("BALANCE SHEET", BALANCE_KEYS)

    # YoY 영역 — 수식 기반: =(curr/prev - 1)*100
    if len(annual_list) >= 2:
        ws.append([])
        _append_section_band("YoY 성장률 (매출 / 영업이익 / 순이익)")

        for key, label in [("revenue", "매출 YoY"),
                           ("op_income", "영업이익 YoY"),
                           ("net_income", "순이익 YoY")]:
            src_row = row_of.get(key)
            ws.append([label] + [None] * (n_cols - 1))
            yoy_row_idx = ws.max_row
            lbl = ws.cell(row=yoy_row_idx, column=1)
            lbl.font = BODY_FONT
            lbl.alignment = INDENT_ALIGN
            if src_row is None:
                continue
            # 과거 → 최신 순이므로 i 열은 (i-1) 열을 전년으로 참조. i=0 은 비교 불가.
            for i in range(len(annual_list)):
                if i == 0:
                    cell = ws.cell(row=yoy_row_idx, column=2 + i)
                    cell.number_format = PCT_FMT
                    continue
                col_curr = get_column_letter(2 + i)
                col_prev = get_column_letter(2 + i - 1)
                cell = ws.cell(row=yoy_row_idx, column=2 + i)
                cell.value = (
                    f'=IFERROR(({col_curr}{src_row}/{col_prev}{src_row}-1)*100,"")'
                )
                cell.number_format = PCT_FMT
                cell.alignment = RIGHT_ALIGN
                cell.font = BODY_FONT
            # 분기 컬럼 포맷
            if latest_q:
                ws.cell(row=yoy_row_idx, column=n_cols).number_format = PCT_FMT

    # ── 검증(Check) 행: 자산 = 부채 + 자본, 매출총이익 원본 vs 수식 일치 등
    ws.append([])
    _append_section_band("✓ 검증 (Check)")

    def _append_check(label: str, formula_by_col):
        ws.append([label] + [None] * (n_cols - 1))
        r = ws.max_row
        lbl = ws.cell(row=r, column=1)
        lbl.font = Font(bold=True, color="2E7D32", name="Calibri", size=10)
        lbl.alignment = INDENT_ALIGN
        for c in range(2, n_cols + 1):
            cell = ws.cell(row=r, column=c)
            fx = formula_by_col(c)
            if fx:
                cell.value = fx
            cell.alignment = RIGHT_ALIGN
            cell.number_format = '"✓ OK";"✗ DIFF";"-"'
            cell.font = Font(bold=True, name="Calibri", size=10)

    # 자산 = 부채 + 자본 검증
    r_assets = row_of.get("total_assets")
    r_liab = row_of.get("total_liabilities")
    r_eq = row_of.get("total_equity")
    if r_assets and r_liab and r_eq:
        def _bs_check(c):
            col = get_column_letter(c)
            return (f'=IF(ISNUMBER({col}{r_assets})*ISNUMBER({col}{r_liab})'
                    f'*ISNUMBER({col}{r_eq}),'
                    f'IF(ABS({col}{r_assets}-{col}{r_liab}-{col}{r_eq})'
                    f'<=ABS({col}{r_assets})*0.001,1,-1),0)')
        _append_check("자산 = 부채 + 자본", _bs_check)

    # EBITDA = OP + D&A 검증 (둘 다 수식 셀이라 항상 일치해야)
    r_ebitda = row_of.get("ebitda")
    r_op = row_of.get("op_income")
    r_da = row_of.get("da")
    if r_ebitda and r_op and r_da:
        def _ebitda_check(c):
            col = get_column_letter(c)
            return (f'=IF(ISNUMBER({col}{r_ebitda})*ISNUMBER({col}{r_op})'
                    f'*ISNUMBER({col}{r_da}),'
                    f'IF(ABS({col}{r_ebitda}-{col}{r_op}-{col}{r_da})<=1,1,-1),0)')
        _append_check("EBITDA = 영업이익 + D&A", _ebitda_check)

    # 혼합 모드 각주
    ofs_years = [y.year for y in annual_list if y.fs_div == "OFS"]
    cfs_years = [y.year for y in annual_list if y.fs_div == "CFS"]
    if ofs_years and cfs_years:
        ws.append([])
        note = (f"※ {', '.join(str(y) for y in ofs_years)}년은 별도기준 "
                f"(연결감사보고서 미제출). 다른 연도는 연결기준과 비교에 유의.")
        ws.append([note])
        ws.cell(row=ws.max_row, column=1).font = NOTE_FONT

    # 열 너비 고정 (라벨 넓게, 연도 일정)
    ws.column_dimensions["A"].width = 32
    for i in range(1, n_cols):
        ws.column_dimensions[get_column_letter(1 + i)].width = 20
    # 상단 Freeze: 헤더 행 + 라벨 열
    ws.freeze_panes = "B2"


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
    years = sorted(years_seen)   # 과거 → 최신 (좌→우)

    ws = wb.create_sheet("재무제표_상세")
    ws.sheet_view.showGridLines = False

    # 좌측 여백
    C = 2  # 콘텐츠 시작 컬럼
    ws.column_dimensions["A"].width = 3

    # 타이틀
    ws.cell(row=1, column=C, value="재무제표 상세 (DART API 원본)").font = Font(
        bold=True, size=14, color=_THEME_HEX, name="Calibri")
    ws.row_dimensions[1].height = 30

    # 헤더 행 (row 3)
    hdr_row = 3
    hdr_labels = ["계정명", *[f"{y}" for y in years]]
    for ci, h in enumerate(hdr_labels):
        cell = ws.cell(row=hdr_row, column=C + ci, value=h)
        cell.font = HEADER_FONT
        cell.fill = PatternFill("solid", fgColor=_THEME_HEX)
        cell.alignment = HEADER_ALIGN
        cell.border = HEADER_BORDER
    ws.row_dimensions[hdr_row].height = 26

    r = hdr_row + 1
    for sj in SJ_ORDER:
        sj_keys = sorted([k for k in pivot if k[0] == sj], key=lambda k: k[3])
        if not sj_keys:
            continue
        # 섹션 배너
        label = SJ_LABEL.get(sj, sj)
        ws.cell(row=r, column=C, value=label).font = Font(
            bold=True, color="FFFFFF", size=11, name="Calibri")
        for ci in range(len(hdr_labels)):
            cell = ws.cell(row=r, column=C + ci)
            cell.fill = PatternFill("solid", fgColor=_THEME_HEX)
            cell.font = Font(bold=True, color="FFFFFF", name="Calibri")
        ws.row_dimensions[r].height = 22
        r += 1

        for idx, key in enumerate(sj_keys):
            sj_, aid, anm, ordn = key
            # 계정명
            ws.cell(row=r, column=C, value=anm).font = BODY_FONT
            # 연도별 금액
            for yi, y in enumerate(years):
                v = pivot[key].get(y)
                cell = ws.cell(row=r, column=C + 1 + yi, value=v)
                cell.number_format = KRW_FMT
                cell.alignment = RIGHT_ALIGN
                cell.font = BODY_FONT
            # zebra striping
            if idx % 2 == 0:
                for ci in range(len(hdr_labels)):
                    ws.cell(row=r, column=C + ci).fill = PatternFill(
                        "solid", fgColor="F7F9FC")
            # border
            for ci in range(len(hdr_labels)):
                ws.cell(row=r, column=C + ci).border = THIN_BORDER
            r += 1
        r += 1  # 섹션 간 빈 행

    # 열 너비
    ws.column_dimensions[get_column_letter(C)].width = 28  # 계정명
    for yi in range(len(years)):
        ws.column_dimensions[get_column_letter(C + 1 + yi)].width = 20
    ws.freeze_panes = ws.cell(row=hdr_row + 1, column=C + 1).coordinate


def _write_raw_fs(wb: Workbook, fin: FinancialsBundle) -> None:
    """비상장 감사보고서에서 파싱한 원본 재무제표 표를 빠짐없이 덤프.

    `fin.raw_fs_tables` 에 `audit_fs_parser.RawFsTable` 객체가 있을 때만 생성.
    하나의 시트 "재무_원본" 안에 섹션 헤더(재무상태표/손익계산서/...) + 원문
    표를 그대로 기록. 금액은 `unit_multiplier` 곱해 원 단위로 정규화.
    """
    tables = getattr(fin, "raw_fs_tables", None) or []
    if not tables:
        return

    ws = wb.create_sheet("재무_원본")
    ws.sheet_view.showGridLines = False

    # 상단 타이틀
    ws["A1"] = "감사보고서 원본 재무제표 (HTML 표 파싱)"
    ws["A1"].font = Font(bold=True, size=13, color=_THEME_HEX, name="Calibri")
    ws.row_dimensions[1].height = 22
    ws["A2"] = "단위: 원 (표별 단위를 자동 환산)"
    ws["A2"].font = NOTE_FONT

    row_cursor = 4

    # 통계 순서 (BS → IS/CIS → CF → SCE → UNKNOWN)
    ORDER = ["BS", "IS", "CIS", "CF", "SCE", "UNKNOWN"]

    def _write_table_block(t) -> int:
        nonlocal row_cursor
        # 섹션 제목
        title_cell = ws.cell(row=row_cursor, column=1,
                             value=f"[{t.statement_label or t.statement}] "
                                   f"({t.fs_div}) {t.report_nm or ''}")
        title_cell.font = Font(bold=True, color="FFFFFF", size=12, name="Calibri")
        title_cell.fill = PatternFill("solid", fgColor=_THEME_HEX)
        title_cell.alignment = Alignment(horizontal="left", vertical="center", indent=1)
        ws.row_dimensions[row_cursor].height = 22
        # 넉넉히 10개 열 머지
        n_cols = max(len(t.headers) or 0, max((len(r) for r in t.rows), default=0), 5)
        ws.merge_cells(start_row=row_cursor, start_column=1,
                       end_row=row_cursor, end_column=n_cols)
        row_cursor += 1

        # 단위 · 원문 제목
        meta_bits = []
        if t.unit and t.unit != "원":
            meta_bits.append(f"원문 단위: {t.unit} (원 기준 ×{int(t.unit_multiplier):,})")
        if t.title:
            # 제목은 노이즈 가능 — 200자 cap
            meta_bits.append(f"표 제목 단서: {t.title[:120]}")
        if meta_bits:
            m = ws.cell(row=row_cursor, column=1, value=" · ".join(meta_bits))
            m.font = NOTE_FONT
            m.alignment = Alignment(horizontal="left")
            ws.merge_cells(start_row=row_cursor, start_column=1,
                           end_row=row_cursor, end_column=n_cols)
            row_cursor += 1

        # 헤더
        if t.headers:
            for c_idx, header in enumerate(t.headers[:n_cols], 1):
                cell = ws.cell(row=row_cursor, column=c_idx, value=header)
            _style_header(ws, row_cursor, min(len(t.headers), n_cols))
            row_cursor += 1

        # 데이터 행 — 금액으로 파싱 가능한 셀은 숫자로(원 단위), 아니면 문자열
        from .audit_fs_parser import _to_amount  # 지연 import (순환 방지)
        for row in t.rows:
            for c_idx, cell_val in enumerate(row[:n_cols], 1):
                amt = _to_amount(cell_val)
                cell = ws.cell(row=row_cursor, column=c_idx)
                if amt is not None:
                    cell.value = amt * t.unit_multiplier
                    cell.number_format = KRW_FMT
                    cell.alignment = RIGHT_ALIGN
                else:
                    cell.value = cell_val
                    cell.alignment = LEFT_ALIGN
                cell.font = BODY_FONT
                cell.border = THIN_BORDER
            row_cursor += 1

        row_cursor += 2   # 블록 간 공백
        return row_cursor

    # 정렬: 재무상태표 먼저, 그 다음 손익/포괄손익/현금흐름/자본변동 순
    ordered = sorted(
        tables,
        key=lambda t: (
            ORDER.index(t.statement) if t.statement in ORDER else 99,
            0 if t.fs_div == "CFS" else 1,
        ),
    )
    for t in ordered:
        _write_table_block(t)

    # 열 너비
    ws.column_dimensions["A"].width = 34
    for i in range(2, 12):
        ws.column_dimensions[get_column_letter(i)].width = 18
    ws.freeze_panes = "B4"


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


def _write_table(wb: Workbook, sheet_name: str, rows: List[Dict[str, Any]],
                 header_map: Optional[Dict[str, str]] = None) -> None:
    """IB 스타일 표: 헤더 딥블루 + zebra + 얇은 보더 + freeze 상단."""
    if not rows:
        return  # 빈 데이터면 시트 자체 미생성
    ws = wb.create_sheet(sheet_name)
    ws.sheet_view.showGridLines = False
    keys = list(rows[0].keys())
    headers = [header_map.get(k, k) if header_map else k for k in keys]
    ws.append(headers)
    _style_header(ws, 1, len(headers))
    # body
    for r in rows:
        ws.append([r.get(k, "") for k in keys])
    # zebra + 얇은 보더 + 우측 정렬 숫자
    for ridx in range(2, ws.max_row + 1):
        fill = PatternFill("solid", fgColor="F7F9FC") if (ridx % 2 == 0) else None
        for cidx in range(1, len(headers) + 1):
            cell = ws.cell(row=ridx, column=cidx)
            cell.font = BODY_FONT
            cell.border = THIN_BORDER
            if fill:
                cell.fill = fill
            # 숫자처럼 보이면 우측 정렬 + 콤마
            v = cell.value
            if isinstance(v, (int, float)):
                cell.alignment = RIGHT_ALIGN
                cell.number_format = KRW_FMT
            else:
                # 숫자 문자열도 한번 시도
                if isinstance(v, str) and v.replace(",", "").replace(".", "").replace("-", "").isdigit():
                    try:
                        cell.value = float(v.replace(",", ""))
                        cell.number_format = KRW_FMT
                        cell.alignment = RIGHT_ALIGN
                    except ValueError:
                        cell.alignment = LEFT_ALIGN
                else:
                    cell.alignment = LEFT_ALIGN
    ws.freeze_panes = "A2"
    _autofit(ws, max_width=45)


def _write_shareholders(wb: Workbook, sh: ShareholderBundle) -> None:
    # 한국어 헤더 매핑 — raw DART 필드명 대신 이해하기 쉬운 라벨로
    major_hdr = {
        "nm":"성명/법인명", "relate":"관계", "stock_knd":"주식종류",
        "bsis_posesn_stock_co":"기초 보유수",
        "bsis_posesn_stock_qota_rt":"기초 지분율(%)",
        "trmend_posesn_stock_co":"기말 보유수",
        "trmend_posesn_stock_qota_rt":"기말 지분율(%)",
        "rm":"비고", "stlm_dt":"결산기준일",
    }
    chg_hdr = {
        "change_on":"변동일", "mxmm_shrholdr_nm":"최대주주명",
        "posesn_stock_co":"보유수", "qota_rt":"지분율(%)",
        "change_cause":"변동사유", "rm":"비고", "stlm_dt":"결산기준일",
    }
    major_stock_hdr = {
        "rcept_no":"접수번호", "rcept_dt":"접수일",
        "report_tp":"보고구분", "repror":"대표보고자",
        "stkqy":"보유주식수", "stkqy_irds":"증감",
        "stkrt":"보유비율(%)", "stkrt_irds":"비율 증감(%)",
        "report_resn":"사유",
    }
    exec_stock_hdr = {
        "rcept_no":"접수번호", "rcept_dt":"접수일", "repror":"보고자",
        "isu_exctv_rgist_at":"등기여부", "isu_exctv_ofcps":"직위",
        "isu_main_shrholdr":"10% 이상 주주",
        "sp_stock_lmp_cnt":"소유수", "sp_stock_lmp_irds_cnt":"증감",
        "sp_stock_lmp_rate":"지분율(%)", "sp_stock_lmp_irds_rate":"증감율(%)",
    }
    minority_hdr = {
        "se":"구분", "shrholdr_co":"주주수", "shrholdr_tot_co":"전체주주수",
        "shrholdr_rate":"주주비율(%)",
        "hold_stock_co":"보유주식수", "stock_tot_co":"총발행주식수",
        "hold_stock_rate":"보유주식비율(%)", "stlm_dt":"결산기준일",
    }
    exec_hdr = {
        "nm":"성명", "sexdstn":"성별", "birth_ym":"생년월",
        "ofcps":"직위", "rgist_exctv_at":"등기여부", "fte_at":"상근여부",
        "chrg_job":"담당업무", "main_career":"주요경력",
        "mxmm_shrholdr_relate":"최대주주 관계",
        "hffc_pd":"재직기간", "tenure_end_on":"임기만료일",
    }
    div_hdr = {
        "se":"구분", "stock_knd":"주식종류",
        "thstrm":"당기", "frmtrm":"전기", "lwfr":"전전기",
        "stlm_dt":"결산기준일",
    }
    otr_hdr = {
        "inv_prm":"법인명", "frst_acqs_de":"최초취득일",
        "invstmnt_purps":"출자목적", "frst_acqs_amount":"최초취득금액",
        "bsis_blce_qy":"기초 수량", "bsis_blce_qota_rt":"기초 지분(%)",
        "bsis_blce_acntbk_amount":"기초 장부가",
        "trmend_blce_qy":"기말 수량", "trmend_blce_qota_rt":"기말 지분(%)",
        "trmend_blce_acntbk_amount":"기말 장부가",
        "recent_bsns_year_fnnr_sttus_tot_assets":"자산총계",
        "recent_bsns_year_fnnr_sttus_thstrm_ntpf":"당기순이익",
        "stlm_dt":"결산기준일",
    }
    audit_hdr = {
        "bsns_year":"사업연도", "adtor":"감사인",
        "adt_opinion":"감사의견",
        "adt_reprt_spcmnt_matter":"특기사항",
        "emphs_matter":"강조사항", "core_adt_matter":"핵심감사사항",
        "stlm_dt":"결산기준일",
    }
    _write_table(wb, "주주_최대",       sh.major,            major_hdr)
    _write_table(wb, "주주_변동",       sh.major_change,     chg_hdr)
    _write_table(wb, "주주_대량보유",   sh.major_stock,      major_stock_hdr)
    _write_table(wb, "주주_임원소유",   sh.executive_stock,  exec_stock_hdr)
    _write_table(wb, "주주_소액",       sh.minority,         minority_hdr)
    _write_table(wb, "임원",            sh.executives,       exec_hdr)
    _write_table(wb, "배당",            sh.dividends,        div_hdr)
    _write_table(wb, "타법인출자",      sh.other_corp_invest, otr_hdr)
    _write_table(wb, "감사의견",        sh.audit_opinion,    audit_hdr)


def _write_disclosure_list(wb: Workbook, discs: List[Disclosure]) -> None:
    if not discs:
        return
    ws = wb.create_sheet("공시리스트")
    headers = ["접수일", "유형", "세부유형", "제출인", "제목", "요약",
               "Implication", "상태", "DART 링크"]
    ws.append(headers)
    _style_header(ws, 1, len(headers))
    # 제목 컬럼(5) 을 하이퍼링크로 만들고, DART 링크 컬럼(9) 에도 "열기" 링크
    for d in discs:
        ws.append([
            d.rcept_dt, d.ty_label, d.pblntf_detail_ty, d.flr_nm,
            d.report_nm, _strip_md(d.summary),
            _strip_md(d.implication), d.llm_status, "",
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
    ok = [d for d in discs if d.llm_status == "ok"]
    if not ok:
        return
    ws = wb.create_sheet("주요공시상세")
    ws.append(["접수일", "제목", "유형", "요약", "핵심 포인트", "Implication", "링크"])
    _style_header(ws, 1, 7)
    for d in discs:
        if d.llm_status != "ok":
            continue
        ws.append([
            d.rcept_dt, "", d.ty_label,
            _strip_md(d.summary),
            "\n".join(f"• {_strip_md(kp)}" for kp in d.key_points),
            _strip_md(d.implication),
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
    ws.sheet_view.showGridLines = False
    # 좌측 여백 컬럼 (IB 스타일)
    ws.column_dimensions["A"].width = 3

    # 타이틀 배너
    ws.cell(row=1, column=2, value="회사 개요 (Business Profile)")
    ws.cell(row=1, column=2).font = Font(bold=True, size=14, color=_THEME_HEX, name="Calibri")
    ws.row_dimensions[1].height = 30
    src = biz.get("_source_report_nm") or ""
    src_rcept = biz.get("_source_rcept_no") or ""
    if src:
        ws.cell(row=2, column=2, value="기준 보고서:")
        ws.cell(row=2, column=2).font = NOTE_FONT
        if src_rcept:
            url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={src_rcept}"
            _apply_hyperlink(ws.cell(row=2, column=3), url, src)
        else:
            ws.cell(row=2, column=3, value=src).font = NOTE_FONT

    # c=2 기준 (좌측 여백 확보)
    C = 2  # 시작 컬럼
    r = 4
    summary = (biz.get("business_summary") or "").strip()
    if summary:
        h = ws.cell(row=r, column=C, value="요약")
        h.font = Font(bold=True, color="305496")
        h.fill = SECTION_FILL
        for ci in range(C, C + 4):
            ws.cell(row=r, column=ci).fill = SECTION_FILL
        r += 1
        c = ws.cell(row=r, column=C, value=_strip_md(summary))
        c.alignment = WRAP
        c.font = BODY_FONT
        ws.merge_cells(start_row=r, start_column=C, end_row=r, end_column=C + 3)
        ws.row_dimensions[r].height = max(40, min(200, 18 * (len(summary) // 50 + 1)))
        r += 2

    products = biz.get("products") or []
    if products:
        h = ws.cell(row=r, column=C, value="주요 제품/서비스")
        h.font = Font(bold=True, color="305496")
        h.fill = SECTION_FILL
        for ci in range(C, C + 4):
            ws.cell(row=r, column=ci).fill = SECTION_FILL
        r += 1
        for p in products[:30]:
            ws.cell(row=r, column=C, value=f"• {p}").font = BODY_FONT
            r += 1
        r += 1

    segments = biz.get("segments") or []
    if segments:
        h = ws.cell(row=r, column=C, value="사업부별 매출·영업이익률")
        h.font = Font(bold=True, color="305496")
        h.fill = SECTION_FILL
        for ci in range(C, C + 4):
            ws.cell(row=r, column=ci).fill = SECTION_FILL
        r += 1
        seg_headers = ["사업부/제품군", "매출", "매출 단위/연도", "영업이익률(%)", "설명"]
        for i, sh in enumerate(seg_headers):
            cell = ws.cell(row=r, column=C + i, value=sh)
            cell.font = HEADER_FONT
            cell.fill = PatternFill("solid", fgColor=_THEME_HEX)
            cell.alignment = HEADER_ALIGN
            cell.border = HEADER_BORDER
        r += 1
        for s in segments[:30]:
            if not isinstance(s, dict):
                continue
            ws.cell(row=r, column=C, value=s.get("name", "")).font = BODY_FONT
            rev = s.get("revenue")
            ws.cell(row=r, column=C + 1, value=rev if isinstance(rev, (int, float)) else None)
            ws.cell(row=r, column=C + 1).number_format = KRW_FMT
            ws.cell(row=r, column=C + 2, value=s.get("revenue_note", "")).font = BODY_FONT
            opm = s.get("op_margin_pct")
            ws.cell(row=r, column=C + 3, value=opm if isinstance(opm, (int, float)) else None)
            ws.cell(row=r, column=C + 3).number_format = PCT_FMT
            ws.cell(row=r, column=C + 4, value=_strip_md(s.get("description", ""))).alignment = WRAP
            for ci in range(C, C + 5):
                ws.cell(row=r, column=ci).border = THIN_BORDER
            r += 1
        r += 1

    customers = biz.get("major_customers") or []
    suppliers = biz.get("major_suppliers") or []
    if customers or suppliers:
        h = ws.cell(row=r, column=C, value="주요 매출처")
        h.font = Font(bold=True, color="305496")
        h.fill = SECTION_FILL
        ws.cell(row=r, column=C + 2, value="주요 매입처").font = Font(bold=True, color="305496")
        ws.cell(row=r, column=C + 2).fill = SECTION_FILL
        for ci in range(C, C + 4):
            ws.cell(row=r, column=ci).fill = SECTION_FILL
        r += 1
        max_n = max(len(customers), len(suppliers))
        for i in range(min(20, max_n)):
            if i < len(customers):
                ws.cell(row=r, column=C, value=f"• {_fmt_counterparty(customers[i])}").font = BODY_FONT
            if i < len(suppliers):
                ws.cell(row=r, column=C + 2, value=f"• {_fmt_counterparty(suppliers[i])}").font = BODY_FONT
            r += 1
        r += 1

    insights = biz.get("key_insights") or []
    if insights:
        h = ws.cell(row=r, column=C, value="투자 포인트")
        h.font = Font(bold=True, color="305496")
        h.fill = SECTION_FILL
        for ci in range(C, C + 4):
            ws.cell(row=r, column=ci).fill = SECTION_FILL
        r += 1
        for x in insights:
            cell = ws.cell(row=r, column=C, value=f"▶ {_strip_md(x) if isinstance(x, str) else x}")
            cell.alignment = WRAP
            cell.font = BODY_FONT
            cell.fill = PatternFill("solid", fgColor="FFF8EF")
            ws.merge_cells(start_row=r, start_column=C, end_row=r, end_column=C + 3)
            r += 1

    # 열 너비 (A=여백, B~F=콘텐츠)
    ws.column_dimensions["A"].width = 3
    ws.column_dimensions["B"].width = 40
    ws.column_dimensions["C"].width = 22
    ws.column_dimensions["D"].width = 28
    ws.column_dimensions["E"].width = 16
    ws.column_dimensions["F"].width = 36
    ws.column_dimensions["E"].width = 50


def _write_footnotes(wb: Workbook, footnotes: Optional[dict]) -> None:
    """주석(Footnotes) 시트 — 감사/사업보고서 주석에서 LLM 추출한 주요 항목."""
    if not footnotes:
        return
    has_any = any(
        footnotes.get(k) for k in (
            "related_party_transactions", "contingent_liabilities",
            "major_contracts", "loans_and_borrowings",
            "subsequent_events", "other_key_footnotes",
        )
    )
    if not has_any:
        return

    ws = wb.create_sheet("주석", 3)  # Profile·Exec·Biz 뒤 3번째에
    ws.sheet_view.showGridLines = False

    # 제목
    ws["A1"] = "주요 주석 (Footnotes)"
    ws["A1"].font = Font(bold=True, size=16, color="1F3864", name="Calibri")
    ws.merge_cells("A1:E1")
    src = footnotes.get("_source_report_nm") or ""
    rcept = footnotes.get("_source_rcept_no") or ""
    if src:
        ws["A2"] = "기준 보고서:"
        ws["A2"].font = NOTE_FONT
        if rcept:
            url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept}"
            _apply_hyperlink(ws.cell(row=2, column=2), url, src)
        else:
            ws["B2"] = src
            ws["B2"].font = NOTE_FONT
        ws.merge_cells("B2:E2")

    r = 4

    def _section(title: str, items: list, columns: List[tuple[str, str, str]]):
        """섹션 제목 + 컬럼 헤더 + 데이터 행. columns = [(key, header, fmt)]."""
        nonlocal r
        if not items:
            return
        # 섹션 헤더 배너
        ws.cell(row=r, column=1, value=title)
        for c in range(1, len(columns) + 2):
            cell = ws.cell(row=r, column=c)
            cell.font = SUBTOTAL_FONT
            cell.fill = SECTION_FILL
        ws.row_dimensions[r].height = 22
        r += 1
        # 컬럼 헤더
        for ci, (_, hdr, _) in enumerate(columns, start=1):
            cell = ws.cell(row=r, column=ci, value=hdr)
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.alignment = HEADER_ALIGN
            cell.border = HEADER_BORDER
        ws.row_dimensions[r].height = 24
        r += 1
        # 데이터
        for i, it in enumerate(items):
            for ci, (k, _, fmt) in enumerate(columns, start=1):
                v = it.get(k) if isinstance(it, dict) else None
                if isinstance(v, str):
                    v = _strip_md(v)
                cell = ws.cell(row=r, column=ci, value=v)
                cell.font = BODY_FONT
                cell.border = THIN_BORDER
                if fmt == "krw" and isinstance(v, (int, float)):
                    cell.number_format = KRW_FMT
                    cell.alignment = RIGHT_ALIGN
                elif fmt == "wrap":
                    cell.alignment = Alignment(wrap_text=True, vertical="top")
                else:
                    cell.alignment = LEFT_ALIGN
                # zebra
                if i % 2 == 1:
                    cell.fill = PatternFill("solid", fgColor="F7F9FC")
            # 주석/설명 열은 행 높이 유연
            ws.row_dimensions[r].height = 34
            r += 1
        r += 1  # 섹션 간 여백

    _section("특수관계자 거래",
             footnotes.get("related_party_transactions") or [],
             [("counterparty", "특수관계자", "text"),
              ("relation", "관계", "text"),
              ("nature", "거래 성격", "text"),
              ("amount", "금액", "krw"),
              ("note", "비고", "wrap")])
    _section("우발부채 / 주요 소송",
             footnotes.get("contingent_liabilities") or [],
             [("title", "제목", "text"),
              ("amount", "금액", "krw"),
              ("note", "내용/진행상황", "wrap")])
    _section("중요한 계약",
             footnotes.get("major_contracts") or [],
             [("title", "계약명", "text"),
              ("counterparty", "상대방", "text"),
              ("value", "계약금액", "krw"),
              ("term", "기간", "text"),
              ("note", "비고", "wrap")])
    _section("차입금 / 사채",
             footnotes.get("loans_and_borrowings") or [],
             [("lender", "차입처", "text"),
              ("balance", "잔액", "krw"),
              ("rate", "이자율", "text"),
              ("maturity", "만기", "text"),
              ("collateral", "담보", "text")])
    _section("보고기간 후 사건",
             footnotes.get("subsequent_events") or [],
             [("title", "제목", "text"),
              ("note", "내용", "wrap")])
    _section("기타 주요 주석",
             footnotes.get("other_key_footnotes") or [],
             [("title", "제목", "text"),
              ("note", "내용", "wrap")])

    # 컬럼 폭
    widths = [22, 18, 18, 18, 60]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A4"


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
    footnotes: Optional[dict] = None,
) -> None:
    # 업종별 테마 적용 (헤더/총계 컬러만 동적)
    _apply_theme(pick_theme(profile))

    wb = Workbook()
    _write_profile(wb.active, profile, period_label)

    if exec_summary:
        es = wb.create_sheet("Executive Summary", 1)
        es.sheet_view.showGridLines = False
        es.column_dimensions["A"].width = 3  # 좌측 여백
        es.cell(row=1, column=2, value="Executive Summary").font = Font(
            bold=True, size=14, color=_THEME_HEX, name="Calibri")
        es.row_dimensions[1].height = 30
        es.cell(row=3, column=2, value=_strip_md(exec_summary))
        es.cell(row=3, column=2).alignment = WRAP
        es.cell(row=3, column=2).font = BODY_FONT
        es.column_dimensions["B"].width = 100

    _write_business(wb, business)
    _write_footnotes(wb, footnotes)
    _write_financials(wb, fin)
    _write_fin_detail(wb, fin)
    _write_raw_fs(wb, fin)
    _write_indicators(wb, fin)
    _write_shareholders(wb, shareholders)
    _write_disclosure_list(wb, disclosures)
    _write_important_details(wb, disclosures)

    wb.save(path)
