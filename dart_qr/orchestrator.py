# -*- coding: utf-8 -*-
"""M10-A — 전체 파이프라인 오케스트레이션 (GUI/CLI 공통).

`run_quickreport()` 가 1건의 회사에 대해 수집→LLM 분석→리포트 생성을 수행.
GUI 는 이 함수를 스레드에서 호출만 하면 된다.
"""
from __future__ import annotations
import os
from dataclasses import dataclass
from datetime import date
from typing import Callable, Optional

from . import corp as corp_mod
from . import dart_api as api
from . import disclosures as disc_mod
from . import financials as fin_mod
from . import llm as llm_mod
from . import profile as profile_mod
from . import shareholders as sh_mod
from . import unlisted as unlisted_mod
from .excel_out import write_excel
from .html_out import write_html


LogFn = Callable[[str], None]


@dataclass
class RunConfig:
    company: str
    period_value: int = 2
    period_unit: str = "년"          # '년' | '개월'
    analyze_bodies: bool = True       # 중요 공시 본문 분석 여부
    body_limit: Optional[int] = 20    # LLM 호출 상한 (None = 무제한)
    years_back: int = 4               # 연간 재무 조회 연수
    output_dir: str = "."
    anthropic_api_key: Optional[str] = None


@dataclass
class RunResult:
    excel_path: str
    html_path: str
    corp_name: str
    n_disclosures: int
    n_analyzed: int


def run_quickreport(cfg: RunConfig, log: LogFn = print) -> RunResult:
    # 1) 회사 식별
    log(f"[1/7] 회사 조회: {cfg.company}")
    c = corp_mod.search_corp(cfg.company, log=log)

    # 2) Profile
    log(f"[2/7] Company Profile 조회")
    profile = profile_mod.fetch_profile(c.corp_code, corp_name_hint=c.corp_name)
    log(f"  → {profile.corp_name} / {profile.corp_cls_label} / CEO {profile.ceo_nm}")

    # 3) 기간 계산
    bgn_de, end_de, period_label = profile_mod.period_from_value(
        cfg.period_value, cfg.period_unit
    )
    log(f"[3/7] 조회기간: {bgn_de} ~ {end_de} ({period_label})")

    is_listed = (profile.corp_cls or "").upper() in ("Y", "K", "N")

    # 비상장은 감사보고서 파싱에 Claude 가 필요
    llm_client = None
    if cfg.analyze_bodies or not is_listed:
        try:
            llm_client = llm_mod.get_client(cfg.anthropic_api_key)
        except RuntimeError as exc:
            if not is_listed:
                log(f"  ⚠ 비상장 분석에는 ANTHROPIC_API_KEY 가 필요합니다: {exc}")
            llm_client = None

    # 4) 재무
    if is_listed:
        log(f"[4/7] 재무 수집 (최근 {cfg.years_back}년 + 최신 분기)")
        fin = fin_mod.fetch_all(c.corp_code, years_back=cfg.years_back)
    else:
        log(f"[4/7] 비상장사 — 감사보고서 기반 재무 추출")
        fin = unlisted_mod.fetch_financials(
            c.corp_code, years_back=cfg.years_back,
            client=llm_client, log=log,
        )
    log(f"  → 연간 {len(fin.annual)}건, 분기 {'있음' if fin.latest_quarter else '없음'}")

    # 5) 주주/지배구조
    if is_listed:
        log(f"[5/7] 주주·지배구조 수집")
        sh = sh_mod.fetch_all(c.corp_code, bgn_de=bgn_de, end_de=end_de)
    else:
        log(f"[5/7] 비상장사 — 감사보고서 주석 기반 지배구조 추출")
        sh = unlisted_mod.fetch_governance(
            c.corp_code, bgn_de=bgn_de, end_de=end_de,
            client=llm_client, log=log,
        )
    log(f"  → 최대주주 {len(sh.major)} · 대량보유 {len(sh.major_stock)} · "
        f"임원소유 {len(sh.executive_stock)} · 임원 {len(sh.executives)}")

    # 6) 공시
    log(f"[6/7] 공시 목록 조회")
    discs = disc_mod.fetch_list(c.corp_code, bgn_de, end_de)
    important = [d for d in discs if d.is_important]
    log(f"  → 전체 {len(discs)}건, 중요(A/B/D) {len(important)}건")

    exec_summary: Optional[str] = None
    n_analyzed = 0
    if cfg.analyze_bodies and important:
        log(f"  본문 다운로드 (상위 {cfg.body_limit or '전체'}건)")
        disc_mod.fill_bodies_for_important(
            discs, limit=cfg.body_limit, log=log,
        )
        ready = [d for d in discs if d.body]
        if ready and llm_client is not None:
            try:
                log(f"  LLM 요약·Implication 생성 (Claude, 병렬 4)")
                llm_mod.summarize_batch(ready, client=llm_client, log=log)
                n_analyzed = sum(1 for d in discs if d.llm_status == "ok")
                log(f"  → LLM 성공 {n_analyzed}건")
                # Executive Summary
                profile_summary = (
                    f"{profile.corp_name} ({profile.corp_cls_label}), "
                    f"CEO {profile.ceo_nm}, 결산월 {profile.acc_mt}, "
                    f"설립 {profile.est_dt}"
                )
                exec_summary = llm_mod.build_executive_summary(
                    profile_summary, discs, client=llm_client
                )
            except RuntimeError as exc:
                log(f"  ⚠ LLM 건너뜀: {exc}")
        elif ready and llm_client is None:
            log(f"  ⚠ ANTHROPIC_API_KEY 없음 — 공시 본문 요약 건너뜀")
    elif not cfg.analyze_bodies:
        log(f"  본문 분석 꺼짐 (제목만 기록)")

    # 7) 파일 저장
    log(f"[7/7] 리포트 저장")
    os.makedirs(cfg.output_dir, exist_ok=True)
    safe = "".join(ch for ch in profile.corp_name if ch not in '/\\:*?"<>|').strip()
    stamp = date.today().strftime("%Y%m%d")
    base = f"DART_QuickReport_{safe}_{stamp}"
    xlsx_path = os.path.join(cfg.output_dir, base + ".xlsx")
    html_path = os.path.join(cfg.output_dir, base + ".html")
    write_excel(xlsx_path, profile, fin, sh, discs, period_label, exec_summary)
    write_html(html_path, profile, fin, sh, discs, period_label, exec_summary)
    log(f"  ✓ Excel:  {xlsx_path}")
    log(f"  ✓ HTML:  {html_path}")

    return RunResult(
        excel_path=xlsx_path, html_path=html_path,
        corp_name=profile.corp_name,
        n_disclosures=len(discs), n_analyzed=n_analyzed,
    )
