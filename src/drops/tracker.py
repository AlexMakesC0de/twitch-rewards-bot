"""
Drop Campaign Tracker

Discovers and monitors Twitch Drop campaigns for tracked games.
Detects new campaigns and triggers notifications.
"""

import asyncio
import logging
from datetime import datetime, timezone

from ..api.twitch_gql import TwitchGQL, DropCampaign
from ..storage.database import Database

logger = logging.getLogger(__name__)


class DropTracker:
    """Monitors Twitch for drop campaigns on tracked games."""

    def __init__(self, gql: TwitchGQL, db: Database):
        self.gql = gql
        self.db = db
        self._known_campaign_ids: set[str] = set()

    async def check_for_drops(self) -> dict:
        """
        Check for available drops on all tracked games.

        Returns:
            {
                "active_campaigns": [...],
                "new_campaigns": [...],   # newly discovered
                "claimable_drops": [...], # ready to claim
            }
        """
        game_names = await self.db.get_tracked_game_names()
        if not game_names:
            logger.info("No games being tracked. Add games with 'add-game' command.")
            return {"active_campaigns": [], "new_campaigns": [], "claimable_drops": []}

        logger.info("Checking drops for %d tracked games: %s", len(game_names), ", ".join(game_names))

        # Fetch all campaigns
        all_campaigns = await self.gql.get_drop_campaigns()
        active_campaigns = [c for c in all_campaigns if c.is_active]

        # Filter to tracked games
        # Build a set of tracked game names (lowercase) for exact matching
        tracked_set = {name.lower() for name in game_names}

        # Also build a map from twitch game IDs for precise matching
        tracked_games_data = await self.db.get_tracked_games()
        tracked_ids = {g["twitch_game_id"] for g in tracked_games_data if g.get("twitch_game_id")}

        tracked_campaigns = []
        for campaign in active_campaigns:
            # Prefer matching by game ID (most precise)
            if campaign.game_id and campaign.game_id in tracked_ids:
                tracked_campaigns.append(campaign)
                continue
            # Fallback to exact name match
            if (
                campaign.game_name.lower() in tracked_set
                or campaign.game_display_name.lower() in tracked_set
            ):
                tracked_campaigns.append(campaign)

        # Detect new campaigns
        new_campaigns = []
        for campaign in tracked_campaigns:
            is_new = await self.db.upsert_campaign(campaign)
            if is_new:
                new_campaigns.append(campaign)
                logger.info(
                    "🆕 New campaign discovered: %s (%s)",
                    campaign.name, campaign.game_display_name,
                )

        # Check inventory for progress
        inventory = await self.gql.get_inventory()

        # Update progress in DB
        for item in inventory.get("in_progress", []):
            drop = item["drop"]
            await self.db.update_drop_progress(
                drop_id=drop.id,
                campaign_id=item["campaign_id"],
                minutes_watched=drop.current_minutes,
                is_complete=drop.is_complete,
                is_claimed=drop.is_claimed,
                drop_instance_id=drop.drop_instance_id,
            )

        # Find claimable drops
        claimable = await self.db.get_claimable_drops()

        logger.info(
            "Found %d active campaigns (%d tracked), %d new, %d claimable",
            len(active_campaigns), len(tracked_campaigns),
            len(new_campaigns), len(claimable),
        )

        return {
            "active_campaigns": tracked_campaigns,
            "new_campaigns": new_campaigns,
            "claimable_drops": claimable,
            "all_active_campaigns": active_campaigns,
            "inventory": inventory,
        }

    async def claim_all_drops(self) -> list[dict]:
        """Claim all completed but unclaimed drops."""
        inventory = await self.gql.get_inventory()
        claimed = []

        for item in inventory.get("in_progress", []):
            drop = item["drop"]
            if drop.is_complete and not drop.is_claimed and drop.drop_instance_id:
                logger.info("Claiming drop: %s", drop.name)
                success = await self.gql.claim_drop(drop.drop_instance_id)
                if success:
                    claimed.append({
                        "name": drop.name,
                        "campaign": item["campaign_name"],
                        "game": item["game_name"],
                    })
                    await self.db.update_drop_progress(
                        drop_id=drop.id,
                        campaign_id=item["campaign_id"],
                        minutes_watched=drop.current_minutes,
                        is_complete=True,
                        is_claimed=True,
                        drop_instance_id=drop.drop_instance_id,
                    )
                    await self.db.record_earned_drop(
                        drop_name=drop.name,
                        campaign_name=item["campaign_name"],
                        game_name=item["game_name"],
                        benefit_name=drop.benefits[0].name if drop.benefits else "",
                        benefit_image_url=drop.benefits[0].image_url if drop.benefits else "",
                    )
                    await self.db.record_claimed_drop(drop.name)
                    await asyncio.sleep(1)  # Rate limit

        if claimed:
            logger.info("Claimed %d drops!", len(claimed))
        else:
            logger.info("No drops to claim.")

        return claimed

    async def get_watchable_drops(self) -> list[dict]:
        """
        Get drops that can be earned by watching, sorted by priority.
        Returns drops that are in-progress or not yet started.
        """
        result = await self.check_for_drops()
        campaigns = result["active_campaigns"]
        inventory = result.get("inventory", {})

        # Build a map of current progress from in-progress inventory
        progress_map = {}
        for item in inventory.get("in_progress", []):
            drop = item["drop"]
            progress_map[drop.id] = {
                "current_minutes": drop.current_minutes,
                "is_complete": drop.is_complete,
                "is_claimed": drop.is_claimed,
            }

        # Build a set of completed drop names from gameEventDrops
        # These are drops that have been earned/claimed and moved out of in_progress
        completed_names = set()
        for item in inventory.get("completed", []):
            name = (item.get("name") or "").strip()
            if name:
                completed_names.add(name.lower())

        # Build game priority map from DB
        tracked_games = await self.db.get_tracked_games()
        game_priority = {}
        for g in tracked_games:
            name = (g.get("game_name") or "").lower()
            game_priority[name] = g.get("priority", 0)

        watchable = []
        for campaign in campaigns:
            for drop in campaign.drops:
                # Skip sub-only drops (required_minutes == 0 means it's not time-based)
                if drop.required_minutes <= 0:
                    continue

                progress = progress_map.get(drop.id, {})
                current = progress.get("current_minutes", 0)
                is_complete = progress.get("is_complete", False)
                is_claimed = progress.get("is_claimed", False)

                # Also check if drop name matches a completed drop
                drop_name_normalized = (drop.name or "").strip().lower()
                if drop_name_normalized and drop_name_normalized in completed_names:
                    is_complete = True

                if not is_claimed and not is_complete:
                    watchable.append({
                        "campaign": campaign,
                        "drop": drop,
                        "current_minutes": current,
                        "remaining_minutes": max(0, drop.required_minutes - current),
                        "game_name": campaign.game_display_name or campaign.game_name,
                    })

        # Sort: by game priority first, then by remaining minutes (closest to completion)
        watchable.sort(key=lambda x: (
            game_priority.get(x["game_name"].lower(), 999),
            x["remaining_minutes"],
        ))
        return watchable

    async def find_streams_for_campaign(self, campaign: DropCampaign) -> list[dict]:
        """Find live streams that qualify for drops on a campaign."""
        game_name = campaign.game_display_name or campaign.game_name
        streams = await self.gql.get_drops_enabled_streams(game_name)
        return streams
