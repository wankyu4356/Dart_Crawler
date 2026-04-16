# -*- coding: utf-8 -*-
"""M10-C — Tkinter GUI (UX 리디자인).

핵심 원칙:
  • **회사명 → 버튼** 이 1초 안에 눈에 들어올 것.
  • 설정은 접이식 (기본 닫힘) — 파워유저만 열어봄.
  • 로그 위에 스텝 인디케이터 (현재 진행 단계 한눈에).
  • ttk "clam" 테마 강제 (크로스 플랫폼 일관성).
"""
from __future__ import annotations
import os
import re
import sys
import threading
import traceback

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from . import config as cfgmod
from ._version import __version__
from .orchestrator import RunConfig, run_quickreport


# ── 디자인 토큰 ──────────────────────────────────────────────────────────
PRIMARY   = "#10174a"
PRIMARY_2 = "#1a237e"
PRIMARY_3 = "#3949ab"
ACCENT    = "#ff6b35"
INK       = "#0f172a"
INK_2     = "#334155"
INK_3     = "#475569"
BG        = "#f1f3f8"
SURFACE   = "#ffffff"
SURFACE_2 = "#f8fafc"
BORDER    = "#dfe3ec"
BORDER_2  = "#c7cfdc"
MUTED     = "#64748b"
LOG_BG    = "#0f172a"
LOG_FG    = "#cbd5e1"
LOG_ACCENT = "#f8fafc"
GOLD      = "#ffd180"

STEP_RE = re.compile(r"\[(\d+)/(\d+)\]")


def _open_path(path: str) -> None:
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            import subprocess; subprocess.Popen(["open", path])
        else:
            import subprocess; subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Company Snapshot")
        self.geometry("840x680")
        self.minsize(720, 540)
        self.configure(bg=BG)
        self._running = False
        self._search_after_id = None
        self._settings_visible = False

        self._apply_style()
        self._build_ui()

    # ── 스타일 ────────────────────────────────────────────────────────
    def _apply_style(self) -> None:
        s = ttk.Style(self)
        try:
            s.theme_use("clam")
        except tk.TclError:
            pass

        s.configure("Header.TFrame", background=PRIMARY)
        s.configure("Header.TLabel", background=PRIMARY, foreground="#ffffff",
                    font=("Segoe UI", 14, "bold"))
        s.configure("HeaderSub.TLabel", background=PRIMARY, foreground="#7b8ec9",
                    font=("Segoe UI", 9))

        s.configure("TLabel", background=SURFACE, foreground=INK,
                    font=("Segoe UI", 10))
        s.configure("Field.TLabel", background=SURFACE, foreground=INK_3,
                    font=("Segoe UI", 9, "bold"))
        s.configure("BG.TLabel", background=BG, foreground=INK,
                    font=("Segoe UI", 10))
        s.configure("Step.TLabel", background=BG, foreground=PRIMARY_3,
                    font=("Segoe UI", 10, "bold"))

        s.configure("Card.TFrame", background=SURFACE)
        s.configure("Card.TLabelframe", background=SURFACE, padding=14,
                    relief="flat", borderwidth=1, bordercolor=BORDER)
        s.configure("Card.TLabelframe.Label", background=SURFACE,
                    foreground=PRIMARY_2, font=("Segoe UI", 10, "bold"))

        s.configure("TEntry", fieldbackground=SURFACE_2, foreground=INK,
                    bordercolor=BORDER_2, lightcolor=BORDER_2, padding=6)
        s.map("TEntry", bordercolor=[("focus", PRIMARY_3)],
              lightcolor=[("focus", PRIMARY_3)])
        s.configure("TCombobox", fieldbackground=SURFACE_2, padding=5,
                    bordercolor=BORDER_2, lightcolor=BORDER_2)
        s.configure("TSpinbox", fieldbackground=SURFACE_2, padding=4,
                    bordercolor=BORDER_2, lightcolor=BORDER_2)
        s.configure("TCheckbutton", background=SURFACE, foreground=INK,
                    font=("Segoe UI", 9))

        s.configure("Primary.TButton", background=PRIMARY_2, foreground="#ffffff",
                    font=("Segoe UI", 12, "bold"), padding=(18, 12),
                    borderwidth=0, relief="flat")
        s.map("Primary.TButton",
              background=[("active", PRIMARY_3), ("pressed", PRIMARY),
                          ("disabled", "#94a3b8")],
              foreground=[("disabled", "#f1f5f9")])
        s.configure("Ghost.TButton", background=SURFACE_2, foreground=PRIMARY_2,
                    font=("Segoe UI", 9, "bold"), padding=(10, 6),
                    borderwidth=1, relief="solid", bordercolor=BORDER_2)
        s.map("Ghost.TButton", background=[("active", "#eef2fb")],
              bordercolor=[("active", PRIMARY_3)])
        s.configure("Toggle.TButton", background=BG, foreground=INK_3,
                    font=("Segoe UI", 9, "bold"), padding=(10, 6),
                    borderwidth=0, relief="flat")
        s.map("Toggle.TButton", background=[("active", BORDER)])

        s.configure("Status.TFrame", background=PRIMARY)
        s.configure("Status.TLabel", background=PRIMARY, foreground="#7b8ec9",
                    font=("Segoe UI", 8))
        s.configure("StatusLink.TLabel", background=PRIMARY, foreground=GOLD,
                    font=("Segoe UI", 8, "bold"))

    # ── UI ─────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        # ─── 1. 슬림 헤더 (1줄)
        hdr = ttk.Frame(self, style="Header.TFrame", padding=(20, 10, 20, 10))
        hdr.pack(fill="x")
        ttk.Label(hdr, text="Company Snapshot",
                  style="Header.TLabel").pack(side="left")
        ttk.Label(hdr, text=f"  v{__version__}",
                  style="HeaderSub.TLabel").pack(side="left", padx=(4, 0))

        # ─── 2. 핵심 입력 영역 (회사명 + 기간 + 버튼)
        core = ttk.Frame(self, style="Card.TFrame", padding=(20, 16, 20, 12))
        core.pack(fill="x", padx=16, pady=(12, 0))

        # 회사명
        row_name = tk.Frame(core, bg=SURFACE)
        row_name.pack(fill="x", pady=(0, 8))
        ttk.Label(row_name, text="회사명", style="Field.TLabel").pack(
            side="left", padx=(0, 10))
        self.var_company = tk.StringVar()
        self.cmb_company = ttk.Combobox(row_name, textvariable=self.var_company)
        self.cmb_company.pack(side="left", fill="x", expand=True)
        self.cmb_company.bind("<KeyRelease>", self._on_company_key)

        # 기간 + 재무연수 (한 줄에 모아서)
        row_opts = tk.Frame(core, bg=SURFACE)
        row_opts.pack(fill="x", pady=(0, 4))
        ttk.Label(row_opts, text="공시 조회", style="Field.TLabel").pack(
            side="left", padx=(0, 6))
        self.var_period = tk.IntVar(value=3)
        ttk.Spinbox(row_opts, from_=1, to=120, width=4,
                    textvariable=self.var_period).pack(side="left")
        self.var_unit = tk.StringVar(value="년")
        ttk.Combobox(row_opts, textvariable=self.var_unit, values=["년", "개월"],
                     width=4, state="readonly").pack(side="left", padx=(4, 0))

        # 구분선
        ttk.Label(row_opts, text="│", style="Field.TLabel").pack(
            side="left", padx=(16, 16))

        ttk.Label(row_opts, text="재무", style="Field.TLabel").pack(
            side="left", padx=(0, 6))
        self.var_years = tk.IntVar(value=4)
        ttk.Spinbox(row_opts, from_=1, to=10, width=4,
                    textvariable=self.var_years).pack(side="left")
        ttk.Label(row_opts, text="년치", style="Field.TLabel").pack(
            side="left", padx=(4, 0))

        # 실행 버튼
        self.btn_run = ttk.Button(core, text="▶  분석 시작",
                                  style="Primary.TButton", command=self._start)
        self.btn_run.pack(fill="x", pady=(10, 0))

        # ─── 3. 설정 토글
        toggle_bar = tk.Frame(self, bg=BG)
        toggle_bar.pack(fill="x", padx=16, pady=(8, 0))
        self.btn_toggle = ttk.Button(toggle_bar, text="⚙  설정 ▸",
                                     style="Toggle.TButton",
                                     command=self._toggle_settings)
        self.btn_toggle.pack(side="left")

        # ─── 4. 접이식 설정 패널 (기본 숨김)
        self._settings_frame = tk.Frame(self, bg=BG)
        # 내부 카드 구성
        inner = tk.Frame(self._settings_frame, bg=BG)
        inner.pack(fill="x", padx=16, pady=(4, 0))

        # 출력 + LLM 을 좌우 2열로 배치
        left = ttk.Labelframe(inner, text=" 출력 ", style="Card.TLabelframe")
        left.pack(side="left", fill="both", expand=True, padx=(0, 6))

        ttk.Label(left, text="저장 폴더", style="Field.TLabel").grid(
            row=0, column=0, sticky="w", padx=2, pady=2)
        self.var_outdir = tk.StringVar(value=os.path.expanduser("~/Desktop"))
        ttk.Entry(left, textvariable=self.var_outdir, width=28).grid(
            row=0, column=1, sticky="we", padx=2)
        ttk.Button(left, text="…", style="Ghost.TButton",
                   command=self._browse, width=3).grid(row=0, column=2, padx=2)
        self.var_save_log = tk.BooleanVar(value=True)
        ttk.Checkbutton(left, text="디버그 로그 파일 저장",
                        variable=self.var_save_log).grid(
            row=1, column=0, columnspan=3, sticky="w", padx=2, pady=(4, 0))
        left.grid_columnconfigure(1, weight=1)

        right = ttk.Labelframe(inner, text=" LLM (Claude) ", style="Card.TLabelframe")
        right.pack(side="left", fill="both", expand=True, padx=(6, 0))

        # 체크박스 2열 배치
        self.var_summarize = tk.BooleanVar(value=True)
        self.var_exec_summary = tk.BooleanVar(value=True)
        self.var_biz_profile = tk.BooleanVar(value=True)
        self.var_footnotes = tk.BooleanVar(value=True)
        self.var_da_llm = tk.BooleanVar(value=True)

        ttk.Checkbutton(right, text="공시별 요약·시사점",
                        variable=self.var_summarize).grid(
            row=0, column=0, sticky="w", padx=2)
        ttk.Checkbutton(right, text="종합 경영진 요약",
                        variable=self.var_exec_summary).grid(
            row=0, column=1, sticky="w", padx=2)
        ttk.Checkbutton(right, text="회사 개요·사업구조",
                        variable=self.var_biz_profile).grid(
            row=1, column=0, sticky="w", padx=2)
        ttk.Checkbutton(right, text="주석 요약 정리",
                        variable=self.var_footnotes).grid(
            row=1, column=1, sticky="w", padx=2)
        ttk.Checkbutton(right, text="D&A 자동 보강",
                        variable=self.var_da_llm).grid(
            row=2, column=0, sticky="w", padx=2, pady=(0, 4))

        # AI 모델 (row 3)
        ttk.Label(right, text="AI 모델", style="Field.TLabel").grid(
            row=3, column=0, sticky="w", padx=2, pady=2)
        self.var_model = tk.StringVar(value=cfgmod.ANTHROPIC_MODEL)
        ttk.Combobox(right, textvariable=self.var_model,
                     values=cfgmod.AVAILABLE_MODELS,
                     width=22, state="readonly").grid(
            row=3, column=1, sticky="w", padx=2)

        # 분석 건수 (row 4)
        self.var_limit = tk.IntVar(value=20)
        ttk.Label(right, text="분석 상한", style="Field.TLabel").grid(
            row=4, column=0, sticky="w", padx=2, pady=2)
        limit_row = tk.Frame(right, bg=SURFACE)
        limit_row.grid(row=4, column=1, sticky="w", padx=2)
        ttk.Spinbox(limit_row, from_=1, to=200, width=4,
                    textvariable=self.var_limit).pack(side="left")
        ttk.Label(limit_row, text=" 건 (공시 본문 분석 최대 건수)",
                  style="Field.TLabel").pack(side="left")

        # API Key (row 5) — 파일 자동 로드 지원
        key_label = "API Key"
        if cfgmod.ANTHROPIC_API_KEY:
            key_label = "API Key ✓"
        ttk.Label(right, text=key_label, style="Field.TLabel").grid(
            row=5, column=0, sticky="w", padx=2, pady=2)
        self.var_key = tk.StringVar(value=cfgmod.ANTHROPIC_API_KEY)
        ttk.Entry(right, textvariable=self.var_key, show="•", width=32).grid(
            row=5, column=1, sticky="we", padx=2)
        ttk.Label(right, text="직접 입력 또는 exe 폴더에 api_key.txt 자동 인식",
                  style="Field.TLabel").grid(
            row=6, column=0, columnspan=2, sticky="w", padx=2, pady=(0, 2))

        right.grid_columnconfigure(1, weight=1)

        # ─── 5. 진행 영역 (스텝 인디케이터 + 로그)
        log_outer = tk.Frame(self, bg=BG)
        log_outer.pack(fill="both", expand=True, padx=16, pady=(8, 8))

        # 스텝 인디케이터 + 프로그레스 바
        step_bar = tk.Frame(log_outer, bg=BG)
        step_bar.pack(fill="x", pady=(0, 4))
        tk.Label(step_bar, text="●", bg=BG, fg=PRIMARY_3,
                 font=("Segoe UI", 9)).pack(side="left")
        self.lbl_step = tk.Label(step_bar, text="  대기 중",
                                 bg=BG, fg=INK_3,
                                 font=("Segoe UI", 9, "bold"), anchor="w")
        self.lbl_step.pack(side="left", padx=(4, 0))
        self.lbl_pct = tk.Label(step_bar, text="",
                                bg=BG, fg=MUTED,
                                font=("Segoe UI", 9), anchor="e")
        self.lbl_pct.pack(side="right")

        self.progress = ttk.Progressbar(log_outer, orient="horizontal",
                                        length=100, mode="determinate",
                                        maximum=100)
        self.progress.pack(fill="x", pady=(0, 4))

        # 로그 텍스트
        log_frame = tk.Frame(log_outer, bg=LOG_BG, bd=0, highlightthickness=0)
        log_frame.pack(fill="both", expand=True)
        self.txt = scrolledtext.ScrolledText(
            log_frame, wrap="word",
            font=("Consolas", 9),
            bg=LOG_BG, fg=LOG_FG,
            insertbackground=LOG_FG,
            bd=0, padx=12, pady=10,
            highlightthickness=0,
        )
        self.txt.pack(fill="both", expand=True)
        self.txt.tag_configure("ok", foreground="#86efac")
        self.txt.tag_configure("warn", foreground="#fcd34d")
        self.txt.tag_configure("err", foreground="#fca5a5")

        # ─── 6. 상태바
        status = ttk.Frame(self, style="Status.TFrame",
                           padding=(16, 6, 16, 6))
        status.pack(fill="x", side="bottom")
        ttk.Label(status, text=f"v{__version__}",
                  style="Status.TLabel").pack(side="left")
        ttk.Label(status, text="│", style="Status.TLabel").pack(
            side="left", padx=8)
        ttk.Label(status, text=cfgmod.CONTACT_EMAIL,
                  style="StatusLink.TLabel").pack(side="left")

        # 포커스 + Enter 바인딩
        self.cmb_company.focus_set()
        self.bind("<Return>", lambda e: self._start())
        self._log("회사명을 입력하고 [▶ 분석 시작] 을 누르세요.")

    # ── 설정 토글 ─────────────────────────────────────────────────────
    def _toggle_settings(self) -> None:
        if self._settings_visible:
            self._settings_frame.pack_forget()
            self.btn_toggle.config(text="⚙  설정 ▸")
            self._settings_visible = False
        else:
            # 로그 영역 바로 위에 삽입 (pack order 보장)
            self._settings_frame.pack(fill="x",
                                      after=self.btn_toggle.master)
            self.btn_toggle.config(text="⚙  설정 ▾")
            self._settings_visible = True

    # ── 회사명 autocomplete ───────────────────────────────────────────
    def _on_company_key(self, event) -> None:
        if event.keysym in ("Up", "Down", "Return", "Tab",
                            "Escape", "Left", "Right"):
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
        s = self.var_company.get().strip()
        i = s.find("[")
        return s[:i].strip() if i > 0 else s

    def _browse(self) -> None:
        d = filedialog.askdirectory(initialdir=self.var_outdir.get() or ".")
        if d:
            self.var_outdir.set(d)

    # ── 로그 + 스텝 업데이트 ──────────────────────────────────────────
    def _log(self, msg: str) -> None:
        def _append():
            self.txt.insert("end", msg.rstrip() + "\n")
            self.txt.see("end")
            # 스텝 인디케이터 + 프로그레스 바 업데이트
            m = STEP_RE.search(msg)
            if m:
                cur, total = int(m.group(1)), int(m.group(2))
                rest = msg[m.end():].strip()
                self.lbl_step.config(text=f"  [{cur}/{total}] {rest[:60]}")
                pct = int(cur / total * 100) if total else 0
                self.progress["value"] = pct
                self.lbl_pct.config(text=f"{pct}%")
            elif "✓" in msg or "완료" in msg:
                self.lbl_step.config(text="  ✓ 완료")
                self.progress["value"] = 100
                self.lbl_pct.config(text="100%")
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
            summarize_disclosures=bool(self.var_summarize.get()),
            exec_summary=bool(self.var_exec_summary.get()),
            business_profile=bool(self.var_biz_profile.get()),
            footnotes=bool(self.var_footnotes.get()),
            da_llm_fallback=bool(self.var_da_llm.get()),
            body_limit=int(self.var_limit.get()) or None,
            years_back=int(self.var_years.get()),
            output_dir=self.var_outdir.get().strip() or ".",
            anthropic_api_key=self.var_key.get().strip() or None,
            anthropic_model=self.var_model.get().strip() or None,
            save_log=bool(self.var_save_log.get()),
        )
        self._running = True
        self.btn_run.config(state="disabled", text="  분석 중…")
        self.lbl_step.config(text="  시작 중…")
        self.lbl_pct.config(text="0%")
        self.progress["value"] = 0
        self.txt.delete("1.0", "end")
        threading.Thread(target=self._worker, args=(cfg,), daemon=True).start()

    def _worker(self, cfg: RunConfig) -> None:
        try:
            result = run_quickreport(cfg, log=self._log)
            self._log("")
            self._log(f"✓ 완료: {result.corp_name}")
            self._log(f"   · 공시 {result.n_disclosures}건 (LLM 분석 {result.n_analyzed}건)")
            self._log(f"   · Excel: {result.excel_path}")
            self._log(f"   · HTML : {result.html_path}")
            self.after(0, lambda: _open_path(cfg.output_dir))
        except Exception as exc:  # noqa: BLE001
            self._log(f"\n[오류] {exc}")
            self._log(traceback.format_exc())
            self.after(0, lambda: messagebox.showerror("실행 오류", str(exc)))
        finally:
            self._running = False
            self.after(0, lambda: self.btn_run.config(
                state="normal", text="▶  분석 시작"))


def main() -> None:
    App().mainloop()
