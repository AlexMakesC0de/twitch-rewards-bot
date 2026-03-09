"""
Twitch Helix API Client

Official Twitch API for streams, games, and user data.
Used alongside the GQL client for operations that the official API supports well.
"""

import logging
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

HELIX_BASE = "https://api.twitch.tv/helix"


class TwitchHelix:
    """Client for the official Twitch Helix API."""

    def __init__(self, auth):
        self.auth = auth
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request(
        self, method: str, endpoint: str, params: dict | None = None, json_data: dict | None = None
    ) -> dict | None:
        """Make an authenticated request to the Helix API."""
        await self.auth.ensure_valid_token()
        headers = self.auth.get_headers()

        url = f"{HELIX_BASE}/{endpoint}"
        session = await self._get_session()

        try:
            async with session.request(method, url, params=params, json=json_data, headers=headers) as resp:
                if resp.status == 200:
                    return await resp.json()
                elif resp.status == 401:
                    logger.warning("Unauthorized — refreshing token...")
                    if await self.auth.refresh():
                        headers = self.auth.get_headers()
                        async with session.request(method, url, params=params, json=json_data, headers=headers) as retry_resp:
                            if retry_resp.status == 200:
                                return await retry_resp.json()
                    logger.error("Failed to refresh token.")
                    return None
                else:
                    text = await resp.text()
                    logger.error("Helix %s %s failed (%d): %s", method, endpoint, resp.status, text[:300])
                    return None
        except aiohttp.ClientError as e:
            logger.error("Helix request error: %s", e)
            return None

    # ── Games ──────────────────────────────────────────────────────

    async def search_games(self, query: str) -> list[dict]:
        """Search for games by name."""
        data = await self._request("GET", "search/categories", params={"query": query, "first": 20})
        if not data:
            return []
        return [
            {
                "id": g["id"],
                "name": g["name"],
                "box_art_url": g.get("box_art_url", ""),
            }
            for g in data.get("data", [])
        ]

    async def get_game_by_name(self, name: str) -> dict | None:
        """Get a specific game by exact name."""
        data = await self._request("GET", "games", params={"name": name})
        if data and data.get("data"):
            g = data["data"][0]
            return {
                "id": g["id"],
                "name": g["name"],
                "box_art_url": g.get("box_art_url", ""),
            }
        return None

    async def get_game_by_id(self, game_id: str) -> dict | None:
        """Get a game by its Twitch ID."""
        data = await self._request("GET", "games", params={"id": game_id})
        if data and data.get("data"):
            g = data["data"][0]
            return {
                "id": g["id"],
                "name": g["name"],
                "box_art_url": g.get("box_art_url", ""),
            }
        return None

    # ── Streams ────────────────────────────────────────────────────

    async def get_streams(
        self,
        game_id: str | None = None,
        user_login: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Get live streams, optionally filtered by game or user."""
        params: dict[str, Any] = {"first": min(limit, 100)}
        if game_id:
            params["game_id"] = game_id
        if user_login:
            params["user_login"] = user_login

        data = await self._request("GET", "streams", params=params)
        if not data:
            return []

        return [
            {
                "id": s["id"],
                "user_id": s["user_id"],
                "user_login": s["user_login"],
                "user_name": s["user_name"],
                "game_id": s.get("game_id", ""),
                "game_name": s.get("game_name", ""),
                "title": s.get("title", ""),
                "viewer_count": s.get("viewer_count", 0),
                "is_live": s["type"] == "live",
                "started_at": s.get("started_at", ""),
                "tags": s.get("tags", []),
            }
            for s in data.get("data", [])
            if s.get("type") == "live"
        ]

    async def is_stream_live(self, user_login: str) -> bool:
        """Check if a specific channel is currently live."""
        streams = await self.get_streams(user_login=user_login, limit=1)
        return len(streams) > 0

    # ── Users ──────────────────────────────────────────────────────

    async def get_user(self, login: str | None = None) -> dict | None:
        """Get user info. If no login specified, returns the authenticated user."""
        params = {}
        if login:
            params["login"] = login
        data = await self._request("GET", "users", params=params)
        if data and data.get("data"):
            u = data["data"][0]
            return {
                "id": u["id"],
                "login": u["login"],
                "display_name": u["display_name"],
                "email": u.get("email"),
                "profile_image_url": u.get("profile_image_url", ""),
            }
        return None

    # ── Drops (Official — limited) ─────────────────────────────────

    async def get_drops_entitlements(
        self, status: str = "CLAIMED", limit: int = 50
    ) -> list[dict]:
        """
        Get drop entitlements (official API — limited info).
        Status: CLAIMED, FULFILLED
        """
        params = {"first": limit}
        if status:
            params["fulfillment_status"] = status

        data = await self._request("GET", "entitlements/drops", params=params)
        if not data:
            return []

        return [
            {
                "id": e["id"],
                "benefit_id": e.get("benefit_id", ""),
                "timestamp": e.get("timestamp", ""),
                "user_id": e.get("user_id", ""),
                "game_id": e.get("game_id", ""),
                "status": e.get("fulfillment_status", ""),
            }
            for e in data.get("data", [])
        ]
