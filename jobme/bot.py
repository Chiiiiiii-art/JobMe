from __future__ import annotations
import asyncio
from datetime import datetime
import io
import json
import logging
import os
import re
import threading
import time
from typing import Optional

import requests
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Button, DynamicItem

from .db import (
    add_favorite,
    get_favorites,
    get_job_and_eval,
    get_recent_profile_fits,
    is_favorite,
    make_job_ref,
    remove_favorite,
    resolve_job_ref,
)
from .exporter import (
    DEFAULT_CSV_PATH,
    DEFAULT_FAVORITES_CSV_PATH,
    DEFAULT_PROFILE_FITS_CSV_PATH,
    export_favorites_to_csv,
)

logger = logging.getLogger("jobme.bot")

_bot_instance: Optional[commands.Bot] = None
_bot_channel_id: Optional[str] = None
_bot_loop: Optional[asyncio.AbstractEventLoop] = None


def build_forward_job_embed(job_data: dict) -> discord.Embed:
    total = round(float(job_data.get("total_score") or 0.0), 1)
    has_low_conf = bool(job_data.get("has_low_confidence", 0))
    color = 0x2ECC71 if total >= 85 else 0xF1C40F
    if has_low_conf:
        color = 0xE67E22

    details_raw = job_data.get("details_json", "")
    breakdown_lines = []
    if details_raw:
        try:
            details = json.loads(details_raw) if isinstance(details_raw, str) else details_raw
            for ds in details:
                conf = ds.get("confidence", 1.0)
                conf_tag = " ⚠️" if conf < 0.4 else ""
                breakdown_lines.append(
                    f"• **{ds.get('name', '')}**: `{round(float(ds.get('score', 0)), 1)} / {ds.get('max', 0)}`{conf_tag}"
                )
        except Exception:
            pass

    breakdown_text = "\n".join(breakdown_lines) if breakdown_lines else "無明細"
    if has_low_conf:
        breakdown_text += "\n\n*(⚠️ 標示代表 Jev 判定時信心度較低)*"

    embed = discord.Embed(
        title=f"[{total}分] {job_data.get('title')}",
        url=job_data.get("url"),
        color=color,
    )
    embed.add_field(name="🏢 公司", value=job_data.get("company") or "未提供", inline=True)
    embed.add_field(name="📍 地點", value=job_data.get("location") or "未提供", inline=True)
    embed.add_field(name="💰 薪資", value=job_data.get("salary") or "面議", inline=True)
    embed.add_field(name="🌐 平台", value=(job_data.get("platform") or "").upper(), inline=True)

    fit_level = job_data.get("fit_level")
    if fit_level:
        fit_icon = "🎯" if str(fit_level).lower() == "high" else "⚖️"
        fit_text = "高度契合 (High)" if str(fit_level).lower() == "high" else "中度契合 (Medium)"
        embed.add_field(name=f"{fit_icon} 個人適配度", value=fit_text, inline=True)

    embed.add_field(name="📊 評分明細", value=breakdown_text, inline=False)
    embed.set_footer(text=f"JobMe 職缺收藏 • 收藏時間：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    return embed


class RemoveFavoriteButton(DynamicItem[Button], template=r"jobme_unfav:(?P<key>.+)"):
    """Persistent button for removing job cards from favorites across Discord bot restarts."""

    def __init__(self, platform: str, job_id: str):
        ref_id = make_job_ref(platform, job_id)
        super().__init__(
            Button(
                label="🗑️ 移除收藏",
                style=discord.ButtonStyle.danger,
                custom_id=f"jobme_unfav:{ref_id}",
            )
        )
        self.platform = platform
        self.job_id = job_id
        self.ref_id = ref_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: Button, match: re.Match[str], /):
        key = match.group("key")
        resolved = resolve_job_ref(key)
        if resolved:
            platform, job_id = resolved
        elif ":" in key:
            platform, job_id = key.split(":", 1)
        else:
            platform, job_id = "", key
        return cls(platform, job_id)

    async def callback(self, interaction: discord.Interaction):
        platform = self.platform
        job_id = self.job_id

        remove_favorite(platform, job_id)
        all_favs = get_favorites()
        export_favorites_to_csv(all_favs)

        try:
            await interaction.message.delete()
        except Exception as e:
            logger.warning(f"刪除收藏訊息失敗 (可能缺少管理訊息權限): {e}")
            try:
                view = discord.ui.View()
                disabled_btn = Button(label="🗑️ 已移除收藏", style=discord.ButtonStyle.secondary, disabled=True)
                view.add_item(disabled_btn)
                await interaction.message.edit(view=view)
            except Exception:
                pass

        await interaction.response.send_message(
            "🗑️ 已從職缺收藏清單移除，並同步更新 records/favorites.csv！",
            ephemeral=True,
        )


class FavoriteButton(DynamicItem[Button], template=r"jobme_fav:(?P<key>.+)"):
    """Persistent button for bookmarking job cards across Discord bot restarts."""

    def __init__(self, platform: str, job_id: str):
        ref_id = make_job_ref(platform, job_id)
        super().__init__(
            Button(
                label="⭐ 納入收藏",
                style=discord.ButtonStyle.primary,
                custom_id=f"jobme_fav:{ref_id}",
            )
        )
        self.platform = platform
        self.job_id = job_id
        self.ref_id = ref_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: Button, match: re.Match[str], /):
        key = match.group("key")
        resolved = resolve_job_ref(key)
        if resolved:
            platform, job_id = resolved
        elif ":" in key:
            platform, job_id = key.split(":", 1)
        else:
            platform, job_id = "", key
        return cls(platform, job_id)

    async def callback(self, interaction: discord.Interaction):
        platform = self.platform
        job_id = self.job_id

        if is_favorite(platform, job_id):
            await interaction.response.send_message("⚠️ 此職缺先前已納入收藏！", ephemeral=True)
            return

        job_data = get_job_and_eval(platform, job_id)
        if not job_data:
            await interaction.response.send_message("⚠️ 找不到該職缺在資料庫的詳細紀錄。", ephemeral=True)
            return

        add_favorite(
            platform=platform,
            job_id=job_id,
            title=job_data["title"],
            company=job_data["company"],
            location=job_data["location"] or "",
            salary=job_data["salary"] or "",
            url=job_data["url"],
            total_score=job_data.get("total_score"),
            details_json=job_data.get("details_json"),
        )

        all_favs = get_favorites()
        export_favorites_to_csv(all_favs)

        fav_channel_id = os.getenv("DISCORD_FAVORITES_CHANNEL_ID", "").strip()
        forwarded = False
        forward_error = ""

        if fav_channel_id and fav_channel_id.isdigit():
            try:
                fav_ch = interaction.client.get_channel(int(fav_channel_id))
                if not fav_ch:
                    fav_ch = await interaction.client.fetch_channel(int(fav_channel_id))
                if fav_ch:
                    embed = build_forward_job_embed(job_data)
                    view = discord.ui.View(timeout=None)
                    view.add_item(RemoveFavoriteButton(platform, job_id))
                    await fav_ch.send(embed=embed, view=view)
                    forwarded = True
                else:
                    forward_error = f"找不到收藏頻道 ID `{fav_channel_id}`"
            except Exception as e:
                logger.error(f"轉發至收藏頻道失敗 ({fav_channel_id}): {e}")
                forward_error = str(e)

        if forwarded:
            await interaction.response.send_message(
                f"✅ 已成功將 **{job_data['title']}** 納入收藏，並已轉發至收藏頻道！",
                ephemeral=True,
            )
        elif not fav_channel_id:
            await interaction.response.send_message(
                f"✅ 已成功將 **{job_data['title']}** 納入收藏並寫入資料庫！\n*(💡 提示：若需自動轉發，請於 `.env` 設定 `DISCORD_FAVORITES_CHANNEL_ID`)*",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"✅ 已成功將 **{job_data['title']}** 納入收藏並寫入資料庫！\n*(⚠️ 轉發至收藏頻道失敗：{forward_error})*",
                ephemeral=True,
            )


def build_scored_job_embed(scored) -> discord.Embed:
    job = scored.job
    total = round(scored.total_score, 1)
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

    embed = discord.Embed(
        title=f"[{total}分] {job.title}",
        url=job.url,
        color=color,
    )
    embed.add_field(name="🏢 公司", value=job.company or "未提供", inline=True)
    embed.add_field(name="📍 地點", value=job.location or "未提供", inline=True)
    embed.add_field(name="💰 薪資", value=job.salary or "面議", inline=True)
    embed.add_field(name="🌐 平台", value=job.platform.upper(), inline=True)

    if scored.profile_fit:
        fit_icon = "🎯" if scored.profile_fit == "high" else "⚖️"
        fit_text = "高度契合 (High)" if scored.profile_fit == "high" else "中度契合 (Medium)"
        embed.add_field(name=f"{fit_icon} 個人適配度", value=fit_text, inline=True)

    embed.add_field(name="📊 評分明細", value=breakdown_text, inline=False)
    embed.set_footer(text=f"JobMe 推播 • {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    return embed


_cached_feed_channel_id: Optional[str] = None


def get_job_feed_channel_id() -> Optional[str]:
    """Get target channel ID for job recommendations feed (e.g. #job-here)."""
    global _cached_feed_channel_id
    if _cached_feed_channel_id:
        return _cached_feed_channel_id

    # 1. Explicit environment variable
    env_id = os.getenv("DISCORD_JOB_CHANNEL_ID") or os.getenv("DISCORD_FEED_CHANNEL_ID")
    if env_id and env_id.strip().isdigit():
        _cached_feed_channel_id = env_id.strip()
        return _cached_feed_channel_id

    # 2. Extract channel_id from DISCORD_WEBHOOK_URL
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if webhook_url and ("discord.com/api/webhooks" in webhook_url or "discordapp.com/api/webhooks" in webhook_url):
        try:
            r = requests.get(webhook_url, timeout=5)
            if r.status_code == 200:
                cid = str(r.json().get("channel_id") or "")
                if cid.isdigit():
                    _cached_feed_channel_id = cid
                    return _cached_feed_channel_id
        except Exception as e:
            logger.debug(f"無法自 Webhook 取得頻道 ID: {e}")

    return _bot_channel_id


def wait_until_bot_ready(timeout: float = 6.0) -> bool:
    """Block until Discord bot is logged in and ready, up to timeout seconds."""
    global _bot_instance
    start = time.time()
    while time.time() - start < timeout:
        if _bot_instance and _bot_instance.is_ready():
            return True
        time.sleep(0.2)
    return False


def post_job_card_via_bot(scored) -> bool:
    """Post job card embed with interactive Favorite button via Discord Bot."""
    global _bot_instance, _bot_loop
    if not _bot_instance or not _bot_instance.is_ready() or not _bot_loop:
        return False

    target_channel_id = get_job_feed_channel_id()
    if not target_channel_id or not target_channel_id.isdigit():
        return False

    async def _send():
        try:
            ch = _bot_instance.get_channel(int(target_channel_id))
            if not ch:
                ch = await _bot_instance.fetch_channel(int(target_channel_id))
            if not ch:
                return False
            embed = build_scored_job_embed(scored)
            view = discord.ui.View(timeout=None)
            view.add_item(FavoriteButton(scored.job.platform, scored.job.job_id))
            await ch.send(embed=embed, view=view)
            return True
        except Exception as e:
            logger.warning(f"透過 Discord Bot 推播職缺至頻道 {target_channel_id} 失敗: {e}")
            return False

    try:
        future = asyncio.run_coroutine_threadsafe(_send(), _bot_loop)
        return future.result(timeout=15)
    except Exception as e:
        logger.warning(f"Discord Bot 發送職缺超時或異常: {e}")
        return False


def post_alert_via_bot(title: str, error_msg: str, platform: str = "") -> bool:
    """Post system alert / error embed via Discord Bot to the primary channel."""
    global _bot_instance, _bot_loop
    if not _bot_instance or not _bot_instance.is_ready() or not _bot_loop:
        return False

    target_channel_id = os.getenv("DISCORD_CHANNEL_ID") or get_job_feed_channel_id()
    if not target_channel_id or not target_channel_id.isdigit():
        return False

    async def _send():
        try:
            ch = _bot_instance.get_channel(int(target_channel_id))
            if not ch:
                ch = await _bot_instance.fetch_channel(int(target_channel_id))
            if not ch:
                return False
            embed = discord.Embed(
                title=f"🚨 [系統通報] {title}",
                color=0xE74C3C,
                timestamp=datetime.now(),
            )
            embed.add_field(name="⚠️ 異常/降級類型", value=title, inline=True)
            if platform:
                embed.add_field(name="🌐 相關平台", value=platform.upper(), inline=True)
            clean_err = str(error_msg).strip()
            if len(clean_err) > 1000:
                clean_err = clean_err[:997] + "..."
            embed.add_field(name="📝 詳細資訊", value=f"```\n{clean_err}\n```", inline=False)
            embed.set_footer(text="JobMe 系統警報")
            await ch.send(embed=embed)
            return True
        except Exception as e:
            logger.warning(f"透過 Discord Bot 推播警報失敗: {e}")
            return False

    try:
        future = asyncio.run_coroutine_threadsafe(_send(), _bot_loop)
        return future.result(timeout=10)
    except Exception as e:
        logger.warning(f"Discord Bot 發送警報超時或異常: {e}")
        return False


async def send_favorites_view(interaction: discord.Interaction):
    favs = get_favorites(limit=10)
    if not favs:
        await interaction.response.send_message("目前尚未有任何收藏職缺。", ephemeral=True)
        return

    all_favs = get_favorites()
    csv_path = export_favorites_to_csv(all_favs, DEFAULT_FAVORITES_CSV_PATH)

    lines = []
    for f in favs:
        score_val = f.get("total_score")
        score_tag = f"[{score_val:.1f}分]" if score_val is not None else "[未評分]"
        dt = f["favorited_at"][-8:] if f.get("favorited_at") else ""
        lines.append(f"⭐ `{score_tag:<7}` [{f['title'][:20]}](<{f['url']}>) @ {f['company'][:12]} ({dt})")

    embed = discord.Embed(
        title=f"⭐ 我的職缺收藏庫 (共 {len(all_favs)} 筆，顯示最新 {len(favs)} 筆)",
        description="\n".join(lines),
        color=0xF1C40F,
    )
    embed.set_footer(text="完整清單請下載隨附的 favorites.csv")

    files = []
    if os.path.exists(csv_path) and os.path.getsize(csv_path) > 0:
        files.append(discord.File(csv_path, filename="favorites.csv"))

    await interaction.response.send_message(embed=embed, files=files, ephemeral=True)



class PipelineState:
    """Thread-safe state manager for JobMe scheduler and bot controls."""

    def __init__(self, interval_seconds: int = 3600):
        self.interval_seconds = interval_seconds
        self.is_paused = False
        self.is_busy = False
        self.last_run: Optional[datetime] = None
        self.next_run: Optional[datetime] = None
        self.last_summary: Optional[dict] = None
        self.lock = threading.Lock()
        self.pipeline_fn = None
        self.args = None
        self.config = None
        self.rubric_file = None

    def trigger_run(self) -> tuple[bool, str]:
        with self.lock:
            if self.is_busy:
                return False, "⚠️ 系統目前正在執行巡檢中，請稍候再試。"
            self.is_busy = True

        threading.Thread(target=self._execute_pipeline, daemon=True).start()
        return True, "🚀 已手動觸發巡檢流程！"

    def _execute_pipeline(self):
        try:
            self.last_run = datetime.now()
            logger.info("[Scheduler] 手動/排程觸發執行 run_pipeline...")
            if self.pipeline_fn and self.args and self.config and self.rubric_file:
                self.pipeline_fn(self.args, self.config, self.rubric_file)
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            logger.error(f"[Scheduler] 執行 pipeline 異常: {e}\n{tb}", exc_info=True)
            try:
                from .notifier import DiscordNotifier
                notifier = DiscordNotifier()
                notifier.notify_error("排程巡檢嚴重崩潰", f"{e}\n\nTraceback:\n{tb[-500:]}")
            except Exception:
                pass
        finally:
            with self.lock:
                self.is_busy = False
                if not self.is_paused:
                    self.next_run = datetime.fromtimestamp(time.time() + self.interval_seconds)


# Global singleton state
state = PipelineState()


class ControlPanelView(discord.ui.View):
    """Interactive Discord Button Panel for cross-device remote control."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="⚡ 立即巡檢",
        style=discord.ButtonStyle.primary,
        custom_id="jobme_btn_run_now",
        row=0,
    )
    async def btn_run_now(self, interaction: discord.Interaction, button: discord.ui.Button):
        ok, msg = state.trigger_run()
        await interaction.response.send_message(msg, ephemeral=True)

    @discord.ui.button(
        label="🟢 啟動監控",
        style=discord.ButtonStyle.success,
        custom_id="jobme_btn_start",
        row=0,
    )
    async def btn_start(self, interaction: discord.Interaction, button: discord.ui.Button):
        with state.lock:
            state.is_paused = False
            state.next_run = datetime.fromtimestamp(time.time() + state.interval_seconds)
        embed = build_status_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(
        label="⏸️ 暫停監控",
        style=discord.ButtonStyle.secondary,
        custom_id="jobme_btn_pause",
        row=0,
    )
    async def btn_pause(self, interaction: discord.Interaction, button: discord.ui.Button):
        with state.lock:
            state.is_paused = True
            state.next_run = None
        embed = build_status_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(
        label="⏱️ 調整頻率",
        style=discord.ButtonStyle.primary,
        custom_id="jobme_btn_interval",
        row=1,
    )
    async def btn_interval(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = IntervalSelectView()
        await interaction.response.send_message(
            "請選擇常駐巡檢週期（變更後立即生效）：",
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(
        label="📊 刷新狀態",
        style=discord.ButtonStyle.secondary,
        custom_id="jobme_btn_refresh",
        row=1,
    )
    async def btn_refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = build_status_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(
        label="📑 最新 Jev 紀錄",
        style=discord.ButtonStyle.secondary,
        custom_id="jobme_btn_fits",
        row=2,
    )
    async def btn_fits(self, interaction: discord.Interaction, button: discord.ui.Button):
        fits = get_recent_profile_fits(limit=8)
        if not fits:
            await interaction.response.send_message("目前尚未有 Jev Profile 適配度評估紀錄。", ephemeral=True)
            return

        lines = []
        for f in fits:
            lvl = f["fit_level"].upper()
            icon = "✅" if lvl in ("HIGH", "MEDIUM") else "❌"
            dt = f["evaluated_at"][-8:] if f.get("evaluated_at") else ""
            lines.append(f"{icon} `[{lvl:<6} {f['confidence']:.0%}]` [{f['title'][:20]}](<{f['url']}>) @ {f['company'][:12]} ({dt})")

        embed = discord.Embed(
            title="📑 最新 Jev Profile 適配度把關紀錄",
            description="\n".join(lines),
            color=0x2ECC71,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(
        label="📁 下載 CSV 報表",
        style=discord.ButtonStyle.secondary,
        custom_id="jobme_btn_export",
        row=2,
    )
    async def btn_export(self, interaction: discord.Interaction, button: discord.ui.Button):
        files = []
        if os.path.exists(DEFAULT_CSV_PATH) and os.path.getsize(DEFAULT_CSV_PATH) > 0:
            files.append(discord.File(DEFAULT_CSV_PATH, filename="records.csv"))
        if os.path.exists(DEFAULT_PROFILE_FITS_CSV_PATH) and os.path.getsize(DEFAULT_PROFILE_FITS_CSV_PATH) > 0:
            files.append(discord.File(DEFAULT_PROFILE_FITS_CSV_PATH, filename="profile_fits.csv"))

        if not files:
            await interaction.response.send_message("目前無 CSV 報表可供下載。", ephemeral=True)
            return

        await interaction.response.send_message("📦 為您匯出最新評分與 Jev 適配度報表：", files=files, ephemeral=True)

    @discord.ui.button(
        label="⭐ 我的收藏",
        style=discord.ButtonStyle.success,
        custom_id="jobme_btn_favorites",
        row=2,
    )
    async def btn_favorites(self, interaction: discord.Interaction, button: discord.ui.Button):
        await send_favorites_view(interaction)

    @discord.ui.button(
        label="🗑️ 清除歷史資料",
        style=discord.ButtonStyle.danger,
        custom_id="jobme_btn_clear_history",
        row=3,
    )
    async def btn_clear_history(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = ConfirmClearHistoryView()
        await interaction.response.send_message(
            "⚠️ **警告：即將清空職缺歷史紀錄！**\n"
            "• **將清空**：`jobs.db`（已讀職缺/評分紀錄/Jev 把關快取）、`records.csv`、`profile_fits.csv`\n"
            "• **將保留**：企業分級資料庫（省 API 額度）、瀏覽器 Cookie（免重新登入）\n\n"
            "確定要立即清空嗎？",
            view=view,
            ephemeral=True,
        )


class ConfirmClearHistoryView(discord.ui.View):
    """Two-step confirmation dialog to safely clear database history."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="⚠️ 確認立即清空",
        style=discord.ButtonStyle.danger,
        custom_id="jobme_btn_confirm_clear",
    )
    async def btn_confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        from .db import clear_db
        from .exporter import clear_records

        clear_db()
        clear_records()
        logger.info("[Discord Bot] 使用者透過 Discord 遠端清空職缺歷史紀錄！")
        await interaction.response.send_message(
            "🗑️ **歷史紀錄已清空完成**！\n• `jobs.db` 與 CSV 已重設。\n• 企業資料庫與 Cookie 完整保留。",
            ephemeral=True,
        )

    @discord.ui.button(
        label="取消",
        style=discord.ButtonStyle.secondary,
        custom_id="jobme_btn_cancel_clear",
    )
    async def btn_cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("已取消清除操作。", ephemeral=True)


class IntervalSelectView(discord.ui.View):
    """Dropdown for changing schedule interval."""

    def __init__(self):
        super().__init__(timeout=120)

    @discord.ui.select(
        placeholder="選擇巡檢間隔時間...",
        options=[
            discord.SelectOption(label="30 分鐘 (1800 秒)", value="1800"),
            discord.SelectOption(label="1 小時 (3600 秒) [預設]", value="3600", default=True),
            discord.SelectOption(label="2 小時 (7200 秒)", value="7200"),
            discord.SelectOption(label="4 小時 (14400 秒)", value="14400"),
            discord.SelectOption(label="8 小時 (28800 秒)", value="28800"),
        ],
    )
    async def select_callback(self, interaction: discord.Interaction, select: discord.ui.Select):
        new_interval = int(select.values[0])
        with state.lock:
            state.interval_seconds = new_interval
            if not state.is_paused:
                state.next_run = datetime.fromtimestamp(time.time() + new_interval)
        mins = new_interval // 60
        await interaction.response.send_message(
            f"✅ 巡檢週期已更新為 **{mins} 分鐘** ({new_interval} 秒)！",
            ephemeral=True,
        )


def build_status_embed() -> discord.Embed:
    """Build standardized status card for JobMe."""
    status_str = "🟢 正常監控中" if not state.is_paused else "⏸️ 已暫停"
    if state.is_busy:
        status_str += " (🚀 巡檢中...)"

    interval_desc = f"{state.interval_seconds // 60} 分鐘 ({state.interval_seconds}s)"
    last_str = state.last_run.strftime("%Y-%m-%d %H:%M:%S") if state.last_run else "尚未執行"
    next_str = state.next_run.strftime("%Y-%m-%d %H:%M:%S") if (state.next_run and not state.is_paused) else "暫停排程中"

    embed = discord.Embed(
        title="🎛️ JobMe 職缺雷達 • 跨裝置中控台",
        description="桌機排程後台管理中心。在筆電或手機直接點選下方按鈕控制排程啟動、暫停與即時巡檢。",
        color=0x3498DB if not state.is_paused else 0x95A5A6,
    )
    embed.add_field(name="運作狀態", value=status_str, inline=True)
    embed.add_field(name="巡檢頻率", value=interval_desc, inline=True)
    embed.add_field(name="上次巡檢", value=last_str, inline=False)
    embed.add_field(name="預計下次巡檢", value=next_str, inline=False)
    embed.set_footer(text=f"最後更新於 {datetime.now().strftime('%H:%M:%S')}")
    return embed


def setup_bot(bot_token: str, channel_id: Optional[str] = None) -> commands.Bot:
    global _bot_instance, _bot_channel_id
    intents = discord.Intents.default()
    bot = commands.Bot(command_prefix="!", intents=intents)
    _bot_instance = bot
    _bot_channel_id = channel_id

    @bot.event
    async def on_ready():
        logger.info(f"✅ Discord 機器人登入成功：{bot.user} (ID: {bot.user.id})")
        try:
            bot.add_view(ControlPanelView())
            bot.add_view(ConfirmClearHistoryView())
            bot.add_dynamic_items(FavoriteButton, RemoveFavoriteButton)
            synced = await bot.tree.sync()
            logger.info(f"✅ Discord 斜線指令已同步：{len(synced)} 個指令")
        except Exception as e:
            logger.error(f"同步斜線指令異常: {e}")

        # Post persistent control panel if channel_id provided
        if channel_id and channel_id.isdigit():
            try:
                ch = bot.get_channel(int(channel_id))
                if not ch:
                    ch = await bot.fetch_channel(int(channel_id))
                if ch:
                    embed = build_status_embed()
                    view = ControlPanelView()
                    await ch.send(embed=embed, view=view)
                    logger.info(f"✅ 控制面板已成功發送至頻道 ID: {channel_id}")
            except Exception as e:
                logger.warning(f"發送控制面板失敗: {e}")

    @bot.tree.command(name="jobme", description="開啟 JobMe 控制面板或操作系統")
    @app_commands.describe(action="操作指令 (panel: 面板, run: 立即執行, status: 狀態, fits: Jev 紀錄, export: 下載報表, fav: 我的收藏, clear: 清除歷史)")
    async def cmd_jobme(
        interaction: discord.Interaction,
        action: Optional[str] = "panel",
    ):
        act = (action or "panel").lower()
        if act == "run":
            ok, msg = state.trigger_run()
            await interaction.response.send_message(msg)
        elif act == "fits":
            fits = get_recent_profile_fits(limit=8)
            if not fits:
                await interaction.response.send_message("目前尚未有 Jev 評估紀錄。")
                return
            lines = []
            for f in fits:
                lvl = f["fit_level"].upper()
                icon = "✅" if lvl in ("HIGH", "MEDIUM") else "❌"
                lines.append(f"{icon} `[{lvl:<6} {f['confidence']:.0%}]` [{f['title'][:20]}](<{f['url']}>) @ {f['company'][:12]}")
            embed = discord.Embed(title="📑 最新 Jev Profile 適配度紀錄", description="\n".join(lines), color=0x2ECC71)
            await interaction.response.send_message(embed=embed)
        elif act == "export":
            files = []
            if os.path.exists(DEFAULT_CSV_PATH):
                files.append(discord.File(DEFAULT_CSV_PATH, filename="records.csv"))
            if os.path.exists(DEFAULT_PROFILE_FITS_CSV_PATH):
                files.append(discord.File(DEFAULT_PROFILE_FITS_CSV_PATH, filename="profile_fits.csv"))
            if files:
                await interaction.response.send_message("📦 為您匯出最新 CSV 報表：", files=files)
            else:
                await interaction.response.send_message("目前無可匯出的 CSV 檔案。")
        elif act in ("fav", "favorite", "favorites"):
            await send_favorites_view(interaction)
        elif act == "clear":
            view = ConfirmClearHistoryView()
            await interaction.response.send_message(
                "⚠️ **警告：即將清空職缺歷史紀錄！**\n• 將重設 `jobs.db` 與 CSV 檔案。\n• 企業資料庫與 Cookie 完整保留。\n\n確定要立即清空嗎？",
                view=view,
                ephemeral=True,
            )
        else:
            embed = build_status_embed()
            view = ControlPanelView()
            await interaction.response.send_message(embed=embed, view=view)

    return bot


def run_bot_in_background(bot_token: str, channel_id: Optional[str] = None):
    """Launch discord bot in dedicated asyncio event loop on a background daemon thread."""
    def _runner():
        global _bot_loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _bot_loop = loop
        bot = setup_bot(bot_token, channel_id)
        try:
            loop.run_until_complete(bot.start(bot_token))
        except Exception as e:
            logger.error(f"[Discord Bot] 機器人運行終止: {e}")
        finally:
            loop.close()

    t = threading.Thread(target=_runner, daemon=True, name="JobMe-DiscordBot")
    t.start()
    return t
