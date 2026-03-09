"""
Discord Webhook Notification System

Sends rich embed notifications to a Discord channel via webhooks.
No bot token required — just a webhook URL.
"""

import json
import logging
import os
from datetime import datetime, timezone

import aiohttp
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Twitch purple color
EMBED_COLOR = 0x9146FF


class DiscordNotifier:
    """Sends Discord webhook notifications for Twitch Drop events."""

    def __init__(self, webhook_url: str | None = None):
        self.webhook_url = webhook_url or os.getenv("DISCORD_WEBHOOK_URL", "")

    @property
    def is_configured(self) -> bool:
        return bool(self.webhook_url) and self.webhook_url.startswith("https://discord.com/api/webhooks/")

    def to_config_json(self) -> str:
        return json.dumps({"webhook_url": self.webhook_url})

    @classmethod
    def from_config_json(cls, config_json: str) -> "DiscordNotifier":
        config = json.loads(config_json)
        return cls(webhook_url=config.get("webhook_url"))

    async def send(self, content: str = "", embeds: list[dict] | None = None) -> bool:
        """Send a Discord webhook message."""
        if not self.is_configured:
            logger.warning("Discord webhook not configured. Skipping notification.")
            return False

        payload = {
            "username": "Twitch Drops Bot",
            "avatar_url": "https://static-cdn.jtvnw.net/ttv-boxart/drops-70x100.png",
        }
        if content:
            payload["content"] = content
        if embeds:
            payload["embeds"] = embeds[:10]  # Discord limit

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                ) as resp:
                    if resp.status in (200, 204):
                        logger.info("💬 Discord notification sent")
                        return True
                    else:
                        text = await resp.text()
                        logger.error("Discord webhook failed (%d): %s", resp.status, text[:300])
                        return False
        except aiohttp.ClientError as e:
            logger.error("Discord webhook error: %s", e)
            return False

    # ── Pre-built notifications ────────────────────────────────────

    async def notify_new_campaigns(self, campaigns: list) -> bool:
        """Send notification about new drop campaigns."""
        if not campaigns:
            return False

        embeds = []
        for c in campaigns:
            rewards = []
            for drop in c.drops:
                reward_names = ", ".join(b.name for b in drop.benefits) or "Reward"
                rewards.append(f"🎁 **{reward_names}** — {drop.required_minutes} min")

            end_str = c.end_at.strftime("%B %d, %Y %H:%M UTC") if c.end_at else "Unknown"
            embed = {
                "title": f"🆕 {c.name}",
                "description": "\n".join(rewards) if rewards else "Check Twitch for details",
                "color": EMBED_COLOR,
                "fields": [
                    {"name": "🎮 Game", "value": c.game_display_name or c.game_name, "inline": True},
                    {"name": "📅 Ends", "value": end_str, "inline": True},
                ],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "footer": {"text": "Twitch Drops Bot"},
            }
            if c.game_box_art_url:
                embed["thumbnail"] = {"url": c.game_box_art_url}
            if c.details_url:
                embed["url"] = c.details_url

            embeds.append(embed)

        header = f"🔔 **{len(campaigns)} New Drop Campaign{'s' if len(campaigns) > 1 else ''} Available!**"
        return await self.send(content=header, embeds=embeds)

    async def notify_drop_earned(self, drop_name: str, game_name: str, campaign_name: str) -> bool:
        """Send notification that a drop was earned."""
        embed = {
            "title": "🎉 Drop Earned!",
            "description": f"**{drop_name}**",
            "color": 0x00FF7F,  # Green
            "fields": [
                {"name": "🎮 Game", "value": game_name, "inline": True},
                {"name": "📋 Campaign", "value": campaign_name, "inline": True},
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {"text": "Twitch Drops Bot"},
        }
        return await self.send(embeds=[embed])

    async def notify_drop_claimed(self, drop_name: str, game_name: str) -> bool:
        """Send notification that a drop was claimed."""
        embed = {
            "title": "✅ Drop Claimed!",
            "description": f"**{drop_name}**",
            "color": 0x00FF7F,
            "fields": [
                {"name": "🎮 Game", "value": game_name, "inline": True},
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {"text": "Twitch Drops Bot"},
        }
        return await self.send(embeds=[embed])

    async def notify_progress(
        self, drop_name: str, game_name: str, channel: str,
        minutes: int, required: int, percent: float,
    ) -> bool:
        """Send a progress update (use sparingly)."""
        bar_filled = int(percent / 10)
        bar_empty = 10 - bar_filled
        progress_bar = "█" * bar_filled + "░" * bar_empty

        embed = {
            "title": f"📊 Drop Progress: {drop_name}",
            "description": f"`{progress_bar}` {percent:.1f}%\n{minutes}/{required} minutes",
            "color": EMBED_COLOR,
            "fields": [
                {"name": "🎮 Game", "value": game_name, "inline": True},
                {"name": "📺 Channel", "value": channel, "inline": True},
            ],
            "footer": {"text": "Twitch Drops Bot"},
        }
        return await self.send(embeds=[embed])

    async def notify_error(self, error_msg: str) -> bool:
        """Send an error notification."""
        embed = {
            "title": "⚠️ Bot Error",
            "description": f"```{error_msg[:1900]}```",
            "color": 0xFF6B6B,  # Red
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {"text": "Twitch Drops Bot"},
        }
        return await self.send(embeds=[embed])

    async def send_test(self) -> bool:
        """Send a test notification."""
        embed = {
            "title": "🧪 Test Notification",
            "description": "If you're seeing this, Discord notifications are working!",
            "color": EMBED_COLOR,
            "fields": [
                {"name": "Status", "value": "✅ Configuration verified", "inline": False},
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {"text": "Twitch Drops Bot"},
        }
        return await self.send(content="🎮 **Twitch Drops Bot — Test**", embeds=[embed])
