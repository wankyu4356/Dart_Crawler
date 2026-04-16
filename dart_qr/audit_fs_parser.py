# -*- coding: utf-8 -*-
"""M-unlisted-B — 감사보고서 HTML 재무제표 표 파서.

비상장 감사보고서는 document.xml ZIP 안에 HTML 본문으로 재무제표가 들어있다.
표준 API(`fnlttSinglAcntAll`)가 동작하지 않는 비상장 케이스에서 감사보고서
HTML 의 `<table>` 요소를 직접 파싱해 **재무상태표 / 손익계산서 /
포괄손익계산서 / 현금흐름표 / 자본변동표** 원본 표를 빠짐없이 복원한다.

- LLM 비의존 — API key 없어도 동작.
- 표 구조·계정명·다년도 금액 열 원형 보존.
- 결과 `RawFsTable` 목록을 Excel "재무_원본" 시트에 그대로 덤프.
- 주요 계정명은 `KEY_MAP` 으로 `YearFin.values` 필드에 매핑 (자동 재무 요약).
"""
from __future__ import annotations
import html
import io
import re
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import dart_api as api
from .financials import YearFin, _empty_values


# ── 데이터 모델 ─────────────────────────────────────────────────────────
@dataclass
class RawFsTable:
    statement: str                  # "BS"/"IS"/"CIS"/"CF"/"SCE"/"UNKNOWN"
    statement_label: str            # 한국어 라벨 ("재무상태표" 등)
    title: str = ""                 # 표 제목 (원문)
    fs_div: str = "OFS"             # CFS/OFS (제목으로 추정)
    unit: str = "원"                # 원/천원/백만원 — 금액 스케일 정규화에 사용
    unit_multiplier: float = 1.0    # 원 기준 배수
    headers: List[str] = field(default_factory=list)      # 열 헤더 (연도·회기)
    year_headers: List[Optional[int]] = field(default_factory=list)  # 헤더에서 뽑은 연도
    rows: List[List[str]] = field(default_factory=list)   # 2-D 문자열 표 (금액 포함)
    rcept_no: str = ""
    report_nm: str = ""


# ── HTML 표 파서 (stdlib) ────────────────────────────────────────────────
class _TableExtractor(HTMLParser):
    """HTML 문서에서 `<table>` 안의 행/셀을 2-D 문자열 배열로 추출.

    중첩 테이블은 바깥 테이블에 한 셀로 통합 (텍스트만 이어 붙임).
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: List[List[List[str]]] = []   # [table][row][col]
        self._stack: List[List[List[str]]] = []   # nested tables
        self._row: Optional[List[str]] = None
        self._cell: Optional[List[str]] = None
        # 표 앞쪽 텍스트(제목·단위) 를 잡기 위해 table 직전 텍스트도 보관
        self.pre_texts: List[str] = []   # table 별로 1개씩
        self._recent_text: List[str] = []

    # -- table --
    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        t = tag.lower()
        if t == "table":
            self._stack.append([])
            # 직전 텍스트 스냅샷을 현재 테이블 인덱스로 기록
            snapshot = " ".join(self._recent_text)[-400:]
            self.pre_texts.append(snapshot)
            self._recent_text = []
        elif t == "tr" and self._stack:
            self._row = []
        elif t in ("td", "th") and self._row is not None:
            self._cell = []
        elif t == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t == "table" and self._stack:
            tbl = self._stack.pop()
            if self._stack:
                # nested: flatten into current outer cell
                if self._cell is not None:
                    flat = " ".join(
                        " ".join(c.strip() for c in row if c).strip()
                        for row in tbl
                    )
                    if flat:
                        self._cell.append(flat)
                # nested 테이블에 해당하는 pre_text 는 버림
                if self.pre_texts:
                    self.pre_texts.pop()
            else:
                self.tables.append(tbl)
        elif t == "tr" and self._row is not None:
            if self._stack and self._row:
                self._stack[-1].append(self._row)
            self._row = None
        elif t in ("td", "th") and self._cell is not None:
            text = _normalize_ws("".join(self._cell))
            if self._row is not None:
                self._row.append(text)
            self._cell = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)
        elif not self._stack:
            # 테이블 바깥 텍스트 — 단위/제목 단서
            t = data.strip()
            if t:
                self._recent_text.append(t)
                # 최근 8토큰만 유지
                self._recent_text = self._recent_text[-8:]


_WS_RE = re.compile(r"\s+")


def _normalize_ws(s: str) -> str:
    return _WS_RE.sub(" ", (s or "").replace("\xa0", " ")).strip()


# ── 표 종류 판정 ────────────────────────────────────────────────────────
STATEMENT_HINTS: List[Tuple[str, str, List[str]]] = [
    # (code, label, keywords)
    ("BS",  "재무상태표",     ["재무상태표", "대차대조표", "statement of financial position", "balance sheet"]),
    ("IS",  "손익계산서",     ["손익계산서", "income statement", "statement of profit or loss"]),
    ("CIS", "포괄손익계산서", ["포괄손익계산서", "comprehensive income"]),
    ("CF",  "현금흐름표",     ["현금흐름표", "현 금 흐 름 표", "cash flow", "cashflow"]),
    ("SCE", "자본변동표",     ["자본변동표", "changes in equity"]),
]


def _classify_table(pre_text: str, first_rows_text: str) -> Tuple[str, str]:
    """표 앞 텍스트 + 첫 몇 행 텍스트로 재무제표 종류 판정.

    포괄손익계산서가 손익계산서보다 더 구체적이므로 CIS 먼저 검사.
    """
    blob_lower = (pre_text + " " + first_rows_text).lower()
    # CIS 는 IS 보다 먼저
    for code, label, kws in STATEMENT_HINTS:
        for kw in kws:
            if kw.lower() in blob_lower:
                return code, label
    return "UNKNOWN", ""


_UNIT_PATTERNS = [
    (re.compile(r"단위\s*[:：]?\s*원"), ("원", 1.0)),
    (re.compile(r"\(단위\s*[:：]?\s*원"), ("원", 1.0)),
    (re.compile(r"단위\s*[:：]?\s*천\s*원"), ("천원", 1_000.0)),
    (re.compile(r"단위\s*[:：]?\s*백만\s*원"), ("백만원", 1_000_000.0)),
    (re.compile(r"단위\s*[:：]?\s*억\s*원"), ("억원", 100_000_000.0)),
    (re.compile(r"\b천\s*원\b"), ("천원", 1_000.0)),
    (re.compile(r"\b백만\s*원\b"), ("백만원", 1_000_000.0)),
]


def _extract_unit(pre_text: str) -> Tuple[str, float]:
    for pat, (name, mul) in _UNIT_PATTERNS:
        if pat.search(pre_text):
            return name, mul
    return "원", 1.0


_YEAR_RE = re.compile(r"(19|20)\d{2}")


def _extract_year(s: str) -> Optional[int]:
    if not s:
        return None
    m = _YEAR_RE.search(s)
    if not m:
        return None
    try:
        y = int(m.group(0))
        if 1990 <= y <= 2100:
            return y
    except ValueError:
        return None
    return None


def _to_amount(s: str) -> Optional[float]:
    if not s:
        return None
    t = s.strip().replace(",", "").replace(" ", "")
    if not t or t in ("-", "–", "—", "─"):
        return None
    neg = False
    # 한국 재무제표: (12,345) 형태 음수
    if t.startswith("(") and t.endswith(")"):
        neg = True
        t = t[1:-1]
    if t.startswith("△") or t.startswith("▲"):
        neg = True
        t = t[1:]
    if t.startswith("-"):
        neg = True
        t = t[1:]
    try:
        v = float(t)
        return -v if neg else v
    except ValueError:
        return None


def _detect_fs_div(title: str, pre_text: str) -> str:
    blob = title + " " + pre_text
    if "연결" in blob or "consolidated" in blob.lower():
        return "CFS"
    return "OFS"


# ── 메인 파싱 ───────────────────────────────────────────────────────────
def parse_audit_fs_from_zip(
    zip_bytes: bytes,
    rcept_no: str = "",
    report_nm: str = "",
) -> List[RawFsTable]:
    """감사보고서 ZIP 바이트 → RawFsTable 목록."""
    if not zip_bytes:
        return []
    out: List[RawFsTable] = []
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for name in zf.namelist():
                if not name.lower().endswith((".html", ".htm", ".xml")):
                    continue
                try:
                    raw = zf.read(name).decode("utf-8", errors="ignore")
                except Exception:
                    continue
                out.extend(_parse_html_tables(raw, rcept_no, report_nm))
    except zipfile.BadZipFile:
        return []
    return out


def _parse_html_tables(
    html_text: str, rcept_no: str, report_nm: str,
) -> List[RawFsTable]:
    # script/style 제거 (파서 충돌 방지)
    clean = re.sub(r"<script[\s\S]*?</script>", " ", html_text, flags=re.I)
    clean = re.sub(r"<style[\s\S]*?</style>", " ", clean, flags=re.I)
    # DART XBRL/XHTML 에서 오는 특수 요소 — namespace prefix 제거
    # (예: <tbl:table>, <a:tr>, </w:tbl>) → html.parser 가 <table>/<tr> 로 인식하도록
    clean = re.sub(r"<(/?)[a-zA-Z]+:([a-zA-Z]+)", r"<\1\2", clean)
    # 개체 참조 복원은 HTMLParser 가 처리 (convert_charrefs=True)
    extractor = _TableExtractor()
    try:
        extractor.feed(clean)
    except Exception:  # noqa: BLE001
        pass  # 일부 파싱 성공분은 tables 에 남음
    raw_tables = extractor.tables

    # ── fallback: HTMLParser 가 못 찾으면 regex 로 <table>...</table> 블록
    #    직접 추출. HTMLParser 가 뭐라도 찾았으면 그대로 사용 (중복 방지).
    if not raw_tables:
        regex_tables = _regex_extract_tables(clean)
        for pre, block_rows in regex_tables:
            raw_tables.append(block_rows)
            extractor.pre_texts.append(pre)

    # 디버그 카운터 — 왜 필터에서 떨어지는지 확인용
    _dbg = {"too_small": 0, "too_few_amounts": 0,
            "unknown_no_kw": 0, "accepted": 0}

    results: List[RawFsTable] = []
    for i, tbl in enumerate(raw_tables):
        if not tbl or len(tbl) < 2:
            _dbg["too_small"] += 1
            continue
        # 금액 셀이 충분해야 재무제표 후보
        num_cells = sum(
            1 for row in tbl for cell in row if _to_amount(cell) is not None
        )
        if num_cells < 2:   # 완화: 3 → 2 (소규모 표도 수용)
            _dbg["too_few_amounts"] += 1
            continue

        pre = extractor.pre_texts[i] if i < len(extractor.pre_texts) else ""
        first_blob = " ".join(
            cell for row in tbl[:3] for cell in row
        )[:600]
        code, label = _classify_table(pre, first_blob)
        if code == "UNKNOWN":
            # 표 내용 키워드로 재무제표 재판정 (목록 대폭 확장)
            body_blob = " ".join(cell for row in tbl for cell in row)[:2000]
            n = body_blob.replace(" ", "")
            bs_kw = (
                "자산총계", "부채총계", "자본총계",
                "유동자산", "비유동자산", "유동부채", "비유동부채",
                "자본금", "자본잉여금", "이익잉여금",
            )
            is_kw = (
                "매출액", "매출총이익", "영업이익", "영업손실",
                "영업수익", "당기순이익", "당기순손실",
                "판매비와관리비", "법인세비용",
            )
            cf_kw = (
                "영업활동현금흐름", "투자활동현금흐름", "재무활동현금흐름",
                "영업활동으로인한현금흐름", "투자활동으로인한현금흐름",
                "재무활동으로인한현금흐름", "현금및현금성자산의증가",
                "기초의현금", "기말의현금",
            )
            cis_kw = ("총포괄손익", "기타포괄손익")
            if any(k in n for k in bs_kw):
                code, label = "BS", "재무상태표"
            elif any(k in n for k in is_kw):
                code, label = "IS", "손익계산서"
            elif any(k in n for k in cis_kw):
                code, label = "CIS", "포괄손익계산서"
            elif any(k in n for k in cf_kw):
                code, label = "CF", "현금흐름표"
            else:
                _dbg["unknown_no_kw"] += 1
                continue
        _dbg["accepted"] += 1

        # 헤더 행 선택: 연도가 가장 많이 등장하는 행 (상위 4행 중)
        header_idx = 0
        best_year_hits = -1
        for r_idx in range(min(4, len(tbl))):
            hits = sum(1 for c in tbl[r_idx] if _extract_year(c) is not None)
            if hits > best_year_hits:
                best_year_hits = hits
                header_idx = r_idx
        headers = tbl[header_idx]
        year_headers = [_extract_year(c) for c in headers]

        unit_name, unit_mul = _extract_unit(pre)
        # 본문에 단위가 없으면 첫 행에도 체크
        if unit_mul == 1.0:
            for row in tbl[:header_idx + 1]:
                for cell in row:
                    n, m = _extract_unit(cell)
                    if m != 1.0:
                        unit_name, unit_mul = n, m
                        break
                if unit_mul != 1.0:
                    break

        title = ""
        if pre:
            # pre_text 의 마지막 의미 있는 토큰이 제목일 가능성 높음
            title = pre[-200:].strip()

        # 표 데이터 행: header_idx 이후
        body_rows = [row for row in tbl[header_idx + 1:] if row and any(cell for cell in row)]
        if not body_rows:
            continue

        fs_div = _detect_fs_div(title, pre)
        results.append(RawFsTable(
            statement=code,
            statement_label=label,
            title=title,
            fs_div=fs_div,
            unit=unit_name,
            unit_multiplier=unit_mul,
            headers=headers,
            year_headers=year_headers,
            rows=body_rows,
            rcept_no=rcept_no,
            report_nm=report_nm,
        ))
    return results


# ── Regex 기반 테이블 추출 fallback (HTMLParser 실패 대비) ─────────────
_TABLE_BLOCK_RE = re.compile(r"<table\b[^>]*>([\s\S]*?)</table>", re.I)
_ROW_BLOCK_RE = re.compile(r"<tr\b[^>]*>([\s\S]*?)</tr>", re.I)
_CELL_BLOCK_RE = re.compile(r"<(?:td|th)\b[^>]*>([\s\S]*?)</(?:td|th)>", re.I)
_TAG_STRIP_RE = re.compile(r"<[^>]+>")
_BR_RE = re.compile(r"<br\s*/?>", re.I)


def _regex_extract_tables(text: str) -> List[Tuple[str, List[List[str]]]]:
    """`<table>...</table>` 블록을 직접 긁어서 (pre_text, 2-D 문자열 행) 반환.

    HTMLParser 가 namespace/DOCTYPE/CDATA 등으로 실패할 때 fallback.
    """
    results: List[Tuple[str, List[List[str]]]] = []
    for m in _TABLE_BLOCK_RE.finditer(text):
        # 앞쪽 최대 600자를 pre_text 로 추출 (태그 제거)
        start = m.start()
        pre_raw = text[max(0, start - 800):start]
        pre_clean = _TAG_STRIP_RE.sub(" ", pre_raw)
        pre_clean = html.unescape(_normalize_ws(pre_clean))[-500:]

        block = m.group(1)
        rows: List[List[str]] = []
        for mr in _ROW_BLOCK_RE.finditer(block):
            row_src = mr.group(1)
            cells: List[str] = []
            for mc in _CELL_BLOCK_RE.finditer(row_src):
                cell_src = _BR_RE.sub(" ", mc.group(1))
                cell_txt = _TAG_STRIP_RE.sub(" ", cell_src)
                cells.append(_normalize_ws(html.unescape(cell_txt)))
            if cells:
                rows.append(cells)
        if rows:
            results.append((pre_clean, rows))
    return results


# ── 주요 계정명 → FinancialsBundle.YearFin.values 매핑 ─────────────────
# 순서 중요: 먼저 매칭된 것을 우선. "영업이익" 과 "영업이익률" 충돌 방지 위해
# 완전일치·정규화 기반으로 판정.
KEY_MAP: List[Tuple[str, List[str]]] = [
    ("revenue",          ["매출액", "매출", "영업수익", "수익(매출액)", "수익", "매출및영업수익"]),
    ("cost_of_sales",    ["매출원가", "영업비용"]),
    ("gross_profit",     ["매출총이익", "매출총손실"]),
    ("sga",              ["판매비와관리비", "판관비"]),
    ("op_income",        ["영업이익", "영업손실", "영업이익(손실)", "영업손익"]),
    ("net_income",       ["당기순이익", "당기순손실", "당기순이익(손실)", "분기순이익", "반기순이익", "순이익"]),
    ("total_assets",     ["자산총계"]),
    ("total_liabilities",["부채총계"]),
    ("total_equity",     ["자본총계"]),
    ("dep",              ["감가상각비"]),
    ("amort",            ["무형자산상각비", "무형자산 상각비"]),
]


def _norm_account_name(s: str) -> str:
    return (s or "").replace(" ", "").replace("\u3000", "").strip()


def _match_key(account_name: str) -> Optional[str]:
    n = _norm_account_name(account_name)
    if not n:
        return None
    for key, names in KEY_MAP:
        for cand in names:
            if n == _norm_account_name(cand):
                return key
    return None


def build_year_fins_from_tables(
    tables: List[RawFsTable],
    fs_div: str = "OFS",
) -> List[YearFin]:
    """RawFsTable 목록 → YearFin 리스트 (연도별 집계).

    같은 연도에 BS/IS/CF 가 분산돼 있어 연도축으로 병합. fs_div 별로 필터링.
    """
    # year → {key: value}
    by_year: Dict[int, Dict[str, Optional[float]]] = {}

    for t in tables:
        if t.fs_div != fs_div:
            continue
        if not t.year_headers:
            continue
        # 행마다: 첫 텍스트 셀이 계정명, 나머지는 금액
        for row in t.rows:
            if not row:
                continue
            # 계정명은 처음 비어있지 않은 셀 (주석 번호 셀 포함 가능)
            name_idx = -1
            for idx, cell in enumerate(row):
                if cell and _to_amount(cell) is None and re.search(r"[가-힣A-Za-z]", cell):
                    name_idx = idx
                    break
            if name_idx < 0:
                continue
            account = row[name_idx]
            key = _match_key(account)
            if not key:
                continue
            # 금액 열 → 연도 매핑
            for col_idx, cell in enumerate(row):
                if col_idx <= name_idx:
                    continue
                if col_idx >= len(t.year_headers):
                    continue
                year = t.year_headers[col_idx]
                if year is None:
                    continue
                amt = _to_amount(cell)
                if amt is None:
                    continue
                val = amt * t.unit_multiplier
                slot = by_year.setdefault(year, _empty_values())
                # 첫 값 우선 (BS 는 연결 vs 개별 중복 가능하니 이미 있으면 skip)
                if slot.get(key) is None:
                    slot[key] = val

    # 파생: gross_profit / da / ebitda / 마진
    out: List[YearFin] = []
    for year, vals in sorted(by_year.items(), reverse=True):
        if vals.get("gross_profit") is None and \
           vals.get("revenue") is not None and vals.get("cost_of_sales") is not None:
            vals["gross_profit"] = vals["revenue"] - vals["cost_of_sales"]
        if vals.get("dep") is not None or vals.get("amort") is not None:
            vals["da"] = (vals.get("dep") or 0.0) + (vals.get("amort") or 0.0)
        if vals.get("op_income") is not None and vals.get("da") is not None:
            vals["ebitda"] = vals["op_income"] + vals["da"]
        rev = vals.get("revenue")
        if rev:
            def _pct(x): return (x / rev * 100.0) if x is not None else None
            vals["gpm"]     = _pct(vals.get("gross_profit"))
            vals["opm"]     = _pct(vals.get("op_income"))
            vals["ebitdam"] = _pct(vals.get("ebitda"))
            vals["npm"]     = _pct(vals.get("net_income"))
        if not any(v is not None for v in vals.values()):
            continue
        out.append(YearFin(
            year=year,
            reprt_code="AUDIT",
            reprt_label="감사보고서",
            fs_div=fs_div,
            currency="KRW",
            values=vals,
        ))
    return out


# ── 편의 함수 ───────────────────────────────────────────────────────────
def fetch_and_parse_audit_tables(
    rcept_no: str,
    report_nm: str = "",
    log: Optional[Callable[[str], None]] = None,
) -> List[RawFsTable]:
    """rcept_no 로 감사보고서 ZIP 다운 → 재무제표 표 파싱."""
    zip_bytes = api.document_zip(rcept_no)
    if not zip_bytes:
        if log:
            log(f"    ZIP 다운 실패: {rcept_no}")
        return []
    # 진단용 raw 카운트
    total_table_tags = 0
    if log:
        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                for name in zf.namelist():
                    if not name.lower().endswith((".html", ".htm", ".xml")):
                        continue
                    try:
                        raw = zf.read(name).decode("utf-8", errors="ignore")
                    except Exception:
                        continue
                    total_table_tags += len(re.findall(r"<table\b", raw, flags=re.I))
        except Exception:  # noqa: BLE001
            pass
    tables = parse_audit_fs_from_zip(zip_bytes, rcept_no, report_nm)
    if log:
        counts: Dict[str, int] = {}
        for t in tables:
            counts[t.statement] = counts.get(t.statement, 0) + 1
        summary = f"원본 <table> 태그 {total_table_tags}개 → 재무제표로 분류 {len(tables)}건"
        if counts:
            summary += " (" + ", ".join(f"{k}:{v}" for k, v in counts.items()) + ")"
        log(f"    {summary}")
    return tables
