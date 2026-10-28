#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""JobMe Data Flow Black-box Auditor (資料流黑箱稽核工具)

Audits job data transformations across 7 lifecycle stages and terminal exports (CSV / SQLite DB).
Follows specs in docs/data_flow_audit_standard.md.
"""

from __future__ import annotations
import argparse
import csv
from dataclasses import dataclass, field
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

VALID_PLATFORMS = {"104", "yourator", "cake", "1111", "yes123"}
VALID_FIT_LEVELS = {"high", "medium", "low", "unrelated"}
VALID_TIERS = {"Tier 1", "Tier 2", "Tier 3", "Tier 4"}

# ANSI color codes
CLR_RESET = "\033[0m"
CLR_RED = "\033[91m"
CLR_GREEN = "\033[92m"
CLR_YELLOW = "\033[93m"
CLR_BLUE = "\033[94m"
CLR_CYAN = "\033[96m"
CLR_BOLD = "\033[1m"


@dataclass
class Defect:
    stage: str
    severity: str  # BLOCKER, MAJOR, MINOR
    job_ref: str
    message: str


@dataclass
class AuditSummary:
    total_samples: int = 0
    stages_checked: List[str] = field(default_factory=list)
    defects: List[Defect] = field(default_factory=list)
    target_name: str = ""

    @property
    def blocker_count(self) -> int:
        return sum(1 for d in self.defects if d.severity == "BLOCKER")

    @property
    def major_count(self) -> int:
        return sum(1 for d in self.defects if d.severity == "MAJOR")

    @property
    def minor_count(self) -> int:
        return sum(1 for d in self.defects if d.severity == "MINOR")

    @property
    def quality_score(self) -> float:
        # ponytail: standard deduction formula (100 - 5*Major - 1*Minor), zero if any blocker
        raw_score = 100.0 - (self.major_count * 5.0) - (self.minor_count * 1.0)
        final_score = max(0.0, raw_score)
        return 0.0 if self.blocker_count > 0 else round(final_score, 1)

    @property
    def passed(self) -> bool:
        return self.blocker_count == 0 and self.quality_score >= 95.0


class DataFlowAuditor:
    def __init__(self, notify_threshold: float = 75.0, exclude_keywords: Optional[List[str]] = None):
        self.notify_threshold = notify_threshold
        self.exclude_keywords = exclude_keywords or [
            "電話行銷", "業務推廣", "保險經紀", "門市計時", "餐飲", "餐廳", "美髮", "沙龍",
            "剪髮", "理髮", "清潔", "風管", "機械加工", "廚房", "外場", "內場", "洗碗"
        ]

    def audit_pipeline_json(self, file_path: str) -> AuditSummary:
        summary = AuditSummary(target_name=file_path)
        summary.stages_checked = [
            "Stage 1 (Raw Card)",
            "Stage 2 (Hard Filter)",
            "Stage 3 (Detail Cleaned)",
            "Stage 4 (Profile Fit)",
            "Stage 5 (Company Tier)",
            "Stage 6 (Fact Prompt)",
            "Stage 7 (Rubric Score)",
        ]

        if not os.path.exists(file_path):
            summary.defects.append(
                Defect("Input File", "BLOCKER", file_path, f"檔案不存在: {file_path}")
            )
            return summary

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            summary.defects.append(
                Defect("Input File", "BLOCKER", file_path, f"JSON 解析失敗: {e}")
            )
            return summary

        if not isinstance(data, list):
            summary.defects.append(
                Defect("Input File", "BLOCKER", file_path, "JSON 頂層必須為職缺清單 Array")
            )
            return summary

        summary.total_samples = len(data)

        for idx, item in enumerate(data, 1):
            ref = f"#{idx} [{item.get('platform', '?')}:{item.get('job_id', '?')}] {item.get('title', '無職稱')[:20]}"
            self._audit_job_pipeline_item(item, ref, summary)

        return summary

    def _audit_job_pipeline_item(self, item: Dict[str, Any], ref: str, summary: AuditSummary) -> None:
        # --- Stage 1: Raw List Card ---
        s1 = item.get("step1") or {}
        if not s1:
            summary.defects.append(Defect("Stage 1 (Raw Card)", "BLOCKER", ref, "缺少 step1 原始卡片資料"))
        else:
            for req_field in ["platform", "job_id", "title", "company", "url"]:
                if not s1.get(req_field):
                    summary.defects.append(Defect("Stage 1 (Raw Card)", "BLOCKER", ref, f"缺少必要欄位: {req_field}"))
            url = str(s1.get("url") or "")
            if not url.startswith(("http://", "https://")):
                summary.defects.append(Defect("Stage 1 (Raw Card)", "BLOCKER", ref, f"URL 格式非 HTTP/HTTPS: {url}"))
            plat = str(s1.get("platform") or "").lower()
            if plat not in VALID_PLATFORMS:
                summary.defects.append(Defect("Stage 1 (Raw Card)", "MAJOR", ref, f"非規範支援之平台: {plat}"))
            if s1.get("salary") is None:
                summary.defects.append(Defect("Stage 1 (Raw Card)", "MINOR", ref, "salary 欄位為 None (應保留原始字串或空字串)"))

        # --- Stage 2: Hard Filter ---
        s2 = item.get("step2") or {}
        if not s2:
            summary.defects.append(Defect("Stage 2 (Hard Filter)", "BLOCKER", ref, "缺少 step2 硬性規則資料"))
            passed_filter = False
        else:
            passed_filter = s2.get("passed", True)
            if passed_filter is False:
                reason = s2.get("reject_reason")
                if not reason:
                    summary.defects.append(Defect("Stage 2 (Hard Filter)", "BLOCKER", ref, "過濾淘汰但缺少 reject_reason"))

        # --- Stage 1 & 2: Heterogeneous Industry Intrusion Audit ---
        title = str(s1.get("title") or "")
        tags = s1.get("tags") or []
        matched_blacklist = [kw for kw in self.exclude_keywords if kw in title or any(kw in str(t) for t in tags)]
        if matched_blacklist:
            kw_str = ", ".join(matched_blacklist)
            if passed_filter:
                summary.defects.append(Defect("Stage 1 (Industry Intrusion)", "BLOCKER", ref, f"非目標異質產業 (命中黑名單: {kw_str}) 滲透且未被硬篩攔截"))
            else:
                summary.defects.append(Defect("Stage 1 (Crawler Ingestion)", "MAJOR", ref, f"爬蟲端分類參數不精確，導致異質產業職缺進入候選名單: {kw_str}"))

        # If eliminated by Hard Filter in Stage 2, verify complete downstream fusing
        s7 = item.get("step7") or {}
        if not passed_filter:
            if s7.get("fused") is not True or s7.get("total_score", 0.0) != 0.0 or s7.get("is_qualified") is True:
                summary.defects.append(Defect("Stage 2 (Hard Filter)", "BLOCKER", ref, "硬篩淘汰職缺未被熔斷攔截，仍進入評分或標註合格"))
            return

        # --- Stage 3: Detail Cleaned ---
        s3 = item.get("step3") or {}
        if s3:
            char_count = s3.get("char_count", 0)
            if char_count < 50:
                summary.defects.append(Defect("Stage 3 (Detail Cleaned)", "BLOCKER", ref, f"內文長度過短 (< 50 字元): {char_count}"))
            snippet = s3.get("snippet", "")
            # Check for raw HTML tags
            if re.search(r"<(div|p|br|span|script|style|table|tr|td)[\s>/]", snippet, re.IGNORECASE):
                summary.defects.append(Defect("Stage 3 (Detail Cleaned)", "BLOCKER", ref, "JD 清洗內文殘留 HTML 標籤語法"))
            if "&nbsp;" in snippet or "\u00a0" in snippet:
                summary.defects.append(Defect("Stage 3 (Detail Cleaned)", "MINOR", ref, "JD 清洗內文殘留 &nbsp; 或 nbsp 空格實體"))

        # --- Stage 4: Profile Fit ---
        s4 = item.get("step4") or {}
        fit_level = str(s4.get("fit_level") or "").lower() if s4 else ""
        if not s4:
            summary.defects.append(Defect("Stage 4 (Profile Fit)", "BLOCKER", ref, "缺少 step4 履歷適配度資料"))
            return

        if fit_level not in VALID_FIT_LEVELS:
            summary.defects.append(Defect("Stage 4 (Profile Fit)", "BLOCKER", ref, f"fit_level 非規範枚舉: {fit_level}"))

        probs = s4.get("probabilities")
        if isinstance(probs, dict):
            prob_sum = sum(probs.values())
            if abs(prob_sum - 1.0) > 0.05:
                summary.defects.append(Defect("Stage 4 (Profile Fit)", "MAJOR", ref, f"機率分佈總和不為 1.0 (當前: {prob_sum:.3f})"))
        conf = s4.get("confidence")
        if conf is not None and not (0.0 <= float(conf) <= 1.0):
            summary.defects.append(Defect("Stage 4 (Profile Fit)", "MINOR", ref, f"信心度超出 [0, 1] 區間: {conf}"))

        # Determine if job is gated / intercepted at Stage 4
        is_intercepted = fit_level in ("low", "unrelated")
        is_fused = s7.get("fused", False)

        if is_intercepted:
            if not is_fused or s7.get("total_score", 0.0) != 0.0 or s7.get("is_qualified") is True:
                summary.defects.append(Defect("Stage 4 (Profile Fit)", "BLOCKER", ref, f"fit_level 為 {fit_level} 但未在 Stage 4 執行熔斷攔截"))
            return

        # From here, job is passed (fit_level in HIGH/MEDIUM)
        if is_fused:
            summary.defects.append(Defect("Stage 4 (Profile Fit)", "BLOCKER", ref, f"fit_level 為 {fit_level} 卻被非預期熔斷"))

        # --- Stage 5: Company Tier (Only for passed jobs) ---
        s5 = item.get("step5") or {}
        if not s5:
            summary.defects.append(Defect("Stage 5 (Company Tier)", "BLOCKER", ref, "放行職缺缺少 step5 企業徵信資料"))
        else:
            tier = s5.get("tier")
            if tier not in VALID_TIERS:
                summary.defects.append(Defect("Stage 5 (Company Tier)", "BLOCKER", ref, f"企業 Tier 等級非有效值: {tier}"))
            basis = s5.get("basis")
            if not basis:
                summary.defects.append(Defect("Stage 5 (Company Tier)", "MAJOR", ref, "缺少分級判定依據 (basis 為空)"))
            if s5.get("is_twse_listed") is True and tier in ("Tier 3", "Tier 4"):
                summary.defects.append(Defect("Stage 5 (Company Tier)", "MAJOR", ref, f"上市公司卻被錨定為低階層: {tier}"))

        # --- Stage 6: Fact Prompt (Only for passed jobs) ---
        s6 = item.get("step6") or {}
        if not s6:
            summary.defects.append(Defect("Stage 6 (Fact Prompt)", "BLOCKER", ref, "放行職缺缺少 step6 Prompt 資料"))
        else:
            state_len = s6.get("state_len", 0)
            if state_len < 1000:
                summary.defects.append(Defect("Stage 6 (Fact Prompt)", "MAJOR", ref, f"事實 Prompt 長度偏低 (< 1000 字元): {state_len}"))
            snippet = s6.get("state_snippet", "")
            if "JOB POSITION FACT SHEET" not in snippet and "1. COMPANY REPUTATION" not in snippet:
                summary.defects.append(Defect("Stage 6 (Fact Prompt)", "BLOCKER", ref, "Prompt 缺少核心標頭或 COMPANY REPUTATION 區塊"))

        # --- Stage 7: Rubric Scoring ---
        if not s7:
            summary.defects.append(Defect("Stage 7 (Rubric Score)", "BLOCKER", ref, "放行職缺缺少 step7 評分資料"))
        else:
            dim_scores = s7.get("dimension_scores", [])
            total_score = s7.get("total_score", 0.0)
            calc_sum = sum(ds.get("score", 0.0) for ds in dim_scores)
            if abs(calc_sum - total_score) > 0.1:
                summary.defects.append(Defect("Stage 7 (Rubric Score)", "BLOCKER", ref, f"維度加總與總分不符: {calc_sum:.1f} != {total_score:.1f}"))

            # Check individual dimensions
            for ds in dim_scores:
                name = ds.get("name", "")
                score = ds.get("score", 0.0)
                max_pts = ds.get("max_points", 0.0)
                if score < 0 or score > max_pts:
                    summary.defects.append(Defect("Stage 7 (Rubric Score)", "BLOCKER", ref, f"維度 [{name}] 得分超出 [0, {max_pts}]: {score}"))

            is_qual = s7.get("is_qualified")
            expected_qual = total_score >= self.notify_threshold
            if is_qual != expected_qual:
                summary.defects.append(Defect("Stage 7 (Rubric Score)", "BLOCKER", ref, f"is_qualified ({is_qual}) 與門檻判斷不一致 (total={total_score}, threshold={self.notify_threshold})"))

            # Low confidence sync check
            has_low_conf = s7.get("has_low_confidence", False)
            any_low = any(ds.get("confidence", 1.0) < 0.4 for ds in dim_scores)
            if has_low_conf != any_low:
                summary.defects.append(Defect("Stage 7 (Rubric Score)", "MAJOR", ref, f"低信心標註 (has_low_confidence={has_low_conf}) 與維度信心度未同步"))

    def audit_csv(self, file_path: str) -> AuditSummary:
        summary = AuditSummary(target_name=file_path)
        summary.stages_checked = ["CSV Headers", "CSV Row Alignment", "Score Formula Check"]

        if not os.path.exists(file_path):
            summary.defects.append(Defect("CSV File", "BLOCKER", file_path, f"CSV 檔案不存在: {file_path}"))
            return summary

        try:
            with open(file_path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.reader(f)
                rows = list(reader)
        except UnicodeDecodeError:
            summary.defects.append(Defect("CSV File", "BLOCKER", file_path, "編碼錯誤: 無法以 utf-8-sig 正常解碼"))
            return summary

        if not rows:
            summary.defects.append(Defect("CSV File", "MAJOR", file_path, "CSV 內容為空 (0 行)"))
            return summary

        headers = rows[0]
        header_len = len(headers)
        summary.total_samples = len(rows) - 1

        is_records = "合計總分" in headers

        for line_num, row in enumerate(rows[1:], 2):
            ref = f"Line {line_num}"
            if len(row) != header_len:
                summary.defects.append(Defect("CSV Row Alignment", "BLOCKER", ref, f"欄位數量與表頭不符: 期望 {header_len} 欄，實際 {len(row)} 欄 (破格/未逸出逗號)"))

            if is_records and len(row) == header_len:
                # Validate score formula in records.csv
                try:
                    total_score = float(row[-1])
                    dim_1 = float(row[-4])
                    dim_2 = float(row[-3])
                    dim_3 = float(row[-2])
                    if abs((dim_1 + dim_2 + dim_3) - total_score) > 0.1:
                        summary.defects.append(Defect("CSV Score Math", "BLOCKER", ref, f"合計總分與子維度不符: {dim_1}+{dim_2}+{dim_3} != {total_score}"))
                except ValueError:
                    summary.defects.append(Defect("CSV Score Math", "MAJOR", ref, "分數欄位包含無法轉為浮點數之非數值"))

        return summary

    def audit_sqlite_db(self, db_path: str) -> AuditSummary:
        summary = AuditSummary(target_name=db_path)
        summary.stages_checked = ["DB Schema", "Primary Key Integrity", "JSON Serialization"]

        if not os.path.exists(db_path):
            summary.defects.append(Defect("SQLite DB", "BLOCKER", db_path, f"DB 檔案不存在: {db_path}"))
            return summary

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        try:
            # Check table existence
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row["name"] for row in cur.fetchall()}
            for req_tbl in ["jobs", "evaluations"]:
                if req_tbl not in tables:
                    summary.defects.append(Defect("DB Schema", "BLOCKER", db_path, f"資料表不存在: {req_tbl}"))

            if "jobs" in tables:
                cur.execute("SELECT count(*) as c FROM jobs WHERE platform IS NULL OR job_id IS NULL")
                null_pks = cur.fetchone()["c"]
                if null_pks > 0:
                    summary.defects.append(Defect("Primary Key Integrity", "BLOCKER", "jobs", f"jobs 資料表有 {null_pks} 筆 Primary Key 為 NULL"))

                cur.execute("SELECT count(*) as total FROM jobs")
                summary.total_samples += cur.fetchone()["total"]

            if "evaluations" in tables:
                cur.execute("SELECT platform, job_id, total_score, details_json FROM evaluations LIMIT 200")
                for row in cur.fetchall():
                    ref = f"eval:{row['platform']}:{row['job_id']}"
                    try:
                        details = json.loads(row["details_json"])
                        if not isinstance(details, (dict, list)):
                            summary.defects.append(Defect("JSON Serialization", "MAJOR", ref, "details_json 解析後非 JSON Array 或 Object"))
                    except Exception as e:
                        summary.defects.append(Defect("JSON Serialization", "BLOCKER", ref, f"details_json 毀損無法以 json.loads 解析: {e}"))
        finally:
            conn.close()

        return summary

    def audit_taxonomy_translation(self, config_path: str = "config/rubric.md") -> AuditSummary:
        summary = AuditSummary(target_name=f"Taxonomy Translation ({config_path})")
        summary.stages_checked = [
            "Taxonomy Dynamic Mapping",
            "104 Jobcat",
            "1111 D0",
            "Yes123 WorkMode",
            "Keywords Derivation",
        ]
        summary.total_samples = 5

        if not os.path.exists(config_path):
            summary.defects.append(Defect("Taxonomy Translation", "BLOCKER", config_path, f"設定檔不存在: {config_path}"))
            return summary

        try:
            from jobme.rubric import parse_rubric_file
            from jobme.category_mapper import (
                get_configured_104_jobcats,
                get_1111_categories,
                get_yes123_work_modes,
                get_category_keywords,
            )
            cfg = parse_rubric_file(config_path)
        except Exception as e:
            summary.defects.append(Defect("Taxonomy Translation", "BLOCKER", config_path, f"無法載入或解析 rubric: {e}"))
            return summary

        # 1. Check 104
        cats_104 = get_configured_104_jobcats(cfg)
        if not cats_104:
            summary.defects.append(Defect("104 Jobcat", "BLOCKER", "104", "104 jobcat 未配置且無法取得分類代碼"))

        # 2. Check 1111
        cats_1111 = get_1111_categories(cfg)
        if not cats_1111:
            summary.defects.append(Defect("1111 D0", "BLOCKER", "1111", "1111 分類代碼為空，導致全量無篩選抓取"))

        # 3. Check Yes123
        modes_yes123 = get_yes123_work_modes(cfg)
        if not modes_yes123:
            summary.defects.append(Defect("Yes123 WorkMode", "BLOCKER", "yes123", "yes123 工作模式代碼為空，導致全量無篩選抓取"))

        # 4. Dynamic category derivation test
        # Verify custom categories not in static tables are dynamically derived into keywords
        from dataclasses import make_dataclass
        DummyConfig = make_dataclass("DummyConfig", [("job_categories", list), ("platforms", dict), ("keywords", list)])
        dummy = DummyConfig(job_categories=["生技醫療", "UIUX設計"], platforms={}, keywords=[])
        derived_kw = get_category_keywords(dummy)
        if "生技醫療" not in derived_kw or "UIUX設計" not in derived_kw:
            summary.defects.append(Defect("Taxonomy Dynamic Mapping", "MAJOR", "CustomCategory", "自訂新類別未能動態衍生為搜尋關鍵字，存在硬編碼閉塞"))

        return summary

    def audit_negative_injections(self) -> AuditSummary:
        summary = AuditSummary(target_name="Synthetic Negative Injections (負向邊界測試)")
        summary.stages_checked = [
            "NEG-01 Empty JD",
            "NEG-02 Truncated JD",
            "NEG-03 HTML Tags",
            "NEG-04 Math Mismatch",
            "NEG-05 Industry Leak",
            "NEG-06 Invalid Enum",
        ]

        test_cases = [
            {
                "id": "NEG-01",
                "name": "空值 JD 注入",
                "payload": {
                    "platform": "104", "job_id": "neg_01", "title": "軟體工程實習生", "company": "測試科技",
                    "step1": {"platform": "104", "job_id": "neg_01", "title": "軟體工程實習生", "company": "測試科技", "url": "https://example.com/1"},
                    "step2": {"passed": True, "reject_reason": "", "location": "台北市"},
                    "step3": {"char_count": 0, "snippet": ""},
                    "step4": {"fit_level": "medium", "confidence": 0.8, "probabilities": {"high": 0.2, "medium": 0.6, "low": 0.2, "unrelated": 0.0}},
                    "step5": {"tier": "Tier 3", "basis": "標準商業實體"},
                    "step6": {"state_len": 5000, "state_snippet": "# JOB POSITION FACT SHEET\n## 1. COMPANY REPUTATION"},
                    "step7": {"fused": False, "total_score": 70.0, "is_qualified": False, "has_low_confidence": False, "dimension_scores": [{"name": "A", "score": 70.0, "max_points": 100.0, "confidence": 0.8}]}
                },
                "expected_stage": "Stage 3 (Detail Cleaned)",
                "expected_severity": "BLOCKER",
            },
            {
                "id": "NEG-02",
                "name": "殘缺截斷 JD 注入 (< 50 字元)",
                "payload": {
                    "platform": "cake", "job_id": "neg_02", "title": "AI 實習生", "company": "測試科技",
                    "step1": {"platform": "cake", "job_id": "neg_02", "title": "AI 實習生", "company": "測試科技", "url": "https://example.com/2"},
                    "step2": {"passed": True, "reject_reason": "", "location": "台北市"},
                    "step3": {"char_count": 20, "snippet": "誠徵實習生，意者內洽"},
                    "step4": {"fit_level": "medium", "confidence": 0.8, "probabilities": {"high": 0.2, "medium": 0.6, "low": 0.2, "unrelated": 0.0}},
                    "step5": {"tier": "Tier 3", "basis": "標準商業實體"},
                    "step6": {"state_len": 5000, "state_snippet": "# JOB POSITION FACT SHEET\n## 1. COMPANY REPUTATION"},
                    "step7": {"fused": False, "total_score": 70.0, "is_qualified": False, "has_low_confidence": False, "dimension_scores": [{"name": "A", "score": 70.0, "max_points": 100.0, "confidence": 0.8}]}
                },
                "expected_stage": "Stage 3 (Detail Cleaned)",
                "expected_severity": "BLOCKER",
            },
            {
                "id": "NEG-03",
                "name": "嚴重 HTML 標籤殘留注入",
                "payload": {
                    "platform": "yourator", "job_id": "neg_03", "title": "前端實習生", "company": "測試科技",
                    "step1": {"platform": "yourator", "job_id": "neg_03", "title": "前端實習生", "company": "測試科技", "url": "https://example.com/3"},
                    "step2": {"passed": True, "reject_reason": "", "location": "台北市"},
                    "step3": {"char_count": 500, "snippet": "<div class='job'><script>alert(1)</script><p>工作內容</p></div>"},
                    "step4": {"fit_level": "medium", "confidence": 0.8, "probabilities": {"high": 0.2, "medium": 0.6, "low": 0.2, "unrelated": 0.0}},
                    "step5": {"tier": "Tier 3", "basis": "標準商業實體"},
                    "step6": {"state_len": 5000, "state_snippet": "# JOB POSITION FACT SHEET\n## 1. COMPANY REPUTATION"},
                    "step7": {"fused": False, "total_score": 70.0, "is_qualified": False, "has_low_confidence": False, "dimension_scores": [{"name": "A", "score": 70.0, "max_points": 100.0, "confidence": 0.8}]}
                },
                "expected_stage": "Stage 3 (Detail Cleaned)",
                "expected_severity": "BLOCKER",
            },
            {
                "id": "NEG-04",
                "name": "分數加總失真注入",
                "payload": {
                    "platform": "1111", "job_id": "neg_04", "title": "數據實習生", "company": "測試科技",
                    "step1": {"platform": "1111", "job_id": "neg_04", "title": "數據實習生", "company": "測試科技", "url": "https://example.com/4"},
                    "step2": {"passed": True, "reject_reason": "", "location": "台北市"},
                    "step3": {"char_count": 600, "snippet": "【工作內容】數據分析與清洗..."},
                    "step4": {"fit_level": "medium", "confidence": 0.8, "probabilities": {"high": 0.2, "medium": 0.6, "low": 0.2, "unrelated": 0.0}},
                    "step5": {"tier": "Tier 3", "basis": "標準商業實體"},
                    "step6": {"state_len": 5000, "state_snippet": "# JOB POSITION FACT SHEET\n## 1. COMPANY REPUTATION"},
                    "step7": {"fused": False, "total_score": 90.0, "is_qualified": True, "has_low_confidence": False, "dimension_scores": [{"name": "A", "score": 30.0, "max_points": 45.0, "confidence": 0.8}, {"name": "B", "score": 20.0, "max_points": 35.0, "confidence": 0.8}]}
                },
                "expected_stage": "Stage 7 (Rubric Score)",
                "expected_severity": "BLOCKER",
            },
            {
                "id": "NEG-05",
                "name": "異質產業嚴重滲漏注入 (餐飲外場未被淘汰)",
                "payload": {
                    "platform": "yes123", "job_id": "neg_05", "title": "知名連鎖餐飲外場門市計時員", "company": "餐飲事業",
                    "step1": {"platform": "yes123", "job_id": "neg_05", "title": "知名連鎖餐飲外場門市計時員", "company": "餐飲事業", "url": "https://example.com/5"},
                    "step2": {"passed": True, "reject_reason": "", "location": "台北市"},
                    "step3": {"char_count": 600, "snippet": "點餐、送餐、桌面清潔..."},
                    "step4": {"fit_level": "medium", "confidence": 0.8, "probabilities": {"high": 0.2, "medium": 0.6, "low": 0.2, "unrelated": 0.0}},
                    "step5": {"tier": "Tier 3", "basis": "在地企業"},
                    "step6": {"state_len": 5000, "state_snippet": "# JOB POSITION FACT SHEET\n## 1. COMPANY REPUTATION"},
                    "step7": {"fused": False, "total_score": 70.0, "is_qualified": False, "has_low_confidence": False, "dimension_scores": [{"name": "A", "score": 70.0, "max_points": 100.0, "confidence": 0.8}]}
                },
                "expected_stage": "Stage 1 (Industry Intrusion)",
                "expected_severity": "BLOCKER",
            },
            {
                "id": "NEG-06",
                "name": "非法枚舉 fit_level 注入",
                "payload": {
                    "platform": "104", "job_id": "neg_06", "title": "後端實習生", "company": "測試科技",
                    "step1": {"platform": "104", "job_id": "neg_06", "title": "後端實習生", "company": "測試科技", "url": "https://example.com/6"},
                    "step2": {"passed": True, "reject_reason": "", "location": "台北市"},
                    "step3": {"char_count": 600, "snippet": "後端 API 開發..."},
                    "step4": {"fit_level": "corrupted_level", "confidence": 0.8, "probabilities": {"high": 0.5, "medium": 0.5}},
                    "step5": {"tier": "Tier 3", "basis": "標準商業實體"},
                    "step6": {"state_len": 5000, "state_snippet": "# JOB POSITION FACT SHEET\n## 1. COMPANY REPUTATION"},
                    "step7": {"fused": False, "total_score": 70.0, "is_qualified": False, "has_low_confidence": False, "dimension_scores": [{"name": "A", "score": 70.0, "max_points": 100.0, "confidence": 0.8}]}
                },
                "expected_stage": "Stage 4 (Profile Fit)",
                "expected_severity": "BLOCKER",
            },
        ]
        summary.total_samples = len(test_cases)

        for tc in test_cases:
            mock_summary = AuditSummary()
            self._audit_job_pipeline_item(tc["payload"], f"[{tc['id']}] {tc['name']}", mock_summary)

            # Check if expected defect was caught
            caught = any(d.severity == tc["expected_severity"] and d.stage == tc["expected_stage"] for d in mock_summary.defects)
            if not caught:
                summary.defects.append(
                    Defect("Negative Injection Defense", "BLOCKER", tc["id"], f"未能成功攔截負向樣本: {tc['name']} (期望捕獲: {tc['expected_stage']} {tc['expected_severity']})")
                )

        return summary


def print_cli_summary(summary: AuditSummary) -> None:
    print("\n" + "=" * 70)
    print(f"{CLR_BOLD}🔍 JOBME 資料流黑箱稽核診斷報告 (Black-box Audit Report){CLR_RESET}")
    print(f"稽核目標: {CLR_CYAN}{summary.target_name}{CLR_RESET}")
    print(f"檢查樣本數: {summary.total_samples} 筆")
    print(f"涵蓋檢驗階段: {', '.join(summary.stages_checked)}")
    print("=" * 70)

    score_color = CLR_GREEN if summary.quality_score >= 95.0 and summary.blocker_count == 0 else CLR_RED
    status_text = f"{CLR_GREEN}✅ 合格 (PASSED){CLR_RESET}" if summary.passed else f"{CLR_RED}❌ 不合格 (FAILED){CLR_RESET}"

    print(f"\n📊 【稽核總評】: {status_text}")
    print(f"品質得分: {score_color}{summary.quality_score:.1f} / 100 分{CLR_RESET}")
    print(f"  • {CLR_RED}Blocker 致命錯誤: {summary.blocker_count} 處 (容許: 0){CLR_RESET}")
    print(f"  • {CLR_YELLOW}Major   重大缺陷: {summary.major_count} 處{CLR_RESET}")
    print(f"  • {CLR_BLUE}Minor   輕微瑕疵: {summary.minor_count} 處{CLR_RESET}")

    if summary.defects:
        print(f"\n{CLR_BOLD}⚠️ 【缺陷明細節錄】(最多顯示前 15 項):{CLR_RESET}")
        for idx, d in enumerate(summary.defects[:15], 1):
            sev_color = CLR_RED if d.severity == "BLOCKER" else (CLR_YELLOW if d.severity == "MAJOR" else CLR_BLUE)
            print(f"  {idx:2d}. [{sev_color}{d.severity:<7}{CLR_RESET}] [{d.stage}] {d.job_ref}")
            print(f"      👉 {d.message}")
        if len(summary.defects) > 15:
            print(f"  ... 另有 {len(summary.defects) - 15} 項缺陷未展開，請參閱生成之 Markdown 報告。")
    else:
        print(f"\n{CLR_GREEN}🎉 太棒了！全節點資料流完全符合規範，無任何異常瑕疵！{CLR_RESET}")

    print("=" * 70 + "\n")


def generate_markdown_report(summaries: List[AuditSummary], output_dir: str = "reports") -> str:
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(output_dir, f"audit_report_{timestamp}.md")

    total_samples = sum(s.total_samples for s in summaries)
    total_blockers = sum(s.blocker_count for s in summaries)
    total_majors = sum(s.major_count for s in summaries)
    total_minors = sum(s.minor_count for s in summaries)
    all_passed = all(s.passed for s in summaries)

    lines = [
        f"# JobMe 資料流黑箱稽核診斷報告",
        f"",
        f"- **稽核時間**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **總體審核結果**: {'`✅ PASS`' if all_passed else '`❌ FAIL`'}",
        f"- **總稽核樣本數**: {total_samples} 筆",
        f"- **缺陷統計**: Blocker: `{total_blockers}` | Major: `{total_majors}` | Minor: `{total_minors}`",
        f"",
        f"---",
        f"",
        f"## 1. 稽核目標綜合成績單",
        f"",
        f"| 稽核對象 | 樣本數 | 品質得分 | 判定結果 | Blocker | Major | Minor |",
        f"| :--- | :---: | :---: | :---: | :---: | :---: | :---: |",
    ]

    for s in summaries:
        res = "✅ PASS" if s.passed else "❌ FAIL"
        label = os.path.basename(s.target_name) if os.path.exists(s.target_name) else s.target_name
        lines.append(
            f"| `{label}` | {s.total_samples} | **{s.quality_score:.1f}** | {res} | {s.blocker_count} | {s.major_count} | {s.minor_count} |"
        )

    lines.extend([
        f"",
        f"---",
        f"",
        f"## 2. 缺陷詳細清單",
        f"",
    ])

    has_any_defects = False
    for s in summaries:
        if s.defects:
            has_any_defects = True
            lines.append(f"### 📍 目標: `{s.target_name}`")
            lines.append(f"| 嚴重度 | 檢驗階段 | 樣本識別 | 缺陷描述 |")
            lines.append(f"| :--- | :--- | :--- | :--- |")
            for d in s.defects:
                lines.append(f"| `{d.severity}` | {d.stage} | {d.job_ref} | {d.message} |")
            lines.append(f"")

    if not has_any_defects:
        lines.append("*(無任何缺陷記錄，所有檢驗項目均 100% 通過)*\n")

    lines.extend([
        f"---",
        f"",
        f"## 3. 稽核標準依據",
        f"- 參照規範：[docs/data_flow_audit_standard.md](../docs/data_flow_audit_standard.md)",
        f"- 合格門檻：Blocker == 0 且品質評分 ≥ 95.0 分",
    ])

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description="JobMe 全生命週期資料流黑箱稽核工具")
    parser.add_argument("--file", help="指定稽核之 Pipeline 階段資料 JSON 檔案 (例如 reports/pipeline_50_jobs_data.json)")
    parser.add_argument("--csv", help="指定稽核之 CSV 報表檔案 (例如 records.csv 或 profile_fits.csv)")
    parser.add_argument("--db", help="指定稽核之 SQLite DB 檔案 (預設: jobs.db)")
    parser.add_argument("--taxonomy", action="store_true", help="檢驗 5 大平台職類映射與自訂類別動態轉譯能力")
    parser.add_argument("--negative", action="store_true", help="執行負向邊界注入測試 (空值 JD、殘缺頁面、分數失真防禦)")
    parser.add_argument("--all", action="store_true", help="執行全域大盤點 (整合：報表 + CSV + DB + 職類映射 + 負向注入)")
    parser.add_argument("--threshold", type=float, default=75.0, help="推播分數門檻 (預設: 75.0)")

    args = parser.parse_args()
    auditor = DataFlowAuditor(notify_threshold=args.threshold)
    summaries: List[AuditSummary] = []

    # If --all or no args specified
    if args.all or (not args.file and not args.csv and not args.db and not args.taxonomy and not args.negative):
        print(f"{CLR_BOLD}🚀 啟動全域資料流大盤點 (含職類映射與負向邊界防禦)...{CLR_RESET}")
        # 1. Pipeline JSON
        candidate_jsons = [
            "reports/pipeline_50_jobs_data.json",
            "reports/pipeline_25_jobs_data.json",
            "reports/jev_payloads_10_internships.json",
        ]
        for c in candidate_jsons:
            if os.path.exists(c):
                summaries.append(auditor.audit_pipeline_json(c))
                break

        # 2. CSVs
        if os.path.exists("records.csv"):
            summaries.append(auditor.audit_csv("records.csv"))
        if os.path.exists("profile_fits.csv"):
            summaries.append(auditor.audit_csv("profile_fits.csv"))

        # 3. SQLite DB
        if os.path.exists("jobs.db"):
            summaries.append(auditor.audit_sqlite_db("jobs.db"))

        # 4. Taxonomy Translation
        summaries.append(auditor.audit_taxonomy_translation())

        # 5. Negative Injections
        summaries.append(auditor.audit_negative_injections())

    else:
        if args.file:
            summaries.append(auditor.audit_pipeline_json(args.file))
        if args.csv:
            summaries.append(auditor.audit_csv(args.csv))
        if args.db:
            summaries.append(auditor.audit_sqlite_db(args.db))
        if args.taxonomy:
            summaries.append(auditor.audit_taxonomy_translation())
        if args.negative:
            summaries.append(auditor.audit_negative_injections())

    for s in summaries:
        print_cli_summary(s)

    report_path = generate_markdown_report(summaries)
    print(f"📄 稽核報告已儲存至: {CLR_CYAN}{report_path}{CLR_RESET}\n")

    # Exit code: 0 if all pass, 1 if any fails
    all_pass = all(s.passed for s in summaries)
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
