# DART QuickReport — PE Quick Due-Diligence

사모펀드 운용역/임원이 한국 상장·비상장 기업을 **5분 안에 파악**하기 위한
단일 실행파일(.exe) GUI 도구. DART OpenAPI + Anthropic Claude 로
Company Profile · 재무 · 주주현황 · 공시 이슈 요약을 자동으로 모아
**Excel + HTML 리포트 2종**을 만든다.

## 일반 사용자 (개발 지식 불필요)

1. **Releases 페이지**에서 최신 `DART_QuickReport.exe` 1개만 다운로드
   <https://github.com/wankyu4356/Dart_Crawler/releases/latest>
2. 더블클릭으로 실행 — Python 설치 / 의존성 설치 모두 불필요.
3. 첫 화면에서:
   - **회사명** (예: `삼성전자`, `에코프로비엠`)
   - **조회기간**: 숫자 + 단위 드롭다운 (`년` / `개월`)
   - **본문 요약·Implication 분석**: 켜면 Claude로 핵심 공시를 요약(권장)
   - **Anthropic API Key**: 본문 요약을 쓸 때만 필요
   - **출력 폴더**: 결과 파일이 저장될 곳 (기본: 바탕화면)
4. **[분석 시작]** 클릭 → 진행 로그가 실시간 표시.
5. 완료되면 출력 폴더가 자동으로 열리며 `DART_QuickReport_<회사명>_<날짜>.xlsx`
   와 `.html` 두 파일이 생성된다.

### 자동 업데이트
.exe 를 실행하면 시작 직후 GitHub Releases 의 최신 버전을 확인하고,
새 버전이 있으면 **자동으로 다운로드 → 자기 파일을 새 .exe 로 교체 → 재시작**한다.
사용자가 직접 업데이트할 필요가 없다.

업데이트를 끄려면 환경변수 `DART_QR_NO_UPDATE=1` 설정 또는 `--no-update` 플래그.

### 출력물

| 시트(엑셀) | 내용 |
|---|---|
| Profile | 회사 기본정보 (CEO·법인구분·결산월·주소 등) |
| Executive Summary | Claude 가 작성한 경영진 요약 (총평/핵심변화/리스크/기회/DD 포인트) |
| 재무 | 최근 N년 매출·영업이익·당기순이익·자산·부채·자본 + 매출 YoY |
| 재무지표 | 수익성/안정성/성장성/활동성 |
| 주주_최대 | 최대주주 및 특수관계인 |
| 주주_변동 | 최대주주 변동 이력 |
| 주주_대량보유 | 5% rule 보고 |
| 주주_임원소유 | 임원·주요주주 소유보고 |
| 주주_소액 | 소액주주 통계 |
| 임원 / 배당 / 타법인출자 / 감사의견 | 사업보고서 추출 |
| 공시리스트 | 기간 내 모든 공시 + Implication |
| 주요공시상세 | LLM 분석된 핵심 공시 카드 |

HTML 리포트는 위 정보를 ① 헤더 ② Executive Summary ③ 재무 하이라이트
④ 지배구조 ⑤ 이슈 타임라인 ⑥ 핵심 이슈 카드 순으로 보여준다.

## API 키 안내

- **DART OpenAPI Key**: <https://opendart.fss.or.kr/> 에서 무료 발급.
  현재 빌드에는 데모용 키가 박혀 있으나 본인 키로 환경변수 `DART_API_KEY`
  를 설정하면 우선 사용된다.
- **Anthropic API Key**: <https://console.anthropic.com/> 에서 발급.
  GUI 입력란에 직접 입력하거나, 환경변수 `ANTHROPIC_API_KEY` / `.env` 파일.

## 개발자 — 로컬 빌드

```bat
build.bat
```
→ `dist\DART_QuickReport.exe` 생성. 빌드 시 버전은 `local` 로 박혀 자동 업데이트가
항상 발동하도록 설정된다.

CLI 모드도 지원:
```bash
python dart_quickreport.py --cli 삼성전자 -p 2 -u 년 --limit 20
```

## 개발자 — 자동 빌드 / 배포 (GitHub Actions)

`main` 브랜치에 코드가 푸시되면 `.github/workflows/build-exe.yml` 이
자동 실행되어:

1. Windows runner 에서 `_version.py` 를 `0.<run_number>.<sha7>` 로 교체
2. PyInstaller `--onefile --windowed` 로 .exe 빌드
3. `vX.Y.Z` 태그로 GitHub Release 생성 + `DART_QuickReport.exe` 업로드

→ 사용자는 항상 Releases 페이지에서 최신 .exe 한 개만 받으면 된다.

수동 빌드: Actions 탭 → "Build Windows .exe" → Run workflow.

## 모듈 구조

```
dart_qr/
  config.py        DART/Anthropic 키 + 엔드포인트 상수
  corp.py          corpCode.xml 캐시 + 회사명 검색
  dart_api.py      DART OpenAPI 공통 GET + 엔드포인트 wrapper 18종
  profile.py       기업개황 + 기간(년/개월) 역산
  financials.py    최근 N년 + 최신 분기 + 4대 지표
  shareholders.py  주주/지배구조 9종 동시 수집
  disclosures.py   list.json 페이징 + document.xml ZIP 본문 추출
  llm.py           Anthropic Claude (prompt caching, 병렬 4)
  excel_out.py     openpyxl 다중 시트
  html_out.py      리포트 HTML
  orchestrator.py  파이프라인 1-call
  app.py           Tkinter GUI
  splash.py        부팅 시 진행 splash
  updater.py       GitHub Release 자가 업데이트
  _version.py      빌드 시점 버전 (CI 에서 교체)

dart_quickreport.py  엔트리 포인트 (PyInstaller --onefile 대상)
build.bat            로컬 Windows 빌드
.github/workflows/build-exe.yml  Windows .exe 자동 빌드 + Release
```
