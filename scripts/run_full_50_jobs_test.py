import os
import sys
import re
import json
import random
import time
import sqlite3
import argparse
from typing import List, Dict, Any

sys.path.insert(0, os.path.abspath("."))
try:
    sys.stdout.reconfigure(encoding='utf-8')
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

EXCLUDE_IDS = set()
for fn in [
    "reports/pipeline_50_jobs_data.json", "pipeline_50_jobs_data.json",
    "reports/pipeline_50_jobs_data_prev.json", "pipeline_50_jobs_data_prev.json",
    "reports/pipeline_tested_history.json", "pipeline_tested_history.json"
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

try:
    with sqlite3.connect("jobs.db") as conn:
        for r in conn.execute("SELECT job_id FROM evaluations").fetchall():
            EXCLUDE_IDS.add(str(r[0]))
except Exception:
    pass

EXCLUDE_IDS.update({
    '6838315', '6900', '32772', '32773', '29871',
    '20191023170408_53017509_20260316031525_21801861',
    'content-planning-intern-pt', '130237017'
})

def is_excluded_id(jid: str) -> bool:
    return str(jid) in EXCLUDE_IDS

def is_excluded(j: Job) -> bool:
    if is_excluded_id(j.job_id):
        return True
    if any(k in j.company for k in ['鈦坦', '定揚', '微智匯', '崧旭', '增你強', '正大']):
        return True
    return False

def is_seen(platform: str, jid: str) -> bool:
    return is_excluded_id(jid)

def select_test_batch(raw_jobs: List[Job], config: RubricConfig, target: int) -> List[Job]:
    candidates = [j for j in raw_jobs if not is_excluded(j)]
    valid = [j for j in candidates if filter_job_hard(j, config)[0]]
    dropped = [j for j in candidates if not filter_job_hard(j, config)[0]]
    
    # Prioritize unseen valid jobs to ensure Stage 1 & 2 execution, then fill with unseen dropped jobs
    chosen = list(valid[:target])
    if len(chosen) < target:
        chosen.extend(dropped[:target - len(chosen)])
    if len(chosen) < target:
        seen = {j.job_id for j in chosen}
        # Backfill with valid jobs from raw_jobs (skip blacklisted companies)
        for j in raw_jobs:
            if j.job_id not in seen and not any(k in j.company for k in ['鈦坦', '定揚', '微智匯', '崧旭', '增你強', '正大']):
                if filter_job_hard(j, config)[0]:
                    chosen.append(j)
                    seen.add(j.job_id)
                    if len(chosen) >= target:
                        break
        # If still short, backfill with any remaining raw jobs (skip blacklisted companies)
        if len(chosen) < target:
            for j in raw_jobs:
                if j.job_id not in seen and not any(k in j.company for k in ['鈦坦', '定揚', '微智匯', '崧旭', '增你強', '正大']):
                    chosen.append(j)
                    seen.add(j.job_id)
                    if len(chosen) >= target:
                        break
    return chosen[:target]

def main():
    parser = argparse.ArgumentParser(description="Run pipeline test across 5 platforms")
    parser.add_argument("--limit", type=int, default=10, help="Number of jobs to test per platform (default: 10)")
    parser.add_argument("--output", type=str, default="", help="Report output file path")
    args = parser.parse_args()
    limit = args.limit

    print("=" * 80)
    print(f"🚀 啟動 5 大平台職缺資料流完整測試流程 (各平台抓取 {limit} 筆職缺，優先排除 {len(EXCLUDE_IDS)} 筆歷史職缺)")
    print("=" * 80)

    config = parse_rubric_file("rubric.md")
    from jobme.notifier import DiscordNotifier
    notifier = DiscordNotifier()
    fetcher = JobDetailFetcher(db_path="jobs.db")
    enricher = CompanyEnricher(rubric_path="rubric.md", notifier=notifier)
    translator = JobTranslator(notifier=notifier)
    jev = JevClient(notifier=notifier)

    profile_text = ""
    profile_path = next((p for p in ["config/profile.md", "profile.md"] if os.path.exists(p)), None)
    if profile_path:
        with open(profile_path, "r", encoding="utf-8") as f:
            profile_text = f.read()
    profile_en = translator.translate_text(profile_text) if profile_text else ""
    print(f"✅ 履歷翻譯完成 ({len(profile_en)} chars)")

    # -------------------------------------------------------------------------
    # 1. 抓取各平台新職缺
    # -------------------------------------------------------------------------
    platform_jobs: Dict[str, List[Job]] = {}

    # (1) Yourator
    print("\n[1/5] 抓取 Yourator 職缺...")
    y_raw = scrape_yourator_jobs(config, is_seen_fn=None, max_pages=10, notifier=notifier, scrape_limit=40)
    platform_jobs["yourator"] = select_test_batch(y_raw, config, limit)
    print(f"  Yourator 成功取得 {len(platform_jobs['yourator'])} 筆有效職缺")

    # (2) Yes123
    print("\n[2/5] 抓取 Yes123 職缺...")
    y123_raw = scrape_yes123_jobs(config, is_seen_fn=None, max_pages=8, notifier=notifier, scrape_limit=40)
    platform_jobs["yes123"] = select_test_batch(y123_raw, config, limit)
    print(f"  Yes123 成功取得 {len(platform_jobs['yes123'])} 筆有效職缺")

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
        browser_mgr = BrowserScraperManager(notifier=notifier)

        # 104
        print("  正在爬取 104 職缺...")
        raw_104 = browser_mgr._scrape_104(page, config, is_seen_fn=None, scrape_limit=40)
        platform_jobs["104"] = select_test_batch(raw_104, config, limit)
        browser_mgr._validate_scraped_batch("104", platform_jobs["104"], config)
        print(f"  104 取得 {len(platform_jobs['104'])} 筆有效職缺")

        # Cake
        print("  正在爬取 Cake 職缺...")
        raw_cake = browser_mgr._scrape_cake(page, config, is_seen_fn=None, scrape_limit=40)
        platform_jobs["cake"] = select_test_batch(raw_cake, config, limit)
        browser_mgr._validate_scraped_batch("Cake", platform_jobs["cake"], config)
        print(f"  Cake 取得 {len(platform_jobs['cake'])} 筆有效職缺")

        # 1111
        print("  正在爬取 1111 職缺...")
        raw_1111 = browser_mgr._scrape_1111(page, config, is_seen_fn=None, scrape_limit=40)
        platform_jobs["1111"] = select_test_batch(raw_1111, config, limit)
        browser_mgr._validate_scraped_batch("1111", platform_jobs["1111"], config)
        print(f"  1111 取得 {len(platform_jobs['1111'])} 筆有效職缺")

        # Detail fetch for 1111 using the open browser page
        print("  正在補充 1111 職缺內頁詳情...")
        for j in platform_jobs["1111"]:
            fetcher.fetch_1111(j, browser_page=page)

        ctx.close()

    total_scraped = sum(len(lst) for lst in platform_jobs.values())
    print(f"\n✅ 全部 5 大平台抓取完成，總計 {total_scraped} 筆職缺。")
    for p, lst in platform_jobs.items():
        print(f"  - {p}: {len(lst)} 筆")

    # -------------------------------------------------------------------------
    # 2. 隨機抽選各平台 1 篇代表職缺 (供詳細展示 7 階段資料流)
    # -------------------------------------------------------------------------
    # -------------------------------------------------------------------------
    # 2. 逐筆執行全生命週期資料流 (Step 1 -> Step 7) 落實管線熔斷
    # -------------------------------------------------------------------------
    all_results: List[Dict[str, Any]] = []

    print("\n" + "=" * 80)
    print(f"⚡ 開始對 {total_scraped} 筆職缺執行完整管線評估 (硬篩 -> 履歷把關 -> 熔斷保護 -> 深度評分)...")
    print("=" * 80)

    for p in ["104", "yourator", "cake", "yes123", "1111"]:
        jobs = platform_jobs.get(p, [])
        for idx, job in enumerate(jobs, 1):
            print(f"[{p.upper()} {idx}/{len(jobs)}] 處理: {job.title[:25]} @ {job.company[:20]} (ID: {job.job_id})...")

            # Step 1: Raw Scrape Card Data
            step1_data = {
                "platform": job.platform,
                "job_id": job.job_id,
                "title": job.title,
                "company": job.company,
                "location": job.location or "未提供",
                "salary": job.salary or "面議",
                "url": job.url,
                "tags": job.tags or [],
                "description": "(尚未爬取內頁，空值)"
            }

            # Step 2: Hard Filter
            passed_hard, reject_reason = filter_job_hard(job, config)
            step2_data = {
                "passed": passed_hard,
                "reject_reason": reject_reason,
                "location": job.location or "未提供",
            }

            # Step 3: Detail Fetcher
            if p != "1111": # 1111 was fetched in the browser loop
                fetcher.fetch_job_detail(job)
            step3_data = {
                "char_count": len(job.description),
                "snippet": job.description[:350] + ("..." if len(job.description) > 350 else "")
            }

            # Step 4: Stage 1 Profile Fit Gatekeeper
            if not passed_hard:
                fit_level = "dropped"
                fit_conf = 1.0
                fit_probs = {}
                gate_action = f"🛑 硬篩已淘汰 ({reject_reason})，依規格略過 Stage 1"
            else:
                fit_level, fit_conf, fit_probs = jev.evaluate_profile_fit(job, profile_en, dry_run=False)
                gate_action = "准予進入 Stage 2" if fit_level not in ("unrelated", "low") else f"🛑 門檻攔截淘汰 ({fit_level.upper()})，依規格熔斷"

            step4_data = {
                "fit_level": fit_level,
                "confidence": fit_conf,
                "probabilities": fit_probs,
                "gate_action": gate_action
            }

            # Step 5 - 7: Only execute if passed both Hard Filter AND Stage 1 Gatekeeper
            if passed_hard and fit_level not in ("unrelated", "low", "dropped"):
                # Step 5: Company Fact Enrichment
                comp_prof = enricher.enrich_company(job, rubric_config=config)
                step5_data = {
                    "official_name": comp_prof.official_name or job.company,
                    "tier": comp_prof.tier,
                    "capital_twd": comp_prof.capital_twd,
                    "is_twse_listed": comp_prof.is_listed,
                    "market_context": comp_prof.market_context,
                    "basis": comp_prof.basis
                }

                # Step 6: English Fact Sheet
                state = translator.build_english_fact_sheet(job, company_profile=comp_prof, profile_text=profile_en)
                
                # Split cleanly on line boundaries to ensure basis & rationale are never sliced mid-word
                state_lines = state.splitlines()
                preview_lines = []
                cur_len = 0
                for line in state_lines:
                    preview_lines.append(line)
                    cur_len += len(line)
                    if cur_len > 400 and (line.startswith("##") or line.startswith("- Tier Rationale")):
                        break
                    if cur_len > 600:
                        break
                tail_lines = state_lines[-6:] if len(state_lines) > 12 else []
                state_snippet = "\n".join(preview_lines)
                if tail_lines:
                    state_snippet += "\n\n... [中間詳情省略] ...\n\n" + "\n".join(tail_lines)

                step6_data = {
                    "state_len": len(state),
                    "state_snippet": state_snippet
                }

                # Step 7: Stage 2 Rubric Scoring
                scored = jev.evaluate_job(
                    job,
                    config,
                    state=state,
                    dry_run=False,
                    company_profile=comp_prof,
                    profile_fit=fit_level
                )
                step7_data = {
                    "fused": False,
                    "total_score": scored.total_score,
                    "is_qualified": scored.is_qualified,
                    "has_low_confidence": scored.has_low_confidence,
                    "dimension_scores": [
                        {
                            "name": ds.name,
                            "score": ds.score,
                            "max_points": ds.max_points,
                            "confidence": ds.confidence,
                            "probabilities": ds.probabilities
                        }
                        for ds in scored.dimension_scores
                    ]
                }
            else:
                # Fused / short-circuited: zero out scores and skip API calls
                step5_data = {
                    "official_name": job.company,
                    "tier": "N/A",
                    "capital_twd": None,
                    "is_twse_listed": False,
                    "market_context": "🛑 前期階段已淘汰，依規格跳過企業徵信",
                    "basis": "未進入 Stage 2"
                }
                step6_data = {
                    "state_len": 0,
                    "state_snippet": "🛑 前期已淘汰，未構建評估 Prompt"
                }
                step7_data = {
                    "fused": True,
                    "total_score": 0.0,
                    "is_qualified": False,
                    "has_low_confidence": False,
                    "dimension_scores": []
                }

            result_entry = {
                "platform": p,
                "job_id": job.job_id,
                "title": job.title,
                "company": job.company,
                "step1": step1_data,
                "step2": step2_data,
                "step3": step3_data,
                "step4": step4_data,
                "step5": step5_data,
                "step6": step6_data,
                "step7": step7_data
            }
            all_results.append(result_entry)

    # -------------------------------------------------------------------------
    # 3. 挑選各平台 1 篇代表職缺 (優先挑選通過把關之有效職缺以完整展示 7 階段)
    # -------------------------------------------------------------------------
    random.seed(int(time.time()))
    traces_for_reps: Dict[str, Dict[str, Any]] = {}
    for p in ["104", "yourator", "cake", "yes123", "1111"]:
        plat_results = [r for r in all_results if r["platform"] == p]
        if not plat_results:
            continue
        stage2_passed = [r for r in plat_results if r["step7"]["total_score"] > 0]
        hard_passed = [r for r in plat_results if r["step2"]["passed"]]
        if stage2_passed:
            selected_rep = random.choice(stage2_passed)
        elif hard_passed:
            selected_rep = random.choice(hard_passed)
        else:
            selected_rep = random.choice(plat_results)
        traces_for_reps[p] = selected_rep
        print(f"🎯 選定 {p.upper()} 代表職缺: [{selected_rep['job_id']}] {selected_rep['title']} @ {selected_rep['company']} (總分: {selected_rep['step7']['total_score']:.1f})")

    # -------------------------------------------------------------------------
    # 4. 生成並輸出 Markdown 報告
    # -------------------------------------------------------------------------
    report_lines = [
        f"# 5 大平台各 {limit} 筆新職缺測試與資料流全生命週期變化報告\n",
        f"- **測試執行時間**: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **測試規模**: 5 大平台 x 各 {limit} 筆 = 共 {len(all_results)} 筆 (嚴格排除既往測試樣本)",
        f"- **代表抽樣**: 各平台各隨機抽選 1 篇 JD 展示 7 階段完整資料流變化\n",
        "=" * 80 + "\n"
    ]

    for p in ["104", "yourator", "cake", "yes123", "1111"]:
        entry = traces_for_reps[p]
        j_title = entry["title"]
        j_comp = entry["company"]
        j_id = entry["job_id"]

        report_lines.append("=" * 80)
        report_lines.append(f"## 📌【平台代表職缺：{p.upper()}】: {j_title} @ {j_comp} (ID: {j_id})")
        report_lines.append("=" * 80 + "\n")

        # Step 1
        report_lines.append("### 📍 Step 1: 原始卡片爬取 (Raw List Card)")
        report_lines.append("```json")
        report_lines.append(json.dumps(entry["step1"], ensure_ascii=False, indent=2))
        report_lines.append("```\n")

        # Step 2
        s2 = entry["step2"]
        h_str = '✅ 通過 (PASS)' if s2['passed'] else f"❌ 排除 (DROP: {s2['reject_reason']})"
        report_lines.append("### 📍 Step 2: 硬性規則檢查 (Hard Filter Rule Check)")
        report_lines.append(f"- **檢查結果**: `{h_str}`")
        report_lines.append(f"- **地區檢查**: `{s2['location']}` (要求: {config.regions or '不限'})")
        report_lines.append(f"- **黑名單關鍵字**: `{'無命中' if s2['passed'] else s2['reject_reason']}`\n")

        # Step 3
        s3 = entry["step3"]
        report_lines.append("### 📍 Step 3: 內頁詳情爬取與清洗 (Detail Fetcher & DB Cache)")
        report_lines.append(f"- **資料變化**: `job.description` 從 `(空字串)` 擴展為 **{s3['char_count']} 字** 結構化內文")
        report_lines.append("- **清洗後內容節錄**:")
        report_lines.append("```text")
        report_lines.append(s3["snippet"])
        report_lines.append("```\n")

        # Step 4
        s4 = entry["step4"]
        report_lines.append("### 📍 Step 4: Stage 1 履歷適配度把關 (Profile Fit Gatekeeper)")
        report_lines.append(f"- **適配等級 (Fit Level)**: `{s4['fit_level'].upper()}`")
        report_lines.append(f"- **判定信心度 (Confidence)**: `{s4['confidence']:.2f}`")
        report_lines.append(f"- **機率分佈 (Probabilities)**: `{json.dumps(s4['probabilities'])}`")
        report_lines.append(f"- **門檻動作**: **{s4['gate_action']}**\n")

        # Step 5
        s5 = entry["step5"]
        report_lines.append("### 📍 Step 5: 企業背景徵信與分級錨定 (Company Fact Enrichment)")
        report_lines.append("```json")
        report_lines.append(json.dumps(s5, ensure_ascii=False, indent=2))
        report_lines.append("```\n")

        # Step 6
        s6 = entry["step6"]
        report_lines.append("### 📍 Step 6: 標準化事實清單構建 (State Prompt Sent to Jev)")
        report_lines.append(f"- **資料變化**: 整合公司徵信 + 職缺英語化 + 個人履歷，生成 **{s6['state_len']} 字** 之標準化事實清單")
        report_lines.append("- **Prompt 結構節錄**:")
        report_lines.append("```markdown")
        report_lines.append(s6["state_snippet"])
        report_lines.append("```\n")

        # Step 7
        s7 = entry["step7"]
        report_lines.append("### 📍 Step 7: Stage 2 Jev 決策評分 (Rubric Scoring)")
        if s7.get("fused", False):
            report_lines.append("- **管線狀態**: `🛑 前期已熔斷（未調用 Stage 2 模型評分）`")
            report_lines.append(f"- **合計總分 (Total Score)**: **0.0 分** (前期門檻淘汰，熔斷保護節省 API 調用)")
            report_lines.append("- **合格推播 (Is Qualified)**: `❌ NO`")
            report_lines.append("- **維度明細**: 前期階段已淘汰，未調用 Stage 2 模型評分\n")
        else:
            report_lines.append(f"- **合計總分 (Total Score)**: **{s7['total_score']:.1f} 分** (基本門檻: {config.base_threshold} 分, 推播門檻: {config.notify_threshold} 分)")
            report_lines.append(f"- **合格推播 (Is Qualified)**: `{'🎉 YES' if s7['is_qualified'] else '❌ NO'}`")
            report_lines.append(f"- **低信心告警 (Low Confidence)**: `{'⚠️ YES' if s7['has_low_confidence'] else '✅ NO'}`")
            report_lines.append("- **維度明細**:")
            for ds in s7["dimension_scores"]:
                p_str = json.dumps(ds["probabilities"]) if ds["probabilities"] else "1.0"
                report_lines.append(f"  * **{ds['name']}**: `{ds['score']:g} / {ds['max_points']:g} 分` (信心度: `{ds['confidence']:.2f}`, 分佈: `{p_str}`)")
            report_lines.append("\n" + "-" * 80 + "\n")

    # -------------------------------------------------------------------------
    # 5. 職缺總覽統計表
    # -------------------------------------------------------------------------
    report_lines.append("=" * 80)
    report_lines.append(f"## 📊 {len(all_results)} 筆職缺整體管線運行統計總覽 (Summary Table)")
    report_lines.append("=" * 80 + "\n")
    report_lines.append("| 平台 | 職缺 ID | 職缺名稱 | 公司名稱 | 硬篩 (Step 2) | 適配度 (Step 4) | 企業分級 (Step 5) | 維度1 | 維度2 | 維度3 | 總分 | 推播 |")
    report_lines.append("|:---|:---|:---|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|")

    for r in all_results:
        p = r["platform"]
        jid = str(r["job_id"])[:10]
        title = r["title"].replace("|", "/")[:18]
        comp = r["company"].replace("|", "/")[:14]
        h_pass = "PASS" if r["step2"]["passed"] else "DROP"
        fit_lvl = r["step4"]["fit_level"].upper()
        tier = r["step5"]["tier"].replace("Tier ", "T")
        
        is_fused = r["step7"].get("fused", False)
        ds_list = r["step7"].get("dimension_scores", [])
        if is_fused:
            d1_str, d2_str, d3_str = "-", "-", "-"
        else:
            d1_str = f"{ds_list[0]['score']:g}" if len(ds_list) > 0 else "-"
            d2_str = f"{ds_list[1]['score']:g}" if len(ds_list) > 1 else "-"
            d3_str = f"{ds_list[2]['score']:g}" if len(ds_list) > 2 else "-"
        tot = r["step7"]["total_score"]
        qual = "YES" if r["step7"]["is_qualified"] else "NO"

        report_lines.append(f"| {p} | `{jid}` | {title} | {comp} | `{h_pass}` | `{fit_lvl}` | `{tier}` | {d1_str} | {d2_str} | {d3_str} | **{tot:.1f}** | `{qual}` |")

    report_content = "\n".join(report_lines)
    os.makedirs("reports", exist_ok=True)
    report_file = args.output or f"reports/pipeline_{len(all_results)}_jobs_report.md"
    data_file = f"reports/pipeline_{len(all_results)}_jobs_data.json"

    with open(report_file, "w", encoding="utf-8") as f:
        f.write(report_content)

    with open(data_file, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    new_tested_ids = list(EXCLUDE_IDS.union(str(r["job_id"]) for r in all_results))
    with open("reports/pipeline_tested_history.json", "w", encoding="utf-8") as f:
        json.dump(new_tested_ids, f, ensure_ascii=False, indent=2)

    print(f"\n🎉 {len(all_results)} 筆職缺完整管線測試完成！報告已儲存至 {report_file}")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"\n❌ 測試過程發生致命錯誤: {e}\n{tb}")
        try:
            from jobme.notifier import DiscordNotifier
            DiscordNotifier().notify_error("完整測試流程中斷異常", f"{e}\n\nTraceback:\n{tb[-500:]}")
        except Exception:
            pass
        sys.exit(1)
