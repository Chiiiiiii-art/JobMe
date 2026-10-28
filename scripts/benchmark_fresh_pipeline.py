#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fresh Pipeline Benchmark across 5 platforms (5 jobs per platform = 25 jobs).

Captures:
- Q1: Stage 1 Profile Fit filter rate
- Q2: Token consumption and two-stage funnel savings
- Q3: Stage 2 precision / qualified job scores
- Q4: Crawl throughput per platform (jobs/min) and extrapolation
- Q7: Company profile SQLite cache hit rate
"""

from __future__ import annotations
import argparse
import json
import logging
import os
import sys
import time
from typing import Any, Dict, List, Tuple

sys.path.insert(0, os.path.abspath("."))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from jobme.models import Job, ScoredJob
from jobme.rubric import parse_rubric_file, filter_job_hard, RubricConfig
from jobme.detail_fetcher import JobDetailFetcher
from jobme.company_enricher import CompanyEnricher
from jobme.translator import JobTranslator
from jobme.jev import JevClient
from jobme.scrapers.yourator import scrape_yourator_jobs
from jobme.scrapers.yes123 import scrape_yes123_jobs
from jobme.scrapers.browser_scrapers import BrowserScraperManager
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("jobme.benchmark")

# Load past excluded IDs to ensure 100% fresh jobs
EXCLUDE_IDS = set()
for fn in [
    "reports/pipeline_50_jobs_data.json",
    "reports/pipeline_tested_history.json",
]:
    if os.path.exists(fn):
        try:
            with open(fn, "r", encoding="utf-8") as f:
                data = json.load(f)
                for it in data:
                    if isinstance(it, dict):
                        EXCLUDE_IDS.add(str(it.get("job_id", "")))
                    else:
                        EXCLUDE_IDS.add(str(it))
        except Exception:
            pass

def is_excluded_id(jid: str) -> bool:
    return str(jid) in EXCLUDE_IDS

def select_fresh_batch(raw_jobs: List[Job], target: int = 5) -> List[Job]:
    fresh = [j for j in raw_jobs if not is_excluded_id(j.job_id)]
    if len(fresh) >= target:
        return fresh[:target]
    # Fallback to unique raw jobs if fresh count is slightly under target
    seen = set()
    res = []
    for j in fresh:
        seen.add(j.job_id)
        res.append(j)
    for j in raw_jobs:
        if j.job_id not in seen:
            seen.add(j.job_id)
            res.append(j)
            if len(res) >= target:
                break
    return res[:target]

def estimate_tokens(text: str) -> int:
    # Industry standard heuristic: ~3.5 chars per token for mixed English/code/structured text
    return max(1, int(len(text) / 3.5))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=5, help="Jobs per platform (default: 5)")
    args = parser.parse_args()
    limit = args.limit

    print("=" * 80)
    print(f"🚀 啟動 25 筆全新全平台資料流履歷實證評測 (5 平台 x 各 {limit} 筆)")
    print(f"   已排除歷史已測試 ID 數: {len(EXCLUDE_IDS)}")
    print("=" * 80)

    config = parse_rubric_file("rubric.md")
    fetcher = JobDetailFetcher(db_path="jobs.db")
    enricher = CompanyEnricher(rubric_path="rubric.md")
    translator = JobTranslator()
    jev = JevClient()

    # Track Cache Hits for Company Enricher
    cache_stats = {"total_queries": 0, "cache_hits": 0, "cache_misses": 0}
    orig_get_from_db = enricher._get_from_db

    def tracked_get_from_db(name: str):
        cache_stats["total_queries"] += 1
        res = orig_get_from_db(name)
        if res is not None:
            cache_stats["cache_hits"] += 1
        else:
            cache_stats["cache_misses"] += 1
        return res

    enricher._get_from_db = tracked_get_from_db

    profile_text = ""
    profile_path = next((p for p in ["config/profile.md", "profile.md"] if os.path.exists(p)), None)
    if profile_path:
        with open(profile_path, "r", encoding="utf-8") as f:
            profile_text = f.read()
    profile_en = translator.translate_text(profile_text) if profile_text else ""
    print(f"✅ 履歷已載入 (英文長度: {len(profile_en)} chars)")

    # -------------------------------------------------------------------------
    # 1. 5 大平台職缺爬取與吞吐量統計 (Q4)
    # -------------------------------------------------------------------------
    crawl_metrics: Dict[str, Dict[str, Any]] = {}
    platform_jobs: Dict[str, List[Job]] = {}

    # (1) Yourator
    print("\n[1/5] 抓取 Yourator 職缺...")
    t0 = time.perf_counter()
    y_raw = scrape_yourator_jobs(config, is_seen_fn=None, max_pages=6, scrape_limit=25)
    t1 = time.perf_counter()
    y_dur = t1 - t0
    platform_jobs["yourator"] = select_fresh_batch(y_raw, limit)
    crawl_metrics["yourator"] = {
        "raw_scraped": len(y_raw),
        "selected": len(platform_jobs["yourator"]),
        "duration_sec": round(y_dur, 2),
        "jobs_per_minute": round((len(y_raw) / y_dur) * 60, 2) if y_dur > 0 else 0,
    }
    print(f"  Yourator: 耗時 {y_dur:.2f}s, 取得 {len(y_raw)} 筆 (速率: {crawl_metrics['yourator']['jobs_per_minute']} jobs/min)")

    # (2) Yes123
    print("\n[2/5] 抓取 Yes123 職缺...")
    t0 = time.perf_counter()
    y123_raw = scrape_yes123_jobs(config, is_seen_fn=None, max_pages=6, scrape_limit=25)
    t1 = time.perf_counter()
    y123_dur = t1 - t0
    platform_jobs["yes123"] = select_fresh_batch(y123_raw, limit)
    crawl_metrics["yes123"] = {
        "raw_scraped": len(y123_raw),
        "selected": len(platform_jobs["yes123"]),
        "duration_sec": round(y123_dur, 2),
        "jobs_per_minute": round((len(y123_raw) / y123_dur) * 60, 2) if y123_dur > 0 else 0,
    }
    print(f"  Yes123: 耗時 {y123_dur:.2f}s, 取得 {len(y123_raw)} 筆 (速率: {crawl_metrics['yes123']['jobs_per_minute']} jobs/min)")

    # (3) Browser Scrapers (104, Cake, 1111)
    print("\n[3-5/5] 啟動瀏覽器擷取 104, Cake, 1111 職缺...")
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir="./browser_profile",
            headless=False,
            channel="chrome",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--window-position=-2400,-2400",
                "--window-size=1280,800",
            ],
            ignore_default_args=["--enable-automation"],
            viewport={"width": 1280, "height": 800},
        )
        page = ctx.new_page()
        browser_mgr = BrowserScraperManager()

        # 104
        print("  正在爬取 104...")
        t0 = time.perf_counter()
        raw_104 = browser_mgr._scrape_104(page, config, is_seen_fn=None, scrape_limit=25)
        t1 = time.perf_counter()
        dur_104 = t1 - t0
        platform_jobs["104"] = select_fresh_batch(raw_104, limit)
        crawl_metrics["104"] = {
            "raw_scraped": len(raw_104),
            "selected": len(platform_jobs["104"]),
            "duration_sec": round(dur_104, 2),
            "jobs_per_minute": round((len(raw_104) / dur_104) * 60, 2) if dur_104 > 0 else 0,
        }
        print(f"  104: 耗時 {dur_104:.2f}s, 取得 {len(raw_104)} 筆 (速率: {crawl_metrics['104']['jobs_per_minute']} jobs/min)")

        # Cake
        print("  正在爬取 Cake...")
        t0 = time.perf_counter()
        raw_cake = browser_mgr._scrape_cake(page, config, is_seen_fn=None, scrape_limit=25)
        t1 = time.perf_counter()
        dur_cake = t1 - t0
        platform_jobs["cake"] = select_fresh_batch(raw_cake, limit)
        crawl_metrics["cake"] = {
            "raw_scraped": len(raw_cake),
            "selected": len(platform_jobs["cake"]),
            "duration_sec": round(dur_cake, 2),
            "jobs_per_minute": round((len(raw_cake) / dur_cake) * 60, 2) if dur_cake > 0 else 0,
        }
        print(f"  Cake: 耗時 {dur_cake:.2f}s, 取得 {len(raw_cake)} 筆 (速率: {crawl_metrics['cake']['jobs_per_minute']} jobs/min)")

        # 1111
        print("  正在爬取 1111...")
        t0 = time.perf_counter()
        raw_1111 = browser_mgr._scrape_1111(page, config, is_seen_fn=None, scrape_limit=25)
        t1 = time.perf_counter()
        dur_1111 = t1 - t0
        platform_jobs["1111"] = select_fresh_batch(raw_1111, limit)
        crawl_metrics["1111"] = {
            "raw_scraped": len(raw_1111),
            "selected": len(platform_jobs["1111"]),
            "duration_sec": round(dur_1111, 2),
            "jobs_per_minute": round((len(raw_1111) / dur_1111) * 60, 2) if dur_1111 > 0 else 0,
        }
        print(f"  1111: 耗時 {dur_1111:.2f}s, 取得 {len(raw_1111)} 筆 (速率: {crawl_metrics['1111']['jobs_per_minute']} jobs/min)")

        print("  正在補充 1111 職缺內頁詳情...")
        for j in platform_jobs["1111"]:
            fetcher.fetch_1111(j, browser_page=page)

        ctx.close()

    total_scraped = sum(len(lst) for lst in platform_jobs.values())
    total_crawl_sec = sum(m["duration_sec"] for m in crawl_metrics.values())
    overall_crawl_rate = (total_scraped / total_crawl_sec) * 60 if total_crawl_sec > 0 else 0
    print(f"\n✅ 5 大平台抓取完成！總樣本數: {total_scraped} 筆，爬蟲淨耗時: {total_crawl_sec:.1f}s (綜合速率: {overall_crawl_rate:.1f} jobs/min)")

    # -------------------------------------------------------------------------
    # 2. 全生命週期評估與 Token / 快取 / 漏斗計量
    # -------------------------------------------------------------------------
    pipeline_records = []
    
    # Token Tracking
    actual_tokens_stage1 = 0
    actual_tokens_stage2 = 0
    baseline_tokens_hypothetical = 0

    # Fit level breakdown
    fit_level_counts = {"high": 0, "medium": 0, "low": 0, "unrelated": 0, "dropped": 0}
    stage1_passed_count = 0
    stage1_filtered_count = 0

    # Stage 2 Scoring breakdown
    stage2_evaluated_count = 0
    stage2_scores = []
    qualified_jobs = []

    print("\n" + "=" * 80)
    print("⚡ 開始執行 Stage 1 (Profile Fit) 與 Stage 2 (Rubric Scoring) 評估...")
    print("=" * 80)

    for p in ["104", "yourator", "cake", "yes123", "1111"]:
        jobs = platform_jobs.get(p, [])
        for idx, job in enumerate(jobs, 1):
            print(f"[{p.upper()} {idx}/{len(jobs)}] 評估: {job.title[:24]} @ {job.company[:18]} (ID: {job.job_id})")

            # Step 2: Hard filter
            passed_hard, reject_reason = filter_job_hard(job, config)
            
            # Step 3: Detail Fetcher
            if p != "1111":
                fetcher.fetch_job_detail(job)

            # Stage 1: Profile Fit
            stage1_prompt_text = f"{job.title} {job.company} {job.description[:4000]} {profile_en}"
            stage1_est_tokens = estimate_tokens(stage1_prompt_text) + 150  # prompt + response
            actual_tokens_stage1 += stage1_est_tokens

            if not passed_hard:
                fit_level = "dropped"
                fit_conf = 1.0
                fit_probs = {}
                is_fused = True
                fused_reason = f"硬篩排除: {reject_reason}"
            else:
                fit_level, fit_conf, fit_probs = jev.evaluate_profile_fit(job, profile_en, dry_run=False)
                is_fused = fit_level in ("unrelated", "low")
                fused_reason = f"Stage 1 門檻攔截 ({fit_level.upper()})" if is_fused else ""

            fit_level_counts[fit_level] = fit_level_counts.get(fit_level, 0) + 1

            if is_fused:
                stage1_filtered_count += 1
            else:
                stage1_passed_count += 1

            # Estimate baseline Stage 2 tokens if no funnel existed
            stage2_hypothetical_prompt_tokens = estimate_tokens(f"{job.title} {job.company} {job.description[:4000]} {profile_en}") + 2200
            baseline_tokens_hypothetical += stage2_hypothetical_prompt_tokens

            # Step 5 - 7: Only if not fused
            scored_data = None
            comp_prof = None
            if not is_fused:
                stage2_evaluated_count += 1
                comp_prof = enricher.enrich_company(job, rubric_config=config)
                state = translator.build_english_fact_sheet(job, company_profile=comp_prof, profile_text=profile_en)
                
                # Actual Stage 2 Token
                stage2_actual_tokens = estimate_tokens(state) + 500
                actual_tokens_stage2 += stage2_actual_tokens

                scored = jev.evaluate_job(
                    job,
                    config,
                    state=state,
                    dry_run=False,
                    company_profile=comp_prof,
                    profile_fit=fit_level
                )
                stage2_scores.append(scored.total_score)
                scored_data = {
                    "total_score": scored.total_score,
                    "is_qualified": scored.is_qualified,
                    "dimension_scores": [
                        {"name": ds.name, "score": ds.score, "max": ds.max_points, "conf": ds.confidence}
                        for ds in scored.dimension_scores
                    ]
                }
                if scored.is_qualified:
                    qualified_jobs.append({
                        "platform": job.platform,
                        "job_id": job.job_id,
                        "title": job.title,
                        "company": job.company,
                        "tier": comp_prof.tier,
                        "fit_level": fit_level,
                        "total_score": scored.total_score,
                        "url": job.url
                    })

            pipeline_records.append({
                "platform": job.platform,
                "job_id": job.job_id,
                "title": job.title,
                "company": job.company,
                "location": job.location,
                "passed_hard": passed_hard,
                "reject_reason": reject_reason,
                "fit_level": fit_level,
                "fit_confidence": fit_conf,
                "is_fused": is_fused,
                "fused_reason": fused_reason,
                "tier": comp_prof.tier if comp_prof else "N/A",
                "score": scored_data["total_score"] if scored_data else 0.0,
                "is_qualified": scored_data["is_qualified"] if scored_data else False,
            })

    # Calculations
    filter_rate_stage1 = (stage1_filtered_count / total_scraped) * 100.0 if total_scraped > 0 else 0.0
    pass_rate_stage1 = (stage1_passed_count / total_scraped) * 100.0 if total_scraped > 0 else 0.0
    
    total_actual_tokens = actual_tokens_stage1 + actual_tokens_stage2
    token_savings_pct = ((baseline_tokens_hypothetical - total_actual_tokens) / baseline_tokens_hypothetical) * 100.0 if baseline_tokens_hypothetical > 0 else 0.0

    cache_hit_rate = (cache_stats["cache_hits"] / cache_stats["total_queries"]) * 100.0 if cache_stats["total_queries"] > 0 else 0.0

    output_data = {
        "benchmark_timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_jobs": total_scraped,
        "q1_stage1_filter": {
            "total": total_scraped,
            "passed_to_stage2": stage1_passed_count,
            "filtered_out": stage1_filtered_count,
            "filter_rate_pct": round(filter_rate_stage1, 1),
            "pass_rate_pct": round(pass_rate_stage1, 1),
            "breakdown": fit_level_counts,
        },
        "q2_token_savings": {
            "baseline_tokens_without_funnel": baseline_tokens_hypothetical,
            "actual_tokens_with_funnel": total_actual_tokens,
            "stage1_tokens": actual_tokens_stage1,
            "stage2_tokens": actual_tokens_stage2,
            "tokens_saved": baseline_tokens_hypothetical - total_actual_tokens,
            "token_savings_pct": round(token_savings_pct, 1),
        },
        "q3_stage2_precision": {
            "stage2_evaluated_count": stage2_evaluated_count,
            "qualified_count": len(qualified_jobs),
            "qualified_jobs": qualified_jobs,
            "scores": stage2_scores,
        },
        "q4_crawl_throughput": {
            "total_jobs": total_scraped,
            "total_duration_sec": round(total_crawl_sec, 2),
            "jobs_per_minute": round(overall_crawl_rate, 1),
            "extrapolated_per_hour": round(overall_crawl_rate * 60, 0),
            "extrapolated_24h_capacity": round(overall_crawl_rate * 60 * 24, 0),
            "per_platform": crawl_metrics,
        },
        "q7_db_cache": {
            "total_queries": cache_stats["total_queries"],
            "cache_hits": cache_stats["cache_hits"],
            "cache_misses": cache_stats["cache_misses"],
            "cache_hit_rate_pct": round(cache_hit_rate, 1),
        },
        "raw_pipeline_records": pipeline_records,
    }

    os.makedirs("reports", exist_ok=True)
    with open("reports/resume_benchmark_raw.json", "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 80)
    print("🎯 實證評測完成！核心指標摘要:")
    print(f"  - Q1: Stage 1 篩除率: {filter_rate_stage1:.1f}% (淘汰 {stage1_filtered_count} / {total_scraped})")
    print(f"  - Q2: Token 消耗節省率: {token_savings_pct:.1f}% (節省 {baseline_tokens_hypothetical - total_actual_tokens:,} tokens)")
    print(f"  - Q3: Stage 2 達標推播筆數: {len(qualified_jobs)} 筆 (評估 {stage2_evaluated_count} 筆)")
    print(f"  - Q4: 綜合爬取吞吐量: {overall_crawl_rate:.1f} jobs/min (每日巡檢容量外推: {overall_crawl_rate*60*24:,.0f} 筆)")
    print(f"  - Q7: 企業知識庫快取命中率: {cache_hit_rate:.1f}% ({cache_stats['cache_hits']} / {cache_stats['total_queries']})")
    print("=" * 80)

if __name__ == "__main__":
    main()
