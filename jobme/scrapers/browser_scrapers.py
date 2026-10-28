from __future__ import annotations
from datetime import datetime
import logging
import re
import time
from typing import Callable, List, Optional
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from ..models import Job
from ..rubric import RubricConfig

logger = logging.getLogger("jobme.scraper.browser")

AREA_MAP_104 = {
    # 台北市
    "台北市": "6001001000",
    "中正區": "6001001001", "大同區": "6001001002", "中山區": "6001001003", "松山區": "6001001004",
    "大安區": "6001001005", "萬華區": "6001001006", "信義區": "6001001007", "士林區": "6001001008",
    "北投區": "6001001009", "內湖區": "6001001010", "南港區": "6001001011", "文山區": "6001001012",
    # 新北市
    "新北市": "6001002000",
    "萬里區": "6001002001", "金山區": "6001002002", "板橋區": "6001002003", "汐止區": "6001002004",
    "深坑區": "6001002005", "石碇區": "6001002006", "瑞芳區": "6001002007", "平溪區": "6001002008",
    "雙溪區": "6001002009", "貢寮區": "6001002010", "新店區": "6001002011", "坪林區": "6001002012",
    "烏來區": "6001002013", "永和區": "6001002014", "中和區": "6001002015", "土城區": "6001002016",
    "三峽區": "6001002017", "樹林區": "6001002018", "鶯歌區": "6001002019", "三重區": "6001002020",
    "新莊區": "6001002021", "泰山區": "6001002022", "林口區": "6001002023", "蘆洲區": "6001002024",
    "五股區": "6001002025", "八里區": "6001002026", "淡水區": "6001002027", "三芝區": "6001002028",
    "石門區": "6001002029",
    # 桃園市
    "桃園市": "6001005000",
    "中壢區": "6001005001", "平鎮區": "6001005002", "龍潭區": "6001005003", "楊梅區": "6001005004",
    "新屋區": "6001005005", "觀音區": "6001005006", "桃園區": "6001005007", "龜山區": "6001005008",
    "八德區": "6001005009", "大溪區": "6001005010", "復興區": "6001005011", "大園區": "6001005012",
    "蘆竹區": "6001005013",
}


class BrowserScraperManager:
    def __init__(self, user_data_dir: str = "./browser_profile", headless: bool = True, notifier: Optional[Any] = None):
        self.user_data_dir = user_data_dir
        self.headless = headless
        self.notifier = notifier
        self.failed_platforms: List[str] = []

    def _get_context(self, p):
        # ponytail: off-screen headful Chrome bypasses Cloudflare challenge cleanly without disturbing user
        return p.chromium.launch_persistent_context(
            user_data_dir=self.user_data_dir,
            headless=False,
            channel="chrome",  # fallback automatically if not present
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--window-position=-2400,-2400",
                "--window-size=1280,800",
            ],
            ignore_default_args=["--enable-automation"],
            viewport={"width": 1280, "height": 800},
        )

    def scrape_all(
        self,
        config: RubricConfig,
        is_seen_fn: Optional[Callable[[str, str], bool]] = None,
        active_platforms: Optional[List[str]] = None,
        scrape_limit: int = 0,
    ) -> List[Job]:
        all_jobs: List[Job] = []
        platforms_to_run = active_platforms or ["all"]

        try:
            with sync_playwright() as p:
                try:
                    context = self._get_context(p)
                except Exception:
                    # Fallback to standard bundled chromium if Google Chrome not installed
                    context = p.chromium.launch_persistent_context(
                        user_data_dir=self.user_data_dir,
                        headless=False,
                        args=[
                            "--disable-blink-features=AutomationControlled",
                            "--no-sandbox",
                            "--window-position=-2400,-2400",
                            "--window-size=1280,800",
                        ],
                        ignore_default_args=["--enable-automation"],
                        viewport={"width": 1280, "height": 800},
                    )

                page = context.new_page()

                # 1. Scrape 104 if enabled
                if ("all" in platforms_to_run or "104" in platforms_to_run) and config.platforms.get("104", {}).get("enabled", True):
                    try:
                        jobs_104 = self._scrape_104(page, config, is_seen_fn, scrape_limit=scrape_limit)
                        self._validate_scraped_batch("104", jobs_104, config)
                        all_jobs.extend(jobs_104)
                    except Exception as e:
                        logger.warning(f"[104] 擷取異常: {e}")
                        self.failed_platforms.append("104")
                        if self.notifier:
                            self.notifier.notify_error("104 擷取異常", str(e), platform="104")

                # 2. Scrape Cake if enabled
                if ("all" in platforms_to_run or "cake" in platforms_to_run) and config.platforms.get("cake", {}).get("enabled", True):
                    try:
                        jobs_cake = self._scrape_cake(page, config, is_seen_fn, scrape_limit=scrape_limit)
                        self._validate_scraped_batch("Cake", jobs_cake, config)
                        all_jobs.extend(jobs_cake)
                    except Exception as e:
                        logger.warning(f"[Cake] 擷取異常: {e}")
                        self.failed_platforms.append("Cake")
                        if self.notifier:
                            self.notifier.notify_error("Cake 擷取異常", str(e), platform="cake")

                # 3. Scrape 1111 if enabled
                if ("all" in platforms_to_run or "1111" in platforms_to_run) and config.platforms.get("1111", {}).get("enabled", True):
                    try:
                        jobs_1111 = self._scrape_1111(page, config, is_seen_fn, scrape_limit=scrape_limit)
                        self._validate_scraped_batch("1111", jobs_1111, config)
                        all_jobs.extend(jobs_1111)
                    except Exception as e:
                        logger.warning(f"[1111] 擷取異常: {e}")
                        self.failed_platforms.append("1111")
                        if self.notifier:
                            self.notifier.notify_error("1111 擷取異常", str(e), platform="1111")

                context.close()

        except Exception as e:
            logger.error(f"Browser automation encountered error: {e}")
            raise e

        return all_jobs

    def _validate_scraped_batch(self, platform_name: str, jobs: List[Job], config: RubricConfig) -> None:
        if not jobs:
            return
        if config.regions:
            target_regions = [r.replace("臺", "台") for r in config.regions]
            invalid_locs = [j for j in jobs if j.location and not any(r in j.location.replace("臺", "台") for r in target_regions)]
            if len(invalid_locs) == len(jobs) and len(jobs) >= 5:
                err_msg = (
                    f"[{platform_name}] 回傳職缺地點嚴重偏離設定！\n"
                    f"共 {len(invalid_locs)}/{len(jobs)} 筆職缺不在指定地區 {config.regions}。\n"
                    f"範例異常職缺: {invalid_locs[0].title} @ {invalid_locs[0].company} (地點: {invalid_locs[0].location})"
                )
                logger.error(err_msg)
                if self.notifier:
                    self.notifier.notify_error(f"{platform_name} 爬取地區嚴重異常", err_msg, platform=platform_name)
                raise RuntimeError(err_msg)

    def _is_older_than_days(self, date_str: str, max_days: Optional[int]) -> bool:
        if not date_str or not max_days or max_days <= 0:
            return False
        try:
            # ponytail: 104 appearDate is 'YYYYMMDD' (e.g. 20261015) or 'YYYY/MM/DD'
            clean = str(date_str).strip().replace("/", "").replace("-", "")
            if len(clean) == 8 and clean.isdigit():
                d = datetime.strptime(clean, "%Y%m%d")
                return (datetime.now() - d).days > max_days
            return False
        except Exception:
            return False

    def _get_104_area_batches(self, config: RubricConfig, custom_params: Optional[dict]) -> List[str]:
        raw_areas = []
        if custom_params and custom_params.get("area"):
            raw_areas = [a.strip() for a in str(custom_params["area"]).split(",") if a.strip()]
        elif config.districts:
            for d in config.districts:
                code = AREA_MAP_104.get(d)
                if code and code not in raw_areas:
                    raw_areas.append(code)
        elif config.regions:
            for r in config.regions:
                code = AREA_MAP_104.get(r)
                if code and code not in raw_areas:
                    raw_areas.append(code)

        if not raw_areas:
            return [""]

        # 104 restricts: "The area may not have more than 10 items."
        # Split into batches of at most 8 items to be safe and avoid 400 error.
        chunk_size = 8
        return [",".join(raw_areas[i:i + chunk_size]) for i in range(0, len(raw_areas), chunk_size)]

    def _scrape_104(self, page, config: RubricConfig, is_seen_fn, scrape_limit: int = 0) -> List[Job]:
        jobs = []
        seen_job_ids = set()
        p104 = config.platforms.get("104", {})
        custom_url = p104.get("url")
        custom_params = p104.get("params")

        area_batches = self._get_104_area_batches(config, custom_params)
        max_pages = 1 if scrape_limit else int(p104.get("max_pages", 50))

        # Base query dict template
        base_query = {}
        if custom_params:
            base_query = {str(k): str(v) for k, v in custom_params.items()}
            if config.keywords and "keyword" not in base_query:
                base_query["keyword"] = config.keywords[0]
            if "order" not in base_query:
                base_query["order"] = "16"
        else:
            if config.keywords:
                base_query["keyword"] = config.keywords[0]
            base_query["order"] = "16"

        from ..category_mapper import get_configured_104_jobcats
        universal_jobcats = get_configured_104_jobcats(config)
        if universal_jobcats and "jobcat" not in base_query:
            base_query["jobcat"] = ",".join(universal_jobcats)

        if config.job_type == "internship" and "rostatus" not in base_query:
            base_query["rostatus"] = "1024"

        if base_query.get("rostatus") and base_query.get("ro") == "3":
            base_query.pop("ro", None)

        import requests
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
            ),
            "Referer": "https://www.104.com.tw/jobs/search/",
            "Accept": "application/json, text/plain, */*",
        }

        for b_idx, area_str in enumerate(area_batches):
            batch_query = dict(base_query)
            if area_str:
                batch_query["area"] = area_str

            if len(area_batches) > 1:
                logger.info(f"[104] 正在執行第 {b_idx + 1}/{len(area_batches)} 批區域檢索 (共 {len(area_str.split(','))} 個行政區代碼)...")

            fast_path_ok = False
            try:
                page_num = 1
                stop_pagination = False
                total_pages = max_pages

                while page_num <= total_pages and not stop_pagination:
                    page_query = dict(batch_query)
                    page_query["page"] = str(page_num)
                    api_resp = requests.get(
                        "https://www.104.com.tw/jobs/search/api/jobs",
                        params=page_query,
                        headers=headers,
                        timeout=10,
                    )
                    if api_resp.status_code != 200:
                        err_msg = f"[104] API 請求失敗 (狀態碼: {api_resp.status_code})，切換至 Playwright 瀏覽器引擎。"
                        logger.warning(err_msg)
                        if self.notifier and api_resp.status_code in (400, 401, 403, 500):
                            self.notifier.notify_error("104 API 請求異常", f"{err_msg}\n回應: {api_resp.text[:300]}", platform="104")
                        break

                    res_json = api_resp.json()
                    meta_pg = res_json.get("metadata", {}).get("pagination", {})
                    last_page = meta_pg.get("lastPage")
                    if last_page:
                        total_pages = min(max_pages, int(last_page))

                    items = res_json.get("data", [])
                    if not items:
                        break

                    page_items_count = len(items)
                    new_in_page = 0

                    for item in items:
                        if not isinstance(item, dict):
                            continue

                        appear_date = str(item.get("appearDate", ""))
                        if self._is_older_than_days(appear_date, config.max_age_days):
                            logger.info(f"[104] 檢測到更新超過 {config.max_age_days} 天之職缺 (更新日: {appear_date})，提早停止翻頁。")
                            stop_pagination = True
                            break

                        jid = str(item.get("jobNo") or item.get("jobId") or "")
                        if not jid or jid in seen_job_ids:
                            continue
                        if is_seen_fn and is_seen_fn("104", jid):
                            continue

                        seen_job_ids.add(jid)
                        new_in_page += 1
                        title = item.get("jobName", "")
                        cust = item.get("custName", "")
                        loc = item.get("jobAddrNoDesc", "")
                        salary = item.get("salaryDesc", "面議")
                        link = item.get("link", {}).get("job", "") if isinstance(item.get("link"), dict) else ""
                        if link.startswith("//"):
                            link = f"https:{link}"

                        raw_tags = item.get("tags", [])
                        norm_tags: List[str] = []
                        if isinstance(raw_tags, dict):
                            for v in raw_tags.values():
                                if isinstance(v, dict):
                                    desc = str(v.get("desc", "")).strip()
                                    if desc:
                                        norm_tags.append(desc)
                                elif isinstance(v, str) and v.strip():
                                    norm_tags.append(v.strip())
                        elif isinstance(raw_tags, list):
                            for t in raw_tags:
                                if isinstance(t, str) and t.strip():
                                    norm_tags.append(t.strip())
                                elif isinstance(t, dict):
                                    desc = str(t.get("desc", "") or t.get("name", "")).strip()
                                    if desc:
                                        norm_tags.append(desc)

                        jobs.append(
                            Job(
                                platform="104",
                                job_id=jid,
                                title=title,
                                company=cust,
                                location=loc,
                                salary=salary,
                                url=link,
                                description="",
                                tags=norm_tags,
                                published_at=appear_date,
                            )
                        )
                        if scrape_limit and len(jobs) >= scrape_limit:
                            stop_pagination = True
                            break

                    if stop_pagination:
                        break

                    if page_items_count > 0 and new_in_page == 0:
                        logger.info(f"[104] 第 {page_num} 頁之職缺皆已入庫記錄，提早停止翻頁。")
                        break

                    page_num += 1

                fast_path_ok = True
            except Exception as e:
                logger.info(f"[104] 快速檢索第 {b_idx + 1} 批異常 ({e})，切換至 Playwright 瀏覽器引擎。")

            if not fast_path_ok:
                # --- Fallback Path: Playwright Browser Automation for this batch ---
                try:
                    captured_api_data = []

                    def handle_resp(r):
                        if "/jobs/search/api/jobs" in r.url and r.status == 200:
                            # 忽略未包含檢索條件之首頁推薦/全站推薦 API (ro=1)
                            if "ro=1" in r.url and "jobcat" not in r.url and "area" not in r.url and "keyword" not in r.url:
                                logger.info("[104] 忽略非檢索目標之預設推薦 API 回應 (ro=1)")
                                return
                            try:
                                captured_api_data.append(r.json())
                            except Exception:
                                pass

                    page.on("response", handle_resp)
                    page_num = 1
                    stop_pagination = False

                    while page_num <= max_pages and not stop_pagination:
                        captured_api_data.clear()

                        if custom_url and page_num == 1 and len(area_batches) == 1:
                            target_url = custom_url
                        else:
                            from urllib.parse import urlencode
                            page_query = dict(batch_query)
                            page_query["page"] = str(page_num)
                            target_url = f"https://www.104.com.tw/jobs/search/?{urlencode(page_query, safe=',')}"

                        logger.info(f"[104] 正在載入第 {page_num} 頁 (瀏覽器模式，批次 {b_idx + 1})...")
                        try:
                            page.goto(target_url, wait_until="domcontentloaded", timeout=20000)
                            page.wait_for_timeout(2500)
                        except Exception as e:
                            logger.warning(f"[104] 頁面載入異常 (第 {page_num} 頁): {e}")
                            break

                        if "Just a moment" in page.title():
                            logger.warning("[104] 遇到 Cloudflare 驗證阻擋，已保留目前所抓取資料並安全結束。")
                            break

                        body_text = page.locator("body").inner_text()
                        if any(k in body_text for k in ["暫停網站服務", "進行網站作業", "網站作業中"]):
                            msg = "104 人力銀行全站例行維護中 (暫停網站服務)"
                            logger.warning(f"[104] {msg}")
                            raise RuntimeError(msg)

                        if not captured_api_data:
                            logger.info(f"[104] 第 {page_num} 頁未擷取到 API 資料，結束翻頁。")
                            break

                        new_in_page = 0
                        page_items_count = 0
                        total_pages = max_pages

                        for api_data in captured_api_data:
                            if isinstance(api_data, dict):
                                meta_pg = api_data.get("metadata", {}).get("pagination", {})
                                if "lastPage" in meta_pg:
                                    total_pages = min(total_pages, int(meta_pg["lastPage"]))

                            item_list = []
                            if isinstance(api_data, dict):
                                raw_data = api_data.get("data", {})
                                if isinstance(raw_data, list):
                                    item_list = raw_data
                                elif isinstance(raw_data, dict):
                                    item_list = raw_data.get("list", [])
                            elif isinstance(api_data, list):
                                item_list = api_data

                            page_items_count += len(item_list)

                            for item in item_list:
                                if not isinstance(item, dict):
                                    continue

                                appear_date = str(item.get("appearDate", ""))
                                if self._is_older_than_days(appear_date, config.max_age_days):
                                    stop_pagination = True
                                    break

                                jid = str(item.get("jobNo") or item.get("jobId") or "")
                                if not jid or jid in seen_job_ids:
                                    continue
                                if is_seen_fn and is_seen_fn("104", jid):
                                    continue

                                seen_job_ids.add(jid)
                                new_in_page += 1
                                title = item.get("jobName", "")
                                cust = item.get("custName", "")
                                loc = item.get("jobAddrNoDesc", "")
                                salary = item.get("salaryDesc", "面議")
                                link = item.get("link", {}).get("job", "") if isinstance(item.get("link"), dict) else ""
                                if link.startswith("//"):
                                    link = f"https:{link}"

                                raw_tags = item.get("tags", [])
                                norm_tags: List[str] = []
                                if isinstance(raw_tags, dict):
                                    for v in raw_tags.values():
                                        if isinstance(v, dict):
                                            desc = str(v.get("desc", "")).strip()
                                            if desc:
                                                norm_tags.append(desc)
                                        elif isinstance(v, str) and v.strip():
                                            norm_tags.append(v.strip())
                                elif isinstance(raw_tags, list):
                                    for t in raw_tags:
                                        if isinstance(t, str) and t.strip():
                                            norm_tags.append(t.strip())
                                        elif isinstance(t, dict):
                                            desc = str(t.get("desc", "") or t.get("name", "")).strip()
                                            if desc:
                                                norm_tags.append(desc)

                                jobs.append(
                                    Job(
                                        platform="104",
                                        job_id=jid,
                                        title=title,
                                        company=cust,
                                        location=loc,
                                        salary=salary,
                                        url=link,
                                        description="",
                                        tags=norm_tags,
                                        published_at=appear_date,
                                    )
                                )

                            if stop_pagination:
                                break

                        if page_items_count > 0 and new_in_page == 0:
                            logger.info(f"[104] 第 {page_num} 頁之職缺皆已入庫記錄，提早停止翻頁。")
                            break

                        if page_items_count == 0 or page_num >= total_pages:
                            break

                        page_num += 1

                except Exception as e:
                    logger.warning(f"Error scraping 104 with browser: {e}")
                    raise e

        logger.info(f"[104] 檢索完成，共取得 {len(jobs)} 筆新職缺。")
        return jobs

    def _scrape_cake(self, page, config: RubricConfig, is_seen_fn, scrape_limit: int = 0) -> List[Job]:
        from ..category_mapper import get_scraper_keywords
        jobs = []
        is_intern = config.job_type == "internship"
        base_cake_url = "https://www.cake.me/jobs?job_types=internship" if is_intern else "https://www.cake.me/jobs"
        keywords = get_scraper_keywords(config, default_keyword="")
        seen_slugs = set()
        kw_list = keywords if keywords else [""]

        for kw in kw_list:
            if scrape_limit and len(jobs) >= scrape_limit:
                break
            try:
                target_url = f"{base_cake_url}&q={kw}" if ("?" in base_cake_url and kw) else (f"{base_cake_url}?q={kw}" if kw else base_cake_url)
                logger.info(f"[Cake] 正在載入頁面: {target_url}")
                page.goto(target_url, wait_until="load", timeout=25000)
                page.wait_for_timeout(2000)

                for page_idx in range(1, 6):
                    if page_idx > 1:
                        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        page.wait_for_timeout(1000)
                        btn = page.locator("button, a").filter(has_text=re.compile(rf"^{page_idx}$")).first
                        if btn.count() > 0:
                            btn.click()
                            page.wait_for_timeout(3000)
                        else:
                            break

                    html = page.content()
                    page_title = page.title() or ""
                    content_sample = html[:2000] if html else ""
                    has_cf_frame = any("challenges.cloudflare.com" in f.url for f in page.frames)

                    if "Just a moment" in page_title or "請稍候" in page_title or has_cf_frame or "正在執行安全驗證" in content_sample:
                        err_msg = "遇到 Cloudflare 驗證阻擋 (Turnstile WAF)。請在桌機執行 `python jobme.py --setup-browser` 解鎖。"
                        logger.warning(f"[Cake] {err_msg}")
                        raise RuntimeError(err_msg)

                    soup = BeautifulSoup(html, "html.parser")
                    links = soup.find_all("a", href=re.compile(r"/(?:companies/[^/]+/)?jobs/([^/?]+)$"))
                    if not links:
                        break

                    for a in links:
                        href = a.get("href", "")
                        m = re.search(r"/(?:companies/([^/]+)/)?jobs/([^/?]+)", href)
                        if not m:
                            continue
                        comp_slug, jid = m.group(1) or "", m.group(2)
                        if not jid or jid in seen_slugs:
                            continue
                        seen_slugs.add(jid)

                        if is_seen_fn and is_seen_fn("cake", jid):
                            continue

                        title = a.get_text(strip=True)
                        if not title or len(title) < 2:
                            continue

                        parent = a.find_parent("div", class_=re.compile(r"JobSearchItem|Card|Item|item")) or a.parent
                        c_elem = parent.find("a", href=re.compile(r"/companies/[^/]+$")) if parent else None
                        raw_c = c_elem.get_text(strip=True) if c_elem else (comp_slug or "未知公司")
                        # Clean company name from promotional/event subtitle (e.g. "公司名稱｜活動名稱")
                        company = re.split(r"\s*[|｜]\s*", raw_c)[0].strip() or raw_c

                        card_text = parent.get_text(separator=" \n ", strip=True) if parent else ""
                        loc = "台北市"
                        salary = "面議"
                        for line in card_text.split("\n"):
                            ls = line.strip()
                            if any(c in ls for c in ["台北", "新北", "桃園", "新竹", "台中", "台南", "高雄"]):
                                loc = ls
                            elif any(s in ls for s in ["TWD", "時薪", "月薪", "面議", "NT$", "/ 小時", "/ 月"]):
                                salary = ls

                        link = f"https://www.cake.me{href}" if href.startswith("/") else href

                        card_tags = []
                        if parent:
                            for tag_el in parent.find_all(class_=re.compile(r"tag|Tag|Badge|badge|Label|label")):
                                tag_txt = tag_el.get_text(strip=True)
                                if tag_txt and tag_txt not in card_tags and len(tag_txt) < 30:
                                    card_tags.append(tag_txt)

                        jobs.append(
                            Job(
                                platform="cake",
                                job_id=jid,
                                title=title,
                                company=company,
                                location=loc,
                                salary=salary,
                                url=link,
                                description="",
                                tags=card_tags,
                            )
                        )
                        if scrape_limit and len(jobs) >= scrape_limit:
                            break

                    if scrape_limit and len(jobs) >= scrape_limit:
                        break

            except Exception as e:
                logger.warning(f"Error scraping Cake for '{kw}': {e}")
                if self.notifier:
                    self.notifier.notify_error("Cake 擷取異常", str(e), platform="cake")
                raise e

        logger.info(f"[Cake] 成功抓取 {len(jobs)} 筆職缺。")
        return jobs

    def _scrape_1111(self, page, config: RubricConfig, is_seen_fn, scrape_limit: int = 0) -> List[Job]:
        from ..category_mapper import get_1111_categories, get_1111_regions, get_scraper_keywords
        jobs = []
        is_intern = config.job_type == "internship" or any("實習" in kw for kw in (config.keywords or []))
        route = "search/intern" if is_intern else "search/job"

        # 1. Native job position support (d0) and region support (c0)
        p1111 = config.platforms.get("1111", {}) if hasattr(config, "platforms") else {}
        custom_params = p1111.get("params", {})
        d0_list = get_1111_categories(config)
        if custom_params.get("d0"):
            d0_list = [str(c).strip() for c in str(custom_params["d0"]).split(",") if str(c).strip()]
        d0_str = ",".join(d0_list) if d0_list else ""

        c0_list = get_1111_regions(config)
        if custom_params.get("c0"):
            c0_list = [str(c).strip() for c in str(custom_params["c0"]).split(",") if str(c).strip()]
        c0_str = ",".join(c0_list) if c0_list else ""

        # 2. Universal keywords
        keywords = get_scraper_keywords(config, default_keyword="")
        seen_jids = set()
        kw_list = keywords if keywords else [""]

        for kw in kw_list:
            if scrape_limit and len(jobs) >= scrape_limit:
                break
            for page_idx in range(1, 6):
                query_parts = [f"page={page_idx}", "col=ab", "sort=desc"]
                if kw:
                    query_parts.append(f"ks={kw}")
                if d0_str:
                    query_parts.append(f"d0={d0_str}")
                if c0_str:
                    query_parts.append(f"c0={c0_str}")
                url = f"https://www.1111.com.tw/{route}?" + "&".join(query_parts)


                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=25000)

                    # Check if stuck on Cloudflare Turnstile challenge and attempt auto-bypass
                    for _ in range(5):
                        cf_frames = [f for f in page.frames if "challenges.cloudflare.com" in f.url or "turnstile" in f.url]
                        is_loading = "Loading..." in page.title()
                        if cf_frames or is_loading:
                            if cf_frames:
                                try:
                                    elem = cf_frames[0].frame_element()
                                    box = elem.bounding_box()
                                    if box:
                                        target_x = box["x"] + 28
                                        target_y = box["y"] + box["height"] / 2
                                        logger.info(f"[1111] 偵測到 Turnstile 驗證框，模擬游標軌跡點擊 ({target_x}, {target_y})...")
                                        page.mouse.move(target_x - 40, target_y - 20)
                                        page.mouse.move(target_x, target_y, steps=15)
                                        page.wait_for_timeout(300)
                                        page.mouse.down()
                                        page.wait_for_timeout(150)
                                        page.mouse.up()
                                        for _ in range(8):
                                            page.wait_for_timeout(1000)
                                            if "Loading" not in page.title() and len(page.title()) > 10:
                                                break
                                except Exception as ex:
                                    logger.debug(f"[1111] Turnstile bypass exception: {ex}")
                            else:
                                page.wait_for_timeout(1000)
                        else:
                            break

                    try:
                        page.wait_for_selector(".job-card, a[href*='/job/']", timeout=8000)
                    except Exception:
                        page.wait_for_timeout(2000)

                    # 嚴格校驗：若關鍵字在載入後被丟棄或被重新導向到非指定頁面，立即報警
                    if kw and "ks=" not in page.url and kw in url:
                        err_msg = f"[1111] 網址搜尋關鍵字遭伺服器重定向丟失 (目標: {url}, 實際跳轉: {page.url})"
                        logger.error(err_msg)
                        if self.notifier:
                            self.notifier.notify_error("1111 搜尋重定向異常", err_msg, platform="1111")
                        raise RuntimeError(err_msg)

                    html = page.content()
                    soup = BeautifulSoup(html, "html.parser")
                    items = soup.find_all("div", class_=re.compile(r"job-card|job_item|job-item"))

                    if not items:
                        job_links = soup.find_all("a", href=re.compile(r"/job/\d+"))
                        if not job_links and ("Loading..." in page.title() or any("challenges.cloudflare.com" in f.url for f in page.frames)):
                            err_msg = "遇到驗證碼防護 (Cloudflare Turnstile)。請在桌機執行 `python jobme.py --setup-browser` 解鎖。"
                            logger.warning(f"[1111] {err_msg}")
                            raise RuntimeError(err_msg)
                        break

                    new_in_page = 0
                    for it in items:
                        a = it.find("a", href=re.compile(r"/job/\d+"))
                        if not a:
                            continue
                        href = a.get("href", "")
                        jid_m = re.search(r"/job/(\d+)", href)
                        jid = jid_m.group(1) if jid_m else href
                        if not jid or jid in seen_jids:
                            continue
                        seen_jids.add(jid)

                        if is_seen_fn and is_seen_fn("1111", jid):
                            continue

                        title = a.get_text(strip=True)
                        c_elem = it.find("a", href=re.compile(r"/corp/")) or it.find("a", class_=re.compile(r"organ|company"))
                        raw_c = c_elem.get_text(strip=True) if c_elem else "未知公司"
                        if any(m in title or m in raw_c for m in ["測試專用", "請勿應徵", "測試職缺"]):
                            continue
                        # Clean company name by stripping appended industry categories and recruitment notes
                        company = re.split(r"\s*[|｜]\s*", raw_c)[0].strip()
                        company = re.sub(r"[\(（]\s*(?:內部徵才|內部招募|總部徵才)\s*[\)）]", "", company).strip()
                        link = f"https://www.1111.com.tw{href}" if href.startswith("/") else href

                        card_text = it.get_text(separator=" \n ", strip=True)
                        if any(k in card_text for k in ["已下架", "已結束", "已額滿", "結束徵才"]) or "⏳" in title:
                            continue

                        loc = ""
                        salary = "面議"
                        for line in card_text.split("\n"):
                            ls = line.strip()
                            if not loc and any(c in ls for c in ["台北", "新北", "桃園", "新竹", "台中", "台南", "高雄", "基隆", "苗栗", "彰化", "南投", "雲林", "嘉義", "屏東", "宜蘭", "花蓮", "台東"]):
                                loc = ls
                            if salary == "面議" and any(s_kw in ls for s_kw in ["時薪", "月薪", "年薪"]):
                                salary = ls

                        if not loc:
                            loc = "未提供"

                        # Extract rich job duties from card paragraph
                        desc_elem = it.find("p")
                        card_desc = desc_elem.get_text("\n", strip=True) if desc_elem else ""
                        full_desc = f"【工作內容與職責】\n{card_desc}" if card_desc else ""

                        jobs.append(
                            Job(
                                platform="1111",
                                job_id=jid,
                                title=title,
                                company=company or raw_c,
                                location=loc,
                                salary=salary,
                                url=link,
                                description=full_desc,
                                tags=["實習"] if (is_intern or "實習" in title) else [],
                            )
                        )
                        new_in_page += 1
                        if scrape_limit and len(jobs) >= scrape_limit:
                            break

                    if scrape_limit and len(jobs) >= scrape_limit:
                        break
                except Exception as e:
                    logger.warning(f"Error scraping 1111 for '{kw}' (page {page_idx}): {e}")
                    if self.notifier:
                        self.notifier.notify_error("1111 擷取異常", str(e), platform="1111")
                    raise e

        logger.info(f"[1111] 成功抓取 {len(jobs)} 筆職缺。")
        return jobs

    def setup_interactive_browser(self):
        """
        Launches an interactive (headful) browser window so the user
        can solve Cloudflare/Captchas for 104, Cake, and 1111 once.
        Cookies will be saved into browser_profile for future automated runs.
        """
        print("\n" + "=" * 60)
        print("🌐 正在開啟互動式瀏覽器視窗 (Headful Mode)...")
        print("請在跳出的瀏覽器視窗中依序確認或通過 104 / Cake / 1111 驗證。")
        print("通過後關閉瀏覽器視窗或按 Enter 結束儲存 Cookie。")
        print("=" * 60 + "\n")

        with sync_playwright() as p:
            try:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=self.user_data_dir,
                    headless=False,
                    channel="chrome",
                    args=["--disable-blink-features=AutomationControlled"],
                    ignore_default_args=["--enable-automation"],
                    viewport={"width": 1280, "height": 850},
                )
            except Exception:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=self.user_data_dir,
                    headless=False,
                    args=["--disable-blink-features=AutomationControlled"],
                    ignore_default_args=["--enable-automation"],
                    viewport={"width": 1280, "height": 850},
                )
            page1 = context.new_page()
            page1.goto("https://www.104.com.tw/jobs/search/?keyword=實習")

            page2 = context.new_page()
            page2.goto("https://www.cake.me/jobs/in-台北市?job_types=internship")

            page3 = context.new_page()
            page3.goto("https://www.1111.com.tw/search/job?ks=實習")

            input("👉 通過各頁面驗證後，請在此處按 [Enter] 鍵繼續儲存並關閉瀏覽器...")
            context.close()
            print("✅ 瀏覽器 Cookie 已成功保存至:", self.user_data_dir)
