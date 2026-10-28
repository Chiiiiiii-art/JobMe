from __future__ import annotations
import logging
import time
from typing import Callable, List, Optional
import requests
from bs4 import BeautifulSoup

from ..models import Job
from ..rubric import RubricConfig

logger = logging.getLogger("jobme.scraper.yourator")
BASE_URL = "https://www.yourator.co"


def scrape_yourator_jobs(
    config: RubricConfig,
    is_seen_fn: Optional[Callable[[str, str], bool]] = None,
    max_pages: int = 3,
    notifier: Optional[Any] = None,
    scrape_limit: int = 0,
) -> List[Job]:
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
        ),
        "Accept": "application/json",
    })

    found_jobs: List[Job] = []
    seen_ids = set()

    from ..category_mapper import get_scraper_keywords
    keywords = get_scraper_keywords(config, default_keyword="")
    regions = [r.replace("台", "臺") for r in config.regions] if config.regions else [None]
    kw_list = keywords if keywords else [""]

    for kw in kw_list:
        for reg in regions:
            consecutive_seen_pages = 0
            for page in range(1, max_pages + 1):
                params: dict = {"page": page}
                if kw:
                    params["term[]"] = kw
                if config.job_type == "internship":
                    params["position[]"] = "intern"
                if reg:
                    params["location"] = reg

                try:
                    resp = session.get(f"{BASE_URL}/api/v4/jobs", params=params, timeout=10)
                    if resp.status_code != 200:
                        err_msg = f"Yourator page {page} returned status {resp.status_code}"
                        logger.warning(err_msg)
                        if notifier:
                            notifier.notify_error("Yourator 請求異常", err_msg, platform="yourator")
                        break

                    data = resp.json().get("payload", {})
                    job_list = data.get("jobs", [])
                    if not job_list:
                        break

                    page_new_count = 0
                    for item in job_list:
                        jid = str(item.get("id"))
                        if jid in seen_ids:
                            continue
                        seen_ids.add(jid)

                        # Dedup check against DB
                        if is_seen_fn and is_seen_fn("yourator", jid):
                            continue

                        page_new_count += 1
                        company_info = item.get("company", {})
                        company_name = company_info.get("brand") or company_info.get("enName", "未知公司")
                        path = item.get("path", "")
                        job_url = f"{BASE_URL}{path}" if path.startswith("/") else path

                        raw_loc = item.get("location") or reg or "未提供"
                        raw_salary = item.get("salary") or "面議"
                        job = Job(
                            platform="yourator",
                            job_id=jid,
                            title=item.get("name", ""),
                            company=company_name,
                            location=raw_loc,
                            salary=raw_salary,
                            url=job_url,
                            tags=item.get("tags") or [],
                        )
                        found_jobs.append(job)
                        if scrape_limit and len(found_jobs) >= scrape_limit:
                            return found_jobs[:scrape_limit]

                    # Incremental crawl stop: If 3 consecutive pages had jobs but none were new to DB, stop pagination
                    if page_new_count == 0:
                        consecutive_seen_pages += 1
                        if consecutive_seen_pages >= 3:
                            logger.info(f"Yourator: 3 consecutive pages had all jobs seen, stopping early.")
                            break
                    else:
                        consecutive_seen_pages = 0

                    if not data.get("hasMore", False):
                        break

                    time.sleep(1.0)
                except Exception as e:
                    logger.error(f"Error scraping Yourator for '{kw}' on page {page}: {e}")
                    break

    return found_jobs


def fetch_yourator_job_detail(job: Job) -> Job:
    """Fetches job description & requirements from job detail page."""
    if not job.url:
        return job

    try:
        resp = requests.get(
            job.url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
                )
            },
            timeout=10,
        )
        if resp.status_code == 200:
            text = resp.text
            import re
            m = re.search(r'<section[^>]*class="[^"]*job-description[^"]*"[^>]*>(.*?)</section>', text, re.DOTALL)
            if m:
                clean = re.sub(r'<[^>]+>', ' ', m.group(1))
                job.description = ' '.join(clean.split())
            else:
                job.description = f"職缺：{job.title} | 公司：{job.company} | 地點：{job.location}"
    except Exception as e:
        logger.warning(f"Could not fetch Yourator details for {job.job_id}: {e}")

    return job
