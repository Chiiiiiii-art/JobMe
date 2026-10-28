"""JobMe: 智慧求職爬蟲與 Jev 決策推播系統"""

__version__ = "0.1.0"

from .company_enricher import CompanyEnricher, CompanyProfile
from .db import init_db, is_job_seen, mark_notified, save_evaluation, save_job
from .exporter import append_scored_jobs_to_csv
from .jev import JevClient
from .models import DimensionScore, Job, ScoredJob
from .notifier import DiscordNotifier
from .rubric import Dimension, RubricConfig, filter_job_hard, parse_rubric_file
from .translator import JobTranslator

__all__ = [
    "Job",
    "DimensionScore",
    "ScoredJob",
    "RubricConfig",
    "Dimension",
    "parse_rubric_file",
    "filter_job_hard",
    "CompanyEnricher",
    "CompanyProfile",
    "JobTranslator",
    "JevClient",
    "DiscordNotifier",
    "append_scored_jobs_to_csv",
    "init_db",
    "save_job",
    "is_job_seen",
    "save_evaluation",
    "mark_notified",
]
