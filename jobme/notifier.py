from __future__ import annotations
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional
import requests

from .models import ScoredJob

logger = logging.getLogger("jobme.notifier")


class DiscordNotifier:
    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = webhook_url or os.getenv("DISCORD_WEBHOOK_URL", "")

    def notify_job(self, scored: ScoredJob) -> bool:
        # 1. Prefer Discord Bot posting with interactive button
        try:
            from .bot import post_job_card_via_bot
            if post_job_card_via_bot(scored):
                return True
        except Exception as e:
            logger.debug(f"Discord Bot 發送退回 Webhook: {e}")

        # 2. Webhook posting fallback
        job = scored.job
        total = round(scored.total_score, 1)

        # Color: Green if >= 85, Gold if >= 70, Orange if low confidence
        color = 0x2ECC71 if total >= 85 else 0xF1C40F
        if scored.has_low_confidence:
            color = 0xE67E22

        dim_lines = []
        for ds in scored.dimension_scores:
            conf_tag = " ⚠️" if ds.confidence < 0.4 else ""
            dim_lines.append(f"• **{ds.name}**: `{round(ds.score, 1)} / {ds.max_points}`{conf_tag}")

        breakdown_text = "\n".join(dim_lines)
        if scored.has_low_confidence:
            breakdown_text += "\n\n*(⚠️ 標示代表 Jev 判定時信心度較低，建議手動點開確認)*"

        fields = [
            {"name": "🏢 公司", "value": job.company or "未提供", "inline": True},
            {"name": "📍 地點", "value": job.location or "未提供", "inline": True},
            {"name": "💰 薪資", "value": job.salary or "面議", "inline": True},
            {"name": "🌐 平台", "value": job.platform.upper(), "inline": True},
        ]
        if scored.profile_fit:
            fit_icon = "🎯" if scored.profile_fit == "high" else "⚖️"
            fit_text = "高度契合 (High)" if scored.profile_fit == "high" else "中度契合 (Medium)"
            fields.append({"name": f"{fit_icon} 個人適配度", "value": fit_text, "inline": True})
        fields.append({"name": "📊 評分明細", "value": breakdown_text, "inline": False})

        embed = {
            "title": f"[{total}分] {job.title}",
            "url": job.url,
            "color": color,
            "fields": fields,
            "footer": {
                "text": f"JobMe 推播 • {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            },
        }

        from .db import make_job_ref
        ref_id = make_job_ref(job.platform, job.job_id)

        payload = {
            "username": "JobMe 職缺雷達",
            "embeds": [embed],
            "components": [
                {
                    "type": 1,
                    "components": [
                        {
                            "type": 2,
                            "style": 1,
                            "label": "⭐ 納入收藏",
                            "custom_id": f"jobme_fav:{ref_id}",
                        }
                    ],
                }
            ],
        }

        if not self.webhook_url:
            print("\n" + "=" * 50)
            print(f"📢 [SIMULATED DISCORD NOTIFICATION] {embed['title']}")
            print(f"URL: {job.url}")
            print(f"Company: {job.company} | Location: {job.location} | Salary: {job.salary}")
            if scored.profile_fit:
                print(f"Profile Fit: {scored.profile_fit.upper()}")
            print(breakdown_text)
            print("=" * 50 + "\n")
            return True

        import time
        for attempt in range(3):
            try:
                r = requests.post(self.webhook_url, json=payload, timeout=10)
                if r.status_code in (200, 204):
                    time.sleep(0.5)  # Polite spacing to avoid Discord webhook 429 rate limit
                    return True
                elif r.status_code == 400 and "components" in payload:
                    # Non-application webhooks reject components; retry without components
                    payload.pop("components", None)
                    continue
                elif r.status_code == 429:
                    try:
                        retry_after = float(r.json().get("retry_after", 2.0))
                    except Exception:
                        retry_after = 2.0
                    logger.warning(f"Discord 遭遇速率限制 (429)，暫停 {retry_after:.1f} 秒後重試 (嘗試 {attempt + 1}/3)...")
                    time.sleep(retry_after + 0.5)
                    continue
                else:
                    logger.error(f"Discord webhook error {r.status_code}: {r.text}")
                    return False
            except Exception as e:
                logger.error(f"Failed to send Discord webhook (attempt {attempt + 1}/3): {e}")
                time.sleep(1.0)
        return False

    def notify_summary(self, summary_data: Dict[str, Any]) -> bool:
        counts_by_platform = summary_data.get("counts_by_platform", {})
        platform_lines = [
            f"• **{p.upper()}**: {cnt} 筆" for p, cnt in counts_by_platform.items()
        ]
        if not platform_lines:
            platform_lines = ["• 無資料"]

        degradations = summary_data.get("degradations", [])
        failed_platforms = summary_data.get("failed_platforms", [])

        # Color: Red if platform failure, Amber if degraded, Blue if normal
        embed_color = 0x3498DB
        if failed_platforms:
            embed_color = 0xE74C3C
        elif degradations:
            embed_color = 0xE67E22

        embed = {
            "title": "📋 JobMe 執行完成報告" if not degradations else "⚠️ JobMe 執行完成報告 (含流程降級)",
            "color": embed_color,
            "fields": [
                {"name": "總抓取筆數", "value": str(summary_data.get("total_scraped", 0)), "inline": True},
                {"name": "通過硬篩筆數", "value": str(summary_data.get("total_filtered", 0)), "inline": True},
                {"name": "達標推播筆數", "value": str(summary_data.get("total_notified", 0)), "inline": True},
                {"name": "各平台抓取概況", "value": "\n".join(platform_lines), "inline": False},
            ],
            "footer": {
                "text": f"JobMe • {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            },
        }

        if degradations:
            embed["fields"].append({
                "name": "🚨 系統降級警告 (已退回基礎/啟發式備援)",
                "value": "\n".join(f"• ⚠️ {d}" for d in degradations),
                "inline": False,
            })

        if failed_platforms:
            embed["fields"].append({
                "name": "❌ 發生異常的平台",
                "value": ", ".join(failed_platforms),
                "inline": False,
            })

        if not self.webhook_url:
            print("\n--- 📋 SUMMARY ---")
            print(f"Scraped: {summary_data.get('total_scraped')}, Filtered: {summary_data.get('total_filtered')}, Notified: {summary_data.get('total_notified')}")
            print("------------------\n")
            return True

        try:
            r = requests.post(self.webhook_url, json={"username": "JobMe 職缺雷達", "embeds": [embed]}, timeout=10)
            return r.status_code in (200, 204)
        except Exception as e:
            logger.error(f"Failed to send Discord summary: {e}")
            return False

    def notify_error(self, title: str, error_msg: str, platform: str = "", domain: str = "") -> bool:
        """
        Send error alert to Discord with anti-flood/anti-spam protection.
        - Domain-level throttling: infrastructure/quota errors alerted at most once per 10 minutes (600s).
        - Cross-platform duplicate throttling: same error signature suppressed for 10 minutes (600s).
        - Global error rate limit: max 5 alerts per 10 minutes.
        """
        import time
        import hashlib
        now = time.time()

        if not hasattr(self, "_recent_errors"):
            self._recent_errors = {}
        if not hasattr(self, "_error_count_window"):
            self._error_count_window = []
        if not hasattr(self, "_domain_cooldowns"):
            self._domain_cooldowns = {}

        # 1. Domain-level cooldown (10 minutes)
        if domain:
            last_domain_sent = self._domain_cooldowns.get(domain, 0.0)
            if (now - last_domain_sent) < 600.0:
                logger.warning(f"[Notifier] 錯誤領域 '{domain}' 已於 10 分鐘內通報過，抑制警報洗屏: {title}")
                return False

        # Clean old timestamps from 10 min window
        cutoff = now - 600.0
        self._error_count_window = [t for t in self._error_count_window if t > cutoff]

        # 2. Global rate limit: max 5 error alerts per 10 minutes
        if len(self._error_count_window) >= 5:
            logger.warning(f"[Notifier] 觸發 Discord 錯誤通報全域防洗屏保護 (10 分鐘內已達 5 次)，略過: {title}")
            return False

        # 3. Duplicate throttling: group by domain or title
        err_key = f"{domain or title}:{error_msg[:120]}"
        err_hash = hashlib.sha256(err_key.encode("utf-8")).hexdigest()
        last_sent = self._recent_errors.get(err_hash, 0.0)
        if (now - last_sent) < 600.0:  # 10 minutes
            logger.warning(f"[Notifier] 觸發重複錯誤冷卻保護 (10 分鐘內相同錯誤)，略過: {title}")
            return False

        # Register this alert
        self._recent_errors[err_hash] = now
        self._error_count_window.append(now)
        if domain:
            self._domain_cooldowns[domain] = now

        # Try Discord Bot first
        try:
            from .bot import post_alert_via_bot
            if post_alert_via_bot(title, error_msg, platform):
                return True
        except Exception as e:
            logger.debug(f"Discord Bot 警報推播退回 Webhook: {e}")

        fields = [
            {"name": "⚠️ 異常類型", "value": title, "inline": True},
            {"name": "⏰ 發生時間", "value": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "inline": True},
        ]
        if platform:
            fields.append({"name": "🌐 相關平台", "value": platform.upper(), "inline": True})

        clean_err = str(error_msg).strip()
        if len(clean_err) > 1000:
            clean_err = clean_err[:997] + "..."
        fields.append({"name": "📝 異常詳情", "value": f"```\n{clean_err}\n```", "inline": False})

        embed = {
            "title": f"🚨 [系統異常通報] {title}",
            "color": 0xE74C3C,
            "fields": fields,
            "footer": {
                "text": "JobMe 自動化警報系統 • 已啟用智慧防洗屏機制 (10分鐘冷卻)"
            },
        }

        payload = {
            "username": "JobMe 系統警報",
            "embeds": [embed],
        }

        if not self.webhook_url:
            print("\n" + "=" * 50)
            print(f"🚨 [SIMULATED DISCORD ERROR] {embed['title']}")
            print(f"Details: {error_msg}")
            print("=" * 50 + "\n")
            return True

        try:
            r = requests.post(self.webhook_url, json=payload, timeout=10)
            return r.status_code in (200, 204)
        except Exception as e:
            logger.error(f"Failed to send Discord error alert: {e}")
            return False
