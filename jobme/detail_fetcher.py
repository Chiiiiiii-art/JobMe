from __future__ import annotations
import logging
import os
import re
import json
import sqlite3
from typing import Optional, Any
import requests
from bs4 import BeautifulSoup

from .models import Job
from .db import get_connection

logger = logging.getLogger("jobme.detail_fetcher")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    )
}

def clean_lines(text: str) -> str:
    if not text:
        return ""
    lines = [l.strip() for l in text.splitlines()]
    res = []
    prev_blank = False
    for l in lines:
        if not l:
            if not prev_blank:
                res.append("")
                prev_blank = True
        else:
            res.append(l)
            prev_blank = False
    return "\n".join(res).strip()

class JobDetailFetcher:
    def __init__(self, db_path: str = "jobs.db"):
        self.db_path = db_path
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def _persist_to_db(self, *args, **kwargs) -> None:
        if args and isinstance(args[0], Job):
            job = args[0]
            platform = job.platform
            job_id = job.job_id
            desc = job.description
            title = job.title
            comp = job.company
            loc = job.location
            sal = job.salary
        else:
            platform = args[0] if len(args) > 0 else kwargs.get("platform")
            job_id = args[1] if len(args) > 1 else kwargs.get("job_id")
            desc = args[2] if len(args) > 2 else kwargs.get("description", "")
            title = kwargs.get("title")
            comp = kwargs.get("company")
            loc = kwargs.get("location")
            sal = kwargs.get("salary")

        if not desc and not title and not comp and not loc and not sal:
            return
        try:
            with get_connection(self.db_path) as conn:
                conn.execute(
                    """
                    UPDATE jobs SET
                        description = COALESCE(NULLIF(?, ''), description),
                        title = COALESCE(NULLIF(?, ''), title),
                        company = COALESCE(NULLIF(NULLIF(?, '未知企業'), ''), company),
                        location = COALESCE(NULLIF(NULLIF(?, '未提供'), ''), location),
                        salary = COALESCE(NULLIF(NULLIF(?, '面議'), ''), salary)
                    WHERE platform = ? AND job_id = ?
                    """,
                    (desc or "", title, comp, loc, sal, platform, str(job_id)),
                )
                conn.commit()
        except Exception as e:
            logger.warning(f"[DetailFetcher] 回寫資料庫異常 ({platform}:{job_id}): {e}")

    def fetch_104(self, job: Job) -> Job:
        m = re.search(r"/job/([a-zA-Z0-9]+)", job.url)
        if not m:
            return job
        job_code = m.group(1)
        api_url = f"https://www.104.com.tw/job/ajax/content/{job_code}"
        try:
            r = self.session.get(api_url, headers={"Referer": job.url}, timeout=10)
            if r.status_code == 200:
                data = r.json().get("data", {})
                header = data.get("header", {})
                cust_name = header.get("custName", "").strip()
                if cust_name and (not job.company or job.company in ("未知企業", "")):
                    job.company = cust_name
                job_name = header.get("jobName", "").strip()
                if job_name and (not job.title or job.title in ("未命名職缺", "") or "｜" in job.title or "|" in job.title):
                    job.title = job_name

                job_detail = data.get("jobDetail", {})
                condition = data.get("condition", {})

                # Extract location and salary
                loc = job_detail.get("addressRegion", "").strip()
                if loc and (not job.location or job.location in ("未提供", "")):
                    job.location = loc
                sal = job_detail.get("salary", "").strip()
                if sal and (not job.salary or job.salary in ("面議", "")):
                    job.salary = sal

                parts = []
                desc = job_detail.get("jobDescription", "").strip()
                if desc:
                    parts.append("【工作內容】\n" + desc)

                cats = [c.get("description", "") for c in job_detail.get("jobCategory", []) if c.get("description")]
                if cats:
                    parts.append("【職務類別】\n" + "、".join(cats))

                cond_parts = []
                edu = condition.get("edu", "")
                if edu:
                    cond_parts.append(f"學歷要求：{edu}")
                exp = condition.get("workExp", "")
                if exp:
                    cond_parts.append(f"工作經驗：{exp}")
                majors = condition.get("major", [])
                if majors:
                    cond_parts.append(f"科系要求：{'、'.join(majors)}")
                skills = [s.get("description", "") for s in condition.get("skill", []) if s.get("description")]
                if skills:
                    cond_parts.append(f"具備技能：{'、'.join(skills)}")
                specialties = [s.get("description", "") for s in condition.get("specialty", []) if s.get("description")]
                if specialties:
                    cond_parts.append(f"擅長工具/語言：{'、'.join(specialties)}")
                other = condition.get("other", "").strip()
                if other:
                    cond_parts.append(f"其他條件：\n{other}")

                if cond_parts:
                    parts.append("【條件要求】\n" + "\n".join(cond_parts))

                job.description = clean_lines("\n\n".join(parts))
                self._persist_to_db(job)
        except Exception as e:
            logger.warning(f"[DetailFetcher] 104 擷取異常 ({job.job_id}): {e}")
        return job

    def fetch_yourator(self, job: Job) -> Job:
        if not job.url:
            return job
        try:
            r = self.session.get(job.url, timeout=10)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                main = soup.find("section", class_="job-description") or soup
                parts = []
                seen_headers = set()

                headings = main.find_all(["h2", "h3", "h4", "h5"])
                for h in headings:
                    htxt = h.get_text(strip=True)
                    if not htxt or htxt in seen_headers:
                        continue
                    if any(k in htxt for k in ["工作內容", "你將在團隊", "關於"]):
                        seen_headers.add(htxt)
                        content = []
                        curr = h.find_next_sibling()
                        while curr and curr.name not in ["h2", "h3"]:
                            txt = curr.get_text("\n", strip=True)
                            if txt and not any(k in txt for k in ["福利", "法定項目", "注意事項"]):
                                content.append(txt)
                            curr = curr.find_next_sibling()
                        if content:
                            parts.append(f"【{htxt}】\n" + "\n".join(content))

                    elif any(k in htxt for k in ["條件要求", "具備以下能力", "具備能力"]):
                        seen_headers.add(htxt)
                        content = []
                        curr = h.find_next_sibling()
                        while curr and curr.name not in ["h2", "h3"]:
                            txt = curr.get_text("\n", strip=True)
                            if txt and not any(k in txt for k in ["福利", "法定項目", "注意事項"]):
                                content.append(txt)
                            curr = curr.find_next_sibling()
                        if content:
                            parts.append("【條件要求】\n" + "\n".join(content))

                    elif any(k in htxt for k in ["加分條件", "更棒了"]):
                        seen_headers.add(htxt)
                        content = []
                        curr = h.find_next_sibling()
                        while curr and curr.name not in ["h2", "h3"]:
                            txt = curr.get_text("\n", strip=True)
                            if txt and not any(k in txt for k in ["福利", "法定項目", "注意事項"]):
                                content.append(txt)
                            curr = curr.find_next_sibling()
                        if content:
                            parts.append("【加分條件】\n" + "\n".join(content))

                if not parts and main:
                    raw = main.get_text("\n", strip=True)
                    if "福利" in raw:
                        raw = raw.split("福利")[0]
                    parts.append(raw)

                job.description = clean_lines("\n\n".join(parts))
                self._persist_to_db(job)
        except Exception as e:
            logger.warning(f"[DetailFetcher] Yourator 擷取異常 ({job.job_id}): {e}")
        return job

    def fetch_yes123(self, job: Job) -> Job:
        if not job.url:
            return job
        try:
            r = self.session.get(job.url, timeout=10)
            r.encoding = "utf-8"
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                parts = []

                for div in soup.find_all("div", class_="job_explain"):
                    txt = div.get_text("\n", strip=True)
                    h = div.find(["span", "div", "h2", "h3", "h4"], class_=lambda c: c and ("title" in c or "header" in c))
                    htxt = h.get_text(strip=True) if h else ""

                    if any(k in htxt or k in txt[:40] for k in ["保險福利", "企業福利", "法定保障", "連絡人", "防騙", "申訴"]):
                        continue

                    if "工作內容" in htxt or "徵才說明" in txt[:30]:
                        clean_lines_list = [l for l in txt.splitlines() if not any(k in l for k in ["職缺更新：", "徵才特色"])]
                        parts.append("【工作內容】\n" + "\n".join(clean_lines_list))
                    elif "工作條件" in txt[:30] or "學歷要求" in htxt:
                        parts.append("【工作條件】\n" + txt)
                    elif "技能與求職專長" in txt[:30] or "電腦技能" in htxt:
                        parts.append("【專業技能與工具】\n" + txt)
                    elif "其他條件" in txt[:30]:
                        parts.append("【其他條件】\n" + txt)

                job.description = clean_lines("\n\n".join(parts))
                self._persist_to_db(job)
        except Exception as e:
            logger.warning(f"[DetailFetcher] Yes123 擷取異常 ({job.job_id}): {e}")
        return job

    def fetch_cake(self, job: Job) -> Job:
        if not job.url:
            return job
        try:
            r = self.session.get(job.url, timeout=10)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                script = soup.find("script", id="__NEXT_DATA__")
                if script:
                    data = json.loads(script.string)
                    job_data = data.get("props", {}).get("pageProps", {}).get("job", {})
                    desc_html = job_data.get("description", "")
                    req_html = job_data.get("requirements", "")

                    parts = []
                    if desc_html:
                        parts.append("【工作內容】\n" + BeautifulSoup(desc_html, "html.parser").get_text("\n", strip=True))
                    if req_html:
                        parts.append("【條件要求】\n" + BeautifulSoup(req_html, "html.parser").get_text("\n", strip=True))
                    tags = job_data.get("tags", [])
                    if tags:
                        parts.append("【技能標籤】\n" + "、".join(tags))

                    comp_data = data.get("props", {}).get("pageProps", {}).get("company", {}) or job_data.get("company", {})
                    # Prioritize registered company/legalName over event title e.g. comp_data.get("company")
                    c_name = comp_data.get("company") or comp_data.get("legalName") or comp_data.get("legal_name") or comp_data.get("name")
                    if c_name and len(c_name) > 1:
                        clean_c = re.split(r"\s*[|｜]\s*", str(c_name))[0].strip()
                        job.company = clean_c or str(c_name).strip()

                    job.description = clean_lines("\n\n".join(parts))
                    self._persist_to_db(job)
        except Exception as e:
            logger.warning(f"[DetailFetcher] Cake 擷取異常 ({job.job_id}): {e}")
        return job

    def fetch_1111(self, job: Job, browser_page: Optional[Any] = None) -> Job:
        if not job.url:
            return job
        try:
            if browser_page:
                browser_page.goto(job.url, wait_until="domcontentloaded", timeout=25000)
                browser_page.wait_for_timeout(2000)
                ld_json_str = ""
                for _ in range(3):
                    try:
                        ld_json_str = browser_page.evaluate('''() => {
                            const s = document.querySelector('script[type="application/ld+json"]');
                            return s ? s.innerText : '';
                        }''')
                        break
                    except Exception:
                        browser_page.wait_for_timeout(1000)
                if ld_json_str:
                    data = json.loads(ld_json_str)
                    item = data[0] if isinstance(data, list) else data
                    desc = item.get("description", "")
                    skills = item.get("skills", "")
                    qual = item.get("qualifications", "")
                    parts = []
                    if desc:
                        parts.append("【工作內容與職責】\n" + desc)
                    if skills:
                        parts.append("【技能要求】\n" + str(skills))
                    if qual:
                        parts.append("【資格條件】\n" + str(qual))
                    hiring_org = item.get("hiringOrganization")
                    if isinstance(hiring_org, dict):
                        org_name = hiring_org.get("name", "").strip()
                        if org_name and len(org_name) > 1:
                            org_clean = re.split(r"\s*[|｜]\s*", org_name)[0].strip()
                            org_clean = re.sub(r"[\(（]\s*(?:內部徵才|內部招募|總部徵才)\s*[\)）]", "", org_clean).strip()
                            if org_clean:
                                job.company = org_clean

                    job.description = clean_lines("\n\n".join(parts))
                    self._persist_to_db(job)
        except Exception as e:
            logger.warning(f"[DetailFetcher] 1111 擷取異常 ({job.job_id}): {e}")
        return job

    def fetch_job_detail(self, job: Job, browser_page: Optional[Any] = None) -> Job:
        # Check if already fetched and contains structured section content and valid company/location/salary
        has_desc = bool(job.description and ("【工作內容" in job.description or "【條件要求" in job.description or len(job.description.strip()) > 100))
        has_comp = bool(job.company and job.company != "未知企業")
        has_loc = bool(job.location and job.location != "未提供")
        has_sal = bool(job.salary and job.salary != "面議")

        if has_desc and has_comp and has_loc and has_sal:
            return job

        # Check DB cache for previously persisted details
        if self.db_path and os.path.exists(self.db_path):
            try:
                with sqlite3.connect(self.db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    row = conn.execute(
                        "SELECT description, location, salary, company FROM jobs WHERE platform = ? AND job_id = ?",
                        (job.platform, str(job.job_id)),
                    ).fetchone()
                    if row:
                        if not job.description and row["description"]:
                            job.description = row["description"]
                        if (not job.location or job.location == "未提供") and row["location"]:
                            job.location = row["location"]
                        if (not job.salary or job.salary == "面議") and row["salary"]:
                            job.salary = row["salary"]
                        if (not job.company or job.company == "未知企業") and row["company"]:
                            job.company = row["company"]
                        if job.description and job.location and job.location != "未提供" and job.salary and job.salary != "面議" and job.company != "未知企業":
                            return job
            except Exception:
                pass

        p = job.platform.lower()
        if p == "104":
            return self.fetch_104(job)
        elif p == "yourator":
            return self.fetch_yourator(job)
        elif p == "yes123":
            return self.fetch_yes123(job)
        elif p == "cake":
            return self.fetch_cake(job)
        elif p == "1111":
            return self.fetch_1111(job, browser_page=browser_page)
        return job
