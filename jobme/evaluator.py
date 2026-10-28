from __future__ import annotations
import hashlib
import json
import logging
import os
import re
from typing import Any, Dict, Optional, Tuple

from .company_enricher import CompanyEnricher
from .db import (
    find_job_by_url,
    get_job_and_eval,
    init_db,
    mark_notified,
    save_evaluation,
    save_job,
    save_profile_fit,
    update_job_status,
)
from .detail_fetcher import JobDetailFetcher
from .exporter import append_profile_fit_to_csv, append_scored_jobs_to_csv
from .jev import JevClient
from .models import DimensionScore, Job, ScoredJob
from .notifier import DiscordNotifier
from .rubric import RubricConfig, parse_rubric_file
from .translator import JobTranslator

logger = logging.getLogger("jobme.evaluator")


def is_valid_job_url(url: str) -> bool:
    """Check if the given URL corresponds to a supported single job listing detail page."""
    if not url:
        return False
    if "104.com.tw" in url:
        return bool(re.search(r"104\.com\.tw/job/([a-zA-Z0-9]+)", url))
    if "cake.me" in url or "cakeresume.com" in url:
        return bool(re.search(r"cake(?:\.me|resume\.com)/companies/[^/]+/jobs/([^/?#]+)", url))
    if "yourator.co" in url:
        return bool(re.search(r"yourator\.co/(?:companies/[^/]+/)?jobs/(\d+)", url))
    if "1111.com.tw" in url:
        return bool(re.search(r"1111\.com\.tw/job/(\d+)", url))
    if "yes123.com.tw" in url:
        return bool(re.search(r"job_id=([a-zA-Z0-9_]+)", url))
    return False


def parse_job_url(url: str) -> Tuple[str, str]:
    """
    Parse a job listing URL to extract (platform, job_id).
    Supports 104, Yourator, Cake, 1111, Yes123.
    Fallback generates deterministic hash for unknown platforms.
    """
    clean_url = url.split("?")[0].split("#")[0].strip()

    # 1. 104
    m104 = re.search(r"104\.com\.tw/job/([a-zA-Z0-9]+)", url)
    if m104:
        return "104", m104.group(1)

    # 2. Yourator
    myourator = re.search(r"yourator\.co/(?:companies/[^/]+/)?jobs/(\d+)", url)
    if myourator:
        return "yourator", myourator.group(1)

    # 3. Cake / CakeResume
    mcake = re.search(r"cake(?:\.me|resume\.com)/companies/[^/]+/jobs/([^/?#]+)", url)
    if mcake:
        return "cake", mcake.group(1)

    # 4. 1111
    m1111 = re.search(r"1111\.com\.tw/job/(\d+)", url)
    if m1111:
        return "1111", m1111.group(1)

    # 5. Yes123
    myes = re.search(r"yes123\.com\.tw/.*(?:job_id=([a-zA-Z0-9_]+))", url)
    if myes:
        pid_m = re.search(r"p_id=([a-zA-Z0-9_]+)", url)
        jid = myes.group(1)
        if pid_m:
            jid = f"{pid_m.group(1)}_{jid}"
        return "yes123", jid

    # Fallback: Extract domain or hash
    m_domain = re.search(r"https?://(?:www\.)?([^/]+)", url)
    domain_name = m_domain.group(1).split(".")[0] if m_domain else "web"
    fallback_id = hashlib.sha256(clean_url.encode("utf-8")).hexdigest()[:16]
    return domain_name, fallback_id


class JobEvaluatorService:
    def __init__(
        self,
        rubric_path: Optional[str] = None,
        profile_path: Optional[str] = None,
        db_path: str = "jobs.db",
    ):
        self.db_path = db_path
        init_db(self.db_path)

        # Rubric loading
        rubric_candidates = (
            [rubric_path]
            if rubric_path
            else ["config/rubric.md", "rubric.md", "config/rubric.example.md", "rubric.example.md"]
        )
        self.rubric_file = next((f for f in rubric_candidates if f and os.path.exists(f)), None)
        if not self.rubric_file:
            raise FileNotFoundError("找不到評分標準檔案 (config/rubric.md 或 rubric.md)")
        self.config: RubricConfig = parse_rubric_file(self.rubric_file)

        # Profile loading
        profile_candidates = (
            [profile_path]
            if profile_path
            else ["config/profile.md", "profile.md", "config/profile.example.md", "profile.example.md"]
        )
        self.profile_file = next((f for f in profile_candidates if f and os.path.exists(f)), None)
        self.profile_text = ""
        if self.profile_file:
            with open(self.profile_file, "r", encoding="utf-8") as f:
                self.profile_text = f.read()

        # Service singletons
        self.detail_fetcher = JobDetailFetcher(db_path=self.db_path)
        self.jev_client = JevClient()
        self.enricher = CompanyEnricher(rubric_path=self.rubric_file)
        self.translator = JobTranslator()
        self.notifier = DiscordNotifier()

        # Cache translated English profile
        self._profile_en: Optional[str] = None

    def get_profile_en(self) -> str:
        if self._profile_en is None:
            if self.profile_text:
                self._profile_en = self.translator.translate_text(self.profile_text)
            else:
                self._profile_en = ""
        return self._profile_en

    def reload_config(self) -> None:
        """Reload rubric.md and profile.md dynamically if changed."""
        if self.rubric_file and os.path.exists(self.rubric_file):
            self.config = parse_rubric_file(self.rubric_file)
        if self.profile_file and os.path.exists(self.profile_file):
            with open(self.profile_file, "r", encoding="utf-8") as f:
                new_profile = f.read()
                if new_profile != self.profile_text:
                    self.profile_text = new_profile
                    self._profile_en = None

    def check_job(
        self,
        url: str,
        platform: Optional[str] = None,
        job_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Check if a job URL or (platform, job_id) has previously been seen/evaluated.
        """
        if not is_valid_job_url(url):
            return {
                "status": "not_a_job",
                "url": url,
                "message": "目前網址非支援求職平台之單一職缺詳情頁 (僅支援 104、Cake、Yourator、1111、yes123 之單一職缺頁面)。",
            }

        if not platform or not job_id:
            parsed_platform, parsed_id = parse_job_url(url)
            platform = platform or parsed_platform
            job_id = job_id or parsed_id

        # 1. Lookup by URL in DB
        record = find_job_by_url(url, db_path=self.db_path)
        if not record and platform and job_id:
            record = get_job_and_eval(platform, str(job_id), db_path=self.db_path)

        if record:
            total_score = record.get("total_score")
            fit_level = record.get("fit_level")

            # Check if Stage 2 evaluated
            if total_score is not None:
                details = []
                raw_details = record.get("details_json")
                if raw_details:
                    try:
                        details = json.loads(raw_details) if isinstance(raw_details, str) else raw_details
                    except Exception:
                        details = []

                # Find company tier from details if present
                tier = "未知"
                for d in details:
                    if "權威" in d.get("name", ""):
                        s = d.get("score", 0)
                        if s >= 45:
                            tier = "Tier 1"
                        elif s >= 30:
                            tier = "Tier 2"
                        elif s >= 15:
                            tier = "Tier 3"
                        else:
                            tier = "Tier 4"

                return {
                    "status": "evaluated",
                    "platform": record["platform"],
                    "job_id": record["job_id"],
                    "title": record["title"],
                    "company": record["company"],
                    "url": record["url"],
                    "total_score": round(float(total_score), 1),
                    "is_qualified": float(total_score) >= self.config.notify_threshold,
                    "has_low_confidence": bool(record.get("has_low_confidence", 0)),
                    "fit_level": fit_level,
                    "company_tier": tier,
                    "dimensions": [
                        {
                            "name": d.get("name", ""),
                            "score": round(float(d.get("score", 0)), 1),
                            "max": d.get("max", d.get("max_points", 0)),
                            "confidence": round(float(d.get("confidence", 1.0)), 2),
                        }
                        for d in details
                    ],
                }

            # Check if rejected in Stage 1
            if fit_level in ("unrelated", "low"):
                return {
                    "status": "stage1_rejected",
                    "platform": record["platform"],
                    "job_id": record["job_id"],
                    "title": record["title"],
                    "company": record["company"],
                    "url": record["url"],
                    "fit_level": fit_level,
                    "total_score": 0.0,
                    "is_qualified": False,
                    "message": f"此職缺先前已被 Stage 1 適配度排除 ({fit_level.upper()})",
                }

            return {
                "status": "pending",
                "platform": record["platform"],
                "job_id": record["job_id"],
                "title": record["title"],
                "company": record["company"],
                "url": record["url"],
                "fit_level": fit_level,
            }

        return {
            "status": "not_evaluated",
            "platform": platform,
            "job_id": job_id,
            "url": url,
        }

    def evaluate_job(
        self,
        url: str,
        title: Optional[str] = None,
        company: Optional[str] = None,
        description: Optional[str] = None,
        platform: Optional[str] = None,
        job_id: Optional[str] = None,
        location: Optional[str] = None,
        salary: Optional[str] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        """
        Evaluate a single job through Stage 1 & Stage 2 pipeline.
        Saves result in DB, writes to CSV, and dispatches Discord notification if qualified.
        """
        if not is_valid_job_url(url):
            return {
                "status": "not_a_job",
                "url": url,
                "message": "目前網址非支援求職平台之單一職缺詳情頁，無法進行 AI 決策評測。",
            }

        self.reload_config()

        parsed_platform, parsed_id = parse_job_url(url)
        platform = platform or parsed_platform
        job_id = job_id or parsed_id

        # If not forcing, check if already evaluated
        if not force:
            existing = self.check_job(url, platform=platform, job_id=job_id)
            if existing.get("status") == "evaluated":
                return existing

        # Build Job instance
        clean_title = (title or "未命名職缺").strip()
        clean_comp = (company or "未知企業").strip()
        clean_loc = (location or "").strip()
        clean_sal = (salary or "面議").strip()

        # Parse company and location from title if title contains pipes (common in 104 document.title)
        if "｜" in clean_title or "|" in clean_title:
            parts = re.split(r"\s*[|｜]\s*", clean_title)
            if len(parts) >= 2:
                clean_title = parts[0].strip()
                if not clean_comp or clean_comp == "未知企業":
                    potential_comp = parts[1].split("－")[0].split("-")[0].strip()
                    if potential_comp:
                        clean_comp = potential_comp
                if (not clean_loc or clean_loc in ("未提供", "")) and len(parts) >= 3:
                    potential_loc = parts[2].split("－")[0].split("-")[0].strip()
                    if potential_loc:
                        clean_loc = potential_loc

        job = Job(
            platform=platform,
            job_id=str(job_id),
            title=clean_title,
            company=clean_comp,
            location=clean_loc,
            salary=clean_sal,
            url=url,
            description=description or "",
        )

        # 1. Fetch missing descriptions, company, location, or salary using DetailFetcher
        needs_detail = (
            not job.description
            or len(job.description.strip()) < 50
            or job.company in ("未知企業", "")
            or not job.location
            or job.location in ("未提供", "")
            or not job.salary
            or job.salary in ("面議", "")
        )
        if needs_detail:
            logger.info(f"[Evaluator] 職缺資訊不完整 (公司/地點/薪資/內文)，正在透過 DetailFetcher 補足: {job.url}")
            job = self.detail_fetcher.fetch_job_detail(job)

        save_job(job, status="pending_stage1", db_path=self.db_path)

        # 2. Stage 1: Profile Fit Gatekeeper
        profile_en = self.get_profile_en()
        fit_level = "unspecified"
        if profile_en:
            try:
                fit_level, fit_conf, fit_probs = self.jev_client.evaluate_profile_fit(
                    job, profile_en, dry_run=False
                )
                save_profile_fit(job, fit_level, fit_conf, fit_probs, db_path=self.db_path)
                append_profile_fit_to_csv(job, fit_level, fit_conf, fit_probs)
            except Exception as e:
                logger.error(f"[Evaluator] Profile Fit 評估失敗: {e}")
                fit_level = "medium"

            if fit_level in ("unrelated", "low"):
                logger.info(
                    f"[Evaluator] Stage 1 適配度未達標 ({fit_level.upper()}): {job.title} @ {job.company}"
                )
                return {
                    "status": "stage1_rejected",
                    "platform": job.platform,
                    "job_id": job.job_id,
                    "title": job.title,
                    "company": job.company,
                    "url": job.url,
                    "fit_level": fit_level,
                    "total_score": 0.0,
                    "is_qualified": False,
                    "message": f"Stage 1 適配度未達標 ({fit_level.upper()})，方向與履歷不符。",
                }

        update_job_status(job.platform, job.job_id, "pending_stage2", db_path=self.db_path)

        # 3. Stage 2: Company Enrichment
        company_prof = self.enricher.enrich_company(job, rubric_config=self.config)

        # 4. Standardized English Fact Sheet
        english_state = self.translator.build_english_fact_sheet(
            job=job,
            company_profile=company_prof,
            profile_text=profile_en,
        )

        # 5. Jev Evaluation
        scored: ScoredJob = self.jev_client.evaluate_job(
            job,
            self.config,
            state=english_state,
            company_profile=company_prof,
            profile_fit=fit_level if fit_level != "unspecified" else None,
        )

        # 6. Save to DB & CSV
        save_evaluation(scored, db_path=self.db_path)
        should_archive = (scored.total_score >= self.config.base_threshold) or self.config.save_all_evaluated
        if should_archive:
            append_scored_jobs_to_csv([scored], "records.csv")

        # 7. Notify Discord if qualified
        notified = False
        if scored.is_qualified:
            sent = self.notifier.notify_job(scored)
            if sent:
                mark_notified(job.platform, job.job_id, db_path=self.db_path)
                notified = True
                logger.info(f"[Evaluator] ✅ 已推播至 Discord: {job.title}")

        return {
            "status": "evaluated",
            "platform": job.platform,
            "job_id": job.job_id,
            "title": job.title,
            "company": job.company,
            "url": job.url,
            "total_score": round(scored.total_score, 1),
            "is_qualified": scored.is_qualified,
            "has_low_confidence": scored.has_low_confidence,
            "fit_level": fit_level,
            "company_tier": company_prof.tier,
            "dimensions": [
                {
                    "name": ds.name,
                    "score": round(ds.score, 1),
                    "max": ds.max_points,
                    "confidence": round(ds.confidence, 2),
                }
                for ds in scored.dimension_scores
            ],
            "notified": notified,
        }
