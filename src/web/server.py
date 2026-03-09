"""
Web Server for Twitch Drops Bot Dashboard

Serves a Twitch-styled web UI and API endpoints for managing the bot.
Uses aiohttp for async HTTP serving.
"""

import asyncio
import json
import logging
import time
from collections import deque
from pathlib import Path

from aiohttp import web

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
TEMPLATE_DIR = Path(__file__).parent / "templates"

# In-memory activity log (ring buffer)
activity_log: deque = deque(maxlen=100)


def create_app(bot) -> web.Application:
    """Create the aiohttp web application with all routes."""
    app = web.Application()
    app["bot"] = bot
    app["auto_watch_enabled"] = True
    app["auto_watch_task"] = None

    # ── Page Routes ────────────────────────────────────────────────
    app.router.add_get("/", handle_index)

    # ── Auth API ───────────────────────────────────────────────────
    app.router.add_get("/api/auth/status", api_auth_status)
    app.router.add_post("/api/auth/device-code", api_auth_device_code)
    app.router.add_post("/api/auth/token", api_auth_token)
    app.router.add_post("/api/auth/logout", api_auth_logout)

    # ── Games API ──────────────────────────────────────────────────
    app.router.add_get("/api/games", api_games_list)
    app.router.add_post("/api/games", api_games_add)
    app.router.add_delete("/api/games/{name}", api_games_remove)
    app.router.add_get("/api/games/search", api_games_search)

    # ── Games Reorder API ──────────────────────────────────────────
    app.router.add_post("/api/games/reorder", api_games_reorder)

    # ── Drops API ──────────────────────────────────────────────────
    app.router.add_get("/api/drops", api_drops_check)
    app.router.add_get("/api/drops/inventory", api_drops_inventory)
    app.router.add_post("/api/drops/claim", api_drops_claim)

    # ── Watch API ──────────────────────────────────────────────────
    app.router.add_get("/api/watch/status", api_watch_status)
    app.router.add_post("/api/watch/start", api_watch_start)
    app.router.add_post("/api/watch/stop", api_watch_stop)
    app.router.add_post("/api/watch/stop/{session_id}", api_watch_stop_session)
    app.router.add_get("/api/watch/auto", api_auto_watch_status)
    app.router.add_post("/api/watch/auto", api_auto_watch_toggle)

    # ── History API ────────────────────────────────────────────────
    app.router.add_get("/api/history", api_history)

    # ── Activity Log API ───────────────────────────────────────────
    app.router.add_get("/api/activity/log", api_activity_log)

    # ── Drops Claim Individual ─────────────────────────────────────
    app.router.add_post("/api/drops/claim/{drop_id}", api_drops_claim_single)

    # ── Notifications API ──────────────────────────────────────────
    app.router.add_get("/api/notifications/config", api_notif_config)
    app.router.add_post("/api/notifications/discord", api_notif_discord)
    app.router.add_post("/api/notifications/email", api_notif_email)
    app.router.add_post("/api/notifications/test", api_notif_test)

    # ── Bot Control API ────────────────────────────────────────────
    app.router.add_get("/api/bot/status", api_bot_status)

    # ── Static Files ───────────────────────────────────────────────
    app.router.add_static("/static/", path=str(STATIC_DIR), name="static")

    # Start auto-watch background loop on startup
    app.on_startup.append(_start_auto_watch_loop)
    app.on_cleanup.append(_stop_auto_watch_loop)

    return app


async def start_server(bot, host: str = "127.0.0.1", port: int = 8189):
    """Start the web server."""
    app = create_app(bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("Web UI running at http://%s:%d", host, port)
    return runner


# ── Page Handlers ──────────────────────────────────────────────────

async def handle_index(request: web.Request) -> web.Response:
    """Serve the main dashboard HTML."""
    html_path = TEMPLATE_DIR / "index.html"
    return web.FileResponse(html_path)


# ── Auth API ───────────────────────────────────────────────────────

async def api_auth_status(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    # Use ensure_valid_token (only hits Twitch when token is near expiry)
    # instead of validate() which makes a network call every time
    is_valid = False
    if bot.auth.is_authenticated:
        is_valid = await bot.auth.ensure_valid_token()

    return web.json_response({
        "authenticated": is_valid,
        "username": bot.auth.username,
        "user_id": bot.auth.user_id,
    })


async def api_auth_device_code(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    device = await bot.auth.request_device_code()
    if not device:
        return web.json_response({"error": "Failed to get device code"}, status=500)

    # Start polling in background — will complete when user authorizes
    asyncio.create_task(bot.auth.poll_device_code())
    return web.json_response(device)


async def api_auth_token(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    data = await request.json()
    raw = data.get("token", "").strip() or data.get("url", "").strip()

    if not raw:
        return web.json_response({"error": "No token provided"}, status=400)

    # login_with_token handles both raw tokens and URLs
    token = raw

    success = await bot.auth.login_with_token(token)
    if success:
        return web.json_response({
            "success": True,
            "username": bot.auth.username,
            "user_id": bot.auth.user_id,
        })
    return web.json_response({"error": "Token validation failed"}, status=401)


async def api_auth_logout(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    await bot.auth.logout()
    return web.json_response({"success": True})


# ── Games API ──────────────────────────────────────────────────────

async def api_games_list(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    games = await bot.db.get_tracked_games()
    return web.json_response({"games": games})


async def api_games_add(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    data = await request.json()
    name = data.get("name", "").strip()
    game_id = data.get("id")
    box_art = data.get("box_art_url")

    if not name:
        return web.json_response({"error": "Game name required"}, status=400)

    # If ID not provided, search for the game
    if not game_id:
        if await bot.auth.ensure_valid_token():
            results = await bot.gql.search_games(name)
            if results:
                exact = next((g for g in results if g["name"].lower() == name.lower()), results[0])
                name = exact["name"]
                game_id = exact["id"]
                box_art = exact.get("box_art_url")

    await bot.db.add_tracked_game(name, twitch_game_id=game_id, display_name=name, box_art_url=box_art)
    return web.json_response({"success": True, "name": name, "id": game_id})


async def api_games_remove(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    name = request.match_info["name"]
    removed = await bot.db.remove_tracked_game(name)
    return web.json_response({"success": removed})


async def api_games_search(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    query = request.query.get("q", "").strip()
    if not query:
        return web.json_response({"results": []})

    if not await bot.auth.ensure_valid_token():
        return web.json_response({"error": "Not authenticated"}, status=401)

    results = await bot.gql.search_games(query)
    return web.json_response({"results": results})


async def api_games_reorder(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    data = await request.json()
    order = data.get("order", [])
    if not isinstance(order, list):
        return web.json_response({"error": "order must be a list"}, status=400)
    await bot.db.reorder_games(order)
    return web.json_response({"success": True})


# ── Drops API ──────────────────────────────────────────────────────

async def api_drops_check(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    if not await bot.auth.ensure_valid_token():
        return web.json_response({"error": "Not authenticated"}, status=401)

    result = await bot.tracker.check_for_drops()
    inventory = result.get("inventory", {})

    # Build set of completed drop names from gameEventDrops
    completed_names = set()
    for item in inventory.get("completed", []):
        name = (item.get("name") or "").strip()
        if name:
            completed_names.add(name.lower())

    # Serialize campaigns
    campaigns = []
    for c in result.get("active_campaigns", []):
        drops = []
        for d in c.drops:
            # Skip sub-only drops (0 required minutes)
            if d.required_minutes <= 0:
                continue

            # Check if this drop was already completed
            drop_name_norm = (d.name or "").strip().lower()
            is_completed = drop_name_norm in completed_names if drop_name_norm else False

            drops.append({
                "id": d.id,
                "name": d.name,
                "required_minutes": d.required_minutes,
                "current_minutes": d.current_minutes,
                "is_claimed": d.is_claimed or is_completed,
                "progress_percent": 100.0 if is_completed else d.progress_percent,
                "benefits": [{"id": b.id, "name": b.name, "image_url": b.image_url} for b in d.benefits],
            })
        if drops:  # Only include campaigns that have watchable drops
            campaigns.append({
                "id": c.id,
                "name": c.name,
                "game_name": c.game_display_name or c.game_name,
                "game_box_art_url": c.game_box_art_url,
                "status": c.status,
                "start_at": c.start_at.isoformat() if c.start_at else None,
                "end_at": c.end_at.isoformat() if c.end_at else None,
                "details_url": c.details_url,
                "drops": drops,
            })

    return web.json_response({
        "campaigns": campaigns,
        "new_count": len(result.get("new_campaigns", [])),
        "claimable_count": len(result.get("claimable_drops", [])),
    })


async def api_drops_inventory(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    if not await bot.auth.ensure_valid_token():
        return web.json_response({"error": "Not authenticated"}, status=401)

    inventory = await bot.gql.get_inventory()

    in_progress = []
    for item in inventory.get("in_progress", []):
        drop = item["drop"]
        in_progress.append({
            "campaign_id": item["campaign_id"],
            "campaign_name": item["campaign_name"],
            "game_name": item["game_name"],
            "drop_name": drop.name,
            "required_minutes": drop.required_minutes,
            "current_minutes": drop.current_minutes,
            "progress_percent": drop.progress_percent,
            "is_complete": drop.is_complete,
            "is_claimed": drop.is_claimed,
            "benefits": [{"name": b.name, "image_url": b.image_url} for b in drop.benefits],
        })

    return web.json_response({
        "in_progress": in_progress,
        "completed": inventory.get("completed", []),
    })


async def api_drops_claim(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    if not await bot.auth.ensure_valid_token():
        return web.json_response({"error": "Not authenticated"}, status=401)

    claimed = await bot.tracker.claim_all_drops()
    if claimed:
        add_activity("claim", f"Claimed {len(claimed)} drop(s)")
    return web.json_response({"claimed": claimed})


# ── Watch API ──────────────────────────────────────────────────────

async def api_watch_status(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    sessions = bot.watcher.get_status() if bot.watcher else []
    return web.json_response({"sessions": sessions})


async def api_watch_start(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    if not await bot.auth.ensure_valid_token():
        return web.json_response({"error": "Not authenticated"}, status=401)

    data = await request.json()
    mode = data.get("mode", "auto")  # "auto" or "specific"

    if mode == "auto":
        # Stop any existing sessions before starting new ones
        if bot.watcher and bot.watcher.active_sessions:
            await bot.watcher.stop_all("restarting")
            await asyncio.sleep(0.5)

        watchable = await bot.tracker.get_watchable_drops()
        if not watchable:
            return web.json_response({"error": "No drops available to watch"}, status=404)

        max_c = bot.config.get("max_concurrent_watches", 2)
        started = []
        for item in watchable[:max_c]:
            ok = await bot.watcher.watch_drop(item["campaign"], item["drop"])
            if ok:
                started.append(item["drop"].name)
            await asyncio.sleep(1)

        if started:
            # Run watch loop in background
            asyncio.create_task(_run_watch_loop(bot))
            add_activity("info", f"Started watching: {', '.join(started)}")
            return web.json_response({"success": True, "watching": started})
        return web.json_response({"error": "Failed to start watching"}, status=500)

    return web.json_response({"error": "Invalid mode"}, status=400)


async def _run_watch_loop(bot):
    """Run the watch loop in background."""
    try:
        await bot.watcher.run_watch_loop()
    except Exception as e:
        logger.error("Watch loop error: %s", e)


async def api_watch_stop(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    if bot.watcher:
        await bot.watcher.stop_all("user_stopped")
        add_activity("info", "Stopped all watch sessions")
    return web.json_response({"success": True})


# ── Auto-Watch Background Loop ────────────────────────────────────

async def _start_auto_watch_loop(app: web.Application):
    """Start the auto-watch background task on server startup."""
    app["auto_watch_task"] = asyncio.create_task(_auto_watch_loop(app))


async def _stop_auto_watch_loop(app: web.Application):
    """Stop the auto-watch background task on server cleanup."""
    task = app.get("auto_watch_task")
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def _auto_watch_loop(app: web.Application):
    """Periodically check for watchable drops and start watching them."""
    bot = app["bot"]
    check_interval = bot.config.get("auto_watch_interval", 5) * 60  # minutes → seconds

    # Wait a few seconds for server to fully start
    await asyncio.sleep(5)

    while True:
        try:
            if not app["auto_watch_enabled"]:
                await asyncio.sleep(30)
                continue

            if not await bot.auth.ensure_valid_token():
                logger.debug("Auto-watch: not authenticated, waiting...")
                await asyncio.sleep(30)
                continue

            # Skip if there are already active sessions
            if bot.watcher and bot.watcher.active_sessions:
                await asyncio.sleep(check_interval)
                continue

            # Check for watchable drops
            watchable = await bot.tracker.get_watchable_drops()
            if not watchable:
                await asyncio.sleep(check_interval)
                continue

            max_c = bot.config.get("max_concurrent_watches", 2)
            started = []
            for item in watchable[:max_c]:
                ok = await bot.watcher.watch_drop(item["campaign"], item["drop"])
                if ok:
                    started.append(item["drop"].name)
                await asyncio.sleep(1)

            if started:
                logger.info("Auto-watch started: %s", ", ".join(started))
                add_activity("info", f"Auto-watch started: {', '.join(started)}")
                asyncio.create_task(_run_watch_loop(bot))

            # Auto-claim completed drops
            if bot.config.get("auto_claim", True):
                claimed = await bot.tracker.claim_all_drops()
                if claimed:
                    add_activity("claim", f"Auto-claimed {len(claimed)} drop(s)")

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("Auto-watch loop error: %s", e)
            add_activity("error", f"Auto-watch error: {e}")

        await asyncio.sleep(check_interval)


async def api_auto_watch_status(request: web.Request) -> web.Response:
    return web.json_response({"enabled": request.app["auto_watch_enabled"]})


async def api_auto_watch_toggle(request: web.Request) -> web.Response:
    data = await request.json()
    enabled = data.get("enabled", True)
    request.app["auto_watch_enabled"] = bool(enabled)
    logger.info("Auto-watch %s", "enabled" if enabled else "disabled")
    return web.json_response({"enabled": request.app["auto_watch_enabled"]})


# ── History API ────────────────────────────────────────────────────

async def api_history(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    history = await bot.db.get_drop_history()
    watch_history = await bot.db.get_watch_history(limit=30)
    return web.json_response({"drops": history, "watches": watch_history})


# ── Notifications API ──────────────────────────────────────────────

async def api_notif_config(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    configs = await bot.db.get_all_notification_configs()

    result = {"email": None, "discord": None}
    for c in configs:
        if c["type"] in result:
            try:
                cfg_data = json.loads(c.get("config", "{}"))
            except json.JSONDecodeError:
                cfg_data = {}
            result[c["type"]] = {
                "enabled": bool(c.get("enabled")),
                "config": cfg_data,
            }

    return web.json_response(result)


async def api_notif_discord(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    data = await request.json()
    webhook_url = data.get("webhook_url", "").strip()

    if not webhook_url.startswith("https://discord.com/api/webhooks/"):
        return web.json_response({"error": "Invalid Discord webhook URL"}, status=400)

    from src.notifications.discord_notifier import DiscordNotifier
    notifier = DiscordNotifier(webhook_url)
    await bot.db.save_notification_config("discord", notifier.to_config_json(), enabled=True)
    bot.discord_notifier = notifier

    return web.json_response({"success": True})


async def api_notif_email(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    data = await request.json()

    smtp_host = data.get("smtp_host", "smtp.gmail.com").strip()
    smtp_port = int(data.get("smtp_port", 587))
    sender_email = data.get("sender_email", "").strip()
    password = data.get("password", "").strip()
    recipient_email = data.get("recipient_email", "").strip()

    if not sender_email or not recipient_email:
        return web.json_response({"error": "Sender and recipient emails are required"}, status=400)

    from src.notifications.email_notifier import EmailNotifier

    # If no password provided, keep the existing one
    if not password and bot.email_notifier and bot.email_notifier.password:
        password = bot.email_notifier.password

    notifier = EmailNotifier(
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        sender_email=sender_email,
        password=password,
        recipient_email=recipient_email,
    )
    await bot.db.save_notification_config("email", notifier.to_config_json(), enabled=True)
    bot.email_notifier = notifier

    return web.json_response({"success": True})


async def api_notif_test(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    results = {}

    if bot.discord_notifier and bot.discord_notifier.is_configured:
        results["discord"] = await bot.discord_notifier.send_test()

    if bot.email_notifier and bot.email_notifier.is_configured:
        results["email"] = await bot.email_notifier.send_test()

    if not results:
        return web.json_response({"error": "No notification channels configured"}, status=400)

    return web.json_response({"results": results})


# ── Bot Status API ─────────────────────────────────────────────────

async def api_bot_status(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    games = await bot.db.get_tracked_games()
    sessions = bot.watcher.get_status() if bot.watcher else []

    return web.json_response({
        "authenticated": bot.auth.is_authenticated,
        "username": bot.auth.username,
        "tracked_games": len(games),
        "active_watches": len(sessions),
        "auto_watch": request.app["auto_watch_enabled"],
        "discord_configured": bot.discord_notifier.is_configured if bot.discord_notifier else False,
        "email_configured": bot.email_notifier.is_configured if bot.email_notifier else False,
        "claimable_count": 0,
    })


# ── Activity Log ───────────────────────────────────────────────────

def add_activity(event_type: str, message: str):
    """Add an event to the activity log ring buffer."""
    activity_log.appendleft({
        "type": event_type,
        "message": message,
        "timestamp": time.time(),
    })


async def api_activity_log(request: web.Request) -> web.Response:
    return web.json_response({"events": list(activity_log)})


# ── Individual Watch Stop ──────────────────────────────────────────

async def api_watch_stop_session(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    session_id = request.match_info["session_id"]

    if not bot.watcher:
        return web.json_response({"error": "No watcher"}, status=400)

    # session_id can be a drop_id or channel_login
    for drop_id, s in bot.watcher._active_sessions.items():
        if drop_id == session_id or getattr(s, "channel_login", "") == session_id:
            await bot.watcher.stop_watching(drop_id, "user_stopped")
            add_activity("info", f"Stopped watching {getattr(s, 'channel_name', '?')}")
            return web.json_response({"success": True})

    return web.json_response({"error": "Session not found"}, status=404)


# ── Individual Drop Claim ──────────────────────────────────────────

async def api_drops_claim_single(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    drop_id = request.match_info["drop_id"]

    if not await bot.auth.ensure_valid_token():
        return web.json_response({"error": "Not authenticated"}, status=401)

    try:
        result = await bot.gql.claim_drop(drop_id)
        if result:
            add_activity("claim", f"Claimed drop {drop_id}")
        return web.json_response({"success": bool(result), "drop_id": drop_id})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)
