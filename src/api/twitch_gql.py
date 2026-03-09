"""
Twitch GraphQL API Client

Uses Twitch's internal GraphQL API to interact with the Drops system.
This provides access to drop campaigns, progress tracking, claiming, and
minute-watched events that the official Helix API does not expose.
"""

import asyncio
import base64
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

GQL_URL = "https://gql.twitch.tv/gql"


# ── Data Models ────────────────────────────────────────────────────

@dataclass
class DropBenefit:
    """A single reward within a drop."""
    id: str
    name: str
    image_url: str
    game_name: str = ""


@dataclass
class TimeBasedDrop:
    """A time-based drop within a campaign."""
    id: str
    name: str
    required_minutes: int
    current_minutes: int = 0
    is_claimed: bool = False
    drop_instance_id: str | None = None
    benefits: list[DropBenefit] = field(default_factory=list)

    @property
    def progress_percent(self) -> float:
        if self.required_minutes <= 0:
            return 100.0
        return min(100.0, (self.current_minutes / self.required_minutes) * 100)

    @property
    def minutes_remaining(self) -> int:
        return max(0, self.required_minutes - self.current_minutes)

    @property
    def is_complete(self) -> bool:
        return self.current_minutes >= self.required_minutes


@dataclass
class DropCampaign:
    """A Twitch Drop campaign for a game."""
    id: str
    name: str
    game_id: str
    game_name: str
    game_display_name: str = ""
    game_box_art_url: str = ""
    status: str = ""
    start_at: datetime | None = None
    end_at: datetime | None = None
    details_url: str = ""
    account_link_url: str = ""
    drops: list[TimeBasedDrop] = field(default_factory=list)

    @property
    def is_active(self) -> bool:
        now = datetime.now(timezone.utc)
        if self.start_at and self.end_at:
            return self.start_at <= now <= self.end_at and self.status == "ACTIVE"
        return self.status == "ACTIVE"

    @property
    def total_required_minutes(self) -> int:
        return sum(d.required_minutes for d in self.drops)

    @property
    def total_earned_minutes(self) -> int:
        return sum(d.current_minutes for d in self.drops)


# ── GraphQL Queries ────────────────────────────────────────────────

QUERY_CAMPAIGNS = """
query ViewerDropsDashboard {
    currentUser {
        dropCampaigns {
            id
            name
            status
            startAt
            endAt
            detailsURL
            accountLinkURL
            game {
                id
                displayName
                name
                boxArtURL(width: 285, height: 380)
            }
            timeBasedDrops {
                id
                name
                requiredMinutesWatched
                benefitEdges {
                    benefit {
                        id
                        name
                        imageAssetURL
                        game {
                            name
                        }
                    }
                }
            }
        }
    }
}
"""

QUERY_INVENTORY = """
query Inventory {
    currentUser {
        inventory {
            dropCampaignsInProgress {
                id
                name
                status
                game {
                    id
                    displayName
                    name
                }
                timeBasedDrops {
                    id
                    name
                    requiredMinutesWatched
                    self {
                        currentMinutesWatched
                        dropInstanceID
                        isClaimed
                    }
                    benefitEdges {
                        benefit {
                            id
                            name
                            imageAssetURL
                        }
                    }
                }
            }
            gameEventDrops {
                id
                name
                totalCount
                imageURL
                game {
                    displayName
                }
                lastAwardedAt
            }
        }
    }
}
"""

QUERY_CLAIM_DROP = """
mutation DropsPage_ClaimDropRewards($input: ClaimDropRewardsInput!) {
    claimDropRewards(input: $input) {
        status
        isUserAccountConnected
    }
}
"""

QUERY_ACTIVE_STREAMS = """
query DirectoryPage_Game($name: String!, $limit: Int, $cursor: Cursor) {
    game(name: $name) {
        id
        displayName
        streams(first: $limit, after: $cursor, options: {
            tags: [],
            sort: VIEWER_COUNT
        }) {
            edges {
                node {
                    id
                    broadcaster {
                        id
                        login
                        displayName
                    }
                    viewersCount
                    title
                    tags {
                        localizedName
                    }
                }
                cursor
            }
        }
    }
}
"""

QUERY_STREAM_PLAYBACK = """
query PlaybackAccessToken($login: String!) {
    streamPlaybackAccessToken(
        channelName: $login,
        params: {
            platform: "web",
            playerBackend: "mediaplayer",
            playerType: "site"
        }
    ) {
        value
        signature
    }
}
"""

QUERY_GAME_DROPS = """
query DropCampaignDetails($channelLogin: String!, $dropID: String!) {
    channel(name: $channelLogin) {
        id
        viewerDropCampaign(id: $dropID) {
            id
            name
            status
            timeBasedDrops {
                id
                name
                requiredMinutesWatched
                self {
                    currentMinutesWatched
                    dropInstanceID
                    isClaimed
                }
            }
        }
    }
}
"""

QUERY_DROPS_ENABLED_STREAMS = """
query DropsHighlightService_AvailableDrops($channelID: ID!) {
    channel(id: $channelID) {
        id
        self {
            availableDrops {
                id
                campaign {
                    id
                    name
                boxArtURL(width: 285, height: 380)
            }
        }
    }
}
"""


QUERY_SEARCH_GAMES = """
query SearchCategories($query: String!) {
    searchCategories(query: $query, first: 20) {
        edges {
            node {
                id
                name
                displayName
                boxArtURL(width: 285, height: 380)
            }
        }
    }
}
"""

QUERY_USER_STREAM = """
query GetUserStream($login: String!) {
    user(login: $login) {
        id
        login
        displayName
        stream {
            id
            viewersCount
            game {
                displayName
            }
        }
    }
}
"""


class TwitchGQL:
    """Client for Twitch's GraphQL API (drops, campaigns, watching)."""

    def __init__(self, auth):
        """
        Args:
            auth: TwitchAuth instance with valid tokens.
        """
        self.auth = auth
        self._session: aiohttp.ClientSession | None = None
        self._spade_urls: dict[str, str] = {}

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _gql_request(
        self,
        query: str,
        variables: dict | None = None,
        operation_name: str | None = None,
    ) -> dict | None:
        """Send a GraphQL request to Twitch."""
        await self.auth.ensure_valid_token()

        headers = self.auth.get_gql_headers()
        headers["Content-Type"] = "application/json"

        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables
        if operation_name:
            payload["operationName"] = operation_name

        session = await self._get_session()
        try:
            async with session.post(GQL_URL, json=payload, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if "errors" in data:
                        for err in data["errors"]:
                            logger.warning("GQL error: %s", err.get("message", err))
                    return data.get("data")
                else:
                    text = await resp.text()
                    logger.error("GQL request failed (%d): %s", resp.status, text[:500])
                    return None
        except aiohttp.ClientError as e:
            logger.error("GQL request error: %s", e)
            return None

    # ── Drop Campaigns ─────────────────────────────────────────────

    async def get_drop_campaigns(self) -> list[DropCampaign]:
        """Fetch all available drop campaigns for the authenticated user."""
        data = await self._gql_request(QUERY_CAMPAIGNS)
        if not data:
            return []

        campaigns = []
        raw_campaigns = (
            data.get("currentUser", {}).get("dropCampaigns") or []
        )

        for raw in raw_campaigns:
            game = raw.get("game") or {}
            campaign = DropCampaign(
                id=raw["id"],
                name=raw.get("name", ""),
                game_id=game.get("id", ""),
                game_name=game.get("name", ""),
                game_display_name=game.get("displayName", ""),
                game_box_art_url=game.get("boxArtURL", ""),
                status=raw.get("status", ""),
                start_at=_parse_datetime(raw.get("startAt")),
                end_at=_parse_datetime(raw.get("endAt")),
                details_url=raw.get("detailsURL", ""),
                account_link_url=raw.get("accountLinkURL", ""),
            )

            for drop_raw in raw.get("timeBasedDrops") or []:
                benefits = []
                for edge in drop_raw.get("benefitEdges") or []:
                    b = edge.get("benefit", {})
                    benefits.append(DropBenefit(
                        id=b.get("id", ""),
                        name=b.get("name", ""),
                        image_url=b.get("imageAssetURL", ""),
                        game_name=b.get("game", {}).get("name", ""),
                    ))

                drop = TimeBasedDrop(
                    id=drop_raw["id"],
                    name=drop_raw.get("name", ""),
                    required_minutes=drop_raw.get("requiredMinutesWatched", 0),
                    benefits=benefits,
                )
                campaign.drops.append(drop)

            campaigns.append(campaign)

        logger.info("Found %d drop campaigns", len(campaigns))
        return campaigns

    async def get_active_campaigns(self, game_names: list[str] | None = None) -> list[DropCampaign]:
        """Get only active campaigns, optionally filtered by game names."""
        campaigns = await self.get_drop_campaigns()
        active = [c for c in campaigns if c.is_active]

        if game_names:
            normalized = [g.lower() for g in game_names]
            active = [
                c for c in active
                if c.game_name.lower() in normalized
                or c.game_display_name.lower() in normalized
            ]

        return active

    # ── Inventory & Progress ───────────────────────────────────────

    async def get_inventory(self) -> dict:
        """Fetch current drop inventory and in-progress campaigns."""
        data = await self._gql_request(QUERY_INVENTORY)
        if not data:
            return {"in_progress": [], "completed": []}

        inventory = data.get("currentUser", {}).get("inventory", {})
        in_progress = []

        for raw_campaign in inventory.get("dropCampaignsInProgress") or []:
            game = raw_campaign.get("game", {})

            for drop_raw in raw_campaign.get("timeBasedDrops") or []:
                self_data = drop_raw.get("self") or {}
                benefits = []
                for edge in drop_raw.get("benefitEdges") or []:
                    b = edge.get("benefit", {})
                    benefits.append(DropBenefit(
                        id=b.get("id", ""),
                        name=b.get("name", ""),
                        image_url=b.get("imageAssetURL", ""),
                    ))

                drop = TimeBasedDrop(
                    id=drop_raw["id"],
                    name=drop_raw.get("name", ""),
                    required_minutes=drop_raw.get("requiredMinutesWatched", 0),
                    current_minutes=self_data.get("currentMinutesWatched", 0),
                    is_claimed=self_data.get("isClaimed", False),
                    drop_instance_id=self_data.get("dropInstanceID"),
                    benefits=benefits,
                )
                in_progress.append({
                    "campaign_id": raw_campaign.get("id", ""),
                    "campaign_name": raw_campaign.get("name", ""),
                    "game_name": game.get("displayName", ""),
                    "drop": drop,
                })

        completed = []
        for item in inventory.get("gameEventDrops") or []:
            completed.append({
                "id": item.get("id", ""),
                "name": item.get("name", ""),
                "game": (item.get("game") or {}).get("displayName", ""),
                "count": item.get("totalCount", 0),
                "image_url": item.get("imageURL", ""),
                "last_awarded": item.get("lastAwardedAt"),
            })

        return {"in_progress": in_progress, "completed": completed}

    async def claim_drop(self, drop_instance_id: str) -> bool:
        """Claim a completed drop reward."""
        data = await self._gql_request(
            QUERY_CLAIM_DROP,
            variables={"input": {"dropInstanceID": drop_instance_id}},
        )
        if data:
            result = data.get("claimDropRewards", {})
            status = result.get("status")
            if status == "ELIGIBLE_FOR_ALL":
                logger.info("Drop claimed successfully!")
                return True
            else:
                logger.warning("Claim status: %s", status)
                connected = result.get("isUserAccountConnected")
                if not connected:
                    logger.warning(
                        "Your Twitch account may not be linked to the game's platform. "
                        "Check the campaign's link requirements."
                    )
        return False

    # ── Stream Discovery ───────────────────────────────────────────

    async def get_game_streams(
        self, game_name: str, limit: int = 30
    ) -> list[dict]:
        """Get live streams for a game (sorted by viewer count)."""
        data = await self._gql_request(
            QUERY_ACTIVE_STREAMS,
            variables={"name": game_name, "limit": limit},
        )
        if not data or not data.get("game"):
            logger.warning("No streams found for game: %s", game_name)
            return []

        streams = []
        for edge in data["game"].get("streams", {}).get("edges", []):
            node = edge.get("node", {})
            broadcaster = node.get("broadcaster", {})
            tags = [t.get("localizedName", "") for t in node.get("tags") or []]

            streams.append({
                "stream_id": node.get("id", ""),
                "broadcaster_id": broadcaster.get("id", ""),
                "broadcaster_login": broadcaster.get("login", ""),
                "broadcaster_name": broadcaster.get("displayName", ""),
                "viewers": node.get("viewersCount", 0),
                "title": node.get("title", ""),
                "tags": tags,
                "has_drops": "drops" in " ".join(tags).lower() or "drop" in (node.get("title") or "").lower(),
            })

        return streams

    async def get_drops_enabled_streams(self, game_name: str) -> list[dict]:
        """Get streams that have drops enabled for a game."""
        all_streams = await self.get_game_streams(game_name, limit=50)
        # Filter for streams likely to have drops enabled
        # We check the tags and title for drop indicators
        drops_streams = [s for s in all_streams if s.get("has_drops")]
        if not drops_streams:
            # If no tagged streams found, return top streams as fallback
            # (most top streamers for a game with active campaigns have drops on)
            logger.info(
                "No explicitly tagged drops streams found for %s, "
                "returning top streams as candidates.", game_name
            )
            return all_streams[:10]
        return drops_streams

    # ── Playback / Watching ────────────────────────────────────────

    async def get_stream_playback_token(self, channel_login: str) -> dict | None:
        """Get a playback access token for a channel (needed for watching)."""
        data = await self._gql_request(
            QUERY_STREAM_PLAYBACK,
            variables={"login": channel_login},
        )
        if data and data.get("streamPlaybackAccessToken"):
            token_data = data["streamPlaybackAccessToken"]
            return {
                "value": token_data.get("value", ""),
                "signature": token_data.get("signature", ""),
            }
        return None

    async def _get_spade_url(self, channel_login: str) -> str | None:
        """Extract the spade tracking URL from a channel's page."""
        session = await self._get_session()
        url = f"https://www.twitch.tv/{channel_login}"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
            ),
        }

        SPADE_RE = re.compile(
            r'"(?:beacon|spade)_?url":\s*"(https://[.\w\-/]+\.ts(?:\?allow_stream=true)?)"',
            re.I,
        )
        SETTINGS_RE = re.compile(
            r'src="(https://[\w.]+/config/settings\.[0-9a-f]{32}\.js)"',
            re.I,
        )

        try:
            async with session.get(url, headers=headers) as resp:
                html = await resp.text()

            match = SPADE_RE.search(html)
            if match:
                return match.group(1)

            match = SETTINGS_RE.search(html)
            if match:
                async with session.get(match.group(1), headers=headers) as resp2:
                    js_text = await resp2.text()
                match = SPADE_RE.search(js_text)
                if match:
                    return match.group(1)
        except Exception as e:
            logger.error("Failed to get spade URL for %s: %s", channel_login, e)

        return None

    async def send_minute_watched(
        self,
        channel_login: str,
        channel_id: str,
        broadcast_id: str,
    ) -> bool:
        """
        Send a 'minute watched' event to Twitch.
        This is how Twitch tracks viewing time for drops.
        """
        await self.auth.ensure_valid_token()

        # Get spade URL (cached per channel)
        if channel_login not in self._spade_urls:
            spade_url = await self._get_spade_url(channel_login)
            if spade_url:
                self._spade_urls[channel_login] = spade_url
            else:
                logger.warning("Could not find spade URL for %s", channel_login)
                return False

        spade_url = self._spade_urls[channel_login]

        payload = [
            {
                "event": "minute-watched",
                "properties": {
                    "broadcast_id": str(broadcast_id),
                    "channel_id": str(channel_id),
                    "channel": channel_login,
                    "hidden": False,
                    "live": True,
                    "location": "channel",
                    "logged_in": True,
                    "muted": False,
                    "player": "site",
                    "user_id": int(self.auth.user_id) if self.auth.user_id else 0,
                }
            }
        ]

        encoded = base64.b64encode(
            json.dumps(payload, separators=(",", ":")).encode()
        ).decode()

        session = await self._get_session()
        try:
            async with session.post(spade_url, data={"data": encoded}) as resp:
                if resp.status == 204:
                    logger.debug(
                        "Minute-watched sent for %s (broadcast: %s)",
                        channel_login, broadcast_id,
                    )
                    return True
                else:
                    text = await resp.text()
                    logger.warning(
                        "Minute-watched failed (%d) for %s: %s",
                        resp.status, channel_login, text[:200],
                    )
                    # Clear cached URL so we refetch next time
                    self._spade_urls.pop(channel_login, None)
                    return False
        except aiohttp.ClientError as e:
            logger.error("Minute-watched error for %s: %s", channel_login, e)
            self._spade_urls.pop(channel_login, None)
            return False


    # ── Game Search (replaces Helix) ────────────────────────────────

    async def search_games(self, query: str) -> list[dict]:
        """Search for games by name using GQL."""
        data = await self._gql_request(
            QUERY_SEARCH_GAMES,
            variables={"query": query},
        )
        if not data:
            return []

        results = []
        for edge in data.get("searchCategories", {}).get("edges", []):
            node = edge.get("node", {})
            results.append({
                "id": node.get("id", ""),
                "name": node.get("displayName") or node.get("name", ""),
                "box_art_url": node.get("boxArtURL", ""),
            })
        return results

    async def is_stream_live(self, login: str) -> bool:
        """Check if a specific channel is currently live using GQL."""
        data = await self._gql_request(
            QUERY_USER_STREAM,
            variables={"login": login},
        )
        if not data:
            return False
        user = data.get("user")
        return user is not None and user.get("stream") is not None


# ── Helpers ────────────────────────────────────────────────────────

def _parse_datetime(s: str | None) -> datetime | None:
    """Parse an ISO datetime string from Twitch."""
    if not s:
        return None
    try:
        # Handle various formats from Twitch
        s = s.replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except ValueError:
        logger.debug("Failed to parse datetime: %s", s)
        return None
