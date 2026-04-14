# -*- coding: utf-8 -*-
"""DART QuickReport — 엔트리 포인트 (PyInstaller --onefile).

GUI 기본. `--cli` 플래그로 CLI 사용. 빌드된 .exe 는 시작 시 GitHub Release
에서 최신 버전을 자동 확인하고, 필요하면 자기 자신을 교체 후 재시작.

사용 예 (개발):
    python dart_quickreport.py                    # GUI
    python dart_quickreport.py --cli 삼성전자 -p 2 -u 년
"""
from __future__ import annotations
import argparse
import sys

# .env 자동 로드 (있을 때만)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


def _run_cli(args: argparse.Namespace) -> int:
    from dart_qr.orchestrator import RunConfig, run_quickreport
    cfg = RunConfig(
        company=args.company,
        period_value=args.period,
        period_unit=args.unit,
        analyze_bodies=not args.no_llm,
        body_limit=args.limit,
        years_back=args.years,
        output_dir=args.outdir,
    )
    res = run_quickreport(cfg, log=print)
    print(f"\n✓ 완료: {res.corp_name}")
    print(f"   Excel: {res.excel_path}")
    print(f"   HTML : {res.html_path}")
    return 0


def _run_gui() -> int:
    """업데이트 체크 (Splash 표시) → 본 GUI 진입."""
    from dart_qr.splash import Splash
    from dart_qr.updater import bootstrap_update_or_pass

    splash = Splash()
    try:
        bootstrap_update_or_pass(log=splash.log)
        # 업데이트가 있었으면 위에서 sys.exit() 했으므로 아래로 못 옴
        splash.log("준비 완료. 메인 화면을 띄우는 중…")
    finally:
        splash.close()

    from dart_qr.app import main as gui_main
    gui_main()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="DART QuickReport")
    parser.add_argument("--cli", action="store_true",
                        help="GUI 대신 CLI 실행")
    parser.add_argument("--no-update", action="store_true",
                        help="자가 업데이트 확인 건너뛰기")
    parser.add_argument("--version", action="store_true",
                        help="버전 출력 후 종료")
    parser.add_argument("company", nargs="?", default="",
                        help="회사명 (CLI 모드 필수)")
    parser.add_argument("-p", "--period", type=int, default=2,
                        help="기간 숫자 (기본 2)")
    parser.add_argument("-u", "--unit", choices=["년", "개월"], default="년",
                        help="기간 단위")
    parser.add_argument("--years", type=int, default=4,
                        help="연간 재무 조회 연수")
    parser.add_argument("--limit", type=int, default=20,
                        help="LLM 본문 분석 최대 건수")
    parser.add_argument("--no-llm", action="store_true",
                        help="본문 분석·요약 끄기 (제목만)")
    parser.add_argument("-o", "--outdir", default=".",
                        help="출력 폴더")
    args = parser.parse_args()

    if args.version:
        from dart_qr import __version__
        print(f"DART QuickReport {__version__}")
        return 0

    if args.no_update:
        import os
        os.environ["DART_QR_NO_UPDATE"] = "1"

    if args.cli:
        if not args.company:
            parser.error("--cli 모드에서는 회사명이 필요합니다.")
        return _run_cli(args)

    return _run_gui()


if __name__ == "__main__":
    sys.exit(main())
