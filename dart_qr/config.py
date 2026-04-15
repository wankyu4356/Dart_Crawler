# -*- coding: utf-8 -*-
"""공통 상수 / 환경변수 로드."""
from __future__ import annotations
import os

# ── DART OpenAPI ──────────────────────────────────────────────────────────
# 1차 우선: 환경변수. 없으면 v10에서 쓰던 기본 키 사용 (개인 키라 배포 전 교체 권장).
DART_API_KEY: str = os.getenv(
    "DART_API_KEY",
    "ba43f1b18f4b189c4a6652a12632d5220618dfa5",
)
DART_BASE_URL: str = "https://opendart.fss.or.kr/api"

# ── Anthropic ────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
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
CONTACT_NAME = "완규의 딸깍공장"
