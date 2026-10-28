from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class Job:
    platform: str
    job_id: str
    title: str
    company: str
    location: str
    salary: str
    url: str
    description: str = ""
    requirements: str = ""
    tags: List[str] = field(default_factory=list)
    published_at: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def dedupe_key(self) -> str:
        return f"{self.platform}:{self.job_id}"


@dataclass
class DimensionScore:
    name: str
    max_points: float
    score: float
    confidence: float
    probabilities: Dict[int, float]
    level_points: List[float]


@dataclass
class ScoredJob:
    job: Job
    total_score: float
    is_qualified: bool
    dimension_scores: List[DimensionScore]
    evaluated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    profile_fit: Optional[str] = None

    @property
    def has_low_confidence(self) -> bool:
        return any(ds.confidence < 0.4 for ds in self.dimension_scores)
