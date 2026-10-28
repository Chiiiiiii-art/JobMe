import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
if sys.stdout:
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr:
    sys.stderr.reconfigure(encoding="utf-8")

from jobme.api import app, check_job_status, evaluate_job_endpoint, get_status, EvaluateRequest
from jobme.db import init_db, save_evaluation, save_job
from jobme.evaluator import JobEvaluatorService, is_valid_job_url, parse_job_url
from jobme.models import DimensionScore, Job, ScoredJob


def test_parse_job_url():
    # 104
    p, jid = parse_job_url("https://www.104.com.tw/job/7yn3u?jobsource=jlist")
    assert p == "104"
    assert jid == "7yn3u"

    # Yourator
    p, jid = parse_job_url("https://www.yourator.co/companies/testco/jobs/12345")
    assert p == "yourator"
    assert jid == "12345"

    # Cake
    p, jid = parse_job_url("https://www.cake.me/companies/tsmc/jobs/senior-ai-engineer")
    assert p == "cake"
    assert jid == "senior-ai-engineer"

    # 1111
    p, jid = parse_job_url("https://www.1111.com.tw/job/987654321")
    assert p == "1111"
    assert jid == "987654321"

    # Yes123
    p, jid = parse_job_url("https://www.yes123.com.tw/wk_index/job.asp?p_id=comp1&job_id=job99")
    assert p == "yes123"
    assert jid == "comp1_job99"
    print("✅ test_parse_job_url passed")


def test_evaluator_service_check_job():
    with tempfile.TemporaryDirectory() as tmpdir:
        test_db = os.path.join(tmpdir, "test.db")
        init_db(test_db)

        service = JobEvaluatorService(db_path=test_db)

        # 1. Unseen job
        res_unseen = service.check_job("https://www.104.com.tw/job/abc12")
        assert res_unseen["status"] == "not_evaluated"
        assert res_unseen["platform"] == "104"
        assert res_unseen["job_id"] == "abc12"

        # 2. Seen and evaluated job
        test_job = Job(
            platform="104",
            job_id="abc12",
            title="Backend Architect",
            company="Tech Corp",
            location="Taipei",
            salary="100k",
            url="https://www.104.com.tw/job/abc12",
        )
        save_job(test_job, status="evaluated", db_path=test_db)

        scored = ScoredJob(
            job=test_job,
            total_score=88.0,
            is_qualified=True,
            dimension_scores=[
                DimensionScore(
                    name="企業權威性",
                    max_points=45.0,
                    score=45.0,
                    confidence=1.0,
                    probabilities={0: 1.0},
                    level_points=[0.0, 45.0],
                ),
                DimensionScore(
                    name="專業技術契合度",
                    max_points=30.0,
                    score=25.0,
                    confidence=0.8,
                    probabilities={0: 1.0},
                    level_points=[0.0, 30.0],
                ),
            ],
        )
        save_evaluation(scored, db_path=test_db)

        res_evaluated = service.check_job("https://www.104.com.tw/job/abc12")
        assert res_evaluated["status"] == "evaluated"
        assert res_evaluated["total_score"] == 88.0
        assert res_evaluated["is_qualified"] is True
        assert res_evaluated["company_tier"] == "Tier 1"
        assert len(res_evaluated["dimensions"]) == 2
        print("✅ test_evaluator_service_check_job passed")


def test_api_status_endpoint():
    status = get_status()
    assert status["status"] == "ok"
    assert status["dimensions_count"] > 0
    assert status["total_max_points"] > 0
    print("✅ test_api_status_endpoint passed")


def test_is_valid_job_url():
    # Valid URLs
    assert is_valid_job_url("https://www.104.com.tw/job/7yn3u") is True
    assert is_valid_job_url("https://www.cake.me/companies/tsmc/jobs/senior-dev") is True
    assert is_valid_job_url("https://www.yourator.co/companies/abc/jobs/12345") is True
    assert is_valid_job_url("https://www.1111.com.tw/job/999888") is True
    assert is_valid_job_url("https://www.yes123.com.tw/wk_index/job.asp?p_id=1&job_id=2") is True

    # Invalid non-JD URLs
    assert is_valid_job_url("https://www.104.com.tw/jobs/search/?keyword=python") is False
    assert is_valid_job_url("https://www.104.com.tw/company/1a2b3c") is False
    assert is_valid_job_url("https://www.google.com") is False
    assert is_valid_job_url("https://www.youtube.com") is False
    assert is_valid_job_url("https://www.cake.me/jobs") is False
    assert is_valid_job_url("") is False
    print("✅ test_is_valid_job_url passed")


def test_evaluator_rejects_non_job():
    with tempfile.TemporaryDirectory() as tmpdir:
        test_db = os.path.join(tmpdir, "test.db")
        init_db(test_db)
        service = JobEvaluatorService(db_path=test_db)

        # Check non-JD URL
        res = service.check_job("https://www.104.com.tw/jobs/search/?keyword=python")
        assert res["status"] == "not_a_job"
        assert "非支援" in res["message"]

        # Evaluate non-JD URL
        eval_res = service.evaluate_job("https://www.google.com")
        assert eval_res["status"] == "not_a_job"

        # Verify get_profile_en does not crash
        profile_en = service.get_profile_en()
        assert isinstance(profile_en, str)
        print("✅ test_evaluator_rejects_non_job passed")


def test_location_and_salary_handling():
    with tempfile.TemporaryDirectory() as tmpdir:
        test_db = os.path.join(tmpdir, "test.db")
        init_db(test_db)

        # 1. EvaluateRequest supports location & salary
        req = EvaluateRequest(
            url="https://www.104.com.tw/job/964wr",
            title="【實習計畫】IT Intern",
            company="台灣賓士",
            location="台北市松山區",
            salary="時薪200元",
        )
        assert req.location == "台北市松山區"
        assert req.salary == "時薪200元"

        # 2. save_job inserts with location and salary
        j = Job(
            platform="104",
            job_id="964wr",
            title="IT Intern",
            company="台灣賓士",
            location="台北市松山區",
            salary="時薪200元",
            url="https://www.104.com.tw/job/964wr",
        )
        save_job(j, db_path=test_db)

        # 3. re-saving with empty/placeholder location and salary does NOT overwrite valid values
        j_sparse = Job(
            platform="104",
            job_id="964wr",
            title="IT Intern",
            company="未知企業",
            location="",
            salary="面議",
            url="https://www.104.com.tw/job/964wr",
        )
        save_job(j_sparse, db_path=test_db)

        import sqlite3
        conn = sqlite3.connect(test_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM jobs WHERE platform='104' AND job_id='964wr'").fetchone()
        assert row["company"] == "台灣賓士"
        assert row["location"] == "台北市松山區"
        assert row["salary"] == "時薪200元"
        conn.close()

        print("✅ test_location_and_salary_handling passed")


if __name__ == "__main__":
    test_parse_job_url()
    test_is_valid_job_url()
    test_evaluator_service_check_job()
    test_api_status_endpoint()
    test_evaluator_rejects_non_job()
    test_location_and_salary_handling()
    print("🎉 All API and Evaluator tests passed!")
