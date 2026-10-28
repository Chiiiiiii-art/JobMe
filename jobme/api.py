from __future__ import annotations
import logging
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn

from .evaluator import JobEvaluatorService

logger = logging.getLogger("jobme.api")

app = FastAPI(
    title="JobMe Extension API",
    description="Local REST API backend for JobMe Chrome Extension evaluation and cache lookup",
    version="0.1.0",
)

# Enable CORS for browser extensions and localhost
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_service: Optional[JobEvaluatorService] = None


import os

def ensure_discord_bot() -> None:
    bot_token = os.getenv("DISCORD_BOT_TOKEN")
    channel_id = os.getenv("DISCORD_CHANNEL_ID")
    if bot_token:
        import jobme.bot as bot
        if bot._bot_instance is None:
            logger.info("🤖 [API] 啟動 Discord 機器人以支援互動式收藏按鈕...")
            bot.run_bot_in_background(bot_token, channel_id)
            bot.wait_until_bot_ready(timeout=6.0)


def get_evaluator_service() -> JobEvaluatorService:
    global _service
    if _service is None:
        ensure_discord_bot()
        _service = JobEvaluatorService()
    return _service


class EvaluateRequest(BaseModel):
    url: str = Field(..., description="Job listing URL")
    title: Optional[str] = Field(None, description="Job title scraped from DOM")
    company: Optional[str] = Field(None, description="Company name scraped from DOM")
    location: Optional[str] = Field(None, description="Job location scraped from DOM")
    salary: Optional[str] = Field(None, description="Job salary scraped from DOM")
    description: Optional[str] = Field(None, description="Job description text scraped from DOM")
    platform: Optional[str] = Field(None, description="Job platform (e.g. 104, cake, yourator)")
    job_id: Optional[str] = Field(None, description="Platform specific job ID")
    force: bool = Field(False, description="Force re-evaluation even if cached")


@app.get("/api/status")
def get_status() -> Dict[str, Any]:
    try:
        service = get_evaluator_service()
        return {
            "status": "ok",
            "rubric_file": service.rubric_file,
            "dimensions_count": len(service.config.dimensions),
            "total_max_points": service.config.total_max_points,
            "base_threshold": service.config.base_threshold,
            "notify_threshold": service.config.notify_threshold,
            "has_profile": bool(service.profile_text),
        }
    except Exception as e:
        logger.error(f"[API] 取得狀態異常: {e}")
        return {"status": "error", "error": str(e)}


@app.get("/api/check")
def check_job_status(
    url: str = Query(..., description="Target job URL to verify"),
    platform: Optional[str] = Query(None, description="Optional platform key"),
    job_id: Optional[str] = Query(None, description="Optional job ID"),
) -> Dict[str, Any]:
    try:
        service = get_evaluator_service()
        return service.check_job(url, platform=platform, job_id=job_id)
    except Exception as e:
        logger.error(f"[API] 檢查職缺異常 ({url}): {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/evaluate")
def evaluate_job_endpoint(payload: EvaluateRequest) -> Dict[str, Any]:
    try:
        service = get_evaluator_service()
        res = service.evaluate_job(
            url=payload.url,
            title=payload.title,
            company=payload.company,
            location=payload.location,
            salary=payload.salary,
            description=payload.description,
            platform=payload.platform,
            job_id=payload.job_id,
            force=payload.force,
        )
        return res
    except Exception as e:
        logger.error(f"[API] 職缺評估失敗 ({payload.url}): {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"評估失敗: {str(e)}")


def run_server(host: str = "127.0.0.1", port: int = 8000, log_level: str = "info") -> None:
    ensure_discord_bot()
    logger.info(f"🚀 JobMe API 伺服器啟動於 http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level=log_level)


if __name__ == "__main__":
    run_server()
