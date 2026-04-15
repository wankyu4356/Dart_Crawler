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

from . import business as biz_mod
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
    anthropic_model: Optional[str] = None   # None 이면 config.ANTHROPIC_MODEL 사용


@dataclass
class RunResult:
    excel_path: str
    html_path: str
    corp_name: str
    n_disclosures: int
    n_analyzed: int


def _fill_da_from_body(
    fin, discs, corp_code: str, is_listed: bool,
    client, model: Optional[str], log: LogFn,
) -> None:
    """API 로 D&A 를 못 잡은 연도를 최후의 LLM 본문 파싱으로 채움.

    감사보고서/사업보고서 1건의 본문을 Claude 에 던져 [{year, dep, amort}]
    배열로 추출. 해당 연도의 YearFin.values 에 주입 + EBITDA 파생 재계산.
    """
    # 1) raw_rows 피벗 (무비용)
    raw_filled = fin_mod.fill_da_from_raw(fin)
    if raw_filled:
        log(f"  D&A raw 피벗: {raw_filled}개 항목 보강 (API raw 재활용, 무비용)")

    missing_years = [
        y.year for y in fin.annual
        if y.values.get("da") is None
    ]
    if not missing_years:
        log(f"  D&A 모든 연도 확보 — LLM fallback 생략")
        return
    log(f"  D&A 본문 LLM fallback: {len(missing_years)}개 연도 누락 "
        f"({', '.join(str(y) for y in missing_years)})")

    candidates = biz_mod.pick_source_reports(
        discs, is_listed, corp_code, log=log, limit=4,
    )
    if not candidates:
        log(f"    보고서 없음 → D&A 보강 skip")
        return
    from . import disclosures as _d

    aggregated: Dict[int, Dict[str, Any]] = {}
    for idx, src in enumerate(candidates, 1):
        rcept_no = src.get("rcept_no")
        if not rcept_no:
            continue
        tag = " (정정본)" if src.get("_amended") else ""
        log(f"    [{idx}/{len(candidates)}] {src.get('report_nm','')}{tag} "
            f"({rcept_no})")
        body_full = _d.fetch_body(rcept_no, cap=600000)
        if not body_full or len(body_full) < 1000:
            log(f"      본문 부족 → 다음 후보")
            continue
        body = biz_mod.slice_da_relevant(body_full, cap=60000)
        log(f"      본문 {len(body_full):,}자 → D&A 관련 {len(body):,}자")
        try:
            parsed = llm_mod.extract_da_from_body(
                body, client=client, **({"model": model} if model else {}),
            )
        except Exception as exc:  # noqa: BLE001
            log(f"      LLM 오류: {exc}")
            continue
        for d in parsed or []:
            y = d.get("year")
            try:
                y = int(y)
            except (TypeError, ValueError):
                continue
            if y in aggregated:
                continue  # 먼저 본 것 유지
            aggregated[y] = d
        # 모든 누락 연도가 채워졌으면 조기 종료
        if all(yr in aggregated for yr in missing_years):
            log(f"      → 모든 누락 연도 확보, 순회 종료")
            break

    if not aggregated:
        log(f"    → D&A 보강 수확 없음")
        return

    filled = 0
    for yf in fin.annual:
        if yf.values.get("da") is not None:
            continue
        d = aggregated.get(yf.year)
        if not d:
            continue
        dep = d.get("dep") if isinstance(d.get("dep"), (int, float)) else None
        amort = d.get("amort") if isinstance(d.get("amort"), (int, float)) else None
        if dep is None and amort is None:
            continue
        yf.values["dep"] = dep
        yf.values["amort"] = amort
        yf.values["da"] = (dep or 0.0) + (amort or 0.0)
        op = yf.values.get("op_income")
        if op is not None:
            yf.values["ebitda"] = op + yf.values["da"]
            rev = yf.values.get("revenue")
            if rev:
                yf.values["ebitdam"] = yf.values["ebitda"] / rev * 100.0
        filled += 1
    log(f"    → D&A {filled}개년 보강 완료")


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

    # 4) 공시 목록 먼저 조회 — 비상장 경로의 감사보고서 찾기에 재활용
    log(f"[4/7] 공시 목록 조회")
    discs = disc_mod.fetch_list(c.corp_code, bgn_de, end_de)
    important = [d for d in discs if d.is_important]
    log(f"  → 전체 {len(discs)}건, 중요(A/B/D) {len(important)}건")

    # 5) 재무
    if is_listed:
        log(f"[5/7] 재무 수집 (최근 {cfg.years_back}년 + 최신 분기)")
        fin = fin_mod.fetch_all(c.corp_code, years_back=cfg.years_back)
    else:
        log(f"[5/7] 비상장사 — 감사보고서 기반 재무 추출")
        fin = unlisted_mod.fetch_financials(
            c.corp_code, years_back=cfg.years_back,
            client=llm_client, log=log, disclosures=discs,
        )
    log(f"  → 연간 {len(fin.annual)}건, 분기 {'있음' if fin.latest_quarter else '없음'}")

    # 6) 주주/지배구조
    if is_listed:
        log(f"[6/7] 주주·지배구조 수집")
        sh = sh_mod.fetch_all(c.corp_code, bgn_de=bgn_de, end_de=end_de)
    else:
        log(f"[6/7] 비상장사 — 감사보고서 주석 기반 지배구조 추출")
        sh = unlisted_mod.fetch_governance(
            c.corp_code, bgn_de=bgn_de, end_de=end_de,
            client=llm_client, log=log, disclosures=discs,
        )
    log(f"  → 최대주주 {len(sh.major)} · 대량보유 {len(sh.major_stock)} · "
        f"임원소유 {len(sh.executive_stock)} · 임원 {len(sh.executives)}")

    # 비상장이면 감사보고서도 LLM 요약 대상에 포함 (is_important 외)
    llm_targets = list(important)
    if not is_listed:
        for d in discs:
            if d in llm_targets:
                continue
            if "감사보고서" in (d.report_nm or ""):
                llm_targets.append(d)

    # 사용할 Claude 모델
    model_name = cfg.anthropic_model or __import__("dart_qr.config", fromlist=["ANTHROPIC_MODEL"]).ANTHROPIC_MODEL

    exec_summary: Optional[str] = None
    n_analyzed = 0
    if cfg.analyze_bodies and llm_targets:
        log(f"  LLM 대상 {len(llm_targets)}건 본문 다운로드 (상위 {cfg.body_limit or '전체'}건)")
        # fill_bodies_for_important 는 is_important 만 보므로, 직접 body 채우기
        from . import disclosures as _d
        cap_per_target = 30000 if is_listed else 60000  # 비상장 감사보고서는 더 크게
        limited = llm_targets[: cfg.body_limit] if cfg.body_limit else llm_targets
        for i, d in enumerate(limited, 1):
            if d.body:
                continue
            log(f"    [{i}/{len(limited)}] {d.rcept_dt} {d.report_nm[:40]}")
            d.body = _d.fetch_body(d.rcept_no, cap=cap_per_target)

        ready = [d for d in limited if d.body]
        if ready and llm_client is not None:
            try:
                log(f"  LLM 요약·Implication 생성 ({model_name}, 병렬 4)")
                llm_mod.summarize_batch(
                    ready, client=llm_client, log=log, model=model_name,
                )
                n_analyzed = sum(1 for d in discs if d.llm_status == "ok")
                log(f"  → LLM 성공 {n_analyzed}건")
                # Executive Summary
                profile_summary = (
                    f"{profile.corp_name} ({profile.corp_cls_label}), "
                    f"CEO {profile.ceo_nm}, 결산월 {profile.acc_mt}, "
                    f"설립 {profile.est_dt}"
                )
                exec_summary = llm_mod.build_executive_summary(
                    profile_summary, discs, client=llm_client, model=model_name,
                )
            except Exception as exc:  # noqa: BLE001
                log(f"  ⚠ LLM 오류: {exc}")
        elif ready and llm_client is None:
            log(f"  ⚠ ANTHROPIC_API_KEY 없음 — 공시 본문 요약 건너뜀")
        elif not ready:
            log(f"  본문 다운로드 결과 비어있음 — LLM 요약 생략")
    elif not cfg.analyze_bodies:
        log(f"  본문 분석 꺼짐 (제목만 기록)")

    # 6.5) Business Profile (LLM 가능할 때만)
    biz: Optional[dict] = None
    footnotes: Optional[dict] = None
    if cfg.analyze_bodies and llm_client is not None:
        log(f"[7/8] 회사 개요(Business Profile) 추출")
        try:
            biz = biz_mod.fetch_business_profile(
                discs=discs, corp_code=c.corp_code, is_listed=is_listed,
                client=llm_client, model=model_name, log=log,
            )
            if biz:
                n_seg = len(biz.get("segments") or [])
                log(f"  → 요약 {len(biz.get('business_summary','') or '')}자 · "
                    f"사업부 {n_seg}건")
        except Exception as exc:  # noqa: BLE001
            log(f"  ⚠ Business Profile 오류: {exc}")
            biz = None

        log(f"  주요 주석(Footnotes) 추출")
        try:
            footnotes = biz_mod.fetch_footnotes(
                discs=discs, corp_code=c.corp_code, is_listed=is_listed,
                client=llm_client, model=model_name, log=log,
            )
            if footnotes:
                n_items = sum(
                    len(v) for k, v in footnotes.items()
                    if not k.startswith("_") and isinstance(v, list)
                )
                log(f"  → 주요 주석 {n_items}건 추출")
        except Exception as exc:  # noqa: BLE001
            log(f"  ⚠ Footnotes 오류: {exc}")
            footnotes = None

    # 6.7) D&A LLM fallback — fnlttSinglAcntAll 에서 못 잡은 연도를 본문에서 추출
    if cfg.analyze_bodies and llm_client is not None and fin.annual:
        _fill_da_from_body(
            fin=fin, discs=discs, corp_code=c.corp_code,
            is_listed=is_listed, client=llm_client, model=model_name, log=log,
        )

    # 7) 파일 저장
    log(f"[8/8] 리포트 저장")
    os.makedirs(cfg.output_dir, exist_ok=True)
    safe = "".join(ch for ch in profile.corp_name if ch not in '/\\:*?"<>|').strip()
    stamp = date.today().strftime("%Y%m%d")
    base = f"DART_QuickReport_{safe}_{stamp}"
    xlsx_path = os.path.join(cfg.output_dir, base + ".xlsx")
    html_path = os.path.join(cfg.output_dir, base + ".html")
    write_excel(xlsx_path, profile, fin, sh, discs, period_label,
                exec_summary=exec_summary, business=biz, footnotes=footnotes)
    write_html(html_path, profile, fin, sh, discs, period_label,
               exec_summary=exec_summary, business=biz, footnotes=footnotes)
    log(f"  ✓ Excel:  {xlsx_path}")
    log(f"  ✓ HTML:  {html_path}")

    return RunResult(
        excel_path=xlsx_path, html_path=html_path,
        corp_name=profile.corp_name,
        n_disclosures=len(discs), n_analyzed=n_analyzed,
    )
