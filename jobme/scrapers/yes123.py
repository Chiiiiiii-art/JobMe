from __future__ import annotations
import logging
import re
import time
from typing import Callable, List, Optional
import requests
from bs4 import BeautifulSoup

from ..models import Job
from ..rubric import RubricConfig

logger = logging.getLogger("jobme.scraper.yes123")
BASE_URL = "https://www.yes123.com.tw/wk_index"


YES123_ZONE_MAP: dict[str, str] = {
    "基隆市": "1_1001_0001_0000",
    "新北市": "1_1001_0002_0000",
    "台北市": "1_1001_0003_0000",
    "桃園市": "1_1001_0004_0000",
    "新竹縣": "1_1001_0005_0000",
    "新竹市": "1_1001_0006_0000",
    "苗栗縣": "1_1001_0007_0000",
    "台中市": "1_1001_0009_0000",
    "彰化縣": "1_1001_0010_0000",
    "南投縣": "1_1001_0011_0000",
    "雲林縣": "1_1001_0012_0000",
    "嘉義市": "1_1001_0013_0000",
    "嘉義縣": "1_1001_0014_0000",
    "台南市": "1_1001_0016_0000",
    "高雄市": "1_1001_0017_0000",
    "屏東縣": "1_1001_0019_0000",
    "宜蘭縣": "1_1001_0020_0000",
    "花蓮縣": "1_1001_0021_0000",
    "台東縣": "1_1001_0022_0000",
    "澎湖縣": "1_1001_0023_0000",
    "金門縣": "1_1001_0024_0000",
    "連江縣": "1_1001_0025_0000",
}


def scrape_yes123_jobs(
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
        "Referer": "https://www.yes123.com.tw/",
    })

    found_jobs: List[Job] = []
    seen_ids = set()
    from ..category_mapper import get_yes123_work_modes, get_scraper_keywords
    keywords = get_scraper_keywords(config, default_keyword="")
    work_modes = get_yes123_work_modes(config)
    kw_list = keywords if keywords else [""]

    for kw in kw_list:
        consecutive_seen_pages = 0
        for page in range(max_pages):
            strrec = page * 30  # yes123 uses strrec offset (0, 30, 60...)
            params: dict = {
                "order_by": "m_date",
                "order_ascend": "desc",
                "strrec": strrec,
                "search_type": "job",
                "search_from": "joblist",
            }
            search_kw = kw
            if not search_kw and config.job_type == "internship":
                search_kw = "實習"

            if search_kw:
                params["find_key1"] = search_kw
                params["search_key_word"] = search_kw

            # Inject mapped category modes (find_work_mode1, find_work_mode2, ...)
            for idx, mode in enumerate(work_modes):
                params[f"find_work_mode{idx + 1}"] = mode


            # Dynamically map configured regions (up to 10 zones supported by Yes123)
            zone_idx = 1
            for reg in (config.regions or []):
                clean_reg = reg.replace("臺", "台")
                if clean_reg in YES123_ZONE_MAP and zone_idx <= 10:
                    params[f"find_zone_mode{zone_idx}"] = YES123_ZONE_MAP[clean_reg]
                    zone_idx += 1

            try:
                resp = session.get(f"{BASE_URL}/joblist.asp", params=params, timeout=12)
                if resp.status_code != 200:
                    err_msg = f"yes123 status {resp.status_code} for '{kw}' on offset {strrec}"
                    logger.warning(err_msg)
                    if notifier:
                        notifier.notify_error("Yes123 請求異常", err_msg, platform="yes123")
                    break

                resp.encoding = "utf-8"
                soup = BeautifulSoup(resp.text, "html.parser")

                items = soup.find_all("div", class_=re.compile(r"Job_opening_item\b"))
                if not items:
                    items = soup.find_all("div", class_=re.compile(r"job_item|job-item"))

                if not items:
                    break

                page_new_count = 0
                for it in items:
                    a = it.find("a", href=re.compile(r"job\.asp\?p_id="))
                    if not a:
                        continue

                    href = a.get("href", "")
                    m = re.search(r"p_id=([^&]+)(?:&job_id=([^&]+))?", href)
                    if not m:
                        continue
                    jid = f"{m.group(1)}_{m.group(2)}" if m.group(2) else m.group(1)

                    if jid in seen_ids:
                        continue
                    seen_ids.add(jid)

                    if is_seen_fn and is_seen_fn("yes123", jid):
                        continue

                    page_new_count += 1
                    title = a.get_text(strip=True)

                    c_link = it.find("a", href=re.compile(r"comp_info\.asp"))
                    company = c_link.get_text(strip=True) if c_link else "未知企業"

                    # Extract card text for salary and location
                    raw_text = it.get_text(separator=" | ", strip=True)
                    parts = [p.strip() for p in raw_text.split(" | ") if p.strip()]

                    salary = "面議"
                    location = ""
                    for p in parts:
                        if any(s in p for s in ["月薪", "時薪", "日薪", "年薪", "面議"]):
                            salary = p
                        elif any(city in p for city in ["台北", "新北", "桃園", "台中", "台南", "高雄", "市", "縣"]):
                            location = p

                    # Extract published date (e.g. '10.24') from card
                    pub_date_str = ""
                    date_el = it.find("div", class_=re.compile(r"Job_opening_item_date|job_item_date"))
                    if date_el:
                        raw_date = date_el.get_text(strip=True)
                        dm = re.search(r"(\d{1,2})\.(\d{1,2})", raw_date)
                        if dm:
                            from datetime import datetime
                            now_dt = datetime.now()
                            m_val = int(dm.group(1))
                            d_val = int(dm.group(2))
                            yr_val = now_dt.year
                            if m_val > now_dt.month:
                                yr_val -= 1
                            pub_date_str = f"{yr_val:04d}-{m_val:02d}-{d_val:02d}"

                    full_url = f"{BASE_URL}/{href}" if not href.startswith("http") else href

                    job = Job(
                        platform="yes123",
                        job_id=jid,
                        title=title,
                        company=company,
                        location=location,
                        salary=salary,
                        url=full_url,
                        published_at=pub_date_str,
                        tags=["實習"] if "實習" in title else [],
                    )
                    found_jobs.append(job)

                    if scrape_limit and len(found_jobs) >= scrape_limit:
                        return found_jobs[:scrape_limit]

                if page_new_count == 0:
                    consecutive_seen_pages += 1
                    if consecutive_seen_pages >= 3:
                        logger.info(f"yes123: 3 consecutive pages had all jobs seen, stopping pagination.")
                        break
                else:
                    consecutive_seen_pages = 0

                time.sleep(1.0)
            except Exception as e:
                logger.error(f"Error scraping yes123 for '{kw}' offset {strrec}: {e}")
                if notifier:
                    notifier.notify_error("Yes123 擷取異常", str(e), platform="yes123")
                break

    return found_jobs
