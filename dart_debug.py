# -*- coding: utf-8 -*-
"""DART 계정명 디버그 도구 v10 — 전체/감가상각/CF 계정 출력"""
import sys, zipfile, io
from xml.etree import ElementTree as ET

missing = []
try:    import requests
except: missing.append("requests")
if missing:
    print("ERROR: pip install requests"); input(); sys.exit(1)

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox

API_KEY  = "ba43f1b18f4b189c4a6652a12632d5220618dfa5"
BASE_URL = "https://opendart.fss.or.kr/api"
_CORP = None

def get_corp_list():
    global _CORP
    if _CORP: return _CORP
    resp = requests.get(f"{BASE_URL}/corpCode.xml",
                        params={"crtfc_key":API_KEY}, timeout=30)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        xml = zf.read("CORPCODE.xml")
    root = ET.fromstring(xml)
    _CORP = [{"corp_code":el.findtext("corp_code","").strip(),
               "corp_name":el.findtext("corp_name","").strip(),
               "stock_code":el.findtext("stock_code","").strip()}
              for el in root.findall("list")]
    return _CORP

def find_corp(name):
    corps = get_corp_list()
    exact = [c for c in corps if c["corp_name"] == name]
    partial = [c for c in corps if name in c["corp_name"]]
    pool = exact if exact else partial
    if not pool: return None, None
    listed = [c for c in pool if c["stock_code"].strip()]
    chosen = sorted(listed or pool, key=lambda c: len(c["corp_name"]))[0]
    return chosen["corp_code"], chosen["corp_name"]

def fetch_items(corp_code, year):
    for fs in ["CFS","OFS"]:
        d = requests.get(f"{BASE_URL}/fnlttSinglAcntAll.json", params={
            "crtfc_key":API_KEY,"corp_code":corp_code,
            "bsns_year":str(year),"reprt_code":"11011","fs_div":fs,
        }, timeout=20).json()
        if d.get("status") == "000" and d.get("list"):
            return d["list"], fs
    return [], None

def fetch_xbrl(corp_code, year):
    """XBRL 전체 계정 — 주석 항목 포함"""
    for fs in ["CFS","OFS"]:
        try:
            d = requests.get(f"{BASE_URL}/fnlttXbrlAll.json", params={
                "crtfc_key":API_KEY,"corp_code":corp_code,
                "bsns_year":str(year),"reprt_code":"11011","fs_div":fs,
            }, timeout=20).json()
            if d.get("status") == "000" and d.get("list"):
                return d["list"], fs
        except Exception:
            pass
    return [], None

def run_debug(name, year, mode, log):
    log(f"검색: {name} {year}년  [모드: {mode}]\n")
    corp_code, matched = find_corp(name)
    if not corp_code:
        log("회사를 찾을 수 없습니다."); return
    log(f"-> {matched} ({corp_code})\n")

    if mode == "XBRL(주석포함)":
        items, fs = fetch_xbrl(corp_code, int(year))
        api_name = "fnlttXbrlAll"
    else:
        items, fs = fetch_items(corp_code, int(year))
        api_name = "fnlttSinglAcntAll"

    if not items:
        log(f"{api_name} 데이터 없음"); return

    currencies = set(it.get("currency","") for it in items)
    sj_divs    = set(it.get("sj_div","") for it in items)
    log(f"API: {api_name}  재무제표: {fs}  총 {len(items)}개\n")
    log(f"currency: {currencies}\nsj_div: {sj_divs}\n")
    log("="*60)

    if mode in ("CF(현금흐름)", "XBRL(주석포함)"):
        kws = ["현금","활동","창출","흐름"]
        matched_items = [it for it in items
                         if any(kw in it.get("account_nm","") or
                                kw in it.get("label_ko","")
                                for kw in kws)]
        log(f"\n[현금흐름 관련] ({len(matched_items)}개)")
        for it in matched_items:
            nm_str  = it.get("account_nm","") or it.get("label_ko","")
            aid     = it.get("account_id","")
            amt_t   = it.get("thstrm_amount","")
            amt_a   = it.get("thstrm_add_amount","")
            sj      = it.get("sj_div","")
            log(f"  sj={sj:4s}| {nm_str[:45]:<45}| "
                f"thstrm={amt_t!r:>22} | add={amt_a!r:>15}")
            if aid:
                log(f"         {aid}")

    if mode in ("감가상각", "XBRL(주석포함)"):
        kws = ["감가상각","상각비","amortiz","depreciat"]
        matched_items = [it for it in items
                         if any(kw in (it.get("account_nm","") or
                                       it.get("label_ko","")).lower()
                                for kw in kws)]
        log(f"\n[감가상각 관련] ({len(matched_items)}개)")
        for it in matched_items:
            nm_str = it.get("account_nm","") or it.get("label_ko","")
            amt_t  = it.get("thstrm_amount","")
            amt_a  = it.get("thstrm_add_amount","")
            sj     = it.get("sj_div","")
            log(f"  sj={sj:4s}| {nm_str[:50]:<50}| "
                f"thstrm={amt_t!r:>22} | add={amt_a!r:>15}")

    if mode == "전체":
        log(f"\n[전체 {len(items)}개]")
        for it in items:
            nm_str = it.get("account_nm","") or it.get("label_ko","")
            amt_t  = it.get("thstrm_amount","")
            sj     = it.get("sj_div","")
            log(f"  sj={sj:4s}| {nm_str[:50]:<50}| thstrm={amt_t!r:>22}")

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("DART 디버그 도구 v10")
        self.geometry("1150x650")
        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="x")
        ttk.Label(frm, text="회사명").grid(row=0,column=0,sticky="w")
        self.v_co = tk.StringVar(value="카카오")
        ttk.Entry(frm, textvariable=self.v_co, width=18).grid(row=0,column=1,padx=4)
        ttk.Label(frm, text="연도").grid(row=0,column=2,sticky="w")
        self.v_yr = tk.StringVar(value="2025")
        ttk.Entry(frm, textvariable=self.v_yr, width=6).grid(row=0,column=3,padx=4)
        ttk.Label(frm, text="모드").grid(row=0,column=4,sticky="w")
        self.v_mode = tk.StringVar(value="감가상각")
        ttk.Combobox(frm, textvariable=self.v_mode, width=16,
                     values=["감가상각","CF(현금흐름)","XBRL(주석포함)","전체"],
                     state="readonly").grid(row=0,column=5,padx=4)
        ttk.Button(frm, text="조회", command=self._run).grid(row=0,column=6,padx=6)
        lf = ttk.LabelFrame(self, text="결과", padding=6)
        lf.pack(fill="both", expand=True, padx=10, pady=4)
        self.box = scrolledtext.ScrolledText(lf, font=("Consolas",8), height=34)
        self.box.pack(fill="both", expand=True)

    def _log(self, msg, end="\n"):
        self.box.insert("end", msg+end)
        self.box.see("end")
        self.update_idletasks()

    def _run(self):
        self.box.delete("1.0","end")
        import threading
        threading.Thread(
            target=run_debug,
            args=(self.v_co.get().strip(), self.v_yr.get().strip(),
                  self.v_mode.get(), self._log),
            daemon=True
        ).start()

if __name__ == "__main__":
    App().mainloop()
