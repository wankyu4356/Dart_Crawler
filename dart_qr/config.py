# -*- coding: utf-8 -*-
"""공통 상수 / 환경변수 로드."""
from __future__ import annotations
import os

# ── DART OpenAPI ──────────────────────────────────────────────────────────
# 1차 우선: 환경변수. 없으면 v10에서 쓰던 기본 키 사용 (개인 키라 배포 전 교체 권장).
# DART OpenAPI 키 — 기본 키 + E&F PE 전용 키 (2개 중 선택)
DEFAULT_DART_KEY = "ba43f1b18f4b189c4a6652a12632d5220618dfa5"
ENF_PE_DART_KEY  = "aa43415e31ef66e6ae4d5402d2c02ebd8f9177ee"

# 런타임 전환용 — dart_api 모듈은 config.DART_API_KEY 를 실시간 참조
DART_API_KEY: str = os.getenv("DART_API_KEY", DEFAULT_DART_KEY)


def use_enf_pe_key(enabled: bool = True) -> None:
    """GUI 의 'E&F PE' 체크박스 변경 시 호출 — API 키 런타임 교체."""
    global DART_API_KEY
    DART_API_KEY = ENF_PE_DART_KEY if enabled else DEFAULT_DART_KEY
DART_BASE_URL: str = "https://opendart.fss.or.kr/api"

# ── Anthropic ────────────────────────────────────────────────────────────
# API Key 우선순위: 1) 환경변수 → 2) exe 옆 api_key.txt 파일 → 3) 빈 문자열
def _load_api_key_from_file() -> str:
    """exe 또는 스크립트와 같은 폴더에 api_key.txt 가 있으면 읽어서 유효 키 반환.

    - 주석 라인 (# 으로 시작) 건너뛰기
    - 빈 줄 건너뛰기
    - Anthropic 키 형식 (`sk-ant-` prefix + ASCII) 만 채택
    - 조건 맞는 첫 줄을 찾지 못하면 빈 문자열 반환 (잘못된 값 반환 금지)
    """
    import sys
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.getcwd()
    for name in ("api_key.txt", "anthropic_api_key.txt"):
        p = os.path.join(base, name)
        if not os.path.isfile(p):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                for raw_line in f:
                    # BOM 제거 + 양끝 공백 제거
                    line = raw_line.lstrip("\ufeff").strip()
                    if not line:
                        continue
                    if line.startswith("#"):
                        continue
                    # ASCII 여야 하고 sk-ant- prefix 있어야 함
                    if not line.startswith("sk-ant-"):
                        continue
                    if not line.isascii():
                        continue
                    return line
        except Exception:
            continue
    return ""


def _ensure_api_key_file() -> None:
    """exe 폴더에 api_key.txt 가 없으면 안내 텍스트가 담긴 빈 파일 생성.

    안내 텍스트는 모두 `#` 주석 라인 — `_load_api_key_from_file()` 이
    자동 skip 하므로 안전 (이전엔 첫 주석 라인이 키로 오인되어 HTTP 헤더
    `x-api-key` 값이 한글 포함 → latin-1 인코딩 크래시의 원인이었음).
    """
    import sys
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.getcwd()
    p = os.path.join(base, "api_key.txt")
    if not os.path.isfile(p):
        try:
            with open(p, "w", encoding="utf-8") as f:
                f.write("# Anthropic API Key 를 아래 줄에 붙여넣으세요 (sk-ant-...)\n")
                f.write("# 저장 후 프로그램을 다시 실행하면 자동으로 읽어옵니다.\n")
        except Exception:
            pass


_ensure_api_key_file()


def _validate_anthropic_key(key: str) -> bool:
    """sk-ant- prefix + ASCII 확인. 유효하면 True."""
    if not key:
        return False
    if not key.startswith("sk-ant-"):
        return False
    if not key.isascii():
        return False
    if len(key) < 20:
        return False
    return True


_env_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
# 환경변수도 한글/잘못된 값일 수 있으므로 검증
if not _validate_anthropic_key(_env_key):
    _env_key = ""
ANTHROPIC_API_KEY: str = _env_key or _load_api_key_from_file()
# 기본 모델. Haiku 는 비용/속도 우선, Sonnet/Opus 는 품질 우선.
ANTHROPIC_MODEL: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")

# GUI 드롭다운 표시용 모델 목록. (첫번째 = 기본)
AVAILABLE_MODELS = [
    "claude-sonnet-4-5",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
    "claude-3-5-sonnet-latest",
    "claude-3-5-haiku-latest",
]

# ── 공시 유형 분류 ────────────────────────────────────────────────────────
# pblntf_ty prefix 기준. LLM 본문 요약 대상 ("중요 공시")
IMPORTANT_PBLNTF_TY = {"A", "B", "D"}  # 정기·주요사항·지분
PBLNTF_TY_LABEL = {
    "A": "정기공시",
    "B": "주요사항",
    "C": "발행공시",
    "D": "지분공시",
    "E": "기타공시",
    "F": "외부감사",
    "G": "펀드",
    "H": "자산유동화",
    "I": "거래소",
    "J": "공정위",
}

# ── 보고서 코드 ──────────────────────────────────────────────────────────
REPRT_CODE = {
    "Q1":  "11013",
    "H1":  "11012",
    "Q3":  "11014",
    "FY":  "11011",  # 사업보고서
}

# 네트워크
REQUEST_TIMEOUT = 30
REQUEST_DELAY   = 0.15   # 호출 간 최소 간격 (초)
MAX_RETRIES     = 3

# DART 뷰어 링크 포맷
DART_VIEWER_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"

# 문의 채널 (GUI/리포트 전반에 노출)
CONTACT_EMAIL = "wankyu.kim@yonsei.ac.kr"
CONTACT_NAME = "Company Snapshot"
