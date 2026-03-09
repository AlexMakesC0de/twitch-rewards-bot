"""
Stream Watcher

Simulates watching Twitch streams by sending periodic minute-watched
events to accumulate drop progress. Handles stream switching,
progress tracking, and auto-claiming.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..api.twitch_gql import TwitchGQL, DropCampaign, TimeBasedDrop
from ..storage.database import Database

logger = logging.getLogger(__name__)


@dataclass
class WatchSession:
    """Represents an active watch session on a channel."""
    channel_login: str
    channel_name: str
    channel_id: str
    stream_id: str
    game_name: str
    campaign: DropCampaign
    drop: TimeBasedDrop
    db_session_id: int | None = None
    started_at: float = field(default_factory=time.time)
    minutes_watched: int = 0
    is_active: bool = True
    last_heartbeat: float = 0


class StreamWatcher:
    """Watches Twitch streams to earn drop rewards."""

    def __init__(
        self,
        gql: TwitchGQL,
        db: Database,
        heartbeat_interval: int = 60,
        auto_claim: bool = True,
        auto_switch: bool = True,
    ):
        self.gql = gql
        self.db = db
        self.heartbeat_interval = heartbeat_interval
        self.auto_claim = auto_claim
        self.auto_switch = auto_switch
        self._active_sessions: dict[str, WatchSession] = {}
        self._stop_event = asyncio.Event()
        self._callbacks: dict[str, list] = {
            "on_progress": [],
            "on_drop_earned": [],
            "on_drop_claimed": [],
            "on_stream_offline": [],
            "on_stream_switch": [],
            "on_error": [],
        }

    def on(self, event: str, callback):
        """Register an event callback."""
        if event in self._callbacks:
            self._callbacks[event].append(callback)

    async def _emit(self, event: str, **kwargs):
        """Emit an event to all registered callbacks."""
        for cb in self._callbacks.get(event, []):
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(**kwargs)
                else:
                    cb(**kwargs)
            except Exception as e:
                logger.error("Error in %s callback: %s", event, e)

    @property
    def active_sessions(self) -> list[WatchSession]:
        return list(self._active_sessions.values())

    async def watch_drop(
        self,
        campaign: DropCampaign,
        drop: TimeBasedDrop,
        preferred_channel: str | None = None,
    ) -> bool:
        """
        Start watching a stream to earn a specific drop.

        Args:
            campaign: The drop campaign
            drop: The specific drop to earn
            preferred_channel: Optional preferred channel to watch

        Returns:
            True if watching started successfully
        """
        game_name = campaign.game_display_name or campaign.game_name

        # Find a stream to watch
        channel = None
        if preferred_channel:
            # Verify the preferred channel is live via GQL
            is_live = await self.gql.is_stream_live(preferred_channel)
            if is_live:
                # Get stream info from the game streams list
                streams = await self.gql.get_game_streams(game_name, limit=50)
                match = next(
                    (s for s in streams if s["broadcaster_login"].lower() == preferred_channel.lower()),
                    None,
                )
                if match:
                    channel = match

        if not channel:
            # Find drops-enabled streams for this game
            streams = await self.gql.get_drops_enabled_streams(game_name)
            if not streams:
                logger.warning("No live streams found for %s", game_name)
                return False
            channel = streams[0]  # Pick highest viewer count

        channel_login = channel["broadcaster_login"]
        channel_name = channel.get("broadcaster_name", channel_login)
        channel_id = channel["broadcaster_id"]
        stream_id = channel.get("stream_id", "")

        # Create watch session
        session = WatchSession(
            channel_login=channel_login,
            channel_name=channel_name,
            channel_id=channel_id,
            stream_id=stream_id,
            game_name=game_name,
            campaign=campaign,
            drop=drop,
        )

        # Record in database
        session.db_session_id = await self.db.start_watch_session(
            channel_login=channel_login,
            channel_name=channel_name,
            game_name=game_name,
            campaign_id=campaign.id,
            drop_id=drop.id,
        )

        self._active_sessions[drop.id] = session

        logger.info(
            "📺 Started watching %s on %s for drop: %s (%d min required)",
            game_name, channel_name, drop.name, drop.required_minutes,
        )

        return True

    async def stop_watching(self, drop_id: str, reason: str = "user_stopped"):
        """Stop watching for a specific drop."""
        session = self._active_sessions.pop(drop_id, None)
        if session:
            session.is_active = False
            if session.db_session_id:
                await self.db.end_watch_session(
                    session.db_session_id, session.minutes_watched, reason
                )
            logger.info(
                "⏹️  Stopped watching %s on %s (%d min watched)",
                session.game_name, session.channel_name, session.minutes_watched,
            )

    async def stop_all(self, reason: str = "shutdown"):
        """Stop all active watch sessions."""
        self._stop_event.set()
        for drop_id in list(self._active_sessions.keys()):
            await self.stop_watching(drop_id, reason)

    async def run_watch_loop(self):
        """
        Main watching loop. Sends heartbeats, checks progress,
        handles stream offline events, and auto-claims drops.
        """
        logger.info("🔄 Watch loop started with %d active sessions", len(self._active_sessions))
        self._stop_event.clear()

        while not self._stop_event.is_set() and self._active_sessions:
            try:
                await self._heartbeat_tick()
                await self._check_progress()

                if self.auto_claim:
                    await self._auto_claim_check()

                # Wait for next interval
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self.heartbeat_interval,
                    )
                    break  # Stop event was set
                except asyncio.TimeoutError:
                    pass  # Normal — interval elapsed

            except Exception as e:
                logger.error("Watch loop error: %s", e)
                await self._emit("on_error", error=str(e))
                await asyncio.sleep(10)

        logger.info("🛑 Watch loop ended.")

    async def _heartbeat_tick(self):
        """Send minute-watched events for all active sessions."""
        for drop_id, session in list(self._active_sessions.items()):
            if not session.is_active:
                continue

            # Check if stream is still live
            is_live = await self.gql.is_stream_live(session.channel_login)
            if not is_live:
                logger.warning(
                    "📴 Stream %s went offline", session.channel_name
                )
                await self._emit(
                    "on_stream_offline",
                    session=session,
                    channel=session.channel_name,
                    game=session.game_name,
                )

                if self.auto_switch:
                    switched = await self._switch_stream(session)
                    if not switched:
                        await self.stop_watching(drop_id, "stream_offline_no_alternative")
                        continue
                else:
                    await self.stop_watching(drop_id, "stream_offline")
                    continue

            # Send minute-watched event
            success = await self.gql.send_minute_watched(
                channel_login=session.channel_login,
                channel_id=session.channel_id,
                broadcast_id=session.stream_id,
            )

            if success:
                session.minutes_watched += 1
                session.last_heartbeat = time.time()

                # Log progress every 5 minutes
                if session.minutes_watched % 5 == 0:
                    drop = session.drop
                    total = drop.current_minutes + session.minutes_watched
                    pct = min(100, (total / drop.required_minutes) * 100) if drop.required_minutes > 0 else 100
                    logger.info(
                        "📊 Progress: %s — %d/%d min (%.1f%%) on %s",
                        drop.name, total, drop.required_minutes, pct,
                        session.channel_name,
                    )
                    await self._emit(
                        "on_progress",
                        session=session,
                        minutes=total,
                        required=drop.required_minutes,
                        percent=pct,
                    )
            else:
                logger.warning("Failed to send heartbeat for %s", session.channel_name)

    async def _check_progress(self):
        """Check actual progress from Twitch inventory."""
        inventory = await self.gql.get_inventory()

        for item in inventory.get("in_progress", []):
            drop = item["drop"]
            session = self._active_sessions.get(drop.id)

            if session:
                # Sync session with real Twitch progress
                session.drop.current_minutes = drop.current_minutes
                session.drop.is_claimed = drop.is_claimed
                session.drop.drop_instance_id = drop.drop_instance_id
                session.minutes_watched = 0  # Reset local counter; real data is in drop

                # Update drop progress in DB
                await self.db.update_drop_progress(
                    drop_id=drop.id,
                    campaign_id=item["campaign_id"],
                    minutes_watched=drop.current_minutes,
                    is_complete=drop.is_complete,
                    is_claimed=drop.is_claimed,
                    drop_instance_id=drop.drop_instance_id,
                )

                if drop.is_complete and not drop.is_claimed:
                    logger.info("🎉 Drop earned: %s!", drop.name)
                    await self._emit(
                        "on_drop_earned",
                        drop=drop,
                        campaign_name=item["campaign_name"],
                        game_name=item["game_name"],
                    )

                    if self.auto_claim and drop.drop_instance_id:
                        success = await self.gql.claim_drop(drop.drop_instance_id)
                        if success:
                            logger.info("✅ Drop claimed: %s", drop.name)
                            await self.db.record_earned_drop(
                                drop_name=drop.name,
                                campaign_name=item["campaign_name"],
                                game_name=item["game_name"],
                                benefit_name=drop.benefits[0].name if drop.benefits else "",
                                benefit_image_url=drop.benefits[0].image_url if drop.benefits else "",
                            )
                            await self.db.record_claimed_drop(drop.name)
                            await self._emit(
                                "on_drop_claimed",
                                drop=drop,
                                campaign_name=item["campaign_name"],
                                game_name=item["game_name"],
                            )

                    await self.stop_watching(drop.id, "drop_earned")

    async def _auto_claim_check(self):
        """Check for and claim any completed drops."""
        claimable = await self.db.get_claimable_drops()
        for drop_info in claimable:
            if drop_info.get("drop_instance_id"):
                success = await self.gql.claim_drop(drop_info["drop_instance_id"])
                if success:
                    logger.info("✅ Auto-claimed: %s", drop_info.get("drop_name"))
                    await self.db.record_claimed_drop(drop_info["drop_name"])

    async def _switch_stream(self, session: WatchSession) -> bool:
        """Switch to a different stream when the current one goes offline."""
        game_name = session.game_name
        streams = await self.gql.get_drops_enabled_streams(game_name)

        # Filter out the current (offline) channel
        streams = [
            s for s in streams
            if s["broadcaster_login"] != session.channel_login
        ]

        if not streams:
            logger.warning("No alternative streams found for %s", game_name)
            return False

        new_stream = streams[0]
        old_channel = session.channel_name

        # End old watch session in DB
        if session.db_session_id:
            await self.db.end_watch_session(
                session.db_session_id, session.minutes_watched, "stream_switch"
            )

        # Update session with new stream
        session.channel_login = new_stream["broadcaster_login"]
        session.channel_name = new_stream.get("broadcaster_name", new_stream["broadcaster_login"])
        session.channel_id = new_stream["broadcaster_id"]
        session.stream_id = new_stream.get("stream_id", "")

        # Start new watch session in DB
        session.db_session_id = await self.db.start_watch_session(
            channel_login=session.channel_login,
            channel_name=session.channel_name,
            game_name=game_name,
            campaign_id=session.campaign.id,
            drop_id=session.drop.id,
        )

        logger.info(
            "🔄 Switched from %s → %s for %s",
            old_channel, session.channel_name, game_name,
        )
        await self._emit(
            "on_stream_switch",
            old_channel=old_channel,
            new_channel=session.channel_name,
            game=game_name,
        )

        return True

    def get_status(self) -> list[dict]:
        """Get current status of all watch sessions."""
        status = []
        for drop_id, session in self._active_sessions.items():
            elapsed = (time.time() - session.started_at) / 60
            drop = session.drop
            # Real Twitch progress + optimistic heartbeats since last sync
            total_watched = drop.current_minutes + session.minutes_watched
            remaining = max(0, drop.required_minutes - total_watched)
            pct = min(100.0, (total_watched / drop.required_minutes) * 100) if drop.required_minutes > 0 else 100.0

            status.append({
                "drop_id": drop_id,
                "drop_name": drop.name,
                "game": session.game_name,
                "channel": session.channel_name,
                "minutes_watched": total_watched,
                "required_minutes": drop.required_minutes,
                "remaining_minutes": remaining,
                "progress_percent": round(pct, 1),
                "session_minutes": round(elapsed, 1),
                "benefits": [b.name for b in drop.benefits],
            })

        return status
