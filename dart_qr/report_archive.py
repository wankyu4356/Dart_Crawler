# -*- coding: utf-8 -*-
"""M-archive — 사업보고서/감사보고서를 Zip 으로 묶어 저장.

DART 가 PDF 를 직접 제공하지 않으므로:
- `document.xml` 엔드포인트로 원본 HTML/XML 패키지 zip 을 다운로드
- 보고서별로 마스터 ZIP 안에 폴더로 정리
- 사용자가 압축 해제 후 HTML 을 브라우저로 열어 Ctrl+P → PDF 저장 가능

폴더 구조 (마스터 ZIP 내부):
  보고서묶음_회사명_날짜.zip
    ├── README.txt                            # 사용 안내
    ├── 사업보고서_2024-12_20250313001313/
    │     ├── 0001_본문.html
    │     ├── 0002_연결재무제표.html
    │     └── ...
    ├── 감사보고서_2024-12_.../
    └── 반기보고서_2024-06_.../
"""
from __future__ import annotations
import io
import os
import re
import zipfile
from datetime import datetime
from typing import Callable, Iterable, List, Optional

from . import dart_api as api
from .disclosures import Disclosure


LogFn = Callable[[str], None]

# 어떤 보고서를 패킹할 것인지 — 정기/감사 위주
_PACK_KEYWORDS = (
    "사업보고서", "반기보고서", "분기보고서",
    "감사보고서", "연결감사보고서",
)


def _select_reports(discs: Iterable[Disclosure]) -> List[Disclosure]:
    out = []
    seen = set()
    for d in discs:
        nm = d.report_nm or ""
        if not any(k in nm for k in _PACK_KEYWORDS):
            continue
        # rcept_no 단위 dedup
        if d.rcept_no in seen:
            continue
        seen.add(d.rcept_no)
        out.append(d)
    # 최신 접수 순
    out.sort(key=lambda d: (d.rcept_dt or "", d.rcept_no), reverse=True)
    return out


_SAFE_RE = re.compile(r'[\\/:*?"<>|]')


def _safe_name(s: str) -> str:
    return _SAFE_RE.sub("_", (s or "").strip())[:80]


def _entry_folder(d: Disclosure) -> str:
    """ZIP 안 하위 폴더명. e.g. '사업보고서_2024-12_20250313001313'"""
    nm = d.report_nm or "보고서"
    nm_clean = _safe_name(nm).replace(" ", "")
    return f"{nm_clean}_{d.rcept_no}"


_README = """\
DART 공시 보고서 패키지
========================

이 ZIP 은 DART OpenAPI 를 통해 다운로드한 사업보고서/감사보고서/반기·분기보고서
원본입니다. 폴더별로 구분되어 있으며 각 폴더 안에는 HTML/XML 형식의 본문이
들어있습니다.

PDF 로 변환하려면:
  1) 폴더 안 .html 파일을 더블클릭 (브라우저로 열림)
  2) Ctrl+P (인쇄 → 대상: PDF 로 저장)

원본 그대로 DART 형식이며, 별도 가공 없이 보존됩니다.
"""


def pack_disclosure_archive(
    disclosures: Iterable[Disclosure],
    output_path: str,
    log: LogFn = print,
    limit: Optional[int] = None,
) -> Optional[str]:
    """선택된 보고서들을 다운로드해 마스터 ZIP 으로 묶어 저장.

    반환: 저장된 ZIP 경로. 보고서 0건이면 None 반환 (생성 안 함).
    """
    targets = _select_reports(disclosures)
    if limit is not None:
        targets = targets[:limit]
    if not targets:
        log(f"  → 패킹할 보고서 없음 (사업/감사/반기/분기보고서 0건)")
        return None

    log(f"  → 패킹 대상 {len(targets)}건 — DART 에서 다운로드 중")
    success = 0
    failed = 0
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zout:
        # README
        zout.writestr("README.txt", _README)
        for i, d in enumerate(targets, 1):
            log(f"    [{i}/{len(targets)}] {d.rcept_dt} {d.report_nm[:36]}")
            try:
                blob = api.document_zip(d.rcept_no)
            except Exception as exc:  # noqa: BLE001
                log(f"      ⚠ 다운로드 오류: {exc}")
                failed += 1
                continue
            if not blob:
                log(f"      ⚠ 빈 응답")
                failed += 1
                continue
            folder = _entry_folder(d)
            # 받은 ZIP 을 다시 풀어서 마스터 ZIP 안에 폴더로 저장
            try:
                with zipfile.ZipFile(io.BytesIO(blob)) as zin:
                    for name in zin.namelist():
                        # 디렉터리 엔트리는 스킵
                        if name.endswith("/"):
                            continue
                        try:
                            data = zin.read(name)
                        except Exception:  # noqa: BLE001
                            continue
                        # 파일명 ASCII safe
                        safe = name.replace("\\", "/").lstrip("/")
                        zout.writestr(f"{folder}/{safe}", data)
                success += 1
            except zipfile.BadZipFile:
                # ZIP 이 아닌 경우 그대로 저장
                zout.writestr(f"{folder}/raw.bin", blob)
                success += 1
    log(f"  ✓ ZIP 저장: {output_path} (성공 {success} / 실패 {failed})")
    return output_path
