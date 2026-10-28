from __future__ import annotations
import argparse
import json
import logging
import os
import sqlite3
import sys
import traceback
if sys.stdout:
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr:
    sys.stderr.reconfigure(encoding="utf-8")
from typing import List, Tuple
from dotenv import load_dotenv
load_dotenv(override=True)

from .bot import run_bot_in_background, state
from .company_enricher import CompanyEnricher
from .db import (
    clear_db,
    get_connection,
    get_pending_jobs,
    init_db,
    is_job_seen,
    mark_notified,
    save_evaluation,
    save_job,
    save_profile_fit,
    update_job_status,
)
from .exporter import append_profile_fit_to_csv, append_scored_jobs_to_csv, clear_records
from .jev import JevClient
from .models import DimensionScore, Job, ScoredJob
from .notifier import DiscordNotifier
from .rubric import (
    RubricConfig,
    filter_job_hard,
    get_corporate_authority_dimension,
    get_tier_points,
    parse_rubric_file,
)
from .detail_fetcher import JobDetailFetcher
from .scrapers.browser_scrapers import BrowserScraperManager
from .scrapers.yes123 import scrape_yes123_jobs
from .scrapers.yourator import scrape_yourator_jobs
from .translator import JobTranslator, TranslationFatalError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("jobme")


def handle_retier(config: RubricConfig, rubric_file: str, limit: int = 0) -> None:
    enricher = CompanyEnricher(rubric_path=rubric_file)
    cleared = enricher.clear_profiles()
    logger.info(f"已清空 {cleared} 筆舊企業快取，開始依最新客觀資料與 DeepSeek 重新分級...")

    tier_points = get_tier_points(config, rubric_file)
    auth_dim = get_corporate_authority_dimension(config)
    if not auth_dim:
        logger.error("無法在 Rubric 找到企業權威性維度，終止重新分級。")
        return

    with get_connection("jobs.db") as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT e.platform, e.job_id, e.total_score, e.is_qualified, e.has_low_confidence,
                   e.details_json, e.evaluated_at, e.notified_at,
                   j.title, j.company, j.location, j.salary, j.url, j.tags
            FROM evaluations e
            JOIN jobs j ON j.platform = e.platform AND j.job_id = e.job_id
            ORDER BY e.evaluated_at ASC
        """).fetchall()

    if not rows:
        logger.info("資料庫中無任何已評估職缺紀錄。")
        return

    target_rows = rows[:limit] if limit > 0 else rows
    logger.info(f"即將重新分級與計分 {len(target_rows)} 筆職缺...")

    diffs = []
    all_scored_jobs: List[ScoredJob] = []

    with get_connection("jobs.db") as conn:
        for idx, row in enumerate(target_rows, 1):
            raw_tags = row["tags"]
            try:
                tags = json.loads(raw_tags) if raw_tags else []
            except Exception:
                tags = []

            job = Job(
                platform=row["platform"],
                job_id=row["job_id"],
                title=row["title"],
                company=row["company"],
                location=row["location"],
                salary=row["salary"],
                url=row["url"],
                tags=tags if isinstance(tags, list) else [],
            )

            # Re-classify company
            prof = enricher.enrich_company(job, rubric_config=config)

            # Parse details_json
            try:
                details = json.loads(row["details_json"])
            except Exception:
                details = []

            old_auth_score = None
            old_tier = "未知"
            for d in details:
                if "權威" in d.get("name", ""):
                    old_auth_score = d.get("score")
                    if old_auth_score == 45: old_tier = "Tier 1"
                    elif old_auth_score == 30: old_tier = "Tier 2"
                    elif old_auth_score == 15: old_tier = "Tier 3"
                    elif old_auth_score == 0: old_tier = "Tier 4"

            new_auth_score = tier_points.get(prof.tier, 15.0)
            levels = auth_dim.level_points
            matched_idx = levels.index(new_auth_score) if new_auth_score in levels else 0

            # Update authority dimension in details
            updated_dim_scores: List[DimensionScore] = []
            for d in details:
                if "權威" in d.get("name", ""):
                    d["score"] = new_auth_score
                    d["probabilities"] = {str(matched_idx): 1.0}

                d_levels = d.get("level_points", levels if "權威" in d.get("name", "") else [0, d.get("max", 0)])
                updated_dim_scores.append(
                    DimensionScore(
                        name=d.get("name", ""),
                        max_points=float(d.get("max", d.get("max_points", 0))),
                        score=float(d.get("score", 0)),
                        confidence=float(d.get("confidence", 1.0)),
                        probabilities=d.get("probabilities", {}),
                        level_points=d_levels,
                    )
                )

            new_total = sum(d["score"] for d in details)
            new_qualified = new_total >= config.notify_threshold

            # Save back to evaluations DB
            conn.execute(
                """
                UPDATE evaluations
                SET total_score = ?, is_qualified = ?, details_json = ?
                WHERE platform = ? AND job_id = ?
                """,
                (
                    new_total,
                    1 if new_qualified else 0,
                    json.dumps(details, ensure_ascii=False),
                    row["platform"],
                    row["job_id"],
                ),
            )

            scored = ScoredJob(
                job=job,
                total_score=new_total,
                is_qualified=new_qualified,
                dimension_scores=updated_dim_scores,
                evaluated_at=row["evaluated_at"] or "",
            )

            all_scored_jobs.append(scored)

            if old_tier != prof.tier:
                diffs.append({
                    "company": row["company"],
                    "old_tier": old_tier,
                    "new_tier": prof.tier,
                    "basis": prof.basis or "(無)",
                    "old_score": row["total_score"],
                    "new_score": new_total,
                })

            if idx % 25 == 0 or idx == len(target_rows):
                logger.info(f"重新校準進度：{idx} / {len(target_rows)} 筆職缺...")

        conn.commit()

    # Rewrite records.csv
    clear_records("records.csv")
    append_scored_jobs_to_csv(all_scored_jobs, "records.csv")
    logger.info(f"✅ records.csv 已全數依最新評分標準重構完成 (共 {len(all_scored_jobs)} 筆)！")

    # Deduplicate diffs by company name
    comp_diffs = {}
    for d in diffs:
        c = d["company"]
        if c not in comp_diffs:
            comp_diffs[c] = d

    # Print summary diff report
    print("\n" + "=" * 100)
    print(f"📊 【企業分級重新校準報告】 共重新計算 {len(all_scored_jobs)} 筆職缺")
    print(f"   其中 {len(comp_diffs)} 家企業分級發生調整 (Tier 變動對照表)：")
    print("-" * 100)
    print(f"{'公司名稱':<36} | {'原 Tier':<7} | {'新 Tier':<7} | {'原總分 -> 新總分':<16} | {'判定依據 (Basis)'}")
    print("-" * 100)
    for c, d in list(comp_diffs.items())[:60]:
        print(f"{d['company'][:34]:<36} | {d['old_tier']:<7} | {d['new_tier']:<7} | {d['old_score']:>4.1f} -> {d['new_score']:<5.1f}    | {d['basis']}")
    if len(comp_diffs) > 60:
        print(f"... 其餘 {len(comp_diffs) - 60} 家企業變動已全數更新並存入 records.csv")
    print("=" * 100 + "\n")


def main():
    load_dotenv(override=True)

    parser = argparse.ArgumentParser(description="JobMe: 智慧求職爬蟲與 Jev 決策推播系統")
    parser.add_argument("--rubric", default=None, help="評分標準檔案路徑 (預設: config/rubric.md 或 rubric.md)")
    parser.add_argument("--profile", default=None, help="個人履歷背景檔案 (預設: config/profile.md 或 profile.md)")
    parser.add_argument("--quiet-first-run", action="store_true", help="首次執行僅入庫記錄，不發送達標推播")
    parser.add_argument("--limit", type=int, default=0, help="單次執行評分職缺上限 (預設: 0 為不設限，評分所有通過硬篩職缺)")
    parser.add_argument("--scrape-limit", type=int, default=0, help="各平台抓取職缺筆數上限 (例如: 10 為每個平台最多抓取 10 筆)")
    parser.add_argument("--dry-run", action="store_true", help="模擬模式 (不呼叫付費 API、不推播 Discord)")
    parser.add_argument("--save-all", action="store_true", help="強制留存所有通過硬篩並評分的職缺 (方便校驗評分標準)")
    parser.add_argument("--setup-browser", action="store_true", help="開啟瀏覽器手動通過 104/Cake/1111 驗證並儲存 Cookie")
    parser.add_argument("--clear-history", action="store_true", help="清空 jobs.db 與 records.csv 歷史紀錄 (保留瀏覽器驗證 Cookie)")
    parser.add_argument("--retier", action="store_true", help="清空公司快取並根據最新 Rubric 與客觀資料重新分級所有已評估職缺，重算企業權威性分數並更新 CSV")
    parser.add_argument("--platforms", default="all", help="指定爬取平台 (逗號分隔，如 yourator,yes123 或 all)")
    parser.add_argument("--daemon", action="store_true", help="啟用常駐輪詢模式 (每隔一段時間自動巡檢新職缺)")
    parser.add_argument("--ignore-seen", action="store_true", help="忽略已入庫快取記錄，強制重新抓取與評分")
    parser.add_argument("--interval", type=int, default=None, help="常駐模式輪詢間隔秒數 (預設: 3600 秒 / 1 小時，亦可於 rubric.md 設定)")
    parser.add_argument("--bot", action="store_true", help="啟用 Discord 跨裝置互動機器人控制台")
    parser.add_argument("--db", default="jobs.db", help="SQLite 資料庫路徑 (預設: jobs.db)")
    parser.add_argument("--serve", action="store_true", help="啟動 FastAPI 本機伺服器 (供 Chrome Extension 即時查詢與評測)")
    parser.add_argument("--host", default="127.0.0.1", help="API 伺服器主機 (預設: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="API 伺服器埠號 (預設: 8000)")
    parser.add_argument("--no-api", action="store_true", help="常駐模式 (--daemon) 下不啟動背景 API 伺服器")
    args = parser.parse_args()

    # Clear history if requested
    if args.clear_history:
        clear_db("jobs.db")
        clear_records("records.csv")
        logger.info("✅ 歷史紀錄已成功清空：jobs.db 與 records.csv 已重設，保留瀏覽器驗證 Cookie。")
        return

    # If setup-browser requested, launch interactive window and exit
    if args.setup_browser:
        manager = BrowserScraperManager(headless=False)
        manager.setup_interactive_browser()
        return

    # Initialize Discord Bot if token configured
    bot_token = os.getenv("DISCORD_BOT_TOKEN")
    channel_id = os.getenv("DISCORD_CHANNEL_ID")
    if bot_token:
        logger.info("🤖 偵測到 DISCORD_BOT_TOKEN，正在啟動 Discord 機器人 (啟用互動式收藏按鈕)...")
        run_bot_in_background(bot_token, channel_id)
        from .bot import wait_until_bot_ready
        if wait_until_bot_ready(timeout=6.0):
            logger.info("✅ Discord 機器人連線就緒，推播頻道將啟用互動式收藏按鈕。")

    # If --serve requested, launch standalone FastAPI server
    if args.serve:
        from .api import run_server
        run_server(host=args.host, port=args.port)
        return

    # Check rubric file fallback
    rubric_candidates = [args.rubric] if args.rubric else ["config/rubric.md", "rubric.md", "config/rubric.example.md", "rubric.example.md"]
    rubric_file = next((f for f in rubric_candidates if f and os.path.exists(f)), None)
    if not rubric_file:
        logger.error("錯誤：找不到評分標準檔案。請先建立 config/rubric.md 或 rubric.md。")
        sys.exit(1)

    config = parse_rubric_file(rubric_file)
    logger.info(f"載入評分標準 ({rubric_file})：{len(config.dimensions)} 個維度，滿分 {config.total_max_points} 分，基本門檻 {config.base_threshold} 分，推播門檻 {config.notify_threshold} 分。")

    # Handle --retier re-calibration
    if args.retier:
        handle_retier(config, rubric_file, args.limit)
        return

    # Determine execution interval
    active_interval = args.interval if args.interval is not None else config.interval
    state.interval_seconds = active_interval
    state.pipeline_fn = run_pipeline
    state.args = args
    state.config = config
    state.rubric_file = rubric_file

    if not bot_token:
        logger.info("ℹ️ 未偵測到 DISCORD_BOT_TOKEN。如需使用手機/筆電在 Discord 按鈕遠端控制桌機，請於 .env 填入 DISCORD_BOT_TOKEN 與 DISCORD_CHANNEL_ID。")

    if args.bot and not args.daemon:
        import time
        logger.info("🤖 僅啟動 Discord 機器人控制台模式 (常駐等待按鈕或指令觸發)...")
        while True:
            time.sleep(2)
        return

    if args.daemon:
        import time
        from datetime import datetime

        if not args.no_api:
            import threading
            def _start_api_server():
                import uvicorn
                from .api import app
                cfg = uvicorn.Config(app, host=args.host, port=args.port, log_level="warning")
                srv = uvicorn.Server(cfg)
                srv.run()

            api_thread = threading.Thread(target=_start_api_server, daemon=True)
            api_thread.start()
            logger.info(f"🚀 [Daemon] 背景 API 伺服器已同步啟動：http://{args.host}:{args.port}")

        logger.info(f"🔄 啟動常駐守護程序模式 (Daemon Mode)，每隔 {state.interval_seconds} 秒 ({state.interval_seconds // 60} 分鐘) 執行一次完整監控流程...")
        state.next_run = datetime.fromtimestamp(time.time() + state.interval_seconds)
        while True:
            if not state.is_paused:
                with state.lock:
                    if state.is_busy:
                        logger.info("系統目前正由手動觸發執行中，本輪排程自動延後。")
                        time.sleep(10)
                        continue
                    state.is_busy = True
                try:
                    # Dynamically reload rubric.md on every iteration
                    active_config = parse_rubric_file(rubric_file)
                    run_pipeline(args, active_config, rubric_file)
                except Exception as e:
                    logger.error(f"常駐排程執行發生例外異常: {e}", exc_info=True)
                    try:
                        import traceback
                        from .notifier import DiscordNotifier
                        DiscordNotifier().notify_error(
                            title="常駐排程執行例外異常",
                            error_msg=f"{e}\n\nTraceback:\n{traceback.format_exc()[-500:]}",
                            domain="daemon_exc",
                        )
                    except Exception:
                        pass
                finally:
                    with state.lock:
                        state.is_busy = False
                        state.last_run = datetime.now()
                        state.next_run = datetime.fromtimestamp(time.time() + state.interval_seconds)

            logger.info(f"💤 進入待機，每隔 {state.interval_seconds} 秒巡檢 (可隨時從 Discord 按鈕暫停或立即觸發)...")
            elapsed = 0
            while elapsed < state.interval_seconds:
                time.sleep(2)
                if not state.is_paused:
                    elapsed += 2
    else:
        run_pipeline(args, config, rubric_file)


def run_pipeline(args, config: RubricConfig, rubric_file: str) -> None:
    # Load optional candidate profile
    profile_candidates = [args.profile] if getattr(args, "profile", None) else [
        "config/profile.md", "profile.md", "config/profile.example.md", "profile.example.md"
    ]
    profile_file = next((f for f in profile_candidates if f and os.path.exists(f)), None)
    profile_text = ""
    if profile_file:
        with open(profile_file, "r", encoding="utf-8") as f:
            profile_text = f.read()
        logger.info(f"載入求職者履歷背景：{profile_file} ({len(profile_text)} 字元)")

    # Init database and notifier
    init_db("jobs.db")
    notifier = DiscordNotifier()

    ignore_seen = getattr(args, "ignore_seen", False)
    scrape_limit = getattr(args, "scrape_limit", 0)

    # Retrieve pending jobs from database (from prior runs or limited batches)
    pending_db_jobs = [] if ignore_seen else get_pending_jobs("jobs.db")
    pending_stage2_jobs = [j for j in pending_db_jobs if getattr(j, "status", "") == "pending_stage2"]
    pending_stage1_jobs = [j for j in pending_db_jobs if getattr(j, "status", "") == "pending_stage1"]
    if pending_stage2_jobs or pending_stage1_jobs:
        logger.info(
            f"📦 自本地資料庫載入待評佇列：Stage 2 (已通過適配度，待綜合評分) {len(pending_stage2_jobs)} 筆，"
            f"Stage 1 (待測適配度) {len(pending_stage1_jobs)} 筆。"
        )

    # Scrape jobs from platforms
    active_platforms = [p.strip().lower() for p in args.platforms.split(",")]
    scraped_jobs: List[Job] = []
    counts_by_platform = {}
    for p in ["104", "yourator", "cake", "1111", "yes123"]:
        if ("all" in active_platforms or p in active_platforms) and config.platforms.get(p, {}).get("enabled", True):
            counts_by_platform[p] = 0
    failed_platforms = []
    seen_fn = None if ignore_seen else is_job_seen

    # 1. Yourator (HTTP JSON API)
    if ("all" in active_platforms or "yourator" in active_platforms) and config.platforms.get("yourator", {}).get("enabled", True):
        try:
            logger.info("正在透過 HTTP API 抓取 Yourator 職缺...")
            y_jobs = scrape_yourator_jobs(config, is_seen_fn=seen_fn, notifier=notifier, scrape_limit=scrape_limit)
            counts_by_platform["yourator"] = len(y_jobs)
            scraped_jobs.extend(y_jobs)
            logger.info(f"Yourator 完成，共抓取 {len(y_jobs)} 筆職缺。")
        except Exception as e:
            tb = traceback.format_exc()
            logger.warning(f"Yourator 擷取失敗: {e}\n{tb}")
            failed_platforms.append("Yourator")
            notifier.notify_error("Yourator 擷取異常", f"{e}\n\nTraceback:\n{tb[-400:]}", platform="yourator")

    # 2. Yes123 (HTTP HTML Search)
    if ("all" in active_platforms or "yes123" in active_platforms) and config.platforms.get("yes123", {}).get("enabled", True):
        try:
            logger.info("正在透過 HTTP API 抓取 Yes123 職缺...")
            y123_jobs = scrape_yes123_jobs(config, is_seen_fn=seen_fn, notifier=notifier, scrape_limit=scrape_limit)
            counts_by_platform["yes123"] = len(y123_jobs)
            scraped_jobs.extend(y123_jobs)
            logger.info(f"Yes123 完成，共抓取 {len(y123_jobs)} 筆職缺。")
        except Exception as e:
            tb = traceback.format_exc()
            logger.warning(f"Yes123 擷取失敗: {e}\n{tb}")
            failed_platforms.append("Yes123")
            notifier.notify_error("Yes123 擷取異常", f"{e}\n\nTraceback:\n{tb[-400:]}", platform="yes123")

    # 3. Browser-based scrapers (104, Cake, 1111)
    browser_needed = any(
        ("all" in active_platforms or p in active_platforms) and config.platforms.get(p, {}).get("enabled", True)
        for p in ["104", "cake", "1111"]
    )
    if browser_needed:
        try:
            logger.info("啟動瀏覽器引擎擷取 104 / Cake / 1111 職缺...")
            browser_mgr = BrowserScraperManager(headless=True, notifier=notifier)
            b_jobs = browser_mgr.scrape_all(config, is_seen_fn=seen_fn, active_platforms=active_platforms, scrape_limit=scrape_limit)
            if browser_mgr.failed_platforms:
                failed_platforms.extend(browser_mgr.failed_platforms)
            for j in b_jobs:
                counts_by_platform[j.platform] = counts_by_platform.get(j.platform, 0) + 1
            scraped_jobs.extend(b_jobs)
            logger.info(f"瀏覽器引擎完成，共抓取 {len(b_jobs)} 筆職缺。")
        except Exception as e:
            tb = traceback.format_exc()
            logger.warning(f"瀏覽器擷取引擎發生異常: {e}\n{tb}")
            err_plat = "104" if "104" in active_platforms else "Browser"
            failed_platforms.append(err_plat)
            detailed_err = f"{e}\n\nTraceback:\n{tb[-500:]}"
            notifier.notify_error("平台存取或維護異常", detailed_err, platform=err_plat)

    logger.info(f"本輪共抓取 {len(scraped_jobs)} 筆新職缺。開始執行本地硬篩與 Jev 評分...")

    # Filter and Evaluate
    jev_client = JevClient(notifier=notifier)
    enricher = CompanyEnricher(rubric_path=rubric_file, notifier=notifier)
    translator = JobTranslator(notifier=notifier)

    # Pre-translate profile once to avoid repetitive translation
    profile_en = translator.translate_text(profile_text) if profile_text else ""

    passed_jobs: List[Job] = []
    for job in scraped_jobs:
        # Apply hard filter
        passed, reject_reason = filter_job_hard(job, config)
        if passed:
            save_job(job, status="pending_stage1")
            passed_jobs.append(job)
        else:
            save_job(job, status="hard_rejected")
            logger.debug(f"硬篩排除 [{job.platform}] {job.title}: {reject_reason}")

    # Merge pending_stage1_jobs from DB that were deferred
    stage1_candidates: List[Job] = list(passed_jobs)
    seen_cand_keys = {(j.platform, str(j.job_id)) for j in stage1_candidates}
    for pj in pending_stage1_jobs:
        k = (pj.platform, str(pj.job_id))
        if k not in seen_cand_keys:
            # Re-check hard filter against current rubric config
            passed, _ = filter_job_hard(pj, config)
            if passed:
                stage1_candidates.append(pj)
                seen_cand_keys.add(k)
            else:
                update_job_status(pj.platform, str(pj.job_id), "hard_rejected")

    logger.info(f"本地硬性條件篩選完成：{len(stage1_candidates)} 筆候選職缺進入 Stage 1 佇列。")

    # Interleave stage1_candidates across platforms so all platforms get fair evaluation
    if stage1_candidates:
        by_platform_raw: Dict[str, List[Job]] = {}
        for j in stage1_candidates:
            by_platform_raw.setdefault(j.platform, []).append(j)
        interleaved_passed: List[Job] = []
        max_len = max(len(lst) for lst in by_platform_raw.values()) if by_platform_raw else 0
        for idx in range(max_len):
            for p, lst in by_platform_raw.items():
                if idx < len(lst):
                    interleaved_passed.append(lst[idx])
        stage1_candidates = interleaved_passed

    # Stage 1: Profile Fit Gatekeeper (Independent, zero company enrichment cost)
    # Seed profile_passed_jobs with prior pending_stage2_jobs (they already passed Stage 1!)
    profile_passed_jobs: List[Tuple[Job, str]] = [(j, "high") for j in pending_stage2_jobs]
    target_stage1_count = max(args.limit * 2, 20) if (args.limit and args.limit > 0) else 0

    detail_fetcher = JobDetailFetcher(db_path=getattr(args, "db", "jobs.db"))

    if profile_en:
        logger.info(f"啟動 Stage 1: Profile 適配度把關門檻 (Gatekeeper)... (待測候選 {len(stage1_candidates)} 筆)")
        for job in stage1_candidates:
            if target_stage1_count and len(profile_passed_jobs) >= target_stage1_count:
                logger.info(f"已取得足夠通過 Stage 1 職缺 ({len(profile_passed_jobs)} 筆，含既有待評佇列)，提前進入 Stage 2。")
                break

            detail_fetcher.fetch_job_detail(job)
            try:
                fit_level, fit_conf, fit_probs = jev_client.evaluate_profile_fit(job, profile_en, dry_run=args.dry_run)
            except Exception as e:
                logger.error(f"[Jev] Profile Gatekeeper 評估異常: {e}")
                notifier.notify_error("Jev Profile 適配度評估異常", f"{e}\n{traceback.format_exc()[-400:]}", platform=job.platform)
                logger.warning(f"  ↳ [Jev 異常放棄測量] 略過此職缺: {job.title} @ {job.company}")
                continue

            # Always save Jev profile evaluation details for user audit
            save_profile_fit(job, fit_level, fit_conf, fit_probs)
            append_profile_fit_to_csv(job, fit_level, fit_conf, fit_probs)

            if fit_level in ("unrelated", "low"):
                logger.info(f"  ↳ [Profile 適配度未達標 ({fit_level.upper()})] 排除職缺 (免查公司): {job.title} @ {job.company}")
                continue
            logger.info(f"  ↳ [Profile 適配度通過 ({fit_level.upper()} / {fit_conf:.2f})] 准予進入 Stage 2: {job.title}")
            profile_passed_jobs.append((job, fit_level))
        logger.info(f"Stage 1 把關完成：共 {len(profile_passed_jobs)} 筆職缺符合個人專業方向，准予進入 Stage 2。")
    else:
        profile_passed_jobs.extend([(j, "unspecified") for j in stage1_candidates])

    # Interleave profile_passed_jobs across platforms so limit doesn't starve later platforms
    if profile_passed_jobs:
        by_platform: Dict[str, List] = {}
        for item in profile_passed_jobs:
            p = item[0].platform
            by_platform.setdefault(p, []).append(item)
        interleaved = []
        max_len = max(len(lst) for lst in by_platform.values()) if by_platform else 0
        for idx in range(max_len):
            for p, lst in by_platform.items():
                if idx < len(lst):
                    interleaved.append(lst[idx])
        profile_passed_jobs = interleaved

    evaluated_count = 0
    notified_count = 0
    all_scored_jobs: List[ScoredJob] = []
    translation_halted = False
    try:
        # Fast parallel warmup of translations & company enrichment (ONLY FOR PASSED JOBS!)
        if profile_passed_jobs:
            jobs_to_warmup = [j for j, _ in profile_passed_jobs]
            if args.limit and args.limit > 0:
                jobs_to_warmup = jobs_to_warmup[:args.limit]
            translator.warmup_translations(jobs_to_warmup, max_workers=2)
            enricher.warmup_companies(jobs_to_warmup, rubric_config=config, max_workers=8)

        for job, fit_level in profile_passed_jobs:
            # Check single-run safety limit (if limit > 0)
            if args.limit and args.limit > 0 and evaluated_count >= args.limit:
                logger.warning(f"已達到單次評分上限 ({args.limit} 筆)，其餘職缺保留至下次執行。")
                break

            # Ensure rich detailed JD is fetched and cached
            detail_fetcher.fetch_job_detail(job)

            # Enrich company facts & market benchmark (TWSE, g0v, DeepSeek)
            company_prof = enricher.enrich_company(job, rubric_config=config)

            # Build standardized English Fact Sheet State
            english_state = translator.build_english_fact_sheet(
                job=job,
                company_profile=company_prof,
                profile_text=profile_en,
            )

            # Jev System One Evaluation with anchored English State
            try:
                scored = jev_client.evaluate_job(
                    job,
                    config,
                    state=english_state,
                    dry_run=args.dry_run,
                    company_profile=company_prof,
                    profile_fit=fit_level if fit_level != "unspecified" else None,
                )
            except Exception as e:
                logger.error(f"[Jev] 評分流程異常: {e}")
                notifier.notify_error("Jev 評分流程異常", str(e), platform=job.platform)
                continue
            evaluated_count += 1

            # Check retention condition: base_threshold or save_all
            save_all = args.save_all or config.save_all_evaluated
            should_archive = (scored.total_score >= config.base_threshold) or save_all

            logger.info(
                f"[{scored.total_score:.1f}分 / 基本 {config.base_threshold:.1f} / 通知 {config.notify_threshold:.1f}] "
                f"({job.platform.upper()}) {job.title} @ {job.company} [{company_prof.tier}]"
            )

            if should_archive:
                all_scored_jobs.append(scored)
                # Save score result to DB
                save_evaluation(scored)
            else:
                logger.info("  ↳ [低於基本門檻] 僅記錄已讀快取，不留存評分明細與 CSV")

            # Check notification condition
            if scored.is_qualified:
                if args.quiet_first_run:
                    logger.info(f"  ↳ [首跑靜音] 符合門檻但暫不發送通知: {job.title}")
                elif args.dry_run:
                    logger.info(f"  ↳ [Dry-Run] 模擬發送通知: {job.title}")
                else:
                    sent = notifier.notify_job(scored)
                    if sent:
                        mark_notified(job.platform, job.job_id)
                        notified_count += 1
                        logger.info("  ↳ ✅ 已成功推播至 Discord!")
    except TranslationFatalError as tfe:
        translation_halted = True
        logger.error(f"[Translator] 轉譯服務硬中斷: {tfe}")
        notifier.notify_error(
            title="Google 轉譯速率超限 (429 安全中止)",
            error_msg=f"{tfe}\n\n依安全防護策略已立即終止本輪評估。",
            domain="translation_limit",
        )

    # Export to CSV if enabled
    if config.export_csv and all_scored_jobs:
        append_scored_jobs_to_csv(all_scored_jobs, "records.csv")
        logger.info(f"已將 {len(all_scored_jobs)} 筆評分紀錄寫入 records.csv。")

    # Send run summary
    degradations = []
    if getattr(jev_client, "has_degraded", False):
        degradations.append("Jev 評分 API 異常 (已停止無效評估，無 Mock 備援)")
    if getattr(enricher, "has_degraded", False):
        degradations.append("DeepSeek 企業分級已降級為資本額推估 (失去 AI 背景研究，權威性可能失準)")
    if translation_halted:
        degradations.append("Google 轉譯速率超限 (429)，已主動終止後續評估以保護 Token")
    else:
        if getattr(translator, "used_deepseek", False):
            degradations.append("Google 轉譯受限，已降級至 DeepSeek 備援轉譯")
        if getattr(translator, "has_degraded", False):
            degradations.append("轉譯服務異常 (Google 與 DeepSeek 均失敗，降級為中文原文直通)")

    summary_data = {
        "total_scraped": len(scraped_jobs),
        "total_filtered": len(passed_jobs),
        "total_notified": notified_count,
        "failed_platforms": failed_platforms,
        "counts_by_platform": counts_by_platform,
        "degradations": degradations,
    }
    if not args.dry_run and not args.quiet_first_run:
        notifier.notify_summary(summary_data)


if __name__ == "__main__":
    main()
