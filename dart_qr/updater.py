# -*- coding: utf-8 -*-
"""M12 — 자가 업데이트.

  • GitHub Releases `latest` 의 tag 를 현재 `__version__` 과 비교.
  • 다르면 동일 release 의 .exe 자산을 다운로드 → `.exe.new` 로 저장 →
    배치 스크립트가 잠시 sleep → 기존 .exe 삭제 → .new 를 .exe 로 rename →
    새 .exe 실행. 현재 프로세스는 종료.
  • PyInstaller frozen 환경에서만 동작. dev 실행 (python script) 은 skip.

`bootstrap_update_or_pass(splash_log=print)` 한 번만 호출하면 끝.
"""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from typing import Callable, Optional, Tuple

from . import __version__

GITHUB_REPO = "wankyu4356/Dart_Crawler"
LATEST_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
ASSET_NAME = "DART_QuickReport.exe"

# 사용자 환경변수로 비활성화 (개발/디버그 용)
DISABLE_ENV = "DART_QR_NO_UPDATE"


# ── 환경 판별 ───────────────────────────────────────────────────────────
def is_frozen() -> bool:
    """PyInstaller --onefile 로 만들어진 실행환경 여부."""
    return getattr(sys, "frozen", False)


def current_exe_path() -> Optional[str]:
    if not is_frozen():
        return None
    return os.path.abspath(sys.executable)


# ── GitHub 조회 ─────────────────────────────────────────────────────────
def fetch_latest_release(timeout: float = 10.0) -> Optional[dict]:
    req = urllib.request.Request(
        LATEST_API,
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": f"DART-QuickReport/{__version__}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return None
    except Exception:  # noqa: BLE001
        return None


def _normalize_version(s: str) -> str:
    s = (s or "").strip()
    if s.lower().startswith("v"):
        s = s[1:]
    return s


def find_exe_asset(release: dict) -> Optional[Tuple[str, str]]:
    """release dict 에서 (asset_name, download_url) 추출."""
    for a in release.get("assets") or []:
        name = a.get("name") or ""
        if name == ASSET_NAME:
            return name, a.get("browser_download_url")
    return None


# ── 다운로드 ────────────────────────────────────────────────────────────
def _download(url: str, dest: str, log: Callable[[str], None]) -> bool:
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": f"DART-QuickReport/{__version__}"})
        with urllib.request.urlopen(req, timeout=60) as resp, \
                open(dest, "wb") as f:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = resp.read(1024 * 64)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total:
                    pct = done * 100 // total
                    log(f"  다운로드 {pct}% ({done/1e6:.1f}/{total/1e6:.1f} MB)")
                else:
                    log(f"  다운로드 {done/1e6:.1f} MB")
        return True
    except Exception as exc:  # noqa: BLE001
        log(f"  ⚠ 다운로드 실패: {exc}")
        return False


# ── self-replace 배치 ─────────────────────────────────────────────────
# NOTE: "Failed to load Python DLL _MEIxxxxx\python311.dll" 방지:
#   - 이전 프로세스가 _MEI 임시폴더를 해제할 시간을 충분히 준다 (5s).
#   - taskkill 로 확실히 종료시킨 뒤 교체 시도.
#   - 교체 후에도 OS 의 DLL 캐시 안정화를 위해 약간 대기 후 재시작.
_REPLACE_BAT = r"""@echo off
setlocal enabledelayedexpansion
set "TARGET={target}"
set "NEWFILE={newfile}"

rem 혹시 남아있을 수 있는 기존 프로세스 강제 종료 (MEI 잠금 해제)
taskkill /F /IM "DART_QuickReport.exe" >nul 2>&1
timeout /t 5 /nobreak >nul

set /a tries=0
:wait_unlock
del "%TARGET%" >nul 2>&1
if exist "%TARGET%" (
    set /a tries+=1
    if !tries! GEQ 45 goto :give_up
    timeout /t 2 /nobreak >nul
    goto :wait_unlock
)

move /y "%NEWFILE%" "%TARGET%" >nul
if errorlevel 1 goto :give_up

rem 새 .exe 를 바로 실행하면 이전 _MEI 잔존 파일과 충돌할 수 있어 추가 대기
timeout /t 2 /nobreak >nul
start "" "%TARGET%"
del "%~f0" >nul 2>&1
exit /b 0

:give_up
echo [DART_QuickReport] 자동 업데이트에 실패했습니다. 새 파일은 다음 위치에 있습니다:
echo   %NEWFILE%
echo 직접 기존 파일을 닫고 새 파일로 교체한 후 실행해 주세요.
pause
exit /b 1
"""


def _spawn_replace_and_exit(
    current_exe: str, new_exe: str, log: Callable[[str], None]
) -> None:
    bat_path = os.path.join(tempfile.gettempdir(),
                            f"dartqr_update_{os.getpid()}.bat")
    with open(bat_path, "w", encoding="cp949", errors="ignore") as f:
        f.write(_REPLACE_BAT.format(
            target=current_exe.replace("/", "\\"),
            newfile=new_exe.replace("/", "\\"),
        ))
    log("  새 .exe 적용을 위해 자기 종료 후 재시작합니다…")
    # CREATE_NEW_PROCESS_GROUP + DETACHED_PROCESS = 콘솔 떼고 실행
    DETACHED = 0x00000008
    NEW_PG   = 0x00000200
    try:
        subprocess.Popen(
            ["cmd", "/c", bat_path],
            close_fds=True,
            creationflags=DETACHED | NEW_PG,
        )
    except Exception:
        # 안전망: shell 실행
        subprocess.Popen(["cmd", "/c", bat_path], close_fds=True, shell=False)
    # 약간의 race 회피 — Tk 종료 시간 확보 후 os._exit 로 즉시 종료
    # (sys.exit 은 Python cleanup 이 돌면서 _MEI 파일 핸들을 늦게 닫는 경우
    #  다음 런치의 DLL 로드를 방해할 수 있음)
    time.sleep(0.4)
    try:
        os._exit(0)
    except Exception:
        sys.exit(0)


# ── 메인 진입점 ─────────────────────────────────────────────────────────
def bootstrap_update_or_pass(log: Callable[[str], None] = print) -> None:
    """앱 GUI 띄우기 직전에 호출. 업데이트 발견 시 본 함수 안에서 종료."""
    if os.environ.get(DISABLE_ENV):
        return
    exe = current_exe_path()
    if not exe:
        return  # frozen 아님 (개발 중)

    log(f"[updater] 현재 버전 {__version__} — 최신 버전 확인 중…")
    rel = fetch_latest_release()
    if not rel:
        log("[updater] 최신 버전 확인 실패 (네트워크/Release 없음). 그대로 진행.")
        return
    latest = _normalize_version(rel.get("tag_name") or "")
    if not latest:
        log("[updater] tag_name 없음. 스킵.")
        return
    if _normalize_version(__version__) == latest:
        log(f"[updater] 이미 최신({latest}). 그대로 진행.")
        return

    asset = find_exe_asset(rel)
    if not asset:
        log(f"[updater] 새 버전 {latest} 가 있지만 .exe 자산을 못 찾음. 스킵.")
        return
    name, url = asset
    log(f"[updater] 새 버전 발견: {__version__} → {latest}")
    log(f"  자산: {name}")

    new_path = exe + ".new"
    if not _download(url, new_path, log):
        try:
            os.remove(new_path)
        except OSError:
            pass
        log("[updater] 업데이트를 건너뛰고 현재 버전으로 진행합니다.")
        return

    # 자기 교체 + 재시작
    _spawn_replace_and_exit(exe, new_path, log)
