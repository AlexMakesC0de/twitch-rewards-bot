"""
SQLite Database for Twitch Drops Bot

Stores tracked games, known campaigns, drop progress history,
and notification settings. Uses aiosqlite for async operations.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

DB_PATH = Path("data/twitch_drops.db")


class Database:
    """Async SQLite database manager."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self._db: aiosqlite.Connection | None = None

    async def connect(self):
        """Open the database connection and create tables."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self.db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._create_tables()
        logger.debug("Database connected: %s", self.db_path)

    async def close(self):
        if self._db:
            await self._db.close()
            self._db = None

    async def _create_tables(self):
        """Create all database tables if they don't exist."""
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS tracked_games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                twitch_game_id TEXT,
                display_name TEXT,
                box_art_url TEXT,
                added_at TEXT NOT NULL DEFAULT (datetime('now')),
                is_active INTEGER NOT NULL DEFAULT 1,
                priority INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS campaigns (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                game_id TEXT,
                game_name TEXT,
                status TEXT,
                start_at TEXT,
                end_at TEXT,
                details_url TEXT,
                first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
                notified INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS drops (
                id TEXT PRIMARY KEY,
                campaign_id TEXT NOT NULL,
                name TEXT NOT NULL,
                required_minutes INTEGER NOT NULL DEFAULT 0,
                benefit_name TEXT,
                benefit_image_url TEXT,
                FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
            );

            CREATE TABLE IF NOT EXISTS drop_progress (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                drop_id TEXT NOT NULL,
                campaign_id TEXT NOT NULL,
                minutes_watched INTEGER NOT NULL DEFAULT 0,
                is_complete INTEGER NOT NULL DEFAULT 0,
                is_claimed INTEGER NOT NULL DEFAULT 0,
                drop_instance_id TEXT,
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(drop_id)
            );

            CREATE TABLE IF NOT EXISTS watch_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id TEXT,
                drop_id TEXT,
                channel_login TEXT NOT NULL,
                channel_name TEXT,
                game_name TEXT,
                started_at TEXT NOT NULL DEFAULT (datetime('now')),
                ended_at TEXT,
                minutes_watched INTEGER NOT NULL DEFAULT 0,
                reason_ended TEXT
            );

            CREATE TABLE IF NOT EXISTS notification_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL UNIQUE,  -- 'email' or 'discord'
                enabled INTEGER NOT NULL DEFAULT 1,
                config TEXT  -- JSON config
            );

            CREATE TABLE IF NOT EXISTS drop_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                drop_name TEXT NOT NULL,
                campaign_name TEXT,
                game_name TEXT,
                benefit_name TEXT,
                benefit_image_url TEXT,
                earned_at TEXT NOT NULL DEFAULT (datetime('now')),
                claimed_at TEXT
            );
        """)
        await self._db.commit()

        # Migrations for existing databases
        await self._migrate()

    async def _migrate(self):
        """Run schema migrations for existing databases."""
        # Add priority column if missing
        cursor = await self._db.execute("PRAGMA table_info(tracked_games)")
        columns = {row[1] for row in await cursor.fetchall()}
        if "priority" not in columns:
            await self._db.execute("ALTER TABLE tracked_games ADD COLUMN priority INTEGER NOT NULL DEFAULT 0")
            await self._db.commit()

    # ── Tracked Games ──────────────────────────────────────────────

    async def add_tracked_game(
        self, game_name: str, twitch_game_id: str | None = None,
        display_name: str | None = None, box_art_url: str | None = None,
    ) -> bool:
        """Add a game to the tracked list."""
        try:
            await self._db.execute(
                """INSERT INTO tracked_games (game_name, twitch_game_id, display_name, box_art_url)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(game_name) DO UPDATE SET
                       is_active = 1,
                       twitch_game_id = COALESCE(excluded.twitch_game_id, twitch_game_id),
                       display_name = COALESCE(excluded.display_name, display_name),
                       box_art_url = COALESCE(excluded.box_art_url, box_art_url)
                """,
                (game_name, twitch_game_id, display_name or game_name, box_art_url),
            )
            await self._db.commit()
            logger.info("Tracking game: %s", game_name)
            return True
        except Exception as e:
            logger.error("Failed to add game %s: %s", game_name, e)
            return False

    async def remove_tracked_game(self, game_name: str) -> bool:
        """Remove a game from the tracked list (soft delete)."""
        cursor = await self._db.execute(
            "UPDATE tracked_games SET is_active = 0 WHERE game_name = ? COLLATE NOCASE",
            (game_name,),
        )
        await self._db.commit()
        if cursor.rowcount > 0:
            logger.info("Stopped tracking: %s", game_name)
            return True
        return False

    async def get_tracked_games(self) -> list[dict]:
        """Get all actively tracked games."""
        cursor = await self._db.execute(
            "SELECT * FROM tracked_games WHERE is_active = 1 ORDER BY priority ASC, game_name"
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_tracked_game_names(self) -> list[str]:
        """Get just the names of tracked games."""
        games = await self.get_tracked_games()
        return [g["game_name"] for g in games]

    async def reorder_games(self, ordered_names: list[str]):
        """Update game priorities based on the given order."""
        for i, name in enumerate(ordered_names):
            await self._db.execute(
                "UPDATE tracked_games SET priority = ? WHERE game_name = ? COLLATE NOCASE",
                (i, name),
            )
        await self._db.commit()

    # ── Campaigns ──────────────────────────────────────────────────

    async def upsert_campaign(self, campaign) -> bool:
        """Insert or update a campaign. Returns True if it's newly discovered."""
        # Check if campaign already exists
        cursor = await self._db.execute(
            "SELECT id FROM campaigns WHERE id = ?", (campaign.id,)
        )
        existing = await cursor.fetchone()
        is_new = existing is None

        await self._db.execute(
            """INSERT INTO campaigns (id, name, game_id, game_name, status, start_at, end_at, details_url)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   name = excluded.name,
                   status = excluded.status,
                   end_at = excluded.end_at
            """,
            (
                campaign.id,
                campaign.name,
                campaign.game_id,
                campaign.game_display_name or campaign.game_name,
                campaign.status,
                campaign.start_at.isoformat() if campaign.start_at else None,
                campaign.end_at.isoformat() if campaign.end_at else None,
                campaign.details_url,
            ),
        )

        # Upsert drops
        for drop in campaign.drops:
            benefit_name = drop.benefits[0].name if drop.benefits else ""
            benefit_image = drop.benefits[0].image_url if drop.benefits else ""
            await self._db.execute(
                """INSERT INTO drops (id, campaign_id, name, required_minutes, benefit_name, benefit_image_url)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       name = excluded.name,
                       required_minutes = excluded.required_minutes,
                       benefit_name = excluded.benefit_name,
                       benefit_image_url = excluded.benefit_image_url
                """,
                (drop.id, campaign.id, drop.name, drop.required_minutes, benefit_name, benefit_image),
            )

        await self._db.commit()
        return is_new

    async def mark_campaign_notified(self, campaign_id: str):
        """Mark a campaign as having been notified about."""
        await self._db.execute(
            "UPDATE campaigns SET notified = 1 WHERE id = ?", (campaign_id,)
        )
        await self._db.commit()

    async def get_unnotified_campaigns(self) -> list[dict]:
        """Get campaigns that haven't been notified about yet."""
        cursor = await self._db.execute(
            "SELECT * FROM campaigns WHERE notified = 0 AND status = 'ACTIVE'"
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # ── Drop Progress ──────────────────────────────────────────────

    async def update_drop_progress(
        self,
        drop_id: str,
        campaign_id: str,
        minutes_watched: int,
        is_complete: bool = False,
        is_claimed: bool = False,
        drop_instance_id: str | None = None,
    ):
        """Update progress for a specific drop."""
        await self._db.execute(
            """INSERT INTO drop_progress (drop_id, campaign_id, minutes_watched, is_complete, is_claimed, drop_instance_id, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(drop_id) DO UPDATE SET
                   minutes_watched = excluded.minutes_watched,
                   is_complete = excluded.is_complete,
                   is_claimed = excluded.is_claimed,
                   drop_instance_id = COALESCE(excluded.drop_instance_id, drop_instance_id),
                   updated_at = datetime('now')
            """,
            (drop_id, campaign_id, minutes_watched, int(is_complete), int(is_claimed), drop_instance_id),
        )
        await self._db.commit()

    async def get_drop_progress(self, drop_id: str) -> dict | None:
        """Get progress for a specific drop."""
        cursor = await self._db.execute(
            "SELECT * FROM drop_progress WHERE drop_id = ?", (drop_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_claimable_drops(self) -> list[dict]:
        """Get drops that are complete but not yet claimed."""
        cursor = await self._db.execute(
            """SELECT dp.*, d.name as drop_name, d.benefit_name, c.name as campaign_name, c.game_name
               FROM drop_progress dp
               JOIN drops d ON dp.drop_id = d.id
               JOIN campaigns c ON dp.campaign_id = c.id
               WHERE dp.is_complete = 1 AND dp.is_claimed = 0 AND dp.drop_instance_id IS NOT NULL
            """
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # ── Watch History ──────────────────────────────────────────────

    async def start_watch_session(
        self, channel_login: str, channel_name: str,
        game_name: str, campaign_id: str | None = None, drop_id: str | None = None,
    ) -> int:
        """Record the start of a watch session. Returns the session ID."""
        cursor = await self._db.execute(
            """INSERT INTO watch_history (campaign_id, drop_id, channel_login, channel_name, game_name)
               VALUES (?, ?, ?, ?, ?)""",
            (campaign_id, drop_id, channel_login, channel_name, game_name),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def end_watch_session(
        self, session_id: int, minutes_watched: int, reason: str = "completed"
    ):
        """Record the end of a watch session."""
        await self._db.execute(
            """UPDATE watch_history SET ended_at = datetime('now'), minutes_watched = ?, reason_ended = ?
               WHERE id = ?""",
            (minutes_watched, reason, session_id),
        )
        await self._db.commit()

    async def get_watch_history(self, limit: int = 50) -> list[dict]:
        """Get recent watch history."""
        cursor = await self._db.execute(
            "SELECT * FROM watch_history ORDER BY started_at DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # ── Drop History ───────────────────────────────────────────────

    async def record_earned_drop(
        self, drop_name: str, campaign_name: str, game_name: str,
        benefit_name: str = "", benefit_image_url: str = "",
    ):
        """Record a drop that was earned."""
        await self._db.execute(
            """INSERT INTO drop_history (drop_name, campaign_name, game_name, benefit_name, benefit_image_url)
               VALUES (?, ?, ?, ?, ?)""",
            (drop_name, campaign_name, game_name, benefit_name, benefit_image_url),
        )
        await self._db.commit()

    async def record_claimed_drop(self, drop_name: str):
        """Record that a drop was claimed."""
        await self._db.execute(
            """UPDATE drop_history SET claimed_at = datetime('now')
               WHERE id = (
                   SELECT id FROM drop_history
                   WHERE drop_name = ? AND claimed_at IS NULL
                   ORDER BY earned_at DESC LIMIT 1
               )""",
            (drop_name,),
        )
        await self._db.commit()

    async def get_drop_history(self, limit: int = 100) -> list[dict]:
        """Get drop earn/claim history."""
        cursor = await self._db.execute(
            "SELECT * FROM drop_history ORDER BY earned_at DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # ── Notification Settings ──────────────────────────────────────

    async def save_notification_config(self, notif_type: str, config: str, enabled: bool = True):
        """Save notification configuration (email or discord)."""
        await self._db.execute(
            """INSERT INTO notification_settings (type, enabled, config)
               VALUES (?, ?, ?)
               ON CONFLICT(type) DO UPDATE SET enabled = excluded.enabled, config = excluded.config
            """,
            (notif_type, int(enabled), config),
        )
        await self._db.commit()

    async def get_notification_config(self, notif_type: str) -> dict | None:
        """Get notification configuration."""
        cursor = await self._db.execute(
            "SELECT * FROM notification_settings WHERE type = ?", (notif_type,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_all_notification_configs(self) -> list[dict]:
        """Get all notification configurations."""
        cursor = await self._db.execute("SELECT * FROM notification_settings")
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
