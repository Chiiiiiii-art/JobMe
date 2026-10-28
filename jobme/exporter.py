from __future__ import annotations
import csv
import os
from typing import List
from .models import ScoredJob

DEFAULT_CSV_PATH = "records.csv"


def append_scored_jobs_to_csv(scored_jobs: List[ScoredJob], file_path: str = DEFAULT_CSV_PATH) -> None:
    if not scored_jobs:
        return

    file_exists = os.path.exists(file_path)

    # Base columns
    base_headers = [
        "evaluated_at",
        "platform",
        "job_id",
        "title",
        "company",
        "location",
        "salary",
        "is_qualified",
        "low_confidence",
        "url",
    ]
    # ponytail: dynamically create N score columns labeled with max points + 1 total score column
    first_job = scored_jobs[0]
    dim_headers = [
        f"{ds.name} ({int(ds.max_points) if int(ds.max_points) == ds.max_points else ds.max_points}分)"
        for ds in first_job.dimension_scores
    ]
    headers = base_headers + dim_headers + ["合計總分"]

    with open(file_path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if not file_exists or os.path.getsize(file_path) == 0:
            writer.writerow(headers)

        for s in scored_jobs:
            base_row = [
                s.evaluated_at,
                s.job.platform,
                s.job.job_id,
                s.job.title,
                s.job.company,
                s.job.location,
                s.job.salary,
                "YES" if s.is_qualified else "NO",
                "YES" if s.has_low_confidence else "NO",
                s.job.url,
            ]
            # Store numeric floats for spreadsheet formulas & sorting
            dim_scores = [round(ds.score, 1) for ds in s.dimension_scores]
            writer.writerow(base_row + dim_scores + [round(s.total_score, 1)])


DEFAULT_PROFILE_FITS_CSV_PATH = "profile_fits.csv"


def append_profile_fit_to_csv(
    job: Job,
    fit_level: str,
    confidence: float,
    probabilities: dict | None = None,
    file_path: str = DEFAULT_PROFILE_FITS_CSV_PATH,
) -> None:
    from datetime import datetime
    file_exists = os.path.exists(file_path)
    headers = [
        "evaluated_at",
        "platform",
        "job_id",
        "title",
        "company",
        "fit_level",
        "confidence",
        "prob_high",
        "prob_medium",
        "prob_low",
        "prob_unrelated",
        "url",
    ]
    probs = probabilities or {}
    row = [
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        job.platform,
        str(job.job_id),
        job.title,
        job.company,
        fit_level.upper(),
        f"{float(confidence):.2f}",
        f"{float(probs.get('high', 0.0)):.3f}" if "high" in probs else "",
        f"{float(probs.get('medium', 0.0)):.3f}" if "medium" in probs else "",
        f"{float(probs.get('low', 0.0)):.3f}" if "low" in probs else "",
        f"{float(probs.get('unrelated', 0.0)):.3f}" if "unrelated" in probs else "",
        job.url,
    ]
    with open(file_path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if not file_exists or os.path.getsize(file_path) == 0:
            writer.writerow(headers)
        writer.writerow(row)


DEFAULT_FAVORITES_CSV_PATH = "favorites.csv"


def export_favorites_to_csv(favorites: list[dict], file_path: str = DEFAULT_FAVORITES_CSV_PATH) -> str:
    """Export all favorited jobs to CSV."""
    headers = [
        "favorited_at",
        "total_score",
        "platform",
        "job_id",
        "title",
        "company",
        "location",
        "salary",
        "url",
    ]
    with open(file_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for fav in favorites:
            writer.writerow([
                fav.get("favorited_at", ""),
                fav.get("total_score", ""),
                fav.get("platform", ""),
                fav.get("job_id", ""),
                fav.get("title", ""),
                fav.get("company", ""),
                fav.get("location", ""),
                fav.get("salary", ""),
                fav.get("url", ""),
            ])
    return file_path


def clear_records(file_path: str = DEFAULT_CSV_PATH) -> None:
    """Clear CSV records export file."""
    if os.path.exists(file_path):
        os.remove(file_path)
    if os.path.exists(DEFAULT_PROFILE_FITS_CSV_PATH):
        os.remove(DEFAULT_PROFILE_FITS_CSV_PATH)



