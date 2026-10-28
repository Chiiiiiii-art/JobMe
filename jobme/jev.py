from __future__ import annotations
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import requests
from dotenv import load_dotenv

from .models import DimensionScore, Job, ScoredJob
from .rubric import Dimension, RubricConfig, get_corporate_authority_dimension, get_tier_points

load_dotenv(override=True)
logger = logging.getLogger("jobme.jev")


@dataclass
class JevProvider:
    name: str
    api_key: str
    api_url: str
    model: str
    disabled: bool = False
    disabled_reason: str = ""


class JevClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        api_url: Optional[str] = None,
        model: Optional[str] = None,
        notifier: Optional[Any] = None,
    ):
        self.notifier = notifier
        self.has_degraded = False
        self._provider_degraded_warned: set = set()
        self.session = requests.Session()

        # Build prioritized provider chain:
        # Priority 1: OpenRouter (paid, fast, typesafe/jev-1.14)
        # Priority 2: TypeSafe native (if configured)
        # Priority 3: DeepSeek structured decision fallback
        self.providers: List[JevProvider] = []

        openrouter_key = os.getenv("OPENROUTER_API_KEY")
        if openrouter_key:
            self.providers.append(
                JevProvider(
                    name="OpenRouter",
                    api_key=openrouter_key,
                    api_url=api_url or "https://openrouter.ai/api/alpha/decisions",
                    model=model or "typesafe/jev-1.14",
                )
            )

        typesafe_key = os.getenv("TYPESAFE_API_KEY") or api_key
        if typesafe_key and not any(p.api_key == typesafe_key for p in self.providers):
            self.providers.append(
                JevProvider(
                    name="TypeSafe",
                    api_key=typesafe_key,
                    api_url=api_url or os.getenv("JEV_API_URL") or "https://api.typesafe.ai/v1/systemone",
                    model=model or os.getenv("JEV_MODEL") or "jev-latest",
                )
            )

        # DeepSeek structured decision fallback
        self.deepseek_key = os.getenv("DEEPSEEK_API_KEY")
        self.deepseek_url = os.getenv("DEEPSEEK_API_URL") or "https://api.deepseek.com"

        active_names = [p.name for p in self.providers]
        if self.deepseek_key:
            active_names.append("DeepSeek-Fallback")
        active_names.append("Heuristic-Local")
        logger.info(f"[Jev] 決策引擎啟動，多級容錯鏈: {' -> '.join(active_names)}")

    def _warn_degradation(self, provider_name: str, reason: str) -> None:
        if provider_name not in self._provider_degraded_warned:
            self._provider_degraded_warned.add(provider_name)
            if self.notifier:
                self.notifier.notify_error(
                    title=f"Jev 供應商切換備援 ({provider_name})",
                    error_msg=f"供應商 {provider_name} 額度超限或不可用：{reason}。\n系統已自動無縫切換至備援決策鏈，巡檢持續進行中。",
                    domain="llm_quota",
                )

    def _post_decision(self, payload: dict) -> Optional[dict]:
        """Try providers in priority order with instant failover on 429/quota errors."""
        for p in self.providers:
            if p.disabled:
                continue

            headers = {
                "Authorization": f"Bearer {p.api_key}",
                "Content-Type": "application/json",
            }
            p_payload = dict(payload)
            p_payload["model"] = p.model

            # OpenRouter score questions criteria must be an array (list)
            if "openrouter.ai" in p.api_url:
                q_copy = {}
                for qk, qv in p_payload.get("questions", {}).items():
                    qc = dict(qv)
                    if qc.get("type") == "score" and isinstance(qc.get("criteria"), dict):
                        # Convert dict criteria {"0": "...", "1": "..."} to list
                        sorted_items = sorted(qc["criteria"].items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else kv[0])
                        qc["criteria"] = [val for _, val in sorted_items]
                    q_copy[qk] = qc
                p_payload["questions"] = q_copy

            try:
                for attempt in range(2):
                    try:
                        resp = self.session.post(p.api_url, json=p_payload, headers=headers, timeout=25)
                        if resp.status_code == 200:
                            return resp.json()

                        err_text = resp.text
                        is_quota_err = (
                            resp.status_code in (401, 402, 403, 429)
                            or "FreeUsageLimitError" in err_text
                            or "insufficient" in err_text.lower()
                            or "credit" in err_text.lower()
                        )
                        if is_quota_err:
                            p.disabled = True
                            p.disabled_reason = f"HTTP {resp.status_code}: {err_text[:120]}"
                            self.has_degraded = True
                            logger.warning(
                                f"[Jev] 供應商 {p.name} 額度超限或不可用 ({p.disabled_reason})，自動封鎖並切換至下一備援供應商。"
                            )
                            self._warn_degradation(p.name, p.disabled_reason)
                            break  # Break inner retry, try next provider immediately

                        logger.warning(f"[Jev] 供應商 {p.name} HTTP {resp.status_code}: {err_text[:120]}")
                        if attempt == 0 and resp.status_code in (500, 502, 503, 504):
                            time.sleep(1.5)
                            continue
                        break
                    except (requests.exceptions.RequestException, requests.exceptions.Timeout) as ex:
                        if attempt == 0:
                            time.sleep(1.5)
                            continue
                        logger.warning(f"[Jev] 供應商 {p.name} 連線超時: {ex}")
                        break
            except Exception as e:
                logger.warning(f"[Jev] 供應商 {p.name} 呼叫異常: {e}")
                continue

        # If all Jev providers fail, attempt DeepSeek structured JSON decision fallback
        if self.deepseek_key:
            if not getattr(self, "_deepseek_fallback_warned", False):
                self._deepseek_fallback_warned = True
                self.has_degraded = True
                if self.notifier:
                    self.notifier.notify_error(
                        title="Jev 主決策鏈不可用 (已切換至 DeepSeek 備援)",
                        error_msg="主決策供應商額度超限或未配置，系統已自動降級至 DeepSeek 結構化決策備援。",
                        domain="llm_quota",
                    )
            try:
                ds_res = self._call_deepseek_decision(payload)
                if ds_res:
                    self.has_degraded = True
                    return ds_res
            except Exception as ex:
                self.has_degraded = True
                logger.warning(f"[Jev] DeepSeek 結構化決策備援調用失敗: {ex}")
                if self.notifier and not getattr(self, "_deepseek_call_failed_warned", False):
                    self._deepseek_call_failed_warned = True
                    self.notifier.notify_error(
                        title="Jev DeepSeek 決策備援調用失敗",
                        error_msg=f"DeepSeek 調用或解析失敗: {ex}\n系統即將嘗試退回本地啟發式規則備援。",
                        domain="jev_deepseek_err",
                    )

        return None

    def _call_deepseek_decision(self, payload: dict) -> Optional[dict]:
        if not self.deepseek_key:
            return None

        state_str = payload.get("state", "")
        questions = payload.get("questions", {})
        if not questions:
            return None

        q_descriptions = []
        for q_id, q_info in questions.items():
            q_type = q_info.get("type", "choice")
            instructions = q_info.get("instructions", "")
            criteria = q_info.get("criteria", {})
            crit_str = json.dumps(criteria, ensure_ascii=False) if isinstance(criteria, (dict, list)) else str(criteria)
            q_descriptions.append(f"- Question ID: '{q_id}' (Type: {q_type})\n  Instructions: {instructions}\n  Criteria: {crit_str}")

        joined_q = "\n".join(q_descriptions)
        prompt = f"""You are a recruitment decision engine acting as Jev System One.
Given the target position facts and candidate profile below, evaluate each question strictly according to the criteria.

[CONTEXT & TARGET]
{state_str[:6000]}

[QUESTIONS TO EVALUATE]
{joined_q}

Return ONLY a valid JSON object matching this schema:
{{
  "answers": {{
    "<question_id>": {{
      "choice": "<best choice string matching criteria>",
      "score": <numeric score level>,
      "confidence": <float 0.0 to 1.0>,
      "probabilities": {{
        "<choice or level_idx>": <probability 0.0 to 1.0>
      }}
    }}
  }}
}}
"""
        headers = {
            "Authorization": f"Bearer {self.deepseek_key}",
            "Content-Type": "application/json",
        }
        req_body = {
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            "temperature": 0.0,
        }
        resp = self.session.post(f"{self.deepseek_url}/chat/completions", json=req_body, headers=headers, timeout=20)
        if resp.status_code == 200:
            content = resp.json()["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.MULTILINE)
                content = re.sub(r"```\s*$", "", content, flags=re.MULTILINE).strip()
            try:
                parsed = json.loads(content)
            except Exception:
                # Fallback: extract JSON substring between outer braces
                m = re.search(r"(\{.*\})", content, re.DOTALL)
                if m:
                    parsed = json.loads(m.group(1))
                else:
                    raise
            if "answers" in parsed:
                return parsed
        return None

    def build_state(self, job: Job, profile_text: str = "") -> str:
        parts = [
            f"【職缺標題】{job.title}",
            f"【公司名稱】{job.company}",
            f"【薪資待遇】{job.salary or '面議'}",
            f"【工作地點】{job.location or '未填'}",
            f"【標籤類別】{', '.join(job.tags) if job.tags else '無'}",
        ]
        if job.description:
            parts.append(f"【工作內容 (JD)】\n{job.description.strip()}")
        if job.requirements:
            parts.append(f"【職缺要求/條件】\n{job.requirements.strip()}")
        if profile_text:
            parts.append(f"【求職者背景/履歷 (Profile)】\n{profile_text.strip()}")

        return "\n\n".join(parts)

    def _heuristic_profile_fit(self, job: Job, profile_text: str) -> Tuple[str, float]:
        """Deterministic heuristic for candidate fit when Jev API is unavailable or dry run."""
        title_lower = (job.title or "").lower()
        job_text = f"{job.title} {job.description} {job.requirements}".lower()

        # 1. Non-IT / Unrelated domains
        unrelated_kws = [
            "餐廳", "襄理", "門市", "店長", "廚師", "牛舌", "餐飲", "接待", "櫃檯", "總務", "工友",
            "出納", "業務推廣", "電話行銷", "保險經紀", "模特", "演員", "剪輯", "拍攝", "社群小編"
        ]
        if any(k in title_lower for k in unrelated_kws):
            return "unrelated", 0.95

        # 2. Pure hardware / physical electronics / circuits
        hardware_kws = [
            "電路", "基頻", "射頻", "pcb", "layout", "光通訊", "晶圓", "ic設計", "半導體製程",
            "硬體測試", "治具", "通訊模組", "零件測試"
        ]
        if any(k in title_lower for k in hardware_kws) and not any(k in title_lower for k in ["軟體", "數據", "資料", "software", "data"]):
            return "unrelated", 0.95

        # 3. Pure HR / Public Relations without tech
        hr_pr_kws = ["人資", "招募", "talent acquisition", "媒體公關", "公關", "hr intern"]
        if any(k in title_lower for k in hr_pr_kws) and not any(k in title_lower for k in ["系統", "軟體", "數據", "data"]):
            return "unrelated", 0.9

        # 4. Pure IT Maintenance / PC repair
        if any(k in title_lower for k in ["資訊維運", "電腦維護", "it支援", "網管", "helpdesk", "pc維修", "裝機"]):
            return "low", 0.85

        # 5. Strong Software / Data / Analytics / MIS / FinTech matches in title
        high_kws = [
            "軟體", "數據", "資料", "演算法", "前端", "後端", "全端", "ai", "machine learning",
            "python", "sql", "金融科技", "fintech", "數據分析", "商業分析", "系統分析",
            "software", "data analyst", "data engineer", "backend", "frontend", "developer",
            "資訊工程", "資訊管理", "mis", "系統工程", "tech intern"
        ]
        if any(k in title_lower for k in high_kws):
            return "high", 0.9

        # 6. Check full text for strong software/data/tech presence
        has_deep_tech = any(k in job_text for k in [
            "python", "sql", "api", "git", "機器學習", "etl", "資料庫", "演算法",
            "軟體開發", "金融科技", "tech", "資訊系統", "軟體工程", "數據分析", "系統架構"
        ])
        if has_deep_tech:
            return "medium", 0.8

        return "low", 0.7

    def evaluate_profile_fit(
        self,
        job: Job,
        profile_text: str,
        dry_run: bool = False,
    ) -> Tuple[str, float, dict]:
        """
        Evaluate candidate profile alignment against job description as an independent gatekeeper.
        Returns: (fit_level, confidence, probabilities) where fit_level in ['high', 'medium', 'low', 'unrelated']
        """
        if not profile_text:
            return "high", 1.0, {}

        if dry_run or not (self.providers or self.deepseek_key):
            fit, conf = self._heuristic_profile_fit(job, profile_text)
            return fit, conf, {fit: 1.0}

        state_parts = [
            f"# JOB EVALUATION TARGET\n- Title: {job.title}\n- Company: {job.company}",
            f"- Location: {job.location}\n- Category/Tags: {', '.join(job.tags) if job.tags else 'N/A'}",
        ]
        if job.description:
            state_parts.append(f"## JOB DESCRIPTION\n{job.description[:4000]}")
        if job.requirements:
            state_parts.append(f"## JOB REQUIREMENTS\n{job.requirements[:3000]}")
        state_parts.append(f"## CANDIDATE BACKGROUND (PROFILE)\n{profile_text}")
        state_str = "\n\n".join(state_parts)

        payload = {
            "model": "typesafe/jev-1.14",
            "state": state_str,
            "questions": {
                "candidate_fit": {
                    "type": "choice",
                    "instructions": "Evaluate the professional alignment between the job requirements/tasks and the applicant's profile. Note: Generic talent pools, open resume intake, or posts lacking specific defined day-to-day job responsibilities must be rated unrelated.",
                    "criteria": {
                        "unrelated": "Completely unrelated to applicant's field of study and skills, OR a generic talent pool/recruitment drive/agency registration lacking specific identifiable job duties and technical requirements.",
                        "low": "Minimal connection, purely administrative or only superficial mention of technology/business without substantive overlap.",
                        "medium": "Transferable domain skills, partial alignment with applicant's technical or business analysis background.",
                        "high": "Direct and strong alignment with applicant's core skills, background, and career direction.",
                    },
                }
            },
        }

        data = self._post_decision(payload)
        if data and "answers" in data:
            ans = data.get("answers", {}).get("candidate_fit", {})
            probs = ans.get("probabilities", {})
            confidence = float(ans.get("confidence", 1.0))
            if probs:
                best_choice = max(probs.items(), key=lambda kv: float(kv[1]))[0]
                return best_choice.lower(), confidence, probs
            choice = ans.get("choice") or ans.get("answer")
            if choice:
                return str(choice).lower(), confidence, probs

        # Fallback to local heuristic if all remote APIs failed
        self.has_degraded = True
        logger.info(f"  ↳ [Jev 遠端決策全數不可用] 啟動本地啟發式決策備援: {job.title}")
        if self.notifier and not getattr(self, "_heuristic_fallback_warned", False):
            self._heuristic_fallback_warned = True
            self.notifier.notify_error(
                title="Jev 遠端決策全數不可用 (降級為本地啟發式決策)",
                error_msg=f"遠端模型 (Jev / DeepSeek) 全數不可用或解析失敗。\n已啟動本地關鍵字啟發式規則備援進行職缺適配度把關。",
                domain="jev_heuristic_fallback",
            )
        fit, conf = self._heuristic_profile_fit(job, profile_text)
        return fit, conf, {fit: 1.0}

    def evaluate_job(
        self,
        job: Job,
        config: RubricConfig,
        state: str = "",
        dry_run: bool = False,
        company_profile: Optional[Any] = None,
        profile_fit: Optional[str] = None,
    ) -> ScoredJob:
        if not state:
            state = self.build_state(job)

        # 1. Deterministic Corporate Authority evaluation from company tier
        auth_dim = get_corporate_authority_dimension(config)
        auth_dim_score: Optional[DimensionScore] = None
        other_dims = [d for d in config.dimensions if auth_dim is None or d.id != auth_dim.id]

        if auth_dim:
            tier_str = "Tier 3"
            if company_profile and hasattr(company_profile, "tier"):
                tier_str = company_profile.tier
            else:
                state_lower = state.lower()
                if "tier classification: tier 1" in state_lower or "tier 1" in job.company.lower():
                    tier_str = "Tier 1"
                elif "tier classification: tier 2" in state_lower or "tier 2" in job.company.lower():
                    tier_str = "Tier 2"
                elif "tier classification: tier 4" in state_lower or "tier 4" in job.company.lower():
                    tier_str = "Tier 4"
                elif "tier classification: tier 3" in state_lower or "tier 3" in job.company.lower():
                    tier_str = "Tier 3"

            tier_points = get_tier_points(config)
            score = tier_points.get(tier_str, 15.0)
            levels = auth_dim.level_points
            matched_idx = levels.index(score) if score in levels else 0

            auth_dim_score = DimensionScore(
                name=auth_dim.name,
                max_points=auth_dim.max_points,
                score=score,
                confidence=1.0,
                probabilities={matched_idx: 1.0},
                level_points=levels,
            )

        # 2. Evaluate remaining dimensions via decision providers
        evaluated_other_scores: Dict[str, DimensionScore] = {}

        if dry_run:
            for dim in other_dims:
                evaluated_other_scores[dim.id] = DimensionScore(
                    name=dim.name,
                    max_points=dim.max_points,
                    score=0.0,
                    confidence=1.0,
                    probabilities={0: 1.0},
                    level_points=dim.level_points,
                )
        elif not (self.providers or self.deepseek_key):
            # No remote API available, use baseline conservative score
            for dim in other_dims:
                mid_idx = len(dim.level_points) // 2
                score = dim.level_points[mid_idx] if dim.level_points else 0.0
                evaluated_other_scores[dim.id] = DimensionScore(
                    name=dim.name,
                    max_points=dim.max_points,
                    score=score,
                    confidence=0.5,
                    probabilities={mid_idx: 1.0},
                    level_points=dim.level_points,
                )
        elif not other_dims:
            evaluated_other_scores = {}
        else:
            questions: Dict[str, Any] = {}
            for dim in other_dims:
                dim_prompt = dim.name_en or dim.name
                questions[dim.id] = {
                    "type": "score",
                    "instructions": f"Rate the position on '{dim_prompt}' according to the provided level criteria.",
                    "criteria": dim.criteria,
                }

            payload = {
                "model": "typesafe/jev-1.14",
                "state": state,
                "questions": questions,
            }

            data = self._post_decision(payload)
            if data and "answers" in data:
                answers = data.get("answers", {})
                for dim in other_dims:
                    ans = answers.get(dim.id, {})
                    probs_dict = ans.get("probabilities", {})
                    confidence = float(ans.get("confidence", 1.0))
                    parsed_probs: Dict[int, float] = {}

                    best_idx = 0
                    best_prob = -1.0
                    for idx, pts in enumerate(dim.level_points):
                        prob = float(probs_dict.get(str(idx), 0.0))
                        parsed_probs[idx] = prob
                        if prob > best_prob:
                            best_prob = prob
                            best_idx = idx

                    discrete_score = dim.level_points[best_idx] if dim.level_points else 0.0

                    evaluated_other_scores[dim.id] = DimensionScore(
                        name=dim.name,
                        max_points=dim.max_points,
                        score=discrete_score,
                        confidence=confidence,
                        probabilities=parsed_probs,
                        level_points=dim.level_points,
                    )
            else:
                # Local conservative heuristic scoring if all remote APIs failed
                self.has_degraded = True
                logger.info(f"  ↳ [Jev 評分遠端全數不可用] 使用安全基準分數留存待覆核: {job.title}")
                if self.notifier and not getattr(self, "_heuristic_scoring_warned", False):
                    self._heuristic_scoring_warned = True
                    self.notifier.notify_error(
                        title="Jev 評分遠端全數不可用 (使用安全基準分留存)",
                        error_msg="遠端評分模型不可用，系統已使用安全基準分數留存待覆核。",
                        domain="jev_heuristic_score",
                    )
                for dim in other_dims:
                    mid_idx = len(dim.level_points) // 2
                    score = dim.level_points[mid_idx] if dim.level_points else 0.0
                    evaluated_other_scores[dim.id] = DimensionScore(
                        name=dim.name,
                        max_points=dim.max_points,
                        score=score,
                        confidence=0.5,
                        probabilities={mid_idx: 1.0},
                        level_points=dim.level_points,
                    )

        # 3. Assemble all dimensions in rubric definition order
        dim_scores: List[DimensionScore] = []
        total_score = 0.0
        for dim in config.dimensions:
            if auth_dim and dim.id == auth_dim.id and auth_dim_score:
                dim_scores.append(auth_dim_score)
                total_score += auth_dim_score.score
            elif dim.id in evaluated_other_scores:
                ds = evaluated_other_scores[dim.id]
                dim_scores.append(ds)
                total_score += ds.score

        return ScoredJob(
            job=job,
            total_score=total_score,
            is_qualified=(total_score >= config.notify_threshold),
            dimension_scores=dim_scores,
            profile_fit=profile_fit,
        )
