# -*- coding: utf-8 -*-
"""DART QuickReport — 엔트리 포인트.

PyInstaller `--onefile` 대상. GUI 기본 실행, `--cli` 플래그로 CLI 실행 가능.

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


def main() -> int:
    parser = argparse.ArgumentParser(description="DART QuickReport")
    parser.add_argument("--cli", action="store_true",
                        help="GUI 대신 CLI 실행")
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

    if args.cli:
        if not args.company:
            parser.error("--cli 모드에서는 회사명이 필요합니다.")
        return _run_cli(args)

    # GUI
    from dart_qr.app import main as gui_main
    gui_main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
