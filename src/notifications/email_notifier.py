"""
Email Notification System

Sends email notifications for drop events using SMTP.
Supports Gmail, Outlook, and custom SMTP servers.
"""

import asyncio
import json
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


# HTML email template
EMAIL_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
<style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0e0e10; color: #efeff1; padding: 20px; }}
    .container {{ max-width: 600px; margin: 0 auto; background: #1f1f23; border-radius: 12px; padding: 30px; }}
    .header {{ text-align: center; margin-bottom: 20px; }}
    .header h1 {{ color: #a970ff; margin: 0; font-size: 24px; }}
    .header p {{ color: #adadb8; margin: 5px 0; }}
    .drop-card {{ background: #26262c; border-radius: 8px; padding: 15px; margin: 10px 0; border-left: 4px solid #a970ff; }}
    .drop-card h3 {{ color: #efeff1; margin: 0 0 8px 0; }}
    .drop-card .game {{ color: #a970ff; font-weight: bold; }}
    .drop-card .details {{ color: #adadb8; font-size: 14px; }}
    .drop-card .reward {{ color: #00ff7f; font-size: 14px; margin-top: 5px; }}
    .footer {{ text-align: center; margin-top: 20px; color: #adadb8; font-size: 12px; }}
    a {{ color: #a970ff; text-decoration: none; }}
</style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>🎮 Twitch Drops Bot</h1>
        <p>{title}</p>
    </div>
    {content}
    <div class="footer">
        <p>Twitch Drops Bot • Automated Drop Tracking</p>
    </div>
</div>
</body>
</html>
"""


class EmailNotifier:
    """Sends email notifications for Twitch Drop events."""

    def __init__(
        self,
        smtp_host: str | None = None,
        smtp_port: int | None = None,
        sender_email: str | None = None,
        password: str | None = None,
        recipient_email: str | None = None,
    ):
        self.smtp_host = smtp_host or os.getenv("EMAIL_SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = smtp_port or int(os.getenv("EMAIL_SMTP_PORT", "587"))
        self.sender_email = sender_email or os.getenv("EMAIL_SENDER", "")
        self.password = password or os.getenv("EMAIL_PASSWORD", "")
        self.recipient_email = recipient_email or os.getenv("EMAIL_RECIPIENT", "")

    @property
    def is_configured(self) -> bool:
        return all([self.smtp_host, self.sender_email, self.password, self.recipient_email])

    def to_config_json(self) -> str:
        """Serialize config to JSON for storage (excludes password)."""
        return json.dumps({
            "smtp_host": self.smtp_host,
            "smtp_port": self.smtp_port,
            "sender_email": self.sender_email,
            "recipient_email": self.recipient_email,
            "has_password": bool(self.password),
        })

    @classmethod
    def from_config_json(cls, config_json: str) -> "EmailNotifier":
        """Create from stored config JSON."""
        config = json.loads(config_json)
        return cls(
            smtp_host=config.get("smtp_host"),
            smtp_port=config.get("smtp_port"),
            sender_email=config.get("sender_email"),
            recipient_email=config.get("recipient_email"),
        )

    async def send(self, subject: str, title: str, content_html: str) -> bool:
        """Send an email notification."""
        if not self.is_configured:
            logger.warning("Email not configured. Skipping notification.")
            return False

        full_html = EMAIL_TEMPLATE.format(title=title, content=content_html)

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"🎮 Twitch Drops: {subject}"
        msg["From"] = self.sender_email
        msg["To"] = self.recipient_email
        msg.attach(MIMEText(full_html, "html"))

        # Run SMTP in thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, self._send_smtp, msg)
            logger.info("📧 Email sent: %s", subject)
            return True
        except Exception as e:
            logger.error("Failed to send email: %s", e)
            return False

    def _send_smtp(self, msg: MIMEMultipart):
        """Send email via SMTP (blocking — called from executor)."""
        with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(self.sender_email, self.password)
            server.send_message(msg)

    # ── Pre-built notifications ────────────────────────────────────

    async def notify_new_campaigns(self, campaigns: list) -> bool:
        """Send notification about new drop campaigns."""
        if not campaigns:
            return False

        cards = ""
        for c in campaigns:
            drops_list = ""
            for drop in c.drops:
                rewards = ", ".join(b.name for b in drop.benefits) or "Reward"
                drops_list += f'<div class="reward">🎁 {rewards} — {drop.required_minutes} min</div>'

            end_str = c.end_at.strftime("%B %d, %Y") if c.end_at else "Unknown"
            cards += f"""
            <div class="drop-card">
                <h3>{c.name}</h3>
                <div class="game">{c.game_display_name}</div>
                <div class="details">Ends: {end_str}</div>
                {drops_list}
            </div>
            """

        subject = f"{len(campaigns)} New Drop Campaign{'s' if len(campaigns) > 1 else ''}!"
        title = "New Drop Campaigns Available"
        return await self.send(subject, title, cards)

    async def notify_drop_earned(self, drop_name: str, game_name: str, campaign_name: str) -> bool:
        """Send notification that a drop was earned."""
        content = f"""
        <div class="drop-card">
            <h3>🎉 Drop Earned!</h3>
            <div class="game">{game_name}</div>
            <div class="details">Campaign: {campaign_name}</div>
            <div class="reward">🎁 {drop_name}</div>
        </div>
        """
        return await self.send(f"Drop Earned: {drop_name}", "Drop Earned!", content)

    async def notify_drop_claimed(self, drop_name: str, game_name: str) -> bool:
        """Send notification that a drop was claimed."""
        content = f"""
        <div class="drop-card">
            <h3>✅ Drop Claimed!</h3>
            <div class="game">{game_name}</div>
            <div class="reward">🎁 {drop_name}</div>
        </div>
        """
        return await self.send(f"Drop Claimed: {drop_name}", "Drop Claimed!", content)

    async def send_test(self) -> bool:
        """Send a test notification."""
        content = """
        <div class="drop-card">
            <h3>🧪 Test Notification</h3>
            <div class="details">If you're seeing this, email notifications are working!</div>
            <div class="reward">✅ Configuration verified</div>
        </div>
        """
        return await self.send("Test Notification", "Test Notification", content)
