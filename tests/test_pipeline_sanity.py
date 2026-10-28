"""
JobMe Pipeline Sanity Self-Check (No test framework required, pure assert).
"""
import sys
import os
sys.stdout.reconfigure(encoding='utf-8')
os.environ["PYTHONPATH"] = "."

from jobme.jev import JevClient
from jobme.models import Job
from jobme.rubric import parse_rubric_file, filter_job_hard
from jobme.translator import JobTranslator, TranslationFatalError


def run_checks():
    print("=== 1. JevClient Providers Check ===")
    client = JevClient()
    provider_names = [p.name for p in client.providers]
    print("Active providers:", provider_names)
    assert "OpenCode" not in provider_names, "OpenCode must NOT be in providers!"
    assert "OpenRouter" in provider_names, "OpenRouter must be Primary provider!"
    print("✅ Provider check passed.")

    print("\n=== 2. Talent Pool vs Real Job Gatekeeper Check ===")
    adecco_job = Job(
        platform="104",
        job_id="test_adecco",
        title="【藝珂Adecco_ CapABLE藝展長才】歡迎持身障證明的各類人才與我們聯繫，與您一起找到發揮專才的工作！正職/兼職/實習/轉職皆歡迎！",
        company="藝珂人事顧問股份有限公司",
        location="台北市",
        salary="面議",
        url="http://example.com/adecco",
        description="歡迎持身心障礙手冊專業人才主動投遞，我們希望能了解你的專長及需求，協助您一起找到能發揮專才的理想工作:)"
    )

    real_tech_job = Job(
        platform="104",
        job_id="test_tech",
        title="後端開發實習生 (Backend Intern)",
        company="國泰世華商業銀行股份有限公司",
        location="台北市信義區",
        salary="時薪 220元",
        url="http://example.com/cathay",
        description="工作內容：\n1. 協助設計與開發 RESTful API (Python / FastAPI)\n2. 資料庫查詢維護與優化 (PostgreSQL)\n3. 本職缺亦歡迎持身心障礙證明之各類優秀人才應徵。"
    )

    profile = "Python, FastAPI, SQL, Data Engineering, Backend Development"

    fit_adecco, conf_adecco, _ = client.evaluate_profile_fit(adecco_job, profile)
    print(f"Adecco Talent Pool Fit: {fit_adecco} (Confidence: {conf_adecco})")
    assert fit_adecco in ("unrelated", "low"), f"Adecco talent pool must be rejected, got {fit_adecco}"
    print("✅ Talent pool successfully rejected in Stage 1!")

    fit_real, conf_real, _ = client.evaluate_profile_fit(real_tech_job, profile)
    print(f"Real Tech Job Fit: {fit_real} (Confidence: {conf_real})")
    assert fit_real in ("high", "medium"), f"Real tech job must pass, got {fit_real}"
    print("✅ Real tech job successfully passed Stage 1!")

    print("\n=== 3. Rubric & Max Age Filter Check ===")
    rubric = parse_rubric_file("config/rubric.md")
    rubric.max_age_days = 90

    stale_job = Job(
        platform="yes123",
        job_id="20100222113526_99958172_20250101091135_2269729",
        title="軟體工程師",
        company="某某醫院",
        location="台北市",
        salary="面議",
        url="http://example.com"
    )
    passed, reason = filter_job_hard(stale_job, rubric)
    print(f"Stale 2025 Job Hard Filter: passed={passed}, reason={reason}")
    assert not passed, "Stale job from 2025 must be rejected by max_age_days!"
    print("✅ Stale job hard filter check passed.")

    print("\n=== 4. Google Translation Fallback to DeepSeek Check ===")
    translator = JobTranslator()
    translator._rate_limit_until = 9999999999.0  # Force simulated Google 429
    res = translator.translate_text("這是一段中文測試")
    assert translator.used_deepseek or translator.has_degraded or not translator._has_chinese(res), "Should have used fallback!"
    print(f"Fallback translated: {res}, used_deepseek={translator.used_deepseek}")
    print("✅ Translation fallback guard passed.")

    print("\n🎉 ALL SANITY CHECKS PASSED PERFECTLY!")


if __name__ == "__main__":
    run_checks()
