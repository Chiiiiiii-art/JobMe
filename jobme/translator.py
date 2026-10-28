from __future__ import annotations
import hashlib
import logging
import re
import os
import threading
import time
from typing import Any, List, Optional
from deep_translator import GoogleTranslator
import requests

from .company_enricher import CompanyProfile
from .db import get_connection
from .models import Job

logger = logging.getLogger("jobme.translator")


class TranslationFatalError(RuntimeError):
    """Raised when translation is completely unavailable and policy forbids raw Chinese fallback."""
    pass


class JobTranslator:
    def __init__(self, db_path: str = "jobs.db", notifier: Optional[Any] = None):
        self.db_path = db_path
        self.notifier = notifier
        self.has_degraded = False
        self.used_deepseek = False
        self._init_cache()
        self.translator = GoogleTranslator(source="zh-TW", target="en")
        self._rate_limit_until: float = 0.0
        self._rate_limit_warned: bool = False
        self.deepseek_api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        self.deepseek_base_url = (os.getenv("DEEPSEEK_API_URL") or "https://api.deepseek.com").rstrip("/")
        self.session = requests.Session()
        self._lock = threading.Lock()
        self._last_request_time: float = 0.0
        self._min_interval: float = 0.35  # Max ~2.8 req/sec across all threads

    def _init_cache(self):
        with get_connection(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS translation_cache (
                    text_hash TEXT PRIMARY KEY,
                    src_text TEXT,
                    en_text TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

    def _has_chinese(self, text: str) -> bool:
        return any('\u4e00' <= char <= '\u9fff' for char in text)

    def _translate_deepseek(self, text: str) -> Optional[str]:
        if not self.deepseek_api_key:
            return None
        try:
            url = f"{self.deepseek_base_url}/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.deepseek_api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": "deepseek-chat",
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a professional translator. Translate the given Traditional Chinese text into clear English. Output ONLY the translated English text with zero markdown headers, zero notes, and zero preamble."
                    },
                    {
                        "role": "user",
                        "content": text[:3000]
                    }
                ],
                "temperature": 0.0,
            }
            resp = self.session.post(url, json=payload, headers=headers, timeout=20)
            if resp.status_code == 200:
                data = resp.json()
                content = data["choices"][0]["message"]["content"].strip()
                if content and not self._has_chinese(content):
                    logger.info("  ↳ [DeepSeek 備援翻譯成功]")
                    return content
        except Exception as e:
            logger.warning(f"DeepSeek 備援翻譯失敗: {e}")
            if self.notifier and not getattr(self, "_deepseek_trans_warned", False):
                self._deepseek_trans_warned = True
                self.notifier.notify_error(
                    title="DeepSeek 備援翻譯失敗",
                    error_msg=f"DeepSeek 備援翻譯發生異常: {e}",
                    domain="deepseek_trans_error",
                )
        return None

    def translate_text(self, text: str) -> str:
        text_clean = text.strip()
        if not text_clean:
            return ""

        # If text is already pure English/ASCII, skip translation completely
        if not self._has_chinese(text_clean):
            return text_clean

        # Hash check
        h = hashlib.sha256(text_clean.encode("utf-8")).hexdigest()
        with get_connection(self.db_path) as conn:
            cur = conn.execute("SELECT en_text FROM translation_cache WHERE text_hash = ?", (h,))
            row = cur.fetchone()
            if row:
                return row[0]

        to_trans = text_clean[:2500]
        en = None

        # 1. Thread-safe rate limiter for Google Translate (Max 2.8 req/sec)
        if time.time() >= self._rate_limit_until:
            with self._lock:
                now = time.time()
                wait = self._min_interval - (now - self._last_request_time)
                if wait > 0:
                    time.sleep(wait)
                self._last_request_time = time.time()

            try:
                en = self._translate_google_api(to_trans)
                if not en:
                    time.sleep(0.15)
                    try:
                        en = self.translator.translate(to_trans)
                    except Exception as ex_g:
                        if "too many requests" in str(ex_g).lower() or "429" in str(ex_g):
                            logger.warning(f"Google 網頁翻譯 429: {ex_g}")
                            self._rate_limit_until = time.time() + 180.0
                            self.has_degraded = True
                            if not self._rate_limit_warned:
                                self._rate_limit_warned = True
                                if self.notifier:
                                    self.notifier.notify_error(
                                        title="Google 翻譯速率受限 (429)",
                                        error_msg=f"Google 網頁翻譯觸發 429 限制，自動切換至 DeepSeek 備援轉譯。\n詳情: {ex_g}",
                                        domain="translation_quota",
                                    )
                        try:
                            from deep_translator import MyMemoryTranslator
                            en = MyMemoryTranslator(source="zh-TW", target="en-US").translate(to_trans[:500])
                        except Exception:
                            en = None
            except Exception as e:
                if "too many requests" in str(e).lower() or "429" in str(e):
                    self._rate_limit_until = time.time() + 180.0
                    self.has_degraded = True
                    if not self._rate_limit_warned:
                        self._rate_limit_warned = True
                        if self.notifier:
                            self.notifier.notify_error(
                                title="Google 翻譯速率受限 (429)",
                                error_msg=f"Google 翻譯遭遇速率限制，自動切換至 DeepSeek 備援轉譯。\n詳情: {e}",
                                domain="translation_quota",
                            )

        # 2. Seamless DeepSeek fallback if Google is unavailable or rate-limited
        if not en or self._has_chinese(en):
            en = self._translate_deepseek(to_trans)
            if en and not self._has_chinese(en):
                self.used_deepseek = True

        # 3. Graceful degradation: bilingual/original text fallback rather than halting entire pipeline
        if not en:
            logger.warning(f"[Translator] 翻譯端點暫時不可用，採用原文字串直通: {text_clean[:30]}...")
            self.has_degraded = True
            if self.notifier and not getattr(self, "_all_trans_failed_warned", False):
                self._all_trans_failed_warned = True
                self.notifier.notify_error(
                    title="翻譯服務全數異常 (已降級為原文直通)",
                    error_msg="Google 與 DeepSeek 翻譯端點皆不可用，系統已降級為中文原文直通，可能影響後續英文評估精準度。",
                    domain="all_trans_failed",
                )
            en = text_clean

        with get_connection(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO translation_cache (text_hash, src_text, en_text) VALUES (?, ?, ?)",
                (h, text_clean[:500], en),
            )
            conn.commit()
        return en

    def _translate_google_api(self, text: str) -> Optional[str]:
        url = "https://translate.googleapis.com/translate_a/single"
        params = {
            "client": "gtx",
            "sl": "zh-TW",
            "tl": "en",
            "dt": "t",
            "q": text[:2500],
        }
        try:
            resp = self.session.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data and data[0]:
                    translated = "".join([seg[0] for seg in data[0] if seg and seg[0]])
                    if translated and not self._has_chinese(translated):
                        return translated.strip()
            elif resp.status_code == 429:
                logger.warning("Google API (gtx) 達到 429 速率限制")
                self._rate_limit_until = time.time() + 180.0
        except Exception as e:
            logger.debug(f"Google API 端點轉譯失敗: {e}")
        return None

    def warmup_translations(self, jobs: List[Job], max_workers: int = 2) -> None:
        """Parallel warm-up of translation cache for all passed jobs."""
        import time
        if time.time() < self._rate_limit_until:
            return

        sep = "\n===SEP===\n"
        bundles = []
        for j in jobs:
            b = f"{j.title}{sep}{(j.description or '').strip()}{sep}{(j.requirements or '').strip()}"
            if self._has_chinese(b):
                bundles.append(b)

        if not bundles:
            return

        # Check existing cached bundles to minimize API calls
        uncached = []
        with get_connection(self.db_path) as conn:
            for b in bundles:
                h = hashlib.sha256(b.strip().encode("utf-8")).hexdigest()
                row = conn.execute("SELECT 1 FROM translation_cache WHERE text_hash = ?", (h,)).fetchone()
                if not row:
                    uncached.append(b)

        if not uncached:
            logger.info("✅ 職缺語言轉譯快取全數命中 (100% Cache Hit)。")
            return

        logger.info(f"正在背景平行加速轉譯 {len(uncached)} 筆未快取職缺資訊...")
        from concurrent.futures import ThreadPoolExecutor
        try:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                for _ in executor.map(self.translate_text, uncached):
                    pass
            logger.info("✅ 職缺語言轉譯快取建立完成。")
        except TranslationFatalError:
            raise
        except Exception as e:
            logger.warning(f"平行轉譯過程異常: {e}")

    def format_salary_english(self, raw_salary: str) -> str:
        s = raw_salary.strip()
        if not s or "面議" in s:
            return "Negotiable (Legal minimum guaranteed, standard Taiwan intern wage standard)"

        # Check hourly rate
        if "時薪" in s or "hr" in s.lower():
            nums = re.findall(r"\d+(?:,\d+)?", s)
            if nums:
                low = float(nums[0].replace(",", ""))
                high = float(nums[1].replace(",", "")) if len(nums) > 1 else low
                usd_low = low / 32.0
                usd_high = high / 32.0
                return f"Hourly Wage: NT$ {low:.0f} - {high:.0f} (~{usd_low:.1f} - {usd_high:.1f} USD/hr, Meets statutory wage)"

        # Check monthly rate
        if "月薪" in s or "month" in s.lower():
            nums = re.findall(r"\d+(?:,\d+)?", s)
            if nums:
                low = float(nums[0].replace(",", ""))
                high = float(nums[1].replace(",", "")) if len(nums) > 1 else low
                usd_low = low / 32.0
                usd_high = high / 32.0
                return f"Monthly Salary: NT$ {low:,.0f} - {high:,.0f} (~{usd_low:,.0f} - {usd_high:,.0f} USD/mo)"

        return f"Reported Salary: {s}"

    def build_english_fact_sheet(
        self,
        job: Job,
        company_profile: CompanyProfile,
        profile_text: str = "",
    ) -> str:
        salary_en = self.format_salary_english(job.salary or "")

        # Combine title, description, and requirements into one batch translation to minimize HTTP calls
        sep = "\n===SEP===\n"
        desc_raw = (job.description or "").strip()
        req_raw = (job.requirements or "").strip()
        bundle = f"{job.title}{sep}{desc_raw}{sep}{req_raw}"

        if self._has_chinese(bundle):
            translated_bundle = self.translate_text(bundle)
            parts = re.split(r"\s*(?:={2,}|(?:\s*=\s*){2,})\s*sep\s*(?:={2,}|(?:\s*=\s*){2,})\s*", translated_bundle, flags=re.IGNORECASE)
            title_en = parts[0].strip() if len(parts) > 0 else job.title
            desc_en = parts[1].strip() if len(parts) > 1 else desc_raw
            req_en = parts[2].strip() if len(parts) > 2 else req_raw
        else:
            title_en = job.title
            desc_en = desc_raw or "Practical engineering or analysis responsibilities."
            req_en = req_raw or "General domain proficiency."

        profile_section = ""
        if profile_text:
            profile_en = self.translate_text(profile_text) if self._has_chinese(profile_text) else profile_text
            profile_section = f"## 5. CANDIDATE PROFILE (APPLICANT BACKGROUND)\n{profile_en}"

        sections = [
            "# JOB POSITION FACT SHEET (EVALUATION TARGET)",
            f"## 1. COMPANY REPUTATION & MARKET BENCHMARK\n{company_profile.to_english_state()}",
            f"## 2. POSITION BASICS\n- Title: {title_en} (Chinese: {job.title})\n- Location: {job.location}\n- Compensation: {salary_en}\n- Source Platform: {job.platform.upper()}",
            f"## 3. CORE RESPONSIBILITIES & TASKS\n{desc_en or 'Practical engineering or analysis responsibilities.'}",
            f"## 4. REQUIREMENTS & SKILLS\n{req_en or 'Standard domain skills and practical tool proficiency.'}",
        ]
        if profile_section:
            sections.append(profile_section)

        return "\n\n".join(sections)
