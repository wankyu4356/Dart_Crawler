# -*- coding: utf-8 -*-
"""
기업분석 엑셀 자동 채우기 v10 - DART OpenAPI
실행: run_v10.bat 더블클릭
"""
import sys, os, time, threading, traceback, zipfile, io
from xml.etree import ElementTree as ET

missing = []
try:    import requests
except: missing.append("requests")
try:    from openpyxl import load_workbook
except: missing.append("openpyxl")

if missing:
    print("="*55)
    print("ERROR: Required libraries are not installed.")
    print("Missing:", ", ".join(missing))
    print("Solution: Double-click  1_install.bat  first.")
    print("="*55)
    input("\nPress Enter to exit...")
    sys.exit(1)

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

API_KEY  = "ba43f1b18f4b189c4a6652a12632d5220618dfa5"
BASE_URL = "https://opendart.fss.or.kr/api"
YEAR_ROW  = 5
YEAR_COLS = list("BCDEF")
ROW_MAP = {
    "revenue":    6,
    "op_income":  7,
    "dep":        8,
    "amort":      9,
    "net_income": 11,
    "tot_asset":  12,
    "tot_liab":   13,
    "tot_equity": 14,
    "st_borrow":  15,
    "lt_borrow":  16,
    "cash":       18,
    "cfo":        20,
    "cfi":        21,
    "cff":        22,
}
CORP_CLS = {"Y":"코스피","K":"코스닥","N":"코넥스","E":"비상장"}

PATTERNS = {
    "revenue": [
        "매출액","수익(매출액)","영업수익","매출","수익",
        "매출액(영업수익)","영업매출","총매출액",
    ],
    "op_income": [
        "영업이익","영업이익(손실)","영업손익","영업손실(이익)",
        "계속영업이익","계속영업이익(손실)",
    ],
    "net_income": [
        "당기순이익","연결당기순이익","당기순이익(손실)",
        "당기순손익","연결당기순손익","분기순이익","반기순이익",
    ],
    "tot_asset":  ["자산총계"],
    "tot_liab":   ["부채총계"],
    "tot_equity": ["자본총계"],
    "st_borrow":  ["단기차입금"],
    "lt_borrow":  ["사채및장기차입금","장기차입금"],
    "cash":       ["현금및현금성자산","현금및현금등가물"],
    "cfo": [
        "영업활동으로인한현금흐름","영업활동현금흐름",
        "영업활동으로인한순현금흐름","영업활동순현금흐름",
        "영업활동으로인한순현금",
        "영업활동으로인한현금및현금성자산의변동",
        "영업에서창출된현금흐름","영업활동으로인한현금의변동",
        "영업으로부터창출된현금",
    ],
    "cfi": [
        "투자활동으로인한현금흐름","투자활동현금흐름",
        "투자활동으로인한순현금흐름","투자활동순현금흐름",
        "투자활동으로인한순현금",
    ],
    "cff": [
        "재무활동으로인한현금흐름","재무활동현금흐름",
        "재무활동으로인한순현금흐름","재무활동순현금흐름",
        "재무활동으로인한순현금",
    ],
}

CF_IDS = {
    "cfo": [
        "ifrs-full_CashFlowsFromUsedInOperatingActivities",
        "CashFlowsFromOperatingActivities",
        "dart_CashFlowsFromOperatingActivities",
    ],
    "cfi": [
        "ifrs-full_CashFlowsFromUsedInInvestingActivities",
        "CashFlowsFromInvestingActivities",
    ],
    "cff": [
        "ifrs-full_CashFlowsFromUsedInFinancingActivities",
        "CashFlowsFromFinancingActivities",
    ],
}

# 감가상각 명시 패턴 — exclusion 없이 바로 매칭
DEP_EXPLICIT = [
    "감가상각비에대한조정",
    "유형자산감가상각비",
    "유형자산상각비",
    "유형자산의감가상각비",
    "유무형자산감가상각비",       # 유ㆍ무형자산감가상각비 → nm 후
    "유형자산및무형자산상각비",
]
# 일반 패턴 — "감가상각" 포함 + 무형/사용권/리스 제외
DEP_GENERAL_EXCL = ["무형","사용권","리스"]

AMORT_PATS = [
    "무형자산상각비에대한조정",
    "무형자산상각비",
    "무형자산의상각비",
    "무형자산상각",
    "무형자산및영업권상각",
    "영업권및무형자산상각",
]

# ── 기업코드 캐시 ─────────────────────────────────────────────────────────────
_CORP_LIST = None

def get_corp_list(log=None):
    global _CORP_LIST
    if _CORP_LIST is not None:
        return _CORP_LIST
    if log:
        log("    DART 기업 목록 다운로드 중... ", end="")
    resp = requests.get(f"{BASE_URL}/corpCode.xml",
                        params={"crtfc_key":API_KEY}, timeout=30)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        xml_bytes = zf.read("CORPCODE.xml")
    root = ET.fromstring(xml_bytes)
    _CORP_LIST = [
        {"corp_code": el.findtext("corp_code","").strip(),
         "corp_name": el.findtext("corp_name","").strip(),
         "stock_code":el.findtext("stock_code","").strip()}
        for el in root.findall("list")
    ]
    if log:
        log(f"완료 ({len(_CORP_LIST):,}개)")
    return _CORP_LIST


def search_corp(name, log=None):
    corps = get_corp_list(log)
    exact   = [c for c in corps if c["corp_name"] == name]
    partial = [c for c in corps if name in c["corp_name"]]
    pool = exact if exact else partial
    if not pool:
        raise ValueError(
            f"'{name}' 을(를) DART에서 찾을 수 없습니다.\n"
            "dart.fss.or.kr 에서 정확한 회사명을 확인하세요."
        )
    listed = [c for c in pool if c["stock_code"].strip()]
    chosen = sorted(listed or pool, key=lambda c: len(c["corp_name"]))[0]
    corp_code = chosen["corp_code"]
    corp_cls = ""
    try:
        r = requests.get(f"{BASE_URL}/company.json",
                         params={"crtfc_key":API_KEY,"corp_code":corp_code},
                         timeout=10)
        info = r.json()
        if info.get("status") == "000":
            corp_cls = info.get("corp_cls","")
    except Exception:
        pass
    if not corp_cls:
        corp_cls = "Y" if chosen["stock_code"].strip() else "E"
    return corp_code, corp_cls, chosen["corp_name"]


def fetch(corp_code, year):
    for fs in ["CFS","OFS"]:
        try:
            d = requests.get(f"{BASE_URL}/fnlttSinglAcntAll.json", params={
                "crtfc_key":API_KEY,"corp_code":corp_code,
                "bsns_year":str(year),"reprt_code":"11011","fs_div":fs,
            }, timeout=20).json()
            if d.get("status") == "000" and d.get("list"):
                return d["list"], fs
        except Exception:
            continue
    return [], None


def _dart_get(endpoint, **params):
    """
    공통 GET 헬퍼. 성공 시 list 반환, 실패/빈 응답 시 빈 리스트.
    일부 엔드포인트(대량보유, 임원·주요주주 소유)는 reprt_code/bsns_year를
    받지 않으므로, 400대 에러 또는 status != 000 이면 그 파라미터들을 제거하고
    재시도한다.
    """
    params_full = {**params, "crtfc_key": API_KEY}
    try:
        r = requests.get(f"{BASE_URL}/{endpoint}", params=params_full, timeout=20)
        d = r.json()
        if d.get("status") == "000":
            return d.get("list", []) or []
        # 파라미터 불일치 시 폴백: reprt_code/bsns_year 제거 후 재시도
        if d.get("status") in ("010","011","013","020","100","800","900"):
            fallback = {k: v for k, v in params.items()
                        if k not in ("reprt_code", "bsns_year")}
            fallback["crtfc_key"] = API_KEY
            r2 = requests.get(f"{BASE_URL}/{endpoint}",
                              params=fallback, timeout=20)
            d2 = r2.json()
            if d2.get("status") == "000":
                return d2.get("list", []) or []
    except Exception:
        pass
    return []


# (시트명, 엔드포인트) — 기업분석용 공시 항목 전체
SECTIONS = [
    ("주주_최대",      "hyslrSttus.json"),
    ("주주_최대변동",  "hyslrChgSttus.json"),
    ("주주_소액",      "mrhlSttus.json"),
    ("주주_대량보유",  "majorstock.json"),
    ("주주_임원소유",  "elestock.json"),
    ("배당",           "alotMatter.json"),
    ("자기주식",       "tsstkAcqsDspsSttus.json"),
    ("증자감자",       "irdsSttus.json"),
    ("임원",           "exctvSttus.json"),
    ("직원",           "empSttus.json"),
    ("보수_이사감사",  "drctrAdtAllMendngSttus.json"),
    ("보수_개인",      "hmvAuditIndvdlBySttus.json"),
    ("타법인출자",     "otrCprInvstmntSttus.json"),
    ("감사의견",       "accnutAdtorNmNdAdtOpinion.json"),
]


def collect_sections(wb, corp_code, years, log):
    """
    각 섹션을 연도별로 조회해 워크북에 시트로 추가/갱신한다.
    - 재무 시트(기존 고정 레이아웃)는 건드리지 않는다.
    - 섹션별 신규 시트를 생성하여 연도·전체 필드를 그대로 기록한다.
    """
    valid_years = [y for y in years if y]
    for sheet_name, endpoint in SECTIONS:
        rows_all = []
        headers = None
        for y in valid_years:
            items = _dart_get(endpoint,
                              corp_code=corp_code,
                              bsns_year=str(y),
                              reprt_code="11011")
            if not items:
                continue
            # 첫 성공 응답의 키 순서로 헤더 고정
            if headers is None:
                headers = ["조회연도"] + list(items[0].keys())
            for it in items:
                rows_all.append([y] + [it.get(k, "") for k in headers[1:]])
            time.sleep(0.2)

        if sheet_name in wb.sheetnames:
            del wb[sheet_name]
        ws = wb.create_sheet(sheet_name)
        if headers and rows_all:
            ws.append(headers)
            for r in rows_all:
                ws.append(r)
            log(f"    {sheet_name}: {len(rows_all)}행")
        else:
            ws.append(["(데이터 없음)"])
            log(f"    {sheet_name}: 없음")


def fetch_xbrl(corp_code, year):
    """
    XBRL 전체 계정 조회 — fnlttSinglAcntAll 에 없는 주석 항목 탐색용
    카카오처럼 감가상각비가 현금흐름 주석에만 있는 경우 사용
    """
    for fs in ["CFS","OFS"]:
        try:
            d = requests.get(f"{BASE_URL}/fnlttXbrlAll.json", params={
                "crtfc_key":API_KEY,"corp_code":corp_code,
                "bsns_year":str(year),"reprt_code":"11011","fs_div":fs,
            }, timeout=20).json()
            if d.get("status") == "000" and d.get("list"):
                return d["list"]
        except Exception:
            continue
    return []


def nm(s):
    for ch in [" ","\n","\t","　",
               "·","·","ㆍ","・",
               "Ⅰ","Ⅱ","Ⅲ","ⅰ","ⅱ","ⅲ",
               "I.","II.","III.","i.","ii.","iii.",
               "1.","2.","3.","합계","(합계)"]:
        s = s.replace(ch,"")
    return s.strip().lower()

def to_int(s):
    if not s or str(s).strip() in ("","-","－"):
        return None
    try:
        return int(str(s).replace(",","").replace("－","-").replace(" ",""))
    except ValueError:
        return None

def get_raw(it):
    for field in ("thstrm_amount","thstrm_add_amount","frmtrm_amount"):
        v = to_int(it.get(field))
        if v is not None:
            return v
    return None

def detect_divisor(items):
    for it in items:
        cur = it.get("currency","").strip().upper()
        if cur == "KRW":
            return 1_000_000, "원→백만원"
        if cur in ("KRW/M","KRW/1M","백만원"):
            return 1, "백만원(그대로)"
    for search_nms in [
        ["매출액","수익(매출액)","영업수익","매출"],
        ["자산총계"],["부채총계"]
    ]:
        for it in items:
            n = nm(it.get("account_nm",""))
            for pat in search_nms:
                if nm(pat) in n:
                    v = get_raw(it)
                    if v is not None:
                        return ((1_000_000,"크기추정:원→백만원")
                                if abs(v) > 500_000_000_000
                                else (1,"크기추정:백만원(그대로)"))
    return 1, "단위불명:백만원가정"

def to_mn(raw, div):
    if raw is None:
        return None
    return round(raw/div) if div != 1 else raw


def try_dep(n, amt):
    """감가상각비 매칭 시도. (True, amt) 또는 (False, None)"""
    # 1) 명시 패턴 — exclusion 없음
    for p in DEP_EXPLICIT:
        if nm(p) == n or n.startswith(nm(p)):
            return True, amt
    # 2) "감가상각" 포함 + 제외 조건
    if "감가상각" in n and all(e not in n for e in DEP_GENERAL_EXCL):
        return True, amt
    return False, None

def try_amort(n, amt):
    for p in AMORT_PATS:
        if nm(p) in n:
            return True, amt
    return False, None


def parse(items, log=None):
    div, umsg = detect_divisor(items)
    if log:
        log(f"[{umsg}]", end="")

    out = {}

    # ── PASS 1: 전체 순회, 계정명 기반 ────────────────────────────────────────
    for it in items:
        n   = nm(it.get("account_nm",""))
        raw = get_raw(it)
        if raw is None:
            continue
        amt = to_mn(raw, div)

        for key, pats in PATTERNS.items():
            if key not in out:
                for p in pats:
                    pn = nm(p)
                    if n == pn or n.startswith(pn):
                        out[key] = amt
                        break

        if "dep" not in out:
            ok, v = try_dep(n, amt)
            if ok:
                out["dep"] = v

        if "amort" not in out:
            ok, v = try_amort(n, amt)
            if ok:
                out["amort"] = v

    # ── PASS 2: 부분일치 재시도 ───────────────────────────────────────────────
    for key, pats in PATTERNS.items():
        if key not in out:
            for it in items:
                n   = nm(it.get("account_nm",""))
                raw = get_raw(it)
                if raw is None:
                    continue
                amt = to_mn(raw, div)
                for p in pats:
                    if nm(p) in n:
                        out[key] = amt
                        break
                if key in out:
                    break

    # ── PASS 3: account_id 기반 CF ────────────────────────────────────────────
    for key in ("cfo","cfi","cff"):
        if key not in out:
            for it in items:
                aid = it.get("account_id","").lower()
                raw = get_raw(it)
                if raw is None:
                    continue
                for cid in CF_IDS[key]:
                    if cid.lower() in aid:
                        out[key] = to_mn(raw, div)
                        break
                if key in out:
                    break

    # ── PASS 4: account_detail 포함 재탐색 (dep/amort) ───────────────────────
    for it in items:
        combined = nm(it.get("account_nm","") + it.get("account_detail",""))
        raw = get_raw(it)
        if raw is None:
            continue
        amt = to_mn(raw, div)
        if "dep" not in out:
            ok, v = try_dep(combined, amt)
            if ok:
                out["dep"] = v
        if "amort" not in out:
            ok, v = try_amort(combined, amt)
            if ok:
                out["amort"] = v

    return out, div


def fetch_dep_amort_from_xbrl(corp_code, year, div):
    """
    fnlttSinglAcntAll 에 dep/amort 없을 때 fnlttXbrlAll 로 재시도.
    카카오처럼 현금흐름 주석에만 감가상각이 있는 경우 사용.
    """
    dep, amort = None, None
    try:
        items = fetch_xbrl(corp_code, year)
        for it in items:
            n   = nm(it.get("label_ko", it.get("account_nm","")))
            raw = get_raw(it)
            if raw is None:
                continue
            amt = to_mn(raw, div)
            if dep is None:
                ok, v = try_dep(n, amt)
                if ok:
                    dep = v
            if amort is None:
                ok, v = try_amort(n, amt)
                if ok:
                    amort = v
            if dep is not None and amort is not None:
                break
    except Exception:
        pass
    return dep, amort


def validate(vals, year, log):
    rev = vals.get("revenue")
    if rev is not None and abs(rev) > 1_000_000_000_000:
        log(f"\n  ⚠ {year} 매출액({rev:,}) 비정상. 단위 확인 필요.")


def run(company, xlsx_path, sheet_name, log, include_all=True):
    try:
        log(f"[1] 검색: {company}")
        corp_code, corp_cls, matched = search_corp(company, log)
        market = CORP_CLS.get(corp_cls, "비상장")
        log(f"    -> {matched}  |  {market}\n")

        if not os.path.exists(xlsx_path):
            raise FileNotFoundError(f"파일 없음: {xlsx_path}")
        wb = load_workbook(xlsx_path)
        if sheet_name not in wb.sheetnames:
            raise ValueError(
                f"시트 '{sheet_name}' 없음. "
                f"존재하는 시트: {', '.join(wb.sheetnames)}"
            )
        ws = wb[sheet_name]

        years = []
        for col in YEAR_COLS:
            v = ws[f"{col}{YEAR_ROW}"].value
            years.append(int(v) if v and str(v).strip().isdigit() else None)
        log(f"[2] 연도: {[y for y in years if y]}\n")

        ws["B3"] = matched
        ws["D3"] = market

        log("[3] DART 사업보고서 조회 (단위 자동 → 백만원)\n")
        for col, year in zip(YEAR_COLS, years):
            if not year:
                continue
            log(f"    {year}... ", end="")
            items, fs = fetch(corp_code, year)
            if not items:
                log("데이터 없음 (미공시)")
                continue

            vals, div = parse(items, log)
            validate(vals, year, log)

            # ── dep/amort 누락 시 XBRL API로 재시도 ─────────────────────────
            if vals.get("dep") is None or vals.get("amort") is None:
                xdep, xamort = fetch_dep_amort_from_xbrl(corp_code, year, div)
                if vals.get("dep") is None and xdep is not None:
                    vals["dep"] = xdep
                if vals.get("amort") is None and xamort is not None:
                    vals["amort"] = xamort

            for key, row in ROW_MAP.items():
                ws[f"{col}{row}"] = vals.get(key)

            cnt  = sum(1 for k in ROW_MAP if vals.get(k) is not None)
            tag  = " [별도]" if fs == "OFS" else ""
            dtag = " ÷1M" if div == 1_000_000 else ""
            miss = [k for k in ROW_MAP if vals.get(k) is None]
            mtag = f"  누락:{miss}" if miss else ""
            log(f" 완료({cnt}/{len(ROW_MAP)}){tag}{dtag}{mtag}")
            time.sleep(0.3)

        if include_all:
            log("\n[4] 주주/배당/임원 등 전체 공시 항목 수집")
            collect_sections(wb, corp_code, years, log)

        wb.save(xlsx_path)
        log(f"\n[{'5' if include_all else '4'}] 저장: {os.path.basename(xlsx_path)}")

        log("\n" + "-"*86)
        log("  최종 검증 (단위: 백만원)")
        log("-"*86)
        wb2 = load_workbook(xlsx_path, data_only=True)
        ws2 = wb2[sheet_name]
        labels = {
            6:"매출액",7:"영업이익",8:"감가상각비",9:"무형자산상각비",
            11:"당기순이익",
            12:"자산총계",13:"부채총계",14:"자본총계",
            15:"단기차입금",16:"사채및장기차입금",
            18:"현금성자산",20:"영업CF",21:"투자CF",22:"재무CF",
        }
        log(f"  {'항목':<16}" + "".join(
            f"{str(y) if y else '':>16}" for y in years))
        for row, label in sorted(labels.items()):
            line = f"  {label:<16}"
            warn = False
            for col in YEAR_COLS:
                v = ws2[f"{col}{row}"].value
                if isinstance(v,(int,float)):
                    line += f"{v:>16,}"
                    if abs(v) > 1_000_000_000_000:
                        warn = True
                else:
                    line += f"{'—':>16}"
            log(line + (" ← ⚠단위확인" if warn else ""))
        log("-"*86)
        log("\n완료! 엑셀 파일을 열어 확인하세요.")
        return True

    except Exception as e:
        log(f"\n오류: {e}")
        log(traceback.format_exc())
        return False


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("기업분석 엑셀 자동 채우기 v10 (DART)")
        self.geometry("780x660")
        self.resizable(True, True)
        self._ui()

    def _ui(self):
        frm = ttk.LabelFrame(self, text="입력", padding=12)
        frm.pack(fill="x", padx=12, pady=8)

        ttk.Label(frm, text="회사명").grid(row=0,column=0,sticky="w")
        self.v_co = tk.StringVar()
        ttk.Entry(frm, textvariable=self.v_co, width=28,
                  font=("맑은 고딕",10)).grid(row=0,column=1,sticky="ew",padx=6)
        ttk.Label(frm,
            text="예) 카카오  풍산  LG디스플레이  현대자동차",
            foreground="#888").grid(row=0,column=2,sticky="w")

        ttk.Label(frm, text="엑셀 파일").grid(row=1,column=0,sticky="w",pady=6)
        self.v_xl = tk.StringVar()
        ttk.Entry(frm, textvariable=self.v_xl, width=42).grid(
            row=1,column=1,sticky="ew",padx=6)
        ttk.Button(frm, text="찾아보기...",
                   command=self._browse).grid(row=1,column=2,sticky="w")

        ttk.Label(frm, text="시트명").grid(row=2,column=0,sticky="w")
        self.v_sh = tk.StringVar(value="1")
        ttk.Entry(frm, textvariable=self.v_sh, width=6).grid(
            row=2,column=1,sticky="w",padx=6)
        ttk.Label(frm,
            text="1번 시트 -> 1   /   2번 시트 -> 2",
            foreground="#888").grid(row=2,column=2,sticky="w")

        self.v_all = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            frm,
            text="주주구성·배당·임원·자기주식 등 전체 공시 항목 포함 (별도 시트 생성)",
            variable=self.v_all,
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 0))

        frm.columnconfigure(1, weight=1)
        self.btn = ttk.Button(self, text="▶  실행", command=self._run)
        self.btn.pack(pady=6)

        lf = ttk.LabelFrame(self, text="진행 상황", padding=6)
        lf.pack(fill="both", expand=True, padx=12, pady=4)
        self.box = scrolledtext.ScrolledText(
            lf, state="disabled", font=("Consolas",9), height=26,
            bg="#1e1e1e", fg="#d4d4d4")
        self.box.pack(fill="both", expand=True)

    def _browse(self):
        p = filedialog.askopenfilename(
            title="엑셀 파일 선택",
            filetypes=[("Excel","*.xlsx *.xlsm"),("All","*.*")])
        if p:
            self.v_xl.set(p)

    def _log(self, msg, end="\n"):
        self.box.config(state="normal")
        self.box.insert("end", msg+end)
        self.box.see("end")
        self.box.config(state="disabled")
        self.update_idletasks()

    def _run(self):
        co = self.v_co.get().strip()
        xl = self.v_xl.get().strip()
        sh = self.v_sh.get().strip() or "1"
        if not co:
            messagebox.showwarning("입력 필요","회사명을 입력하세요.")
            return
        if not xl:
            messagebox.showwarning("입력 필요","엑셀 파일을 선택하세요.")
            return
        self.box.config(state="normal")
        self.box.delete("1.0","end")
        self.box.config(state="disabled")
        self.btn.config(state="disabled", text="조회 중...")

        include_all = self.v_all.get()

        def task():
            ok = run(co, xl, sh, self._log, include_all=include_all)
            self.btn.config(state="normal", text="▶  실행")
            if ok:
                messagebox.showinfo("완료",
                    f"'{co}' 완료!\n엑셀을 열어 확인하세요.")
        threading.Thread(target=task, daemon=True).start()


if __name__ == "__main__":
    app = App()
    app.mainloop()
