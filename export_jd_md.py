#!/usr/bin/env python3
"""
職缺 JD 打包工具 (export_jd_md.py)
抓取指定分類職缺網址之詳細內容並輸出為 Markdown 檔案。
"""

from __future__ import annotations
import argparse
import json
import os
import re
import sqlite3
import sys
import time
from typing import Dict, List, Optional
import requests
from bs4 import BeautifulSoup

# Ensure utf-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_RAW_JOBS = """
旗艦
[https://www.104.com.tw/job/96pa9]
[https://www.104.com.tw/job/8wtoj](https://www.104.com.tw/job/8wtoj)
[https://www.104.com.tw/job/7gmn5](https://www.104.com.tw/job/7gmn5)
[https://www.104.com.tw/job/96zup](https://www.google.com/search?q=https://www.104.com.tw/job/96zup)
[https://www.104.com.tw/job/7w4n3](https://www.104.com.tw/job/7w4n3)
主力
[https://www.yourator.co/companies/7ELEVEn/jobs/43717](https://www.yourator.co/companies/7ELEVEn/jobs/43717)
[https://www.yes123.com.tw/wk_index/job.asp?p_id=20191023170408_53017509&job_id=20260316031525_21801861](https://www.yes123.com.tw/wk_index/job.asp?p_id=20191023170408_53017509&job_id=20260316031525_21801861)
[https://www.104.com.tw/job/8svlj](https://www.104.com.tw/job/8svlj)
[https://www.104.com.tw/job/8z1la](https://www.104.com.tw/job/8z1la)
[https://www.104.com.tw/job/86nvv](https://www.104.com.tw/job/86nvv)
[https://www.104.com.tw/job/96pm7](https://www.google.com/search?q=https://www.104.com.tw/job/96pm7)
[https://www.104.com.tw/job/7hw0o](https://www.google.com/search?q=https://www.104.com.tw/job/7hw0o)
[https://www.104.com.tw/job/96pa9](https://www.google.com/search?q=https://www.104.com.tw/job/96pa9)
[https://www.104.com.tw/job/960ry](https://www.104.com.tw/job/960ry)
[https://www.104.com.tw/job/95dqu](https://www.104.com.tw/job/95dqu)
[https://www.104.com.tw/job/96pa9](https://www.google.com/search?q=https://www.104.com.tw/job/96pa9)
[https://www.104.com.tw/job/8jtjh](https://www.104.com.tw/job/8jtjh)
[https://www.104.com.tw/job/8z7dp](https://www.104.com.tw/job/8z7dp)
[https://www.1111.com.tw/job/132813621?mid=searchlist](https://www.1111.com.tw/job/132813621?mid=searchlist)
[https://www.cake.me/companies/mediatek/jobs/e97ac265-f82d-47a2-8afb-1939bffd47fd-engineering-intern-1608b6227767be739c9abbf2c85d68](https://www.cake.me/companies/mediatek/jobs/e97ac265-f82d-47a2-8afb-1939bffd47fd-engineering-intern-1608b6227767be739c9abbf2c85d68)
[https://www.cake.me/companies/tsmc/jobs/928c890b-bb51-4027-ac7d-cadf83d5c308-information-technology-intern-h-9b1444a76267336c87b8d17283e3ea](https://www.cake.me/companies/tsmc/jobs/928c890b-bb51-4027-ac7d-cadf83d5c308-information-technology-intern-h-9b1444a76267336c87b8d17283e3ea)
[https://www.cake.me/companies/micron-technology/jobs/4879089c-723c-49fd-8aa7-73dee2468c47-intern-process-equipment-eng-390937b33805686e37f8ed66b2be05](https://www.google.com/search?q=https://www.cake.me/companies/micron-technology/jobs/4879089c-723c-49fd-8aa7-73dee2468c47-intern-process-equipment-eng-390937b33805686e37f8ed66b2be05)
[https://www.cake.me/companies/micron-technology/jobs/69edd132-f01f-446c-ba28-6db32e9f5188-intern-probe-automation-f49e8071500ef104e205c6bd20b2eb](https://www.google.com/search?q=https://www.cake.me/companies/micron-technology/jobs/69edd132-f01f-446c-ba28-6db32e9f5188-intern-probe-automation-f49e8071500ef104e205c6bd20b2eb)
[https://www.cake.me/companies/micron-technology/jobs/d43a8dac-0b0b-464d-ae30-43d201323aeb-intern-hvm-pee-photo-ai-1385ab0f6493c64bd82d52b8ab73a3](https://www.google.com/search?q=https://www.cake.me/companies/micron-technology/jobs/d43a8dac-0b0b-464d-ae30-43d201323aeb-intern-hvm-pee-photo-ai-1385ab0f6493c64bd82d52b8ab73a3)
[https://www.cake.me/companies/micron-technology/jobs/5690a4bf-12ee-45b1-bf28-cb5f9e70d1ab-intern-assembly-npi-93aff74efc127653291f76ace78442](https://www.google.com/search?q=https://www.cake.me/companies/micron-technology/jobs/5690a4bf-12ee-45b1-bf28-cb5f9e70d1ab-intern-assembly-npi-93aff74efc127653291f76ace78442)
候選
[https://www.104.com.tw/job/63355](https://www.google.com/search?q=https://www.104.com.tw/job/63355)
[https://www.104.com.tw/job/95ckk](https://www.104.com.tw/job/95ckk)
[https://www.104.com.tw/job/7dn1v](https://www.104.com.tw/job/7dn1v)
[https://www.104.com.tw/job/95j5e](https://www.google.com/search?q=https://www.104.com.tw/job/95j5e)
[https://www.104.com.tw/job/96hlu](https://www.104.com.tw/job/96hlu)
[https://www.104.com.tw/job/9605i](https://www.google.com/search?q=https://www.104.com.tw/job/9605i)
[https://www.cake.me/companies/micron-technology/jobs/6868a896-4453-4cb1-a057-2d8af4abde7a-intern-2026-sales-operation-an-c0fd2c5bd1c8ec2608cfb983ec1e97](https://www.cake.me/companies/micron-technology/jobs/6868a896-4453-4cb1-a057-2d8af4abde7a-intern-2026-sales-operation-an-c0fd2c5bd1c8ec2608cfb983ec1e97)
[https://www.cake.me/companies/micron-technology/jobs/3302a66c-7cff-4e2a-8c01-0adbcb07fb62-intern-fe-materials-procuremen-e15d4a94320ee6fb7f414fb10b6d74](https://www.google.com/search?q=https://www.cake.me/companies/micron-technology/jobs/3302a66c-7cff-4e2a-8c01-0adbcb07fb62-intern-fe-materials-procuremen-e15d4a94320ee6fb7f414fb10b6d74)
[https://www.cake.me/companies/micron-technology/jobs/715fb00a-ec12-43fb-bd28-e41c9a576b4d-intern-nand-product-developmen-f1ac5a708cb078a00be75ebcc95d22](https://www.google.com/search?q=https://www.cake.me/companies/micron-technology/jobs/715fb00a-ec12-43fb-bd28-e41c9a576b4d-intern-nand-product-developmen-f1ac5a708cb078a00be75ebcc95d22)
[https://www.1111.com.tw/job/132813624?mid=searchlist](https://www.1111.com.tw/job/132813624?mid=searchlist)
[https://www.104.com.tw/job/8bc1p](https://www.104.com.tw/job/8bc1p)
[https://www.104.com.tw/job/8qu2q](https://www.104.com.tw/job/8qu2q)
[https://www.104.com.tw/job/8qoor](https://www.104.com.tw/job/8qoor)
[https://www.104.com.tw/job/8xygm](https://www.104.com.tw/job/8xygm)
[https://www.104.com.tw/job/86nvv](https://www.104.com.tw/job/86nvv)
[https://www.104.com.tw/job/8nyw3](https://www.104.com.tw/job/8nyw3)
[https://www.104.com.tw/job/8pv49](https://www.google.com/search?q=https://www.104.com.tw/job/8pv49)
[https://www.104.com.tw/job/8r34c](https://www.104.com.tw/job/8r34c)
[https://www.104.com.tw/job/793nx](https://www.104.com.tw/job/793nx)
[https://www.104.com.tw/job/96qrc](https://www.google.com/search?q=https://www.104.com.tw/job/96qrc)
[https://www.yourator.co/companies/sudoresearch/jobs/46536](https://www.yourator.co/companies/sudoresearch/jobs/46536)
[https://www.cake.me/companies/micron-technology/jobs/3dd6fe45-21b3-4f22-b289-8ea56ca690c4-intern-ssd-manufacturing-dcf4d1a8a18a25ef017fdfd38e6fa0](https://www.google.com/search?q=https://www.cake.me/companies/micron-technology/jobs/3dd6fe45-21b3-4f22-b289-8ea56ca690c4-intern-ssd-manufacturing-dcf4d1a8a18a25ef017fdfd38e6fa0)
"""

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    )
}


def clean_text(text: str) -> str:
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines()]
    res = []
    prev_blank = False
    for line in lines:
        if not line:
            if not prev_blank:
                res.append("")
                prev_blank = True
        else:
            res.append(line)
            prev_blank = False
    return "\n".join(res).strip()


def safe_request(session: requests.Session, url: str, headers: Optional[dict] = None) -> requests.Response:
    # ponytail: retry on 429 rate-limiting with brief backoff
    for attempt in range(3):
        h = headers or {}
        r = session.get(url, headers=h, timeout=12)
        if r.status_code == 429:
            time.sleep(2.5 * (attempt + 1))
            continue
        return r
    return r


def fetch_104(url: str, session: requests.Session) -> dict:
    m = re.search(r"/job/([a-zA-Z0-9]+)", url)
    if not m:
        return {}
    job_code = m.group(1)
    api_url = f"https://www.104.com.tw/job/ajax/content/{job_code}"
    r = safe_request(session, api_url, headers={"Referer": url})
    if r.status_code != 200:
        return {"error": f"104 API HTTP {r.status_code}"}

    data = r.json().get("data", {})
    header = data.get("header", {})
    detail = data.get("jobDetail", {})
    condition = data.get("condition", {})
    welfare = data.get("welfare", {})

    title = header.get("jobName", "").strip()
    company = header.get("custName", "").strip()
    salary = detail.get("salary", "").strip()
    location = f"{detail.get('addressRegion', '')} {detail.get('addressDetail', '')}".strip()

    desc_parts = []
    desc = detail.get("jobDescription", "").strip()
    if desc:
        desc_parts.append(desc)
    cats = [c.get("description", "") for c in detail.get("jobCategory", []) if c.get("description")]
    if cats:
        desc_parts.append(f"【職務類別】" + "、".join(cats))

    req_parts = []
    if condition.get("workExp"):
        req_parts.append(f"- **工作經歷**：{condition.get('workExp')}")
    if condition.get("edu"):
        req_parts.append(f"- **學歷要求**：{condition.get('edu')}")
    if condition.get("major"):
        req_parts.append(f"- **科系要求**：{'、'.join(condition.get('major'))}")
    skills = [s.get("description", "") for s in condition.get("skill", []) if s.get("description")]
    if skills:
        req_parts.append(f"- **具備技能**：{'、'.join(skills)}")
    specs = [s.get("description", "") for s in condition.get("specialty", []) if s.get("description")]
    if specs:
        req_parts.append(f"- **擅長工具**：{'、'.join(specs)}")
    if condition.get("other"):
        req_parts.append(f"- **其他條件**：\n{condition.get('other').strip()}")

    wel = welfare.get("welfare", "").strip()

    return {
        "platform": "104 人力銀行",
        "title": title,
        "company": company,
        "salary": salary,
        "location": location,
        "description": clean_text("\n\n".join(desc_parts)),
        "requirements": clean_text("\n".join(req_parts)),
        "welfare": clean_text(wel),
    }


def fetch_cake(url: str, session: requests.Session) -> dict:
    r = safe_request(session, url)
    if r.status_code != 200:
        return {"error": f"Cake HTTP {r.status_code}"}

    soup = BeautifulSoup(r.text, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        return {"error": "Cake __NEXT_DATA__ not found"}

    data = json.loads(script.string)
    props = data.get("props", {}).get("pageProps", {})
    job = props.get("job", {})
    comp = props.get("company", {}) or job.get("company", {})

    title = job.get("title", "").strip()
    c_name = comp.get("name") or comp.get("legalName") or comp.get("company") or ""
    company = str(c_name).strip()
    salary = job.get("salaryDescription") or ""
    if not salary and job.get("salaryMin"):
        salary = f"{job.get('salaryMin')} - {job.get('salaryMax', '')} {job.get('salaryCurrency', '')}".strip()
    location = job.get("locationSummary") or job.get("location") or ""

    desc_html = job.get("description", "")
    desc = BeautifulSoup(desc_html, "html.parser").get_text("\n", strip=True) if desc_html else ""

    req_html = job.get("requirements", "")
    req = BeautifulSoup(req_html, "html.parser").get_text("\n", strip=True) if req_html else ""

    tags = job.get("tags", [])
    tag_str = ""
    if tags:
        tag_str = "- **標籤技能**：" + "、".join(tags)

    req_final = clean_text(f"{tag_str}\n\n{req}" if tag_str else req)

    return {
        "platform": "Cake (CakeResume)",
        "title": title,
        "company": company,
        "salary": salary,
        "location": location,
        "description": clean_text(desc),
        "requirements": req_final,
        "welfare": "",
    }


def fetch_yourator(url: str, session: requests.Session) -> dict:
    r = safe_request(session, url)
    if r.status_code != 200:
        return {"error": f"Yourator HTTP {r.status_code}"}

    soup = BeautifulSoup(r.text, "html.parser")
    title_el = soup.find("h1")
    title = title_el.get_text(strip=True) if title_el else ""

    comp_el = soup.find("a", href=re.compile(r"/companies/[^/]+$")) or soup.find("a", href=re.compile(r"/companies/"))
    company = comp_el.get_text(strip=True) if comp_el else ""

    salary = ""
    location = ""
    basic = soup.find("div", class_=re.compile(r"basic-info|job-info"))
    if basic:
        txt = basic.get_text(" ", strip=True)
        if any(k in txt for k in ["月薪", "年薪", "時薪"]):
            salary = txt

    main = soup.find("section", class_="job-description") or soup
    desc_parts = []
    req_parts = []

    headings = main.find_all(["h2", "h3", "h4", "h5"])
    for h in headings:
        htxt = h.get_text(strip=True)
        if not htxt:
            continue
        content = []
        curr = h.find_next_sibling()
        while curr and curr.name not in ["h2", "h3"]:
            t = curr.get_text("\n", strip=True)
            if t and not any(k in t for k in ["福利", "法定保障"]):
                content.append(t)
            curr = curr.find_next_sibling()
        c_text = "\n".join(content)
        if any(k in htxt for k in ["工作內容", "你將在", "職責", "關於"]):
            desc_parts.append(f"#### {htxt}\n{c_text}")
        elif any(k in htxt for k in ["條件", "能力", "加分", "要求"]):
            req_parts.append(f"#### {htxt}\n{c_text}")

    if not desc_parts and not req_parts:
        desc_parts.append(main.get_text("\n", strip=True))

    return {
        "platform": "Yourator",
        "title": title,
        "company": company,
        "salary": salary,
        "location": location,
        "description": clean_text("\n\n".join(desc_parts)),
        "requirements": clean_text("\n\n".join(req_parts)),
        "welfare": "",
    }


def fetch_yes123(url: str, session: requests.Session) -> dict:
    r = safe_request(session, url)
    r.encoding = "utf-8"
    if r.status_code != 200:
        return {"error": f"Yes123 HTTP {r.status_code}"}

    soup = BeautifulSoup(r.text, "html.parser")
    full_title = soup.find("title").get_text(strip=True) if soup.find("title") else ""
    t_parts = [p.strip() for p in full_title.split("|")]
    title = t_parts[0] if len(t_parts) > 0 else ""
    company = t_parts[1] if len(t_parts) > 1 else ""

    parts = []
    for div in soup.find_all("div", class_="job_explain"):
        txt = div.get_text("\n", strip=True)
        if any(k in txt[:40] for k in ["保險福利", "企業福利", "連絡人", "防騙"]):
            continue
        parts.append(txt)

    return {
        "platform": "Yes123",
        "title": title,
        "company": company,
        "salary": "",
        "location": "",
        "description": clean_text("\n\n".join(parts)),
        "requirements": "",
        "welfare": "",
    }


def fetch_1111(url: str, db_path: str = "jobs.db") -> dict:
    m = re.search(r"/job/([0-9]+)", url)
    if not m:
        return {}
    jid = m.group(1)
    # ponytail: 1111 has Cloudflare/WAF block on raw requests; reads cached record from jobs.db
    if os.path.exists(db_path):
        with sqlite3.connect(db_path) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT title, company, location, salary, description FROM jobs WHERE platform='1111' AND job_id=?",
                (jid,),
            )
            row = cur.fetchone()
            if row:
                return {
                    "platform": "1111 人力銀行",
                    "title": row[0],
                    "company": row[1],
                    "location": row[2],
                    "salary": row[3],
                    "description": clean_text(row[4]),
                    "requirements": "",
                    "welfare": "",
                }
    return {
        "platform": "1111 人力銀行",
        "title": f"1111 職缺 ({jid})",
        "company": "",
        "salary": "",
        "location": "",
        "description": f"請透過瀏覽器開啟連結查看：{url}",
        "requirements": "",
        "welfare": "",
    }


def parse_job_entries(raw_text: str) -> List[tuple[str, str]]:
    current_category = "未分類"
    entries = []
    seen = set()

    for line in raw_text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        if line in ["旗艦", "主力", "候選"] or line.startswith("#"):
            current_category = line.lstrip("#").strip()
            continue

        # Extract markdown link text or direct url
        m = re.search(r"\[(https?://[^\]]+)\]", line)
        if m:
            url = m.group(1)
        else:
            m_direct = re.search(r"(https?://\S+)", line)
            if m_direct:
                url = m_direct.group(1).rstrip(")")
            else:
                continue

        # Clean Google search redirect if present
        if "google.com/search?q=" in url:
            url = url.split("google.com/search?q=")[-1]

        # ponytail: deduplicate URLs while preserving the highest priority category encountered
        if url in seen:
            continue
        seen.add(url)
        entries.append((current_category, url))

    return entries


def fetch_all(entries: List[tuple[str, str]], db_path: str = "jobs.db") -> List[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)
    results = []
    total = len(entries)

    for i, (category, url) in enumerate(entries, 1):
        print(f"[{i}/{total}] 擷取中 ({category}): {url} ...", flush=True)
        info = {}
        try:
            if "104.com.tw" in url:
                info = fetch_104(url, session)
            elif "cake.me" in url:
                info = fetch_cake(url, session)
            elif "yourator.co" in url:
                info = fetch_yourator(url, session)
            elif "yes123.com.tw" in url:
                info = fetch_yes123(url, session)
            elif "1111.com.tw" in url:
                info = fetch_1111(url, db_path=db_path)
            else:
                info = {"error": f"不支援的平台網址: {url}"}
        except Exception as e:
            info = {"error": str(e)}

        info["category"] = category
        info["url"] = url
        results.append(info)
        # Avoid aggressive request bursts
        time.sleep(0.8)

    return results


def generate_markdown(results: List[dict], output_path: str) -> None:
    lines = []
    lines.append("# 職缺詳細 JD 匯總清單\n")
    lines.append(f"> 自動打包時間：{time.strftime('%Y-%m-%d %H:%M:%S')}  ")
    lines.append(f"> 總職缺筆數：共 {len(results)} 筆（已去重）\n")

    # Table of contents
    lines.append("## 職缺索引目錄\n")
    categories = ["旗艦", "主力", "候選"]
    by_cat: Dict[str, List[dict]] = {c: [] for c in categories}
    by_cat["其他"] = []

    for r in results:
        c = r.get("category", "其他")
        if c in by_cat:
            by_cat[c].append(r)
        else:
            by_cat["其他"].append(r)

    for cat in categories + (["其他"] if by_cat["其他"] else []):
        cat_jobs = by_cat.get(cat, [])
        if not cat_jobs:
            continue
        lines.append(f"### {cat} ({len(cat_jobs)} 筆)\n")
        for idx, job in enumerate(cat_jobs, 1):
            title = job.get("title") or "（無職稱）"
            comp = job.get("company") or "（未知公司）"
            anchor = re.sub(r"[^\w\u4e00-\u9fff\- ]", "", f"{cat}-{title}-{comp}").strip().replace(" ", "-").lower()
            lines.append(f"{idx}. [{title} - {comp}](#{anchor})")
        lines.append("")

    lines.append("---\n")

    # Details
    for r in results:
        cat = r.get("category", "未分類")
        title = r.get("title") or "未命名職缺"
        comp = r.get("company") or "未知公司"
        platform = r.get("platform") or "職缺平台"
        salary = r.get("salary") or "依公司規定 / 面議"
        location = r.get("location") or "未提供"
        url = r.get("url")

        lines.append(f"## [{cat}] {title} - {comp}\n")
        lines.append(f"- **職缺平台**：{platform}")
        lines.append(f"- **所屬公司**：{comp}")
        lines.append(f"- **薪資待遇**：{salary}")
        lines.append(f"- **工作地點**：{location}")
        lines.append(f"- **職缺連結**：[{url}]({url})\n")

        desc = r.get("description", "").strip()
        req = r.get("requirements", "").strip()
        wel = r.get("welfare", "").strip()

        if desc:
            lines.append("### 工作內容\n")
            lines.append(desc + "\n")

        if req:
            lines.append("### 條件要求\n")
            lines.append(req + "\n")

        if wel:
            lines.append("### 福利與其他\n")
            lines.append(wel + "\n")

        if not desc and not req:
            lines.append(f"> ⚠️ 內容擷取備註：{r.get('error', '無詳細說明')}\n")

        lines.append("---\n")

    content = "\n".join(lines)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"\n[完成] Markdown 已輸出至: {os.path.abspath(output_path)}")


def run_self_check() -> None:
    # ponytail: minimal assert-based check verifies one job per platform
    print("[Self-Check] 開始測試核心擷取模組...")
    s = requests.Session()
    s.headers.update(HEADERS)

    # 1. 104
    r_104 = fetch_104("https://www.104.com.tw/job/96pa9", s)
    assert r_104.get("title") and r_104.get("company"), "104 擷取失敗"
    print("  [OK] 104 擷取正常")

    # 2. Cake
    r_cake = fetch_cake("https://www.cake.me/companies/tsmc/jobs/928c890b-bb51-4027-ac7d-cadf83d5c308-information-technology-intern-h-9b1444a76267336c87b8d17283e3ea", s)
    assert r_cake.get("title") and r_cake.get("company"), "Cake 擷取失敗"
    print("  [OK] Cake 擷取正常")

    # 3. Yourator
    r_your = fetch_yourator("https://www.yourator.co/companies/7ELEVEn/jobs/43717", s)
    assert r_your.get("title") and r_your.get("company"), "Yourator 擷取失敗"
    print("  [OK] Yourator 擷取正常")

    # 4. Yes123
    r_yes = fetch_yes123("https://www.yes123.com.tw/wk_index/job.asp?p_id=20191023170408_53017509&job_id=20260316031525_21801861", s)
    assert r_yes.get("title") and r_yes.get("company"), "Yes123 擷取失敗"
    print("  [OK] Yes123 擷取正常")

    # 5. 1111 (from DB)
    r_1111 = fetch_1111("https://www.1111.com.tw/job/132813621?mid=searchlist")
    assert r_1111.get("title") and r_1111.get("company"), "1111 擷取失敗"
    print("  [OK] 1111 擷取正常")

    print("[Self-Check] 所有平台測試通過！")


def main() -> None:
    parser = argparse.ArgumentParser(description="打包職缺網址 JD 至 Markdown 檔案")
    parser.add_argument("--input", "-i", type=str, default="", help="輸入的職缺網址清單檔案 (可省略，預設使用內嵌清單)")
    parser.add_argument("--output", "-o", type=str, default="job_descriptions.md", help="輸出的 Markdown 檔案路徑")
    parser.add_argument("--check", action="store_true", help="執行各平台爬蟲單元自我檢查")
    args = parser.parse_args()

    if args.check:
        run_self_check()
        return

    raw_text = DEFAULT_RAW_JOBS
    if args.input and os.path.exists(args.input):
        with open(args.input, "r", encoding="utf-8") as f:
            raw_text = f.read()

    entries = parse_job_entries(raw_text)
    print(f"解析到 {len(entries)} 筆唯一職缺網址，開始批次抓取...")
    results = fetch_all(entries)
    generate_markdown(results, args.output)


if __name__ == "__main__":
    main()
