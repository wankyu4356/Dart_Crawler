# -*- coding: utf-8 -*-
"""M13-A — 부팅 시 사용자에게 진행 상황을 보여주는 작은 Tk Splash.

업데이트 체크/다운로드 동안 콘솔이 없는 --windowed 빌드에서도
사용자가 멍하니 기다리지 않도록 한 줄 진행 메시지를 보여준다.
"""
from __future__ import annotations
from typing import Optional


class Splash:
    def __init__(self, title: str = "완규의 딸깍공장") -> None:
        try:
            import tkinter as tk
        except ImportError:
            self._tk = None
            return
        self._tk = tk
        self.root = tk.Tk()
        self.root.title(title)
        self.root.geometry("420x140+600+340")
        self.root.resizable(False, False)
        try:
            self.root.attributes("-topmost", True)
        except Exception:
            pass
        self.root.configure(bg="#1a237e")

        tk.Label(self.root, text="완규의 딸깍공장",
                 fg="#fff", bg="#1a237e",
                 font=("Segoe UI", 14, "bold")).pack(pady=(18, 4))
        tk.Label(self.root, text="DART 공시 자동 분석",
                 fg="#bbdefb", bg="#1a237e",
                 font=("Segoe UI", 9)).pack()
        self._var = tk.StringVar(value="시작 중…")
        tk.Label(self.root, textvariable=self._var,
                 fg="#fff", bg="#283593",
                 font=("Segoe UI", 9), anchor="w",
                 padx=12, pady=8).pack(fill="x", padx=14, pady=(14, 14))
        self.root.update()

    def log(self, msg: str) -> None:
        if not self._tk:
            print(msg)
            return
        # 마지막 한 줄만 표시
        line = msg.strip().splitlines()[-1] if msg.strip() else ""
        try:
            self._var.set(line[:120])
            self.root.update()
        except Exception:
            pass

    def close(self) -> None:
        if not self._tk:
            return
        try:
            self.root.destroy()
        except Exception:
            pass
