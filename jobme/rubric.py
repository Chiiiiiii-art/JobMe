from __future__ import annotations
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
import yaml

from .models import Job


@dataclass
class Level:
    points: float
    description: str


@dataclass
class Dimension:
    id: str
    name: str  # Display name (typically Chinese for Discord)
    name_en: str  # English prompt name for Jev
    levels: List[Level]

    @property
    def max_points(self) -> float:
        return max(lvl.points for lvl in self.levels) if self.levels else 0.0

    @property
    def criteria(self) -> List[str]:
        # Sort levels ascending by points for Jev criteria ordering
        sorted_levels = sorted(self.levels, key=lambda l: l.points)
        return [lvl.description for lvl in sorted_levels]

    @property
    def level_points(self) -> List[float]:
        sorted_levels = sorted(self.levels, key=lambda l: l.points)
        return [lvl.points for lvl in sorted_levels]


@dataclass
class RubricConfig:
    keywords: List[str] = field(default_factory=list)
    job_type: str = "internship"
    regions: List[str] = field(default_factory=list)
    districts: List[str] = field(default_factory=list)
    exclude_keywords: List[str] = field(default_factory=list)
    max_age_days: Optional[int] = None
    base_threshold: float = 65.0
    notify_threshold: float = 75.0
    low_confidence_below: float = 0.4
    interval: int = 3600
    export_csv: bool = True
    save_all_evaluated: bool = False
    platforms: Dict[str, Any] = field(default_factory=dict)
    job_categories: List[str] = field(default_factory=list)
    dimensions: List[Dimension] = field(default_factory=list)

    @property
    def total_max_points(self) -> float:
        return sum(d.max_points for d in self.dimensions)


def parse_rubric_file(file_path: str) -> RubricConfig:
    import os
    if not os.path.exists(file_path):
        candidate = os.path.join("config", os.path.basename(file_path))
        if os.path.exists(candidate):
            file_path = candidate

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    frontmatter_dict: Dict[str, Any] = {}
    body = content

    # Parse YAML frontmatter if exists
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            raw_frontmatter = parts[1]
            body = parts[2]
            frontmatter_dict = yaml.safe_load(raw_frontmatter) or {}

    notify_threshold = float(frontmatter_dict.get("notify_threshold", 75.0))
    raw_base = frontmatter_dict.get("base_threshold")
    # ponytail: fallback base_threshold to notify_threshold for backward compatibility
    base_threshold = float(raw_base) if raw_base is not None else notify_threshold

    raw_age = frontmatter_dict.get("max_age_days")
    max_age = int(raw_age) if raw_age is not None and str(raw_age).isdigit() and int(raw_age) > 0 else None

    raw_interval = frontmatter_dict.get("interval", 3600)
    interval = int(raw_interval) if str(raw_interval).isdigit() and int(raw_interval) > 0 else 3600

    raw_cats = frontmatter_dict.get("job_categories") or frontmatter_dict.get("categories") or []
    if isinstance(raw_cats, str):
        job_categories = [c.strip() for c in raw_cats.split(",") if c.strip()]
    elif isinstance(raw_cats, list):
        job_categories = [str(c).strip() for c in raw_cats if str(c).strip()]
    else:
        job_categories = []

    config = RubricConfig(
        keywords=frontmatter_dict.get("keywords", []),
        job_type=frontmatter_dict.get("job_type", "internship"),
        regions=frontmatter_dict.get("regions", []),
        districts=frontmatter_dict.get("districts", []),
        exclude_keywords=frontmatter_dict.get("exclude_keywords", []),
        max_age_days=max_age,
        base_threshold=base_threshold,
        notify_threshold=notify_threshold,
        low_confidence_below=float(frontmatter_dict.get("low_confidence_below", 0.4)),
        interval=interval,
        export_csv=bool(frontmatter_dict.get("export_csv", True)),
        save_all_evaluated=bool(frontmatter_dict.get("save_all_evaluated", False)),
        platforms=frontmatter_dict.get("platforms", {}),
        job_categories=job_categories,
    )

    # Parse markdown sections for dimensions
    dimensions: List[Dimension] = []
    # Split by ## headers
    section_pattern = re.compile(r"^##\s+(.+)$", re.MULTILINE)
    splits = section_pattern.split(body)

    # splits[0] is preamble before first ##
    for i in range(1, len(splits), 2):
        header_text = splits[i].strip()
        section_body = splits[i + 1] if i + 1 < len(splits) else ""

        # Remove optional weight annotation like (40%) from title
        clean_header = re.sub(r"\s*\(\d+%\)\s*$", "", header_text).strip()
        if "/" in clean_header:
            parts = clean_header.split("/", 1)
            name_en = parts[0].strip()
            name_zh = parts[1].strip()
        else:
            name_en = clean_header
            name_zh = clean_header

        dim_id = f"dim_{len(dimensions) + 1}"

        levels: List[Level] = []
        cur_pts = None
        cur_lines = []
        for line in section_body.splitlines():
            st = line.strip()
            if not st:
                continue
            m = re.match(r"^[-*]\s*(\d+(?:\.\d+)?)\s*[:：]\s*(.*)$", st)
            if m:
                if cur_pts is not None and cur_lines:
                    levels.append(Level(points=cur_pts, description="\n".join(cur_lines).strip()))
                cur_pts = float(m.group(1))
                cur_lines = [m.group(2).strip()] if m.group(2).strip() else []
            elif cur_pts is not None:
                cur_lines.append(st)
        if cur_pts is not None and cur_lines:
            levels.append(Level(points=cur_pts, description="\n".join(cur_lines).strip()))

        if levels:
            # Sort levels ascending
            levels.sort(key=lambda l: l.points)
            dimensions.append(Dimension(id=dim_id, name=name_zh, name_en=name_en, levels=levels))

    config.dimensions = dimensions
    return config


def filter_job_hard(job: Job, config: RubricConfig) -> Tuple[bool, str]:
    """
    Evaluates hard filters on job.
    Returns (True, '') if passed, or (False, reason) if rejected.
    """
    # 1. Exclude keywords in title and company
    title_lower = job.title.lower()
    comp_lower = (job.company or "").lower()
    for kw in config.exclude_keywords:
        kw_l = kw.lower()
        if kw_l in title_lower:
            return False, f"標題含排除關鍵字: {kw}"
        if kw_l in comp_lower:
            return False, f"公司名稱含排除關鍵字: {kw}"

    # Generic check: Exclude dummy/test postings across platforms
    dummy_markers = ["測試專用", "請勿應徵", "測試職缺", "test only", "do not apply"]
    if any(m in title_lower for m in dummy_markers) or any(m in comp_lower for m in dummy_markers):
        return False, f"排除測試或無效職缺: {job.title} @ {job.company}"

    # 2. Location filter
    loc = (job.location or "").replace("臺", "台")
    target_regions = [r.replace("臺", "台") for r in config.regions] if config.regions else []
    if target_regions and not any(r in loc for r in target_regions):
        return False, f"地點不符指定地區 ({job.location} 不在 {config.regions})"

    if config.districts and "區" in loc:
        if not any(d in loc for d in config.districts):
            return False, f"地點不符指定行政區 ({job.location} 不在 {config.districts})"

    # 3. Max age filter
    if config.max_age_days:
        if job.published_at:
            try:
                pub_date = None
                if len(job.published_at) == 10:
                    pub_date = datetime.strptime(job.published_at, "%Y-%m-%d")
                elif "T" in job.published_at:
                    pub_date = datetime.fromisoformat(job.published_at.replace("Z", "+00:00"))
                if pub_date:
                    age_days = (datetime.now() - pub_date.replace(tzinfo=None)).days
                    if age_days > config.max_age_days:
                        return False, f"職缺上架過久 ({age_days} 天 > 上限 {config.max_age_days} 天)"
            except Exception:
                pass

        elif job.platform == "yes123" and not job.published_at:
            m = re.search(r"_(\d{8})\d{6}_", str(job.job_id))
            if m:
                try:
                    pub_date = datetime.strptime(m.group(1), "%Y%m%d")
                    age_days = (datetime.now() - pub_date).days
                    if age_days > config.max_age_days:
                        return False, f"Yes123 職缺上架過久 ({age_days} 天 > 上限 {config.max_age_days} 天)"
                except Exception:
                    pass


    return True, ""


def get_corporate_authority_dimension(config: RubricConfig) -> Optional[Dimension]:
    for dim in config.dimensions:
        name_lower = (dim.name or "").lower()
        en_lower = (dim.name_en or "").lower()
        if any(k in name_lower for k in ["權威", "品牌", "聲量"]) or any(k in en_lower for k in ["corporate", "brand", "prestige", "authority"]):
            return dim
    return None



def get_tier_definitions(config: Optional[RubricConfig] = None, rubric_path: str = "config/rubric.md") -> Dict[str, str]:
    """Extract Tier 1-4 textual definitions directly from rubric Corporate Authority dimension."""
    if config is None:
        import os
        candidates = [
            rubric_path,
            os.path.join("config", os.path.basename(rubric_path)),
            "config/rubric.md",
            "rubric.md",
            "config/rubric.example.md",
            "rubric.example.md",
        ]
        path = next((c for c in candidates if os.path.exists(c)), "config/rubric.example.md")
        config = parse_rubric_file(path)

    dim = get_corporate_authority_dimension(config)
    defs = {
        "Tier 1": "Top global Tier-1 multinational, domestic tech titan, or top financial holding HQ (45分)",
        "Tier 2": "Prominent listed enterprise, mid-sized multinational tech/consulting, or well-known unicorn/scale-up (30分)",
        "Tier 3": "General SME or boutique team with localized or niche recognition (15分)",
        "Tier 4": "Obscure startup, shell company, negative reputation, or no verifiable records (0分)",
    }
    if not dim:
        return defs

    for lvl in dim.levels:
        m = re.search(r"tier\s*([1-4])", lvl.description, re.IGNORECASE)
        if m:
            defs[f"Tier {m.group(1)}"] = f"{lvl.description} ({lvl.points:g}分)"
    return defs


def get_tier_points(config: Optional[RubricConfig] = None, rubric_path: str = "config/rubric.md") -> Dict[str, float]:
    """Map Tier 1-4 directly to points based on rubric Corporate Authority dimension."""
    if config is None:
        import os
        candidates = [
            rubric_path,
            os.path.join("config", os.path.basename(rubric_path)),
            "config/rubric.md",
            "rubric.md",
            "config/rubric.example.md",
            "rubric.example.md",
        ]
        path = next((c for c in candidates if os.path.exists(c)), "config/rubric.example.md")
        config = parse_rubric_file(path)

    dim = get_corporate_authority_dimension(config)
    pts = {"Tier 1": 45.0, "Tier 2": 30.0, "Tier 3": 15.0, "Tier 4": 0.0}
    if not dim or not dim.levels:
        return pts

    matched = {}
    for lvl in dim.levels:
        m = re.search(r"tier\s*([1-4])", lvl.description, re.IGNORECASE)
        if m:
            matched[f"Tier {m.group(1)}"] = lvl.points

    if len(matched) >= 3:
        pts.update(matched)
    else:
        sorted_lvls = sorted(dim.levels, key=lambda l: l.points)
        if len(sorted_lvls) >= 4:
            pts["Tier 4"] = sorted_lvls[0].points
            pts["Tier 3"] = sorted_lvls[1].points
            pts["Tier 2"] = sorted_lvls[2].points
            pts["Tier 1"] = sorted_lvls[3].points
        elif len(sorted_lvls) == 3:
            pts["Tier 4"] = 0.0
            pts["Tier 3"] = sorted_lvls[0].points
            pts["Tier 2"] = sorted_lvls[1].points
            pts["Tier 1"] = sorted_lvls[2].points

    return pts

