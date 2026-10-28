from __future__ import annotations
from contextlib import contextmanager
import json
import sqlite3
from typing import Iterator, Optional
from .models import Job, ScoredJob

DEFAULT_DB_PATH = "jobs.db"


@contextmanager
def get_connection(db_path: str = DEFAULT_DB_PATH) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass
    try:
        yield conn
    finally:
        conn.close()


def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                platform TEXT NOT NULL,
                job_id TEXT NOT NULL,
                title TEXT NOT NULL,
                company TEXT NOT NULL,
                location TEXT,
                salary TEXT,
                url TEXT NOT NULL,
                tags TEXT,
                description TEXT,
                status TEXT DEFAULT 'pending_stage1',
                first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (platform, job_id)
            )
        """)
        # Migration: ensure description and status columns exist in legacy jobs table
        cur = conn.execute("PRAGMA table_info(jobs)")
        existing_cols = {row["name"] for row in cur.fetchall()}
        if "description" not in existing_cols:
            conn.execute("ALTER TABLE jobs ADD COLUMN description TEXT")
        if "status" not in existing_cols:
            conn.execute("ALTER TABLE jobs ADD COLUMN status TEXT DEFAULT 'pending_stage1'")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS evaluations (
                platform TEXT NOT NULL,
                job_id TEXT NOT NULL,
                total_score REAL NOT NULL,
                is_qualified INTEGER NOT NULL,
                has_low_confidence INTEGER NOT NULL,
                details_json TEXT NOT NULL,
                evaluated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                notified_at TIMESTAMP,
                PRIMARY KEY (platform, job_id),
                FOREIGN KEY (platform, job_id) REFERENCES jobs(platform, job_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS profile_fits (
                platform TEXT NOT NULL,
                job_id TEXT NOT NULL,
                title TEXT NOT NULL,
                company TEXT NOT NULL,
                fit_level TEXT NOT NULL,
                confidence REAL NOT NULL,
                probabilities_json TEXT,
                url TEXT,
                evaluated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (platform, job_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS favorites (
                platform TEXT NOT NULL,
                job_id TEXT NOT NULL,
                title TEXT NOT NULL,
                company TEXT NOT NULL,
                location TEXT,
                salary TEXT,
                url TEXT NOT NULL,
                total_score REAL,
                details_json TEXT,
                favorited_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (platform, job_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS job_refs (
                ref_id TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                job_id TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Sync legacy statuses
        conn.execute("UPDATE jobs SET status = 'evaluated' WHERE (platform, job_id) IN (SELECT platform, job_id FROM evaluations)")
        conn.execute("UPDATE jobs SET status = 'stage1_rejected' WHERE status != 'evaluated' AND (platform, job_id) IN (SELECT platform, job_id FROM profile_fits WHERE fit_level IN ('unrelated', 'low'))")
        conn.execute("UPDATE jobs SET status = 'pending_stage2' WHERE status NOT IN ('evaluated', 'stage1_rejected') AND (platform, job_id) IN (SELECT platform, job_id FROM profile_fits WHERE fit_level NOT IN ('unrelated', 'low'))")
        conn.commit()


def make_job_ref(platform: str, job_id: str, db_path: str = DEFAULT_DB_PATH) -> str:
    """Generate a compact 20-char deterministic ref_id for a job to fit Discord's 100-char custom_id limit."""
    init_db(db_path)
    import hashlib
    ref_id = hashlib.sha256(f"{platform}:{job_id}".encode("utf-8")).hexdigest()[:20]
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO job_refs (ref_id, platform, job_id) VALUES (?, ?, ?)",
            (ref_id, platform, str(job_id)),
        )
        conn.commit()
    return ref_id


def resolve_job_ref(ref_id: str, db_path: str = DEFAULT_DB_PATH) -> Optional[tuple[str, str]]:
    """Resolve a compact ref_id back to (platform, job_id)."""
    init_db(db_path)
    with get_connection(db_path) as conn:
        cur = conn.execute(
            "SELECT platform, job_id FROM job_refs WHERE ref_id = ? LIMIT 1",
            (ref_id,),
        )
        row = cur.fetchone()
        if row:
            return (row["platform"], row["job_id"])
    return None


def is_job_seen(platform: str, job_id: str, db_path: str = DEFAULT_DB_PATH) -> bool:
    with get_connection(db_path) as conn:
        cur = conn.execute(
            "SELECT 1 FROM jobs WHERE platform = ? AND job_id = ? LIMIT 1",
            (platform, str(job_id)),
        )
        return cur.fetchone() is not None


def update_job_status(platform: str, job_id: str, status: str, db_path: str = DEFAULT_DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE jobs SET status = ? WHERE platform = ? AND job_id = ?",
            (status, platform, str(job_id)),
        )
        conn.commit()


def get_pending_jobs(db_path: str = DEFAULT_DB_PATH, limit: Optional[int] = None) -> List[Job]:
    """
    Fetch jobs from DB that are pending evaluation:
    1. pending_stage2 (already passed Stage 1, awaiting Stage 2 evaluation)
    2. pending_stage1 (passed hard filter, awaiting Stage 1 profile fit)
    """
    query = """
        SELECT platform, job_id, title, company, location, salary, url, tags, description, status
        FROM jobs
        WHERE status IN ('pending_stage2', 'pending_stage1')
        ORDER BY CASE status WHEN 'pending_stage2' THEN 1 WHEN 'pending_stage1' THEN 2 ELSE 3 END,
                 first_seen_at ASC
    """
    params = ()
    if limit and limit > 0:
        query += " LIMIT ?"
        params = (limit,)

    with get_connection(db_path) as conn:
        cur = conn.execute(query, params)
        rows = cur.fetchall()
        jobs: List[Job] = []
        for r in rows:
            tags = []
            if r["tags"]:
                try:
                    tags = json.loads(r["tags"])
                except Exception:
                    pass
            job = Job(
                platform=r["platform"],
                job_id=r["job_id"],
                title=r["title"],
                company=r["company"],
                location=r["location"] or "",
                salary=r["salary"] or "",
                url=r["url"],
                tags=tags,
                description=r["description"] or "",
            )
            setattr(job, "status", r["status"])
            jobs.append(job)
        return jobs


def save_job(job: Job, status: str = "pending_stage1", db_path: str = DEFAULT_DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO jobs (platform, job_id, title, company, location, salary, url, tags, description, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(platform, job_id) DO UPDATE SET
                description = COALESCE(NULLIF(excluded.description, ''), jobs.description),
                title = COALESCE(NULLIF(excluded.title, ''), jobs.title),
                company = CASE 
                    WHEN excluded.company IN ('未知企業', '') THEN jobs.company 
                    ELSE excluded.company 
                END,
                location = CASE 
                    WHEN excluded.location IN ('未提供', '') THEN jobs.location 
                    ELSE excluded.location 
                END,
                salary = CASE 
                    WHEN excluded.salary IN ('面議', '') THEN jobs.salary 
                    ELSE excluded.salary 
                END,
                url = excluded.url,
                tags = excluded.tags
            """,
            (
                job.platform,
                str(job.job_id),
                job.title,
                job.company,
                job.location,
                job.salary,
                job.url,
                json.dumps(job.tags, ensure_ascii=False),
                job.description or "",
                status,
            ),
        )
        conn.commit()


def save_evaluation(scored: ScoredJob, db_path: str = DEFAULT_DB_PATH) -> None:
    details = [
        {
            "name": ds.name,
            "score": round(ds.score, 2),
            "max": ds.max_points,
            "confidence": round(ds.confidence, 2),
            "probabilities": {str(k): round(v, 4) for k, v in ds.probabilities.items()},
        }
        for ds in scored.dimension_scores
    ]

    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO evaluations (
                platform, job_id, total_score, is_qualified, has_low_confidence, details_json, evaluated_at
            ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                scored.job.platform,
                str(scored.job.job_id),
                round(scored.total_score, 2),
                1 if scored.is_qualified else 0,
                1 if scored.has_low_confidence else 0,
                json.dumps(details, ensure_ascii=False),
            ),
        )
        conn.execute(
            "UPDATE jobs SET status = 'evaluated' WHERE platform = ? AND job_id = ?",
            (scored.job.platform, str(scored.job.job_id)),
        )
        conn.commit()


def mark_notified(platform: str, job_id: str, db_path: str = DEFAULT_DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE evaluations SET notified_at = CURRENT_TIMESTAMP WHERE platform = ? AND job_id = ?",
            (platform, str(job_id)),
        )
        conn.commit()


def save_profile_fit(
    job: Job,
    fit_level: str,
    confidence: float,
    probabilities: Optional[dict] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO profile_fits (
                platform, job_id, title, company, fit_level, confidence, probabilities_json, url, evaluated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                job.platform,
                str(job.job_id),
                job.title,
                job.company,
                fit_level,
                round(float(confidence), 2),
                json.dumps(probabilities or {}, ensure_ascii=False),
                job.url,
            ),
        )
        new_status = "stage1_rejected" if fit_level in ("unrelated", "low") else "pending_stage2"
        conn.execute(
            "UPDATE jobs SET status = ? WHERE platform = ? AND job_id = ? AND status != 'evaluated'",
            (new_status, job.platform, str(job.job_id)),
        )
        conn.commit()


def get_recent_profile_fits(limit: int = 10, db_path: str = DEFAULT_DB_PATH) -> list[dict]:
    with get_connection(db_path) as conn:
        cur = conn.execute(
            """
            SELECT platform, job_id, title, company, fit_level, confidence, probabilities_json, url, evaluated_at
            FROM profile_fits
            ORDER BY evaluated_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cur.fetchall()
        results = []
        for r in rows:
            probs = {}
            if r["probabilities_json"]:
                try:
                    probs = json.loads(r["probabilities_json"])
                except Exception:
                    pass
            results.append({
                "platform": r["platform"],
                "job_id": r["job_id"],
                "title": r["title"],
                "company": r["company"],
                "fit_level": r["fit_level"],
                "confidence": r["confidence"],
                "probabilities": probs,
                "url": r["url"],
                "evaluated_at": r["evaluated_at"],
            })
        return results


def clear_db(db_path: str = DEFAULT_DB_PATH) -> None:
    """Clear job, evaluation, and profile_fit records from SQLite database."""
    init_db(db_path)
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM evaluations")
        conn.execute("DELETE FROM profile_fits")
        conn.execute("DELETE FROM jobs")
        conn.commit()


def is_favorite(platform: str, job_id: str, db_path: str = DEFAULT_DB_PATH) -> bool:
    init_db(db_path)
    with get_connection(db_path) as conn:
        cur = conn.execute(
            "SELECT 1 FROM favorites WHERE platform = ? AND job_id = ? LIMIT 1",
            (platform, str(job_id)),
        )
        return cur.fetchone() is not None


def get_job_and_eval(platform: str, job_id: str, db_path: str = DEFAULT_DB_PATH) -> Optional[dict]:
    init_db(db_path)
    with get_connection(db_path) as conn:
        cur = conn.execute(
            """
            SELECT j.platform, j.job_id, j.title, j.company, j.location, j.salary, j.url,
                   e.total_score, e.has_low_confidence, e.details_json,
                   pf.fit_level
            FROM jobs j
            LEFT JOIN evaluations e ON j.platform = e.platform AND j.job_id = e.job_id
            LEFT JOIN profile_fits pf ON j.platform = pf.platform AND j.job_id = pf.job_id
            WHERE j.platform = ? AND j.job_id = ?
            LIMIT 1
            """,
            (platform, str(job_id)),
        )
        row = cur.fetchone()
        if not row:
            return None
        return dict(row)


def find_job_by_url(url: str, db_path: str = DEFAULT_DB_PATH) -> Optional[dict]:
    init_db(db_path)
    clean_url = url.split("?")[0].split("#")[0].rstrip("/")
    with get_connection(db_path) as conn:
        cur = conn.execute(
            """
            SELECT j.platform, j.job_id, j.title, j.company, j.location, j.salary, j.url,
                   e.total_score, e.has_low_confidence, e.details_json,
                   pf.fit_level
            FROM jobs j
            LEFT JOIN evaluations e ON j.platform = e.platform AND j.job_id = e.job_id
            LEFT JOIN profile_fits pf ON j.platform = pf.platform AND j.job_id = pf.job_id
            WHERE j.url = ? OR j.url LIKE ?
            ORDER BY e.total_score DESC
            LIMIT 1
            """,
            (url, f"{clean_url}%"),
        )
        row = cur.fetchone()
        if not row:
            return None
        return dict(row)


def add_favorite(
    platform: str,
    job_id: str,
    title: str,
    company: str,
    location: str,
    salary: str,
    url: str,
    total_score: Optional[float] = None,
    details_json: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> bool:
    init_db(db_path)
    with get_connection(db_path) as conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO favorites (
                platform, job_id, title, company, location, salary, url, total_score, details_json, favorited_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (platform, str(job_id), title, company, location, salary, url, total_score, details_json),
        )
        conn.commit()
        return cur.rowcount > 0


def get_favorites(limit: Optional[int] = None, db_path: str = DEFAULT_DB_PATH) -> list[dict]:
    init_db(db_path)
    query = """
        SELECT platform, job_id, title, company, location, salary, url, total_score, details_json, favorited_at
        FROM favorites
        ORDER BY favorited_at DESC
    """
    params = ()
    if limit and limit > 0:
        query += " LIMIT ?"
        params = (limit,)

    with get_connection(db_path) as conn:
        cur = conn.execute(query, params)
        return [dict(r) for r in cur.fetchall()]


def remove_favorite(platform: str, job_id: str, db_path: str = DEFAULT_DB_PATH) -> bool:
    init_db(db_path)
    with get_connection(db_path) as conn:
        cur = conn.execute(
            "DELETE FROM favorites WHERE platform = ? AND job_id = ?",
            (platform, str(job_id)),
        )
        conn.commit()
        return cur.rowcount > 0



