# -*- coding: utf-8 -*-
"""M10-B — Tkinter GUI (리디자인).

디자인 원칙:
  • ttk "clam" 테마 강제 (크로스 플랫폼 일관성)
  • HERO 배너 (남색 그라데이션 느낌) + 입력 카드 + Primary 버튼 + 다크 로그
  • 상태바에 이메일 문의 메모 상시 노출
"""
from __future__ import annotations
import os
import sys
import threading
import traceback

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from . import config as cfgmod
from ._version import __version__
from .orchestrator import RunConfig, run_quickreport


# ── 디자인 토큰 ──────────────────────────────────────────────────────────
PRIMARY   = "#1a237e"
PRIMARY_2 = "#283593"
PRIMARY_3 = "#3949ab"
ACCENT    = "#ff6b35"
ACCENT_2  = "#e65a2a"
INK       = "#1c1f24"
INK_2     = "#37474f"
BG        = "#f4f5f7"
SURFACE   = "#ffffff"
BORDER    = "#e4e7ec"
MUTED     = "#78909c"
LOG_BG    = "#1c1f24"
LOG_FG    = "#cfd8dc"


def _open_path(path: str) -> None:
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
        self.geometry("900x760")
        self.minsize(820, 640)
        self.configure(bg=BG)
        self._running = False
        self._search_after_id = None

        self._apply_style()
        self._build_ui()

    # ── 스타일 적용 ───────────────────────────────────────────────────
    def _apply_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        # HERO
        style.configure("Hero.TFrame", background=PRIMARY)
        style.configure("Hero.TLabel", background=PRIMARY, foreground="#ffffff",
                        font=("Segoe UI", 18, "bold"))
        style.configure("HeroSub.TLabel", background=PRIMARY, foreground="#bbdefb",
                        font=("Segoe UI", 10))
        style.configure("HeroPill.TLabel", background=PRIMARY_2, foreground="#ffffff",
                        font=("Segoe UI", 9), padding=(10, 4))

        # Card
        style.configure("Card.TFrame", background=SURFACE, borderwidth=0)
        style.configure("Card.TLabelframe", background=SURFACE, padding=14,
                        relief="flat", borderwidth=0)
        style.configure("Card.TLabelframe.Label", background=SURFACE,
                        foreground=PRIMARY, font=("Segoe UI", 10, "bold"))

        # 일반 라벨/입력
        style.configure("TLabel", background=SURFACE, foreground=INK,
                        font=("Segoe UI", 10))
        style.configure("Field.TLabel", background=SURFACE, foreground=INK_2,
                        font=("Segoe UI", 9))
        style.configure("Hint.TLabel", background=SURFACE, foreground=MUTED,
                        font=("Segoe UI", 8))
        style.configure("TEntry", fieldbackground=SURFACE, foreground=INK,
                        padding=4)
        style.configure("TCombobox", fieldbackground=SURFACE, padding=3)
        style.configure("TSpinbox", fieldbackground=SURFACE, padding=3)
        style.configure("TCheckbutton", background=SURFACE, foreground=INK,
                        font=("Segoe UI", 10))

        # Primary 버튼
        style.configure("Primary.TButton",
                        background=PRIMARY, foreground="#ffffff",
                        font=("Segoe UI", 12, "bold"),
                        padding=(16, 12),
                        borderwidth=0, relief="flat")
        style.map("Primary.TButton",
                  background=[("active", PRIMARY_3), ("disabled", "#90a4ae")],
                  foreground=[("disabled", "#eceff1")])
        style.configure("Accent.TButton",
                        background=ACCENT, foreground="#ffffff",
                        font=("Segoe UI", 10, "bold"),
                        padding=(10, 6),
                        borderwidth=0, relief="flat")
        style.map("Accent.TButton",
                  background=[("active", ACCENT_2)])
        style.configure("Ghost.TButton",
                        background=SURFACE, foreground=PRIMARY,
                        font=("Segoe UI", 9),
                        padding=(8, 4),
                        borderwidth=1, relief="solid")
        style.map("Ghost.TButton",
                  background=[("active", "#eceff4")])

        # 상태바
        style.configure("Status.TFrame", background=PRIMARY_2)
        style.configure("Status.TLabel", background=PRIMARY_2, foreground="#cfd8dc",
                        font=("Segoe UI", 9))
        style.configure("StatusLink.TLabel", background=PRIMARY_2, foreground="#ffab91",
                        font=("Segoe UI", 9, "bold"))

    # ── UI 구성 ───────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        # ─── 1) HERO 배너
        hero = ttk.Frame(self, style="Hero.TFrame", padding=(24, 18, 24, 18))
        hero.pack(fill="x")
        ttk.Label(hero, text="완규의 딸깍공장",
                  style="Hero.TLabel").pack(anchor="w")
        ttk.Label(hero, text="DART 공시 자동 분석 · 클릭 한 번으로 기업 리포트",
                  style="HeroSub.TLabel").pack(anchor="w", pady=(2, 8))
        ttk.Label(hero, text=f"v{__version__}  ·  문의: {cfgmod.CONTACT_EMAIL}",
                  style="HeroPill.TLabel").pack(anchor="w")

        # ─── 2) 본문 영역 (카드 래퍼)
        wrap = ttk.Frame(self, padding=(18, 12, 18, 0))
        wrap.configure(style="Card.TFrame")
        wrap.pack(fill="x")

        # Card 1: 분석 대상
        card1 = ttk.Labelframe(wrap, text=" 분석 대상 ", style="Card.TLabelframe")
        card1.pack(fill="x", pady=(0, 10))

        ttk.Label(card1, text="회사명", style="Field.TLabel").grid(
            row=0, column=0, sticky="w", padx=4, pady=6)
        self.var_company = tk.StringVar(value="")
        self.cmb_company = ttk.Combobox(card1, textvariable=self.var_company, width=40)
        self.cmb_company.grid(row=0, column=1, columnspan=3, sticky="we", padx=4)
        self.cmb_company.bind("<KeyRelease>", self._on_company_key)

        ttk.Label(card1, text="조회기간", style="Field.TLabel").grid(
            row=1, column=0, sticky="w", padx=4, pady=6)
        self.var_period = tk.IntVar(value=3)
        ttk.Spinbox(card1, from_=1, to=120, width=6,
                    textvariable=self.var_period).grid(row=1, column=1, sticky="w", padx=4)
        self.var_unit = tk.StringVar(value="년")
        ttk.Combobox(card1, textvariable=self.var_unit, values=["년", "개월"],
                     width=6, state="readonly").grid(row=1, column=2, sticky="w", padx=4)

        ttk.Label(card1, text="재무 조회 연수", style="Field.TLabel").grid(
            row=1, column=3, sticky="e", padx=(20, 4))
        self.var_years = tk.IntVar(value=4)
        ttk.Spinbox(card1, from_=1, to=10, width=6,
                    textvariable=self.var_years).grid(row=1, column=4, sticky="w", padx=4)

        card1.grid_columnconfigure(1, weight=1)

        # Card 2: LLM 설정
        card2 = ttk.Labelframe(wrap, text=" LLM 분석 설정 ", style="Card.TLabelframe")
        card2.pack(fill="x", pady=(0, 10))

        self.var_analyze = tk.BooleanVar(value=True)
        ttk.Checkbutton(card2, text="본문 요약 · Implication · 회사개요 분석 (Claude 사용)",
                        variable=self.var_analyze).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=4, pady=4)

        ttk.Label(card2, text="분석 최대 건수", style="Field.TLabel").grid(
            row=0, column=3, sticky="e", padx=(20, 4))
        self.var_limit = tk.IntVar(value=20)
        ttk.Spinbox(card2, from_=1, to=200, width=6,
                    textvariable=self.var_limit).grid(row=0, column=4, sticky="w", padx=4)

        ttk.Label(card2, text="Claude 모델", style="Field.TLabel").grid(
            row=1, column=0, sticky="w", padx=4, pady=6)
        self.var_model = tk.StringVar(value=cfgmod.ANTHROPIC_MODEL)
        ttk.Combobox(card2, textvariable=self.var_model,
                     values=cfgmod.AVAILABLE_MODELS,
                     width=28, state="readonly").grid(
            row=1, column=1, columnspan=2, sticky="w", padx=4)

        ttk.Label(card2, text="Anthropic API Key", style="Field.TLabel").grid(
            row=2, column=0, sticky="w", padx=4, pady=6)
        self.var_key = tk.StringVar(value=cfgmod.ANTHROPIC_API_KEY)
        ttk.Entry(card2, textvariable=self.var_key, show="•", width=56).grid(
            row=2, column=1, columnspan=4, sticky="we", padx=4)

        card2.grid_columnconfigure(1, weight=1)

        # Card 3: 출력
        card3 = ttk.Labelframe(wrap, text=" 출력 ", style="Card.TLabelframe")
        card3.pack(fill="x", pady=(0, 10))

        ttk.Label(card3, text="출력 폴더", style="Field.TLabel").grid(
            row=0, column=0, sticky="w", padx=4, pady=4)
        self.var_outdir = tk.StringVar(value=os.path.expanduser("~/Desktop"))
        ttk.Entry(card3, textvariable=self.var_outdir).grid(
            row=0, column=1, sticky="we", padx=4)
        ttk.Button(card3, text="찾아보기", style="Ghost.TButton",
                   command=self._browse).grid(row=0, column=2, padx=4)
        card3.grid_columnconfigure(1, weight=1)

        # ─── 3) 실행 버튼 (큰 Primary)
        btnwrap = ttk.Frame(self, padding=(18, 4, 18, 8))
        btnwrap.configure(style="Card.TFrame")
        btnwrap.pack(fill="x")
        self.btn_run = ttk.Button(btnwrap, text="▶  분석 시작",
                                  style="Primary.TButton", command=self._start)
        self.btn_run.pack(fill="x")

        # ─── 4) 로그 영역 (다크 배경)
        logwrap = ttk.Frame(self, padding=(18, 0, 18, 8))
        logwrap.configure(style="Card.TFrame")
        logwrap.pack(fill="both", expand=True)
        ttk.Label(logwrap, text="진행 로그", style="Field.TLabel").pack(
            anchor="w", pady=(4, 2))

        log_inner = tk.Frame(logwrap, bg=LOG_BG, bd=0, highlightthickness=0)
        log_inner.pack(fill="both", expand=True)
        self.txt = scrolledtext.ScrolledText(
            log_inner, height=14, wrap="word",
            font=("Consolas", 10),
            bg=LOG_BG, fg=LOG_FG,
            insertbackground=LOG_FG,
            bd=0, padx=10, pady=10,
            highlightthickness=0,
        )
        self.txt.pack(fill="both", expand=True)
        self.txt.tag_configure("ok", foreground="#81c784")
        self.txt.tag_configure("warn", foreground="#ffb74d")
        self.txt.tag_configure("err", foreground="#ef5350")

        # ─── 5) 상태바
        status = ttk.Frame(self, style="Status.TFrame",
                           padding=(16, 6, 16, 6))
        status.pack(fill="x", side="bottom")
        ttk.Label(status,
                  text=f"완규의 딸깍공장  v{__version__}",
                  style="Status.TLabel").pack(side="left")
        ttk.Label(status, text="│", style="Status.TLabel").pack(side="left", padx=8)
        ttk.Label(status,
                  text=f"문의: {cfgmod.CONTACT_EMAIL}",
                  style="StatusLink.TLabel").pack(side="left")

        # 기본 포커스·엔터 바인딩
        self.cmb_company.focus_set()
        self.bind("<Return>", lambda e: self._start())

        self._log("완규의 딸깍공장 준비 완료.  회사명을 입력하고 [▶ 분석 시작] 을 누르세요.")

    # ── 회사명 후보 autocomplete ─────────────────────────────────────
    def _on_company_key(self, event) -> None:
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
            anthropic_model=self.var_model.get().strip() or None,
        )
        self._running = True
        self.btn_run.config(state="disabled", text="  분석 중…")
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
