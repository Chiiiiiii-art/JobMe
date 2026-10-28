import sys
sys.stdout.reconfigure(encoding='utf-8')
from jobme.models import Job
from jobme.rubric import RubricConfig, filter_job_hard
from jobme.category_mapper import get_scraper_keywords

def test_no_keywords_when_not_configured():
    config = RubricConfig(keywords=[])
    kws = get_scraper_keywords(config)
    assert kws == [], f"Expected empty keywords, got {kws}"
    print("✅ get_scraper_keywords correctly returns empty list when no keywords configured")

def test_hard_filter_user_exclusions_and_location():
    config = RubricConfig(
        job_type="internship",
        regions=["台北市"],
        exclude_keywords=["電話行銷", "門市計時"],
    )
    
    # Excluded by user-configured keyword
    job_tele = Job(platform="yourator", job_id="1", title="電話行銷工讀生", company="某公司", location="台北市", salary="面議", url="https://example.com/1")
    passed, reason = filter_job_hard(job_tele, config)
    assert not passed, f"Expected reject for 電話行銷, but got passed ({reason})"
    print(f"✅ User exclude keyword correctly rejected: {reason}")

    # Excluded by location
    job_loc = Job(platform="cake", job_id="2", title="軟體工程實習生", company="某公司", location="高雄市", salary="面議", url="https://example.com/2")
    passed, reason = filter_job_hard(job_loc, config)
    assert not passed, f"Expected reject for location, but got passed ({reason})"
    print(f"✅ Location correctly rejected: {reason}")

    # Normal intern job passes cleanly without hardcoded keyword traps (e.g. 完成主管交辦事項)
    job_intern = Job(platform="yourator", job_id="3", title="數據分析實習生 (協助主管交辦事項)", company="晶盛科技", location="台北市", salary="面議", url="https://example.com/3")
    passed, reason = filter_job_hard(job_intern, config)
    assert passed, f"Expected pass for valid intern job, got rejected ({reason})"
    print("✅ Valid intern job correctly passed (no false positive on '主管')")

def test_category_mapper_platform_codes():
    from jobme.category_mapper import get_1111_categories, get_1111_regions, get_yes123_work_modes
    config = RubricConfig(
        regions=["台北市", "新北市", "新竹市"],
        platforms={
            "104": {"params": {"jobcat": "2007000000,2004000000"}}
        }
    )
    d0_codes = get_1111_categories(config)
    assert "140100" in d0_codes and "140200" in d0_codes, f"1111 d0 codes missing tech: {d0_codes}"
    print(f"✅ 1111 d0 job position mapping verified: {d0_codes}")

    c0_codes = get_1111_regions(config)
    assert "100100" in c0_codes and "100200" in c0_codes and "100600" in c0_codes, f"1111 c0 region codes wrong: {c0_codes}"
    print(f"✅ 1111 c0 region mapping verified: {c0_codes}")

    yes_modes = get_yes123_work_modes(config)
    assert "2_1011_0001_0000" in yes_modes, f"Yes123 work modes missing software: {yes_modes}"
    print(f"✅ Yes123 work mode mapping verified: {yes_modes}")

if __name__ == "__main__":
    test_no_keywords_when_not_configured()
    test_hard_filter_user_exclusions_and_location()
    test_category_mapper_platform_codes()
    print("\n🎉 ALL CLEAN FILTER TESTS PASSED!")

