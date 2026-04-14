# Dart_Crawler

금융감독원 전자공시시스템(DART) OpenAPI 기반 크롤러.

## 요구 사항

- Python 3.10+
- DART OpenAPI 키 ([발급받기](https://opendart.fss.or.kr/))

## 설치

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

## 설정

```bash
cp .env.example .env
# .env 파일을 열어 DART_API_KEY 값을 실제 키로 교체
```

## 실행

```bash
python -m dart_crawler
```

## 테스트

```bash
pytest
```

## 프로젝트 구조

```
.
├── src/dart_crawler/      # 패키지 소스
│   ├── __init__.py
│   ├── __main__.py
│   ├── client.py          # DART API 클라이언트
│   └── config.py          # 환경 변수 로더
├── tests/                 # 테스트
├── .env.example           # 환경 변수 템플릿
├── requirements.txt       # 런타임 의존성
└── requirements-dev.txt   # 개발 의존성
```
