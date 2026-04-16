# -*- coding: utf-8 -*-
"""M10-A — 전체 파이프라인 오케스트레이션 (GUI/CLI 공통).

`run_quickreport()` 가 1건의 회사에 대해 수집→LLM 분석→리포트 생성을 수행.
GUI 는 이 함수를 스레드에서 호출만 하면 된다.

상세 로그: 출력 폴더에 `_log_<회사>_<타임스탬프>.txt` 자동 생성. GUI 와
동시에 기록되어 문제 발생 시 troubleshoot 용으로 사용.
"""
from __future__ import annotations
import os
import platform
import sys
import traceback
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Optional
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
    # ─── 개별 Claude 작업 토글 (모두 독립) ─────────────────────
    # 공시 본문 요약 · key_points · Implication (한 번의 Claude 호출로 묶음)
    summarize_disclosures: bool = True
    # 경영진 요약 (요약된 공시들을 재료로 종합) — summarize_disclosures 필수
    exec_summary: bool = True
    # 회사 개요 · 사업부 구조
    business_profile: bool = True
    # 감사보고서/사업보고서 주석 주요 항목 추출
    footnotes: bool = True
    # API 로 못 잡은 D&A 를 본문에서 LLM 으로 보강
    da_llm_fallback: bool = True
    # ─── 그 외 설정 ────────────────────────────────────────────
    body_limit: Optional[int] = 20    # LLM 호출 상한 (None = 무제한)
    years_back: int = 4               # 연간 재무 조회 연수
    output_dir: str = "."
    anthropic_api_key: Optional[str] = None
    anthropic_model: Optional[str] = None   # None 이면 config.ANTHROPIC_MODEL 사용
    save_log: bool = True             # 상세 로그 파일(_log_회사_시각.txt) 저장 여부

    def needs_llm(self) -> bool:
        """어느 하나라도 Claude 가 필요한 작업이 켜져 있는가?"""
        return any([
            self.summarize_disclosures, self.exec_summary,
            self.business_profile, self.footnotes, self.da_llm_fallback,
        ])

    # 하위 호환: 기존에 analyze_bodies 로 참조하던 코드 대응
    @property
    def analyze_bodies(self) -> bool:
        return self.needs_llm()


@dataclass
class RunResult:
    excel_path: str
    html_path: str
    corp_name: str
    n_disclosures: int
    n_analyzed: int


def _validate_financials(fin, log: LogFn) -> Dict[str, Any]:
    """재무 검수 — 필수 키(revenue, op_income, net_income, da, ebitda) 의
    연도 커버리지 계산. 로그에 상세 진단 출력."""
    import json as _json
    report: Dict[str, Any] = {"annual": {}, "cfs": {}, "ofs": {}}

    def _coverage(seq, key):
        if not seq:
            return (0, 0)
        filled = sum(1 for y in seq if y.values.get(key) is not None)
        return (filled, len(seq))

    keys = ["revenue", "op_income", "net_income", "da", "ebitda",
            "total_assets", "total_liabilities", "total_equity"]
    targets = {
        "annual":     fin.annual,
        "cfs":        fin.annual_cfs,
        "ofs":        fin.annual_ofs,
    }
    missing_by_key: Dict[str, List[int]] = {}
    for bundle_name, seq in targets.items():
        if not seq:
            continue
        for k in keys:
            filled, total = _coverage(seq, k)
            report[bundle_name][k] = f"{filled}/{total}"
            if bundle_name == "annual" and filled < total:
                missing_by_key.setdefault(k, []).extend(
                    y.year for y in seq if y.values.get(k) is None
                )

    log(f"  ▸ 재무 커버리지 검수: "
        + ", ".join(f"{k}={report.get('annual', {}).get(k, '-')}"
                    for k in ["revenue", "op_income", "net_income", "da", "ebitda"]))
    if missing_by_key:
        details = []
        for k, yrs in missing_by_key.items():
            details.append(f"{k}={sorted(set(yrs))}")
        log(f"  ⚠ 누락 연도: " + " / ".join(details))
    return {"report": report, "missing_by_key": missing_by_key}


def _propagate_da_to_fs_lists(fin, year: int, dep, amort) -> None:
    """fin.annual 의 특정 year 에 D&A 를 주입한 직후, 같은 year 의
    annual_cfs / annual_ofs YearFin 에도 동일한 값을 복사.

    fs_div 별 pivot 을 안 쓰고 LLM 으로 뽑힌 D&A 라도 Standalone 탭이
    비지 않도록 하는 장치. 이미 값이 있으면 덮어쓰지 않음.
    """
    da_sum = (dep or 0.0) + (amort or 0.0)
    if dep is None and amort is None:
        return
    for attr in ("annual_cfs", "annual_ofs"):
        seq = getattr(fin, attr, None) or []
        for yf in seq:
            if yf.year != year:
                continue
            if yf.values.get("da") is not None:
                continue
            yf.values["dep"] = dep
            yf.values["amort"] = amort
            yf.values["da"] = da_sum
            op = yf.values.get("op_income")
            rev = yf.values.get("revenue")
            if op is not None:
                yf.values["ebitda"] = op + da_sum
                if rev:
                    yf.values["ebitdam"] = yf.values["ebitda"] / rev * 100.0


def _fill_da_per_year(
    fin, discs, corp_code: str, is_listed: bool,
    client, model: Optional[str], log: LogFn,
) -> int:
    """누락 연도별로 해당 연도 사업보고서를 찾아 LLM 으로 개별 추출.

    discs 에서 report_nm 에 결산연도가 매칭되는 사업보고서를 우선 탐색.
    해당 rcept_no 본문을 다운로드해 slice_da_relevant 후 Claude 호출.
    """
    import re as _re
    missing = [y for y in fin.annual if y.values.get("da") is None]
    if not missing:
        return 0
    log(f"  ▸ 연도별 D&A 전용 LLM 재시도 ({len(missing)}개년)")
    from . import disclosures as _d

    def _find_report_for_year(year: int):
        """해당 연도 결산의 사업보고서 rcept_no 반환 (정정본 배제)."""
        # report_nm 에 "(YYYY.12)" 또는 "(YYYY.N)" 포함
        cand = []
        for d in discs:
            nm = d.report_nm or ""
            if "사업보고서" not in nm:
                continue
            m = _re.search(r"\((\d{4})[.\-/]?\s*\d{1,2}", nm)
            if not m:
                continue
            if int(m.group(1)) != year:
                continue
            cand.append(d)
        # 원본 우선, 접수일 최신
        def _rank(d):
            is_amend = bool(_re.match(
                r"^\s*\[(?:첨부정정|기재정정|정정|첨부추가|변경등록)\]", d.report_nm or ""
            ))
            return (1 if is_amend else 0,
                    -int(str(d.rcept_dt or "0").replace("-", "") or "0"))
        cand.sort(key=_rank)
        return cand[0] if cand else None

    filled = 0
    for yf in missing:
        src = _find_report_for_year(yf.year)
        if src is None:
            log(f"    [{yf.year}] 해당 연도 사업보고서 없음 — skip")
            continue
        log(f"    [{yf.year}] {src.report_nm} ({src.rcept_no})")
        body_full = _d.fetch_body(src.rcept_no, cap=900000)
        if not body_full or len(body_full) < 1000:
            log(f"      본문 다운로드 실패 또는 짧음 ({len(body_full) if body_full else 0}자) → skip")
            continue
        # 1차: 마커 기반 슬라이싱
        body = biz_mod.slice_da_relevant(body_full, cap=120000)
        slice_method = "마커 슬라이싱"
        # 2차 fallback: 슬라이싱 결과가 너무 작으면 본문 앞부분 그대로
        if len(body) < 5000:
            body = body_full[:120000]
            slice_method = "fallback (앞 120k)"
        log(f"      본문 {len(body_full):,}자 → {slice_method} {len(body):,}자")
        try:
            parsed, raw = llm_mod.extract_da_from_body(
                body, client=client,
                return_raw=True,
                **({"model": model} if model else {}),
            )
        except Exception as exc:  # noqa: BLE001
            log(f"      LLM 오류: {exc}")
            continue
        # raw 응답 일부 기록 (디버그)
        preview = (raw or "").replace("\n", " ")[:300]
        log(f"      LLM 응답({len(raw)}자): {preview}")
        # year 이 정확히 일치하는 항목만 사용
        matched = False
        for d in parsed or []:
            try:
                y_val = int(d.get("year"))
            except (TypeError, ValueError):
                continue
            if y_val != yf.year:
                continue
            dep = d.get("dep") if isinstance(d.get("dep"), (int, float)) else None
            amort = d.get("amort") if isinstance(d.get("amort"), (int, float)) else None
            if dep is None and amort is None:
                log(f"      ✗ {yf.year} 응답에 dep/amort 둘 다 null → skip")
                continue
            yf.values["dep"] = dep
            yf.values["amort"] = amort
            yf.values["da"] = (dep or 0.0) + (amort or 0.0)
            op = yf.values.get("op_income")
            rev = yf.values.get("revenue")
            if op is not None:
                yf.values["ebitda"] = op + yf.values["da"]
                if rev:
                    yf.values["ebitdam"] = yf.values["ebitda"] / rev * 100.0
            # Standalone / Consolidated 리스트에도 같은 값 전파
            _propagate_da_to_fs_lists(fin, yf.year, dep, amort)
            filled += 1
            src_label = d.get("source", "?")
            log(f"      ✓ {yf.year} dep={dep} amort={amort} "
                f"da={yf.values['da']:,.0f} (source={src_label})")
            matched = True
            break
        if not matched:
            years_in_resp = [d.get("year") for d in (parsed or [])]
            log(f"      ✗ {yf.year} 응답에 해당 연도 없음 (응답 연도: {years_in_resp})")
    if filled:
        log(f"  → 연도별 재시도로 {filled}개년 추가 보강")
    else:
        log(f"  ⚠ 연도별 재시도 실패 — 모든 후보에서 D&A 추출 불가")
    return filled


def _fill_da_from_body(
    fin, discs, corp_code: str, is_listed: bool,
    client, model: Optional[str], log: LogFn,
) -> None:
    """API 로 D&A 를 못 잡은 연도를 최후의 LLM 본문 파싱으로 채움.

    감사보고서/사업보고서 1건의 본문을 Claude 에 던져 [{year, dep, amort}]
    배열로 추출. 해당 연도의 YearFin.values 에 주입 + EBITDA 파생 재계산.

    ※ raw_rows 피벗은 fetch_all 직후 unconditional 로 이미 수행됨 (여기선 스킵).
    """
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
        body_full = _d.fetch_body(rcept_no, cap=900000)
        if not body_full or len(body_full) < 1000:
            log(f"      본문 부족 → 다음 후보")
            continue
        body = biz_mod.slice_da_relevant(body_full, cap=120000)
        if len(body) < 5000:
            body = body_full[:120000]   # slice 실패 시 앞부분 fallback
        log(f"      본문 {len(body_full):,}자 → D&A 관련 {len(body):,}자")
        try:
            parsed, raw = llm_mod.extract_da_from_body(
                body, client=client, return_raw=True,
                **({"model": model} if model else {}),
            )
            preview = (raw or "").replace("\n", " ")[:250]
            log(f"      LLM 응답({len(raw)}자): {preview}")
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
        # Standalone / Consolidated 리스트에도 같은 값 전파
        _propagate_da_to_fs_lists(fin, yf.year, dep, amort)
        filled += 1
    log(f"    → D&A {filled}개년 보강 완료")


def run_quickreport(cfg: RunConfig, log: LogFn = print) -> RunResult:
    # ─── 0. 상세 로그 파일 자동 생성 (troubleshoot 용) ──────────────
    os.makedirs(cfg.output_dir, exist_ok=True)
    _log_fh = None
    _log_path = None
    if cfg.save_log:
        _stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        _safe_company = "".join(ch for ch in (cfg.company or "session")
                                if ch not in '/\\:*?"<>|').strip()
        _log_path = os.path.join(cfg.output_dir, f"_log_{_safe_company}_{_stamp}.txt")
        try:
            _log_fh = open(_log_path, "w", encoding="utf-8")
        except Exception:
            _log_fh = None

    _orig_log = log  # 원본 GUI 로거 보관

    def _tee_log(msg: str) -> None:
        # 원본 로거 호출 (GUI/콘솔) — 자기 자신 아니라 _orig_log 참조해야 재귀 방지
        try:
            _orig_log(msg)
        except Exception:
            pass
        if _log_fh:
            try:
                _log_fh.write(msg + "\n")
                _log_fh.flush()
            except Exception:
                pass

    log = _tee_log   # 이후 본문에서 log(...) 호출은 tee 로 동작

    if _log_fh:
        log(f"[로그파일] {_log_path}")
        log(f"[환경] Python {sys.version.split()[0]} · {platform.platform()}")
        log(f"[인코딩] stdout={getattr(sys.stdout, 'encoding', '?')} "
            f"stderr={getattr(sys.stderr, 'encoding', '?')} "
            f"fs={sys.getfilesystemencoding()}")
        try:
            import anthropic as _anth
            log(f"[SDK] anthropic {_anth.__version__}")
        except Exception:
            pass
        log(f"[설정] company={cfg.company} period={cfg.period_value}{cfg.period_unit} "
            f"years_back={cfg.years_back} body_limit={cfg.body_limit} "
            f"model={cfg.anthropic_model or 'default'}")
        log(f"[LLM] summarize={cfg.summarize_disclosures} exec={cfg.exec_summary} "
            f"biz={cfg.business_profile} footnotes={cfg.footnotes} "
            f"da_fallback={cfg.da_llm_fallback}")

    try:
        return _run_quickreport_impl(cfg, log)
    except Exception as exc:
        log(f"\n[치명적 오류] {exc}")
        log(traceback.format_exc())
        raise
    finally:
        if _log_fh:
            try:
                _log_fh.close()
            except Exception:
                pass


def _run_quickreport_impl(cfg: RunConfig, log: LogFn) -> RunResult:
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

    # 비상장은 감사보고서 파싱에 Claude 가 필요 (구 로직은 analyze_bodies 단일 체크였음)
    llm_client = None
    if cfg.needs_llm() or not is_listed:
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

    # 5.5) D&A raw 피벗 — LLM 무관 무비용 작업. Consolidated/Standalone
    #      둘 다 누락 연도 보강 (CFS 전용·OFS 전용·통합 pivot 세 벌로 fs_div 인지).
    raw_filled = fin_mod.fill_da_from_raw(fin)
    if raw_filled:
        log(f"  D&A raw 피벗 (무비용): {raw_filled}개 항목 보강")

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
    if cfg.summarize_disclosures and llm_targets:
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
                # Executive Summary — 독립 토글
                if cfg.exec_summary:
                    profile_summary = (
                        f"{profile.corp_name} ({profile.corp_cls_label}), "
                        f"CEO {profile.ceo_nm}, 결산월 {profile.acc_mt}, "
                        f"설립 {profile.est_dt}"
                    )
                    exec_summary = llm_mod.build_executive_summary(
                        profile_summary, discs, client=llm_client, model=model_name,
                    )
                else:
                    log(f"  Executive Summary 꺼짐")
            except Exception as exc:  # noqa: BLE001
                log(f"  ⚠ LLM 오류: {exc}")
        elif ready and llm_client is None:
            log(f"  ⚠ ANTHROPIC_API_KEY 없음 — 공시 본문 요약 건너뜀")
        elif not ready:
            log(f"  본문 다운로드 결과 비어있음 — LLM 요약 생략")
    elif not cfg.summarize_disclosures:
        log(f"  공시 요약 꺼짐 (제목만 기록)")
        if cfg.exec_summary:
            log(f"  Executive Summary 도 스킵 (공시 요약이 재료)")

    # 6.5) Business Profile (독립 토글)
    biz: Optional[dict] = None
    footnotes: Optional[dict] = None
    if cfg.business_profile and llm_client is not None:
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
            import traceback as _tb
            log(f"  ⚠ Business Profile 오류: {type(exc).__name__}: {exc}")
            log(f"    {_tb.format_exc().splitlines()[-2] if _tb.format_exc() else ''}")
            biz = None

    # 6.6) Footnotes (독립 토글)
    if cfg.footnotes and llm_client is not None:
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
    if cfg.da_llm_fallback and llm_client is not None and fin.annual:
        _fill_da_from_body(
            fin=fin, discs=discs, corp_code=c.corp_code,
            is_listed=is_listed, client=llm_client, model=model_name, log=log,
        )

    # 6.8) 중간 검수(validation) — 누락 D&A 가 여전히 있으면 연도별 재시도
    log(f"\n[검수] 재무 데이터 품질 점검")
    _validate_financials(fin, log)
    if cfg.da_llm_fallback and llm_client is not None and fin.annual:
        still_missing = [y for y in fin.annual if y.values.get("da") is None]
        if still_missing:
            log(f"  D&A 여전히 {len(still_missing)}개년 누락 → 연도별 직접 추출 시작")
            _fill_da_per_year(
                fin=fin, discs=discs, corp_code=c.corp_code,
                is_listed=is_listed, client=llm_client, model=model_name, log=log,
            )
            # 최종 검수
            log(f"\n[검수] 재무 최종 점검")
            _validate_financials(fin, log)

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
