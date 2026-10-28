"""
JobMe Self-Check Test Suite (Assert-based, no heavy test frameworks)
Tests:
1. Rubric parser and validation
2. SQLite database init, deduplication, and persistence
3. Hard filter logic (keywords, districts, job types)
4. Jev score computation mathematics (expected value calculation)
5. CSV export formatting
6. CompanyEnricher (TWSE Exact Matching, Substring Anti-Pollution, Fallbacks)
7. JobTranslator (Salary formatting, Fact Sheet)
"""
import os
import sys

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

if sys.stdout:
    sys.stdout.reconfigure(encoding="utf-8")

from jobme.company_enricher import CompanyEnricher, CompanyProfile
from jobme.db import clear_db, get_connection, init_db, is_job_seen, save_evaluation, save_job
from jobme.detail_fetcher import JobDetailFetcher
from jobme.exporter import append_scored_jobs_to_csv, clear_records
from jobme.jev import JevClient
from jobme.models import DimensionScore, Job, ScoredJob
from jobme.rubric import filter_job_hard, get_tier_points, parse_rubric_file

from jobme.translator import JobTranslator


def test_rubric_parser():
    print("Testing rubric parser...")
    config = parse_rubric_file("rubric.example.md")
    assert len(config.keywords) > 0, "Keywords must not be empty"
    assert config.job_type == "internship"
    assert config.base_threshold == 65.0, f"Expected base_threshold 65.0, got {config.base_threshold}"
    assert config.notify_threshold == 75.0, f"Expected notify_threshold 75.0, got {config.notify_threshold}"
    assert config.save_all_evaluated is False, "Expected save_all_evaluated False"
    assert len(config.dimensions) == 3, f"Expected 3 dimensions, got {len(config.dimensions)}"
    assert config.total_max_points == 100.0, f"Total points must sum to 100, got {config.total_max_points}"
    for dim in config.dimensions:
        assert len(dim.criteria) >= 2, f"Dimension {dim.name} must have at least 2 levels"
        pts = dim.level_points
        assert pts == sorted(pts), f"Levels in {dim.name} must be sorted ascending"
    print("  -> Rubric parser passed!")


def test_dual_threshold_and_save_all():
    print("Testing dual threshold & save-all archiving logic...")
    config = parse_rubric_file("rubric.example.md")

    # 1. Backward compatibility check
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as tf:
        tf.write("---\nnotify_threshold: 80.0\n---\n## Dimension 1 / 維度一 (100%)\n- 0: None\n- 100: Top\n")
        temp_path = tf.name
    try:
        legacy_config = parse_rubric_file(temp_path)
        assert legacy_config.notify_threshold == 80.0
        assert legacy_config.base_threshold == 80.0, "base_threshold should fallback to notify_threshold"
        assert legacy_config.save_all_evaluated is False
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    # 2. Decision logic simulation
    scores = [50.0, 68.0, 82.0]
    archived_normal = [
        s for s in scores
        if (s >= config.base_threshold) or config.save_all_evaluated
    ]
    qualified_normal = [s for s in scores if s >= config.notify_threshold]
    assert archived_normal == [68.0, 82.0], f"Expected [68.0, 82.0], got {archived_normal}"
    assert qualified_normal == [82.0], f"Expected [82.0], got {qualified_normal}"

    save_all = True
    archived_save_all = [
        s for s in scores
        if (s >= config.base_threshold) or save_all
    ]
    assert archived_save_all == [50.0, 68.0, 82.0], "All evaluated jobs must be archived in save-all mode"
    print("  -> Dual threshold & save-all passed!")


def test_hard_filter():
    print("Testing hard filter...")
    config = parse_rubric_file("rubric.example.md")
    config.districts = ["信義區", "大安區"]

    # Valid job in 信義區
    job_ok = Job(
        platform="yourator",
        job_id="1",
        title="資料分析實習生",
        company="優質外商",
        location="台北市信義區松仁路",
        salary="NT$ 200/hr",
        url="https://example.com/1",
    )
    passed, reason = filter_job_hard(job_ok, config)
    assert passed, f"Job in 信義區 should pass, but got: {reason}"

    # Excluded keyword job
    job_bad_kw = Job(
        platform="yourator",
        job_id="2",
        title="電話行銷業務專員",
        company="業務公司",
        location="台北市信義區",
        salary="面議",
        url="https://example.com/2",
    )
    passed, reason = filter_job_hard(job_bad_kw, config)
    assert not passed and "排除關鍵字" in reason, f"Should reject excluded keyword: {reason}"

    # Wrong district
    job_wrong_dist = Job(
        platform="yourator",
        job_id="3",
        title="後端實習生",
        company="某公司",
        location="台北市北投區",
        salary="面議",
        url="https://example.com/3",
    )
    passed, reason = filter_job_hard(job_wrong_dist, config)
    assert not passed and "行政區" in reason, f"Should reject non-target district: {reason}"
    print("  -> Hard filter passed!")


def test_sqlite_db():
    print("Testing SQLite deduplication & evaluation tracking...")
    test_db = "test_jobs.db"
    if os.path.exists(test_db):
        os.remove(test_db)

    init_db(test_db)
    assert not is_job_seen("yourator", "999", test_db)

    dummy_job = Job(
        platform="yourator",
        job_id="999",
        title="測試實習生",
        company="測試公司",
        location="台北市",
        salary="面議",
        url="https://example.com/test",
    )
    save_job(dummy_job, db_path=test_db)
    assert is_job_seen("yourator", "999", test_db), "Job should now be marked as seen"

    # Test DetailFetcher persistence (with comp=None, must not fail NOT NULL constraint)
    fetcher = JobDetailFetcher(db_path=test_db)
    fetcher._persist_to_db("yourator", "999", "更新詳細說明")
    with get_connection(test_db) as conn:
        row = conn.execute("SELECT company, description FROM jobs WHERE platform = 'yourator' AND job_id = '999'").fetchone()
        assert row["company"] == "測試公司", "Existing company should be preserved when comp is None"
        assert row["description"] == "更新詳細說明", "Description should be updated"

    # Save evaluation
    scored = ScoredJob(
        job=dummy_job,
        total_score=85.5,
        is_qualified=True,
        dimension_scores=[
            DimensionScore(
                name="企業品牌",
                max_points=40,
                score=35.0,
                confidence=0.9,
                probabilities={0: 0.0, 1: 0.1, 2: 0.9},
                level_points=[0, 15, 40],
            )
        ],
    )
    save_evaluation(scored, test_db)

    # Test clear_db
    clear_db(test_db)
    assert not is_job_seen("yourator", "999", test_db), "Job should no longer be seen after clear_db"

    if os.path.exists(test_db):
        os.remove(test_db)
    print("  -> SQLite deduplication & clear_db passed!")


def test_company_enricher():
    print("Testing CompanyEnricher (TWSE Exact Matching, Substring Anti-Pollution, Fallbacks)...")
    if os.path.exists("test_jobs.db"):
        os.remove("test_jobs.db")
    enricher = CompanyEnricher(db_path="test_jobs.db", deepseek_api_key="")

    # 1. TWSE exact match (國泰金控 >= 10B -> Tier 1)
    prof_cathay = enricher.enrich_company("國泰金融控股股份有限公司")
    assert prof_cathay.is_listed is True
    assert prof_cathay.tier == "Tier 1"
    assert prof_cathay.capital_twd and prof_cathay.capital_twd > 10_000_000_000

    # 2. Substring Anti-Pollution verification (Critical regressions)
    # 易可思科技 contains characters "思科" but must NOT be misclassified as Tier 1 Cisco
    prof_cisco_false = enricher.enrich_company("易可思科技股份有限公司")
    assert prof_cisco_false.tier != "Tier 1", f"Expected not Tier 1, got {prof_cisco_false.tier}"
    assert prof_cisco_false.is_listed is False
    assert "台灣思科" not in prof_cisco_false.official_name

    # 台灣群創廣告行銷 must NOT match 群創光電
    prof_inno_false = enricher.enrich_company("台灣群創廣告行銷有限公司")
    assert prof_inno_false.tier != "Tier 1", f"Expected not Tier 1, got {prof_inno_false.tier}"
    assert "群創光電" not in prof_inno_false.official_name
    assert prof_inno_false.is_listed is False

    # 鼎泰豐小吃店 must NOT match 泰豐輪胎
    prof_ding_false = enricher.enrich_company("鼎泰豐小吃店股份有限公司")
    assert "泰豐輪胎" not in prof_ding_false.official_name
    assert prof_ding_false.is_listed is False


    # 3. Fallback behavior (provisional, never Tier 4 in fallback)
    prof_unknown = enricher.enrich_company("某某地方手作小工坊")
    assert prof_unknown.tier == "Tier 3"
    assert prof_unknown.is_provisional is True, "Fallback tier must be marked provisional"

    # 4. Platform native tag enrichment (Job object with foreign tag -> Tier 2)
    job_foreign = Job(
        platform="104",
        job_id="tag_test_1",
        title="商業分析實習生",
        company="某未上市外商科技部",
        location="台北市",
        salary="",
        url="",
        tags=[{"zoneForeign": {"desc": "外商公司"}}],
    )
    prof_foreign = enricher.enrich_company(job_foreign)
    assert prof_foreign.tier == "Tier 2", f"Expected Tier 2 for foreign tag, got {prof_foreign.tier}"

    # 5. English state serialization
    state_snippet = prof_cathay.to_english_state()
    assert "Tier 1" in state_snippet
    assert "國泰" in state_snippet

    # 6. Verify toolsearch is disabled
    assert enricher._search_deepseek("任意企業") == "", "DeepSeek toolsearch must be disabled"

    if os.path.exists("test_jobs.db"):
        os.remove("test_jobs.db")
    print("  -> CompanyEnricher passed!")


def test_job_translator():
    print("Testing JobTranslator (Salary formatting, Fact Sheet)...")
    translator = JobTranslator(db_path="test_jobs.db")

    # Salary formatting
    s1 = translator.format_salary_english("NT$ 200 - 250 (時薪)")
    assert "Hourly Wage" in s1 and "USD/hr" in s1

    s2 = translator.format_salary_english("月薪 40,000 - 50,000元")
    assert "Monthly Salary" in s2 and "USD/mo" in s2

    dummy_prof = CompanyProfile(
        name="TSMC",
        official_name="Taiwan Semiconductor Manufacturing Co.",
        capital_twd=259323700670,
        is_listed=True,
        tier="Tier 1",
        market_context="Global semiconductor titan",
        benchmark_summary="Level 3 / Tier 1 benchmark",
    )
    job = Job(
        platform="yourator",
        job_id="303",
        title="演算法實習生",
        company="TSMC",
        location="新竹市",
        salary="時薪 350",
        url="https://example.com/tsmc",
        description="開發深度學習模型與 Python 效能優化",
    )
    sheet = translator.build_english_fact_sheet(job, dummy_prof)
    assert "# JOB POSITION FACT SHEET" in sheet
    assert "TSMC" in sheet
    assert "Tier 1" in sheet

    if os.path.exists("test_jobs.db"):
        os.remove("test_jobs.db")
    print("  -> JobTranslator passed!")


def test_jev_math_and_mock():
    print("Testing Jev expected value calculation and deterministic Tier scoring...")
    client = JevClient(api_key="")  # mock mode
    config = parse_rubric_file("rubric.example.md")

    job = Job(
        platform="yourator",
        job_id="101",
        title="AI Engineer Intern",
        company="Google Taiwan",
        location="台北市信義區",
        salary="時薪 350",
        url="https://example.com/google",
        description="Build LLM applications and evaluation pipelines with Python",
    )

    scored = client.evaluate_job(job, config, dry_run=True)
    assert 0 <= scored.total_score <= 100, f"Score {scored.total_score} must be between 0 and 100"
    assert len(scored.dimension_scores) == len(config.dimensions)

    # Test Corporate Authority deterministic scoring for Tier 1, 2, 3, 4
    tier_pts_map = get_tier_points(config)
    for t_name in ["Tier 1", "Tier 2", "Tier 3", "Tier 4"]:
        expected_pts = tier_pts_map[t_name]
        prof_t = CompanyProfile(
            name="測試企業",
            official_name="測試企業",
            capital_twd=None,
            is_listed=False,
            tier=t_name,
            market_context="",
            benchmark_summary="",
        )
        scored_t = client.evaluate_job(job, config, dry_run=True, company_profile=prof_t)
        auth_score = next(ds.score for ds in scored_t.dimension_scores if any(k in ds.name for k in ["權威", "品牌"]))
        assert auth_score == expected_pts, f"Expected {expected_pts} for {t_name}, got {auth_score}"


    # Test Multi-Provider chain detection
    saved_or_key = os.environ.pop("OPENROUTER_API_KEY", None)
    saved_ts_key = os.environ.pop("TYPESAFE_API_KEY", None)
    try:
        os.environ["OPENROUTER_API_KEY"] = "mock_or_key"
        os.environ["TYPESAFE_API_KEY"] = "mock_ts_key"
        multi_client = JevClient()
        p_names = [p.name for p in multi_client.providers]
        assert "OpenRouter" in p_names, f"Expected OpenRouter in providers, got {p_names}"
        assert "TypeSafe" in p_names, f"Expected TypeSafe in providers, got {p_names}"
    finally:
        os.environ.pop("OPENROUTER_API_KEY", None)
        os.environ.pop("TYPESAFE_API_KEY", None)
        if saved_or_key is not None:
            os.environ["OPENROUTER_API_KEY"] = saved_or_key
        if saved_ts_key is not None:
            os.environ["TYPESAFE_API_KEY"] = saved_ts_key

    # Test Profile Gatekeeper (eliminating non-IT roles while preserving Data/Software)
    dummy_profile = "資訊管理系學生，熟悉 Python, SQL, 資料分析與後端開發。"
    
    # 1. Non-IT restaurant role
    fnb_job = Job(platform="104", job_id="f1", title="餐廳襄理（起薪41K）", company="麥當勞", location="台北市", salary="月薪41000", url="")
    fit_fnb, _, _ = client.evaluate_profile_fit(fnb_job, dummy_profile, dry_run=True)
    assert fit_fnb == "unrelated", f"Expected unrelated for restaurant, got {fit_fnb}"

    # 2. Hardware circuit role
    hw_job = Job(platform="104", job_id="h1", title="5G行動裝置基頻電路設計工程師", company="仁寶電腦", location="台北市", salary="月薪35000", url="")
    fit_hw, _, _ = client.evaluate_profile_fit(hw_job, dummy_profile, dry_run=True)
    assert fit_hw == "unrelated", f"Expected unrelated for hardware circuit, got {fit_hw}"

    # 3. IT PC maintenance role
    it_support_job = Job(platform="104", job_id="s1", title="【學生實習】資訊維運實習工程師 全台營運據點", company="仁大資訊", location="新北市", salary="面議", url="")
    fit_it, _, _ = client.evaluate_profile_fit(it_support_job, dummy_profile, dry_run=True)
    assert fit_it == "low", f"Expected low for IT hardware maintenance, got {fit_it}"

    # 4. Target Software / Data role
    tech_job = Job(platform="104", job_id="t1", title="軟體開發實習生", company="星展銀行", location="台北市", salary="月薪35000", url="")
    fit_tech, _, _ = client.evaluate_profile_fit(tech_job, dummy_profile, dry_run=True)
    assert fit_tech in ("high", "medium"), f"Expected high/medium for software intern, got {fit_tech}"

    # 5. Marketing with Data / Python (MarTech should NOT be mistakenly killed)
    martech_job = Job(
        platform="104", job_id="m1", title="行銷數據分析實習生", company="某科技公司",
        location="台北市", salary="面議", url="", description="負責行銷漏斗數據分析，撰寫 Python / SQL 撈取會員行為特徵。"
    )
    fit_martech, _, _ = client.evaluate_profile_fit(martech_job, dummy_profile, dry_run=True)
    assert fit_martech in ("high", "medium"), f"Expected high/medium for MarTech, got {fit_martech}"

    # 6. Verify heuristic score on hardware under rubric.md: Business & Tech must be 0.0
    rubric_user = parse_rubric_file("rubric.md")
    hw_scored = client.evaluate_job(hw_job, rubric_user, dry_run=True)
    biz_tech_score = next(ds.score for ds in hw_scored.dimension_scores if any(k in ds.name for k in ["商業", "技術"]))
    assert biz_tech_score == 0.0, f"Expected 0.0 for pure hardware circuit in Business & Tech, got {biz_tech_score}"
    assert hw_scored.total_score < rubric_user.notify_threshold, f"Hardware job should not qualify: {hw_scored.total_score}"

    print(f"  -> Jev evaluation calculated: {scored.total_score} points (Qualified: {scored.is_qualified})")
    print("  -> Jev calculation, OpenCode detection, and Profile Gatekeeper passed!")


def test_csv_exporter():
    print("Testing CSV exporter...")
    test_csv = "test_records.csv"
    if os.path.exists(test_csv):
        os.remove(test_csv)

    job = Job(
        platform="yourator",
        job_id="202",
        title="Data Intern",
        company="Big Tech",
        location="台北市",
        salary="面議",
        url="https://example.com/202",
    )
    scored = ScoredJob(
        job=job,
        total_score=92.0,
        is_qualified=True,
        dimension_scores=[
            DimensionScore(
                name="技能",
                max_points=25,
                score=25.0,
                confidence=1.0,
                probabilities={0: 0.0, 1: 1.0},
                level_points=[0, 25],
            )
        ],
    )
    append_scored_jobs_to_csv([scored], test_csv)
    assert os.path.exists(test_csv), "CSV file should be created"

    with open(test_csv, "r", encoding="utf-8-sig") as f:
        content = f.read()
        assert "Data Intern" in content
        assert "技能 (25分)" in content, "Should contain dimension header with max points"
        assert "合計總分" in content, "Should contain 合計總分 column header"
        assert "25.0" in content, "Should contain pure float dimension score"
        assert "92.0" in content, "Should contain pure float total score"

    clear_records(test_csv)
    assert not os.path.exists(test_csv), "clear_records should remove the file"
    print("  -> CSV exporter passed!")


if __name__ == "__main__":
    test_rubric_parser()
    test_dual_threshold_and_save_all()
    test_hard_filter()
    test_sqlite_db()
    test_company_enricher()
    test_job_translator()
    test_jev_math_and_mock()
    test_csv_exporter()
    print("\n✅ All self-checks passed successfully!")
