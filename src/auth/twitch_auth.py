"""
Twitch Authentication via Device Code Flow

Uses the OAuth2 Device Code flow with Twitch's Android app client ID.
This produces tokens that work with the Twitch GQL API (which rejects
tokens issued for third-party app client IDs).

Flow:
1. Request a device code from Twitch
2. User visits twitch.tv/activate and enters the code
3. Poll Twitch until the user completes authorization
4. Token is validated and stored
"""

import asyncio
import json
import logging
import time
import webbrowser
from pathlib import Path

import aiohttp

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────

TWITCH_DEVICE_URL = "https://id.twitch.tv/oauth2/device"
TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
TWITCH_VALIDATE_URL = "https://id.twitch.tv/oauth2/validate"
TWITCH_REVOKE_URL = "https://id.twitch.tv/oauth2/revoke"

# Twitch's Android app client ID — the GQL API only accepts tokens
# issued for first-party Twitch client IDs.
CLIENT_ID = "kd1unb4b3q4t58fwlpcbzcbnm76a8fp"

TOKEN_FILE = Path("tokens/twitch_token.json")


# ── Main Auth Class ────────────────────────────────────────────────

class TwitchAuth:
    """Manages Twitch authentication via Device Code Flow."""

    def __init__(self):
        self.client_id: str = CLIENT_ID
        self.access_token: str | None = None
        self.expires_at: float = 0
        self.user_id: str | None = None
        self.username: str | None = None
        # Device code flow state (for web UI polling)
        self._device_code: str | None = None
        self._device_interval: int = 5
        self._device_expires_at: float = 0
        self._device_polling: bool = False

    # ── Token Persistence ──────────────────────────────────────────

    def _save_token(self):
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(json.dumps({
            "access_token": self.access_token,
            "client_id": self.client_id,
            "expires_at": self.expires_at,
            "user_id": self.user_id,
            "username": self.username,
        }, indent=2))

    def _load_token(self) -> bool:
        if not TOKEN_FILE.exists():
            return False
        try:
            data = json.loads(TOKEN_FILE.read_text())
            self.access_token = data.get("access_token")
            self.client_id = data.get("client_id", CLIENT_ID)
            self.expires_at = data.get("expires_at", 0)
            self.user_id = data.get("user_id")
            self.username = data.get("username")
            return self.access_token is not None
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("Failed to load token: %s", e)
            return False

    # ── Device Code Flow ───────────────────────────────────────────

    async def request_device_code(self) -> dict | None:
        """Request a device code from Twitch. Returns {user_code, verification_uri, ...}."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(TWITCH_DEVICE_URL, data={
                    "client_id": self.client_id,
                    "scopes": "",
                }) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        self._device_code = data["device_code"]
                        self._device_interval = data.get("interval", 5)
                        self._device_expires_at = time.time() + data.get("expires_in", 1800)
                        return {
                            "user_code": data["user_code"],
                            "verification_uri": data["verification_uri"],
                        }
                    else:
                        logger.error("Device code request failed: %d", resp.status)
                        return None
        except aiohttp.ClientError as e:
            logger.error("Device code request error: %s", e)
            return None

    async def poll_device_code(self) -> bool:
        """Poll Twitch until the user completes device code authorization."""
        if not self._device_code:
            return False
        if self._device_polling:
            return False
        self._device_polling = True

        try:
            async with aiohttp.ClientSession() as session:
                while time.time() < self._device_expires_at:
                    await asyncio.sleep(self._device_interval)
                    async with session.post(TWITCH_TOKEN_URL, data={
                        "client_id": self.client_id,
                        "device_code": self._device_code,
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    }) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            self.access_token = data["access_token"]
                            expires_in = data.get("expires_in", 14400)
                            self.expires_at = time.time() + expires_in
                            if await self.validate():
                                self._save_token()
                                logger.info("Logged in as: %s (ID: %s)", self.username, self.user_id)
                                return True
                            self.access_token = None
                            return False
                        # 400 = user hasn't entered code yet; keep polling
            logger.warning("Device code expired")
            return False
        finally:
            self._device_code = None
            self._device_polling = False

    # ── Login Flows ────────────────────────────────────────────────

    async def login(self) -> bool:
        """CLI login flow using Device Code."""
        device = await self.request_device_code()
        if not device:
            print("\n  Failed to get device code from Twitch.")
            return False

        uri = device["verification_uri"]
        code = device["user_code"]

        print(f"\n  1. Go to:  {uri}")
        print(f"  2. Enter code:  {code}\n")
        webbrowser.open(uri)
        print("  Waiting for authorization... (Ctrl+C to cancel)\n")

        try:
            return await self.poll_device_code()
        except KeyboardInterrupt:
            print("\n  Cancelled.")
            self._device_code = None
            return False

    async def login_with_token(self, token: str) -> bool:
        """Login with a raw access token string."""
        token = token.strip()
        if not token:
            return False
        self.access_token = token
        self.expires_at = time.time() + 14400

        if await self.validate():
            self._save_token()
            logger.info("Logged in as: %s (ID: %s)", self.username, self.user_id)
            return True

        self.access_token = None
        return False

    # ── Token Validation ───────────────────────────────────────────

    async def validate(self) -> bool:
        """Validate the current access token with Twitch."""
        if not self.access_token:
            return False

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    TWITCH_VALIDATE_URL,
                    headers={"Authorization": f"OAuth {self.access_token}"},
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        self.user_id = data.get("user_id", self.user_id)
                        self.username = data.get("login", self.username)
                        returned_cid = data.get("client_id")
                        if returned_cid:
                            self.client_id = returned_cid
                        expires_in = data.get("expires_in", 0)
                        if expires_in > 0:
                            self.expires_at = time.time() + expires_in
                        return True
                    else:
                        logger.warning("Token validation failed (status %d)", resp.status)
                        return False
        except aiohttp.ClientError as e:
            logger.error("Token validation error: %s", e)
            return False

    async def ensure_valid_token(self) -> bool:
        """Ensure we have a valid, non-expired access token."""
        if not self.access_token:
            if not self._load_token():
                return False

        if time.time() > self.expires_at - 300:
            if not await self.validate():
                return False

        return True

    async def logout(self):
        """Revoke the current token and clear stored credentials."""
        if self.access_token:
            try:
                async with aiohttp.ClientSession() as session:
                    await session.post(
                        TWITCH_REVOKE_URL,
                        data={
                            "client_id": self.client_id,
                            "token": self.access_token,
                        },
                    )
            except Exception:
                pass

        self.access_token = None
        self.expires_at = 0
        self.user_id = None
        self.username = None

        if TOKEN_FILE.exists():
            TOKEN_FILE.unlink()

        logger.info("Logged out and tokens cleared.")

    # ── Headers for API calls ──────────────────────────────────────

    def get_gql_headers(self) -> dict:
        """Headers for Twitch GQL API calls."""
        return {
            "Authorization": f"OAuth {self.access_token}",
            "Client-Id": self.client_id,
        }

    def get_headers(self) -> dict:
        """Headers for Twitch Helix API calls."""
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Client-Id": self.client_id,
        }

    @property
    def is_authenticated(self) -> bool:
        """Check if we have a token (may still be invalid/expired)."""
        if not self.access_token:
            self._load_token()
        return self.access_token is not None
