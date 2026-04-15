# -*- coding: utf-8 -*-
"""M10-B — Tkinter GUI.

사용자 입력:
  • 회사명
  • 기간 숫자 + 단위 드롭다운(년/개월)
  • 본문분석 체크박스, LLM 호출 상한
  • 출력 폴더
  • (선택) Anthropic API Key 입력란 (.env 또는 환경변수 미설정 시 직접 입력)

백그라운드 스레드로 `run_quickreport()` 실행. 진행 로그 ScrolledText.
"""
from __future__ import annotations
import os
import sys
import threading
import traceback

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from . import config as cfgmod
from .orchestrator import RunConfig, run_quickreport


def _open_path(path: str) -> None:
    """완료 후 폴더/파일 열기."""
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", path])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("완규의 딸깍공장 — DART 공시 자동 분석")
        self.geometry("820x680")
        self.resizable(True, True)
        self._running = False

        self._build_ui()

    # ── UI 구성 ────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 6}

        frm = ttk.LabelFrame(self, text="입력")
        frm.pack(fill="x", **pad)

        # 회사명 — Combobox 로 후보 표시 (상장/비상장 구분)
        ttk.Label(frm, text="회사명").grid(row=0, column=0, sticky="w", padx=6, pady=6)
        self.var_company = tk.StringVar(value="")
        self.cmb_company = ttk.Combobox(frm, textvariable=self.var_company, width=32)
        self.cmb_company.grid(row=0, column=1, sticky="w", padx=6, pady=6)
        # 입력할 때마다 300ms 후 후보 갱신
        self._search_after_id = None
        self.cmb_company.bind("<KeyRelease>", self._on_company_key)

        # 기간
        ttk.Label(frm, text="조회기간").grid(row=0, column=2, sticky="e", padx=6)
        self.var_period = tk.IntVar(value=2)
        ttk.Spinbox(frm, from_=1, to=120, width=6, textvariable=self.var_period).grid(
            row=0, column=3, sticky="w")
        self.var_unit = tk.StringVar(value="년")
        ttk.Combobox(frm, textvariable=self.var_unit, values=["년", "개월"],
                     width=6, state="readonly").grid(row=0, column=4, sticky="w", padx=4)

        # 본문 분석
        self.var_analyze = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm, text="본문 요약·Implication 분석 (Claude)",
                        variable=self.var_analyze).grid(
            row=1, column=0, columnspan=3, sticky="w", padx=6, pady=4)

        ttk.Label(frm, text="분석 최대 건수").grid(row=1, column=3, sticky="e", padx=6)
        self.var_limit = tk.IntVar(value=20)
        ttk.Spinbox(frm, from_=1, to=200, width=6, textvariable=self.var_limit).grid(
            row=1, column=4, sticky="w")

        # 연간 재무 조회 연수
        ttk.Label(frm, text="재무 조회 연수").grid(row=2, column=0, sticky="w", padx=6)
        self.var_years = tk.IntVar(value=4)
        ttk.Spinbox(frm, from_=1, to=10, width=6, textvariable=self.var_years).grid(
            row=2, column=1, sticky="w")

        # API Key
        ttk.Label(frm, text="Anthropic API Key").grid(row=3, column=0, sticky="w", padx=6)
        self.var_key = tk.StringVar(value=cfgmod.ANTHROPIC_API_KEY)
        ttk.Entry(frm, textvariable=self.var_key, width=50, show="*").grid(
            row=3, column=1, columnspan=3, sticky="we", padx=6)

        # 출력 폴더
        ttk.Label(frm, text="출력 폴더").grid(row=4, column=0, sticky="w", padx=6, pady=4)
        self.var_outdir = tk.StringVar(value=os.path.expanduser("~/Desktop"))
        ttk.Entry(frm, textvariable=self.var_outdir, width=50).grid(
            row=4, column=1, columnspan=3, sticky="we", padx=6)
        ttk.Button(frm, text="찾아보기", command=self._browse).grid(
            row=4, column=4, padx=4)

        # 실행 버튼
        self.btn_run = ttk.Button(self, text="분석 시작", command=self._start)
        self.btn_run.pack(fill="x", **pad)

        # 로그
        logfrm = ttk.LabelFrame(self, text="진행 로그")
        logfrm.pack(fill="both", expand=True, **pad)
        self.txt = scrolledtext.ScrolledText(logfrm, height=20, wrap="word",
                                             font=("Consolas", 10))
        self.txt.pack(fill="both", expand=True)

        self._log("완규의 딸깍공장 준비 완료. 회사명을 입력하고 [분석 시작]을 누르세요.")

    # ── 회사명 후보 autocomplete ─────────────────────────────────────
    def _on_company_key(self, event) -> None:
        # 방향/enter 키는 선택 이동용 — 무시
        if event.keysym in ("Up", "Down", "Return", "Tab", "Escape", "Left", "Right"):
            return
        if self._search_after_id is not None:
            try:
                self.after_cancel(self._search_after_id)
            except Exception:
                pass
        self._search_after_id = self.after(300, self._refresh_candidates)

    def _refresh_candidates(self) -> None:
        self._search_after_id = None
        q = self.var_company.get().strip()
        if len(q) < 1:
            self.cmb_company["values"] = []
            return
        try:
            from . import corp as corp_mod
            cands = corp_mod.search_corp_candidates(q, limit=15)
        except Exception:
            return
        labels = []
        for c in cands:
            tag = "상장" if c.is_listed else "비상장"
            code = c.stock_code if c.is_listed else c.corp_code
            labels.append(f"{c.corp_name} [{tag} {code}]")
        self.cmb_company["values"] = labels

    def _resolve_selected_corp(self) -> str:
        """Combobox 에 선택된 값이 'XXX [태그 코드]' 형식이면 회사명만 추출."""
        s = self.var_company.get().strip()
        i = s.find("[")
        return s[:i].strip() if i > 0 else s

    def _browse(self) -> None:
        d = filedialog.askdirectory(initialdir=self.var_outdir.get() or ".")
        if d:
            self.var_outdir.set(d)

    # ── 로그 ──────────────────────────────────────────────────────────
    def _log(self, msg: str) -> None:
        def _append():
            self.txt.insert("end", msg.rstrip() + "\n")
            self.txt.see("end")
        try:
            self.after(0, _append)
        except Exception:
            print(msg)

    # ── 실행 ──────────────────────────────────────────────────────────
    def _start(self) -> None:
        if self._running:
            messagebox.showwarning("진행중", "이미 실행 중입니다.")
            return
        company = self._resolve_selected_corp()
        if not company:
            messagebox.showerror("입력 오류", "회사명을 입력하세요.")
            return
        cfg = RunConfig(
            company=company,
            period_value=int(self.var_period.get()),
            period_unit=self.var_unit.get(),
            analyze_bodies=bool(self.var_analyze.get()),
            body_limit=int(self.var_limit.get()) or None,
            years_back=int(self.var_years.get()),
            output_dir=self.var_outdir.get().strip() or ".",
            anthropic_api_key=self.var_key.get().strip() or None,
        )
        self._running = True
        self.btn_run.config(state="disabled")
        self.txt.delete("1.0", "end")
        threading.Thread(target=self._worker, args=(cfg,), daemon=True).start()

    def _worker(self, cfg: RunConfig) -> None:
        try:
            result = run_quickreport(cfg, log=self._log)
            self._log("")
            self._log(f"✓ 완료: {result.corp_name}")
            self._log(f"  · 공시 {result.n_disclosures}건 (LLM 분석 {result.n_analyzed}건)")
            self._log(f"  · Excel: {result.excel_path}")
            self._log(f"  · HTML:  {result.html_path}")
            self.after(0, lambda: _open_path(cfg.output_dir))
        except Exception as exc:  # noqa: BLE001
            self._log(f"\n[오류] {exc}")
            self._log(traceback.format_exc())
            self.after(0, lambda: messagebox.showerror("실행 오류", str(exc)))
        finally:
            self._running = False
            self.after(0, lambda: self.btn_run.config(state="normal"))


def main() -> None:
    App().mainloop()
