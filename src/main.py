"""
Twitch Drops Bot — Main Entry Point

Orchestrates all components: authentication, tracking, watching,
and notifications into a unified CLI application.

Usage:
    python -m src.main <command> [args]

Commands:
    login               Authenticate with Twitch
    logout              Clear stored credentials
    add-game <name>     Track a game for drops
    remove-game <name>  Stop tracking a game
    list-games          Show tracked games
    check-drops         Check available drops
    watch               Select and watch for a drop
    watch-auto          Auto-watch all available drops
    status              Show watch status
    claim               Claim completed drops
    inventory           Show drop inventory
    history             Show drop history
    config-email        Configure email notifications
    config-discord      Configure Discord notifications
    test-notify         Send test notification
    run                 Run full bot (monitor + watch + notify)
"""

import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.auth.twitch_auth import TwitchAuth
from src.api.twitch_gql import TwitchGQL
from src.storage.database import Database
from src.drops.tracker import DropTracker
from src.drops.watcher import StreamWatcher
from src.notifications.email_notifier import EmailNotifier
from src.notifications.discord_notifier import DiscordNotifier
from src.ui.cli import (
    console, print_banner, print_success, print_error, print_warning, print_info,
    display_campaigns, display_drops_detail, display_watch_status,
    display_inventory, display_tracked_games, display_history,
    select_drop, confirm_action, format_minutes,
)

load_dotenv()

# ── Logging Setup ──────────────────────────────────────────────────

def setup_logging(config: dict):
    log_config = config.get("logging", {})
    level = getattr(logging, log_config.get("level", "INFO").upper(), logging.INFO)
    log_file = log_config.get("file")

    handlers = [logging.StreamHandler()]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(
        level=level,
        format="%(asctime)s │ %(levelname)-7s │ %(name)s │ %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )
    # Quiet down noisy libraries
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)


def load_config() -> dict:
    config_path = Path("config/config.yaml")
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    return {}


logger = logging.getLogger(__name__)


# ── Core App ───────────────────────────────────────────────────────

class TwitchDropsBot:
    """Main bot application orchestrator."""

    def __init__(self):
        self.config = load_config()
        setup_logging(self.config)

        self.auth = TwitchAuth()
        self.gql = TwitchGQL(self.auth)
        self.db = Database()
        self.tracker: DropTracker | None = None
        self.watcher: StreamWatcher | None = None
        self.email_notifier: EmailNotifier | None = None
        self.discord_notifier: DiscordNotifier | None = None

    async def initialize(self):
        """Initialize database and components."""
        await self.db.connect()
        self.tracker = DropTracker(self.gql, self.db)
        self.watcher = StreamWatcher(
            self.gql, self.db,
            heartbeat_interval=self.config.get("watch_heartbeat_interval", 60),
            auto_claim=self.config.get("auto_claim", True),
            auto_switch=self.config.get("auto_switch_streams", True),
        )
        await self._load_notifiers()

    async def cleanup(self):
        """Clean up resources."""
        if self.watcher:
            await self.watcher.stop_all()
        await self.gql.close()
        await self.db.close()

    async def _load_notifiers(self):
        """Load notification configurations from DB or env."""
        # Email
        email_config = await self.db.get_notification_config("email")
        if email_config and email_config.get("config"):
            self.email_notifier = EmailNotifier.from_config_json(email_config["config"])
            # Password from env (not stored in DB)
            self.email_notifier.password = os.getenv("EMAIL_PASSWORD", "")
        else:
            self.email_notifier = EmailNotifier()

        # Discord
        discord_config = await self.db.get_notification_config("discord")
        if discord_config and discord_config.get("config"):
            self.discord_notifier = DiscordNotifier.from_config_json(discord_config["config"])
        else:
            self.discord_notifier = DiscordNotifier()

    async def _notify(self, method_name: str, **kwargs):
        """Send notification through all configured channels."""
        notif_config = self.config.get("notifications", {})

        if self.email_notifier and self.email_notifier.is_configured:
            try:
                method = getattr(self.email_notifier, method_name, None)
                if method:
                    await method(**kwargs)
            except Exception as e:
                logger.error("Email notification error: %s", e)

        if self.discord_notifier and self.discord_notifier.is_configured:
            try:
                method = getattr(self.discord_notifier, method_name, None)
                if method:
                    await method(**kwargs)
            except Exception as e:
                logger.error("Discord notification error: %s", e)

    # ── Commands ───────────────────────────────────────────────────

    async def cmd_login(self):
        """Authenticate with Twitch."""
        print_info("Starting Twitch authentication...")
        success = await self.auth.login()
        if success:
            print_success(f"Logged in as: {self.auth.username}")
        else:
            print_error("Login failed. Check your Client ID and Secret in .env")

    async def cmd_logout(self):
        """Clear authentication."""
        await self.auth.logout()
        print_success("Logged out and credentials cleared.")

    async def cmd_add_game(self, game_name: str):
        """Add a game to tracked list."""
        if not await self.auth.ensure_valid_token():
            print_error("Not logged in. Run 'login' first.")
            return

        # Search for the game on Twitch to get exact name and ID
        print_info(f"Searching for '{game_name}' on Twitch...")
        results = await self.gql.search_games(game_name)

        if not results:
            print_warning(f"No game found matching '{game_name}'. Adding anyway...")
            await self.db.add_tracked_game(game_name)
            print_success(f"Now tracking: {game_name}")
            return

        # If exact match found, use it
        exact = next((g for g in results if g["name"].lower() == game_name.lower()), None)
        if exact:
            await self.db.add_tracked_game(
                exact["name"], twitch_game_id=exact["id"],
                display_name=exact["name"], box_art_url=exact.get("box_art_url"),
            )
            print_success(f"Now tracking: {exact['name']} (ID: {exact['id']})")
            return

        # Show options
        console.print("\n  Found these games:")
        for i, g in enumerate(results[:10], 1):
            console.print(f"    {i}. [magenta]{g['name']}[/magenta]")

        from rich.prompt import Prompt
        choice = Prompt.ask("  Select game [number]", default="1")
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(results):
                game = results[idx]
                await self.db.add_tracked_game(
                    game["name"], twitch_game_id=game["id"],
                    display_name=game["name"], box_art_url=game.get("box_art_url"),
                )
                print_success(f"Now tracking: {game['name']}")
            else:
                print_error("Invalid selection.")
        except ValueError:
            print_error("Invalid input.")

    async def cmd_remove_game(self, game_name: str):
        """Remove a game from tracked list."""
        removed = await self.db.remove_tracked_game(game_name)
        if removed:
            print_success(f"Stopped tracking: {game_name}")
        else:
            print_warning(f"Game '{game_name}' was not being tracked.")

    async def cmd_list_games(self):
        """Show tracked games."""
        games = await self.db.get_tracked_games()
        display_tracked_games(games)

    async def cmd_check_drops(self):
        """Check available drops for tracked games."""
        if not await self.auth.ensure_valid_token():
            print_error("Not logged in. Run 'login' first.")
            return

        print_info("Checking for drops...")
        result = await self.tracker.check_for_drops()

        campaigns = result["active_campaigns"]
        if campaigns:
            display_campaigns(campaigns, title="Drops for Your Tracked Games")

            # Show if there are new campaigns
            new = result["new_campaigns"]
            if new:
                console.print(f"  🆕 [bold green]{len(new)} new campaign(s) discovered![/bold green]\n")
        else:
            print_info("No active drop campaigns for your tracked games.")

            # Show all active campaigns as suggestions
            all_active = result.get("all_active_campaigns", [])
            if all_active:
                console.print(f"\n  [dim]There are {len(all_active)} active campaigns for other games.[/dim]")
                console.print("  [dim]Use 'add-game <name>' to track a game.[/dim]\n")

        # Show claimable
        claimable = result["claimable_drops"]
        if claimable:
            console.print(f"  🎁 [bold green]{len(claimable)} drop(s) ready to claim![/bold green]")
            console.print("  [dim]Run 'claim' to claim them.[/dim]\n")

    async def cmd_watch(self):
        """Interactively select and watch for a drop."""
        if not await self.auth.ensure_valid_token():
            print_error("Not logged in. Run 'login' first.")
            return

        print_info("Finding watchable drops...")
        watchable = await self.tracker.get_watchable_drops()

        selection = select_drop(watchable)
        if not selection:
            return

        campaign = selection["campaign"]
        drop = selection["drop"]
        game_name = selection["game_name"]

        rewards = ", ".join(b.name for b in drop.benefits) or drop.name
        console.print(f"\n  🎯 Watching for: [bold]{rewards}[/bold]")
        console.print(f"  🎮 Game: [magenta]{game_name}[/magenta]")
        console.print(f"  ⏱️  Time needed: [cyan]{format_minutes(selection['remaining_minutes'])}[/cyan]\n")

        started = await self.watcher.watch_drop(campaign, drop)
        if not started:
            print_error("Failed to start watching. No qualifying streams found.")
            return

        # Register notification callbacks
        self.watcher.on("on_drop_earned", self._on_drop_earned_callback)
        self.watcher.on("on_drop_claimed", self._on_drop_claimed_callback)

        print_success("Watching started! Press Ctrl+C to stop.\n")

        try:
            await self.watcher.run_watch_loop()
        except KeyboardInterrupt:
            print_info("\nStopping...")
            await self.watcher.stop_all("user_interrupt")
            print_success("Watching stopped.")

    async def cmd_watch_auto(self):
        """Automatically watch for all available drops."""
        if not await self.auth.ensure_valid_token():
            print_error("Not logged in. Run 'login' first.")
            return

        print_info("Finding all available drops...")
        watchable = await self.tracker.get_watchable_drops()

        if not watchable:
            print_info("No drops available to watch.")
            return

        max_concurrent = self.config.get("max_concurrent_watches", 2)
        to_watch = watchable[:max_concurrent]

        console.print(f"\n  📺 Starting {len(to_watch)} watch session(s):\n")
        for item in to_watch:
            rewards = ", ".join(b.name for b in item["drop"].benefits) or item["drop"].name
            console.print(f"    • [bold]{rewards}[/bold] — {item['game_name']} ({format_minutes(item['remaining_minutes'])})")

        console.print()

        # Register callbacks
        self.watcher.on("on_drop_earned", self._on_drop_earned_callback)
        self.watcher.on("on_drop_claimed", self._on_drop_claimed_callback)

        for item in to_watch:
            await self.watcher.watch_drop(item["campaign"], item["drop"])
            await asyncio.sleep(2)

        print_success(f"Auto-watching {len(to_watch)} drop(s). Press Ctrl+C to stop.\n")

        try:
            await self.watcher.run_watch_loop()
        except KeyboardInterrupt:
            print_info("\nStopping...")
            await self.watcher.stop_all("user_interrupt")
            print_success("Auto-watching stopped.")

    async def cmd_status(self):
        """Show current watch status."""
        if self.watcher:
            sessions = self.watcher.get_status()
            display_watch_status(sessions)
        else:
            print_info("Not currently watching anything.")

    async def cmd_claim(self):
        """Claim all completed drops."""
        if not await self.auth.ensure_valid_token():
            print_error("Not logged in. Run 'login' first.")
            return

        print_info("Checking for claimable drops...")
        claimed = await self.tracker.claim_all_drops()

        if claimed:
            for c in claimed:
                print_success(f"Claimed: {c['name']} ({c['game']})")
        else:
            print_info("No drops to claim right now.")

    async def cmd_inventory(self):
        """Show drop inventory."""
        if not await self.auth.ensure_valid_token():
            print_error("Not logged in. Run 'login' first.")
            return

        print_info("Fetching inventory...")
        inventory = await self.gql.get_inventory()
        display_inventory(inventory)

    async def cmd_history(self):
        """Show drop history."""
        history = await self.db.get_drop_history()
        display_history(history)

    async def cmd_config_email(self):
        """Configure email notifications interactively."""
        from rich.prompt import Prompt

        console.print("\n  📧 [bold]Email Notification Setup[/bold]\n")
        console.print("  [dim]For Gmail: use an App Password from https://myaccount.google.com/apppasswords[/dim]\n")

        smtp_host = Prompt.ask("  SMTP Host", default="smtp.gmail.com")
        smtp_port = int(Prompt.ask("  SMTP Port", default="587"))
        sender = Prompt.ask("  Sender Email")
        password = Prompt.ask("  Email Password (App Password for Gmail)", password=True)
        recipient = Prompt.ask("  Recipient Email", default=sender)

        notifier = EmailNotifier(smtp_host, smtp_port, sender, password, recipient)

        # Test
        if confirm_action("Send a test email?"):
            success = await notifier.send_test()
            if success:
                print_success("Test email sent! Check your inbox.")
            else:
                print_error("Test email failed. Check your settings.")
                return

        # Save config (password stays in .env)
        await self.db.save_notification_config("email", notifier.to_config_json(), enabled=True)
        self.email_notifier = notifier
        print_success("Email notifications configured!")
        print_info("Note: Store your email password in .env as EMAIL_PASSWORD for security.")

    async def cmd_config_discord(self, webhook_url: str | None = None):
        """Configure Discord webhook notifications."""
        from rich.prompt import Prompt

        if not webhook_url:
            console.print("\n  💬 [bold]Discord Notification Setup[/bold]\n")
            console.print("  [dim]Create a webhook in Server Settings → Integrations → Webhooks[/dim]\n")
            webhook_url = Prompt.ask("  Webhook URL")

        notifier = DiscordNotifier(webhook_url)

        if not notifier.is_configured:
            print_error("Invalid webhook URL. Must start with 'https://discord.com/api/webhooks/'")
            return

        # Test
        if confirm_action("Send a test message to Discord?"):
            success = await notifier.send_test()
            if success:
                print_success("Test message sent! Check your Discord channel.")
            else:
                print_error("Test failed. Check your webhook URL.")
                return

        await self.db.save_notification_config("discord", notifier.to_config_json(), enabled=True)
        self.discord_notifier = notifier
        print_success("Discord notifications configured!")

    async def cmd_test_notify(self):
        """Send test notifications through all configured channels."""
        sent = False
        if self.email_notifier and self.email_notifier.is_configured:
            success = await self.email_notifier.send_test()
            if success:
                print_success("Test email sent!")
                sent = True

        if self.discord_notifier and self.discord_notifier.is_configured:
            success = await self.discord_notifier.send_test()
            if success:
                print_success("Test Discord message sent!")
                sent = True

        if not sent:
            print_warning("No notification channels configured. Use 'config-email' or 'config-discord'.")

    async def cmd_run(self):
        """Run the full bot: monitor, auto-watch, and notify."""
        if not await self.auth.ensure_valid_token():
            print_error("Not logged in. Run 'login' first.")
            return

        games = await self.db.get_tracked_game_names()
        if not games:
            print_error("No games tracked. Use 'add-game <name>' to start.")
            return

        check_interval = self.config.get("check_interval", 15)

        print_banner()
        console.print(f"  🎮 Tracking: [magenta]{', '.join(games)}[/magenta]")
        console.print(f"  🔄 Check interval: [cyan]{check_interval} minutes[/cyan]")

        notif_channels = []
        if self.email_notifier and self.email_notifier.is_configured:
            notif_channels.append("📧 Email")
        if self.discord_notifier and self.discord_notifier.is_configured:
            notif_channels.append("💬 Discord")
        if notif_channels:
            console.print(f"  🔔 Notifications: {', '.join(notif_channels)}")
        else:
            console.print("  🔕 [dim]No notifications configured[/dim]")

        console.print(f"\n  Press [bold]Ctrl+C[/bold] to stop.\n")
        console.print("  " + "─" * 50 + "\n")

        # Register notification callbacks
        self.watcher.on("on_drop_earned", self._on_drop_earned_callback)
        self.watcher.on("on_drop_claimed", self._on_drop_claimed_callback)

        try:
            while True:
                # Check for drops
                logger.info("🔍 Checking for drops...")
                result = await self.tracker.check_for_drops()

                # Notify about new campaigns
                new_campaigns = result["new_campaigns"]
                if new_campaigns:
                    notif_config = self.config.get("notifications", {})
                    if notif_config.get("on_new_campaign", True):
                        await self._notify("notify_new_campaigns", campaigns=new_campaigns)

                    # Mark as notified
                    for c in new_campaigns:
                        await self.db.mark_campaign_notified(c.id)

                # Auto-claim any completed drops
                if self.config.get("auto_claim", True):
                    claimed = await self.tracker.claim_all_drops()
                    for c in claimed:
                        console.print(f"  ✅ Auto-claimed: [green]{c['name']}[/green] ({c['game']})")

                # Start watching if not already
                if not self.watcher.active_sessions:
                    watchable = await self.tracker.get_watchable_drops()
                    max_concurrent = self.config.get("max_concurrent_watches", 2)
                    to_watch = watchable[:max_concurrent]

                    for item in to_watch:
                        await self.watcher.watch_drop(item["campaign"], item["drop"])
                        await asyncio.sleep(2)

                    if to_watch:
                        console.print(f"  📺 Watching {len(to_watch)} stream(s) for drops...")

                # Run watch loop for the check interval, then recheck
                if self.watcher.active_sessions:
                    try:
                        watch_task = asyncio.create_task(self.watcher.run_watch_loop())
                        await asyncio.sleep(check_interval * 60)
                        await self.watcher.stop_all("recheck")
                        watch_task.cancel()
                        try:
                            await watch_task
                        except asyncio.CancelledError:
                            pass
                    except Exception as e:
                        logger.error("Watch error: %s", e)
                else:
                    console.print(f"  💤 No drops to watch. Checking again in {check_interval}min...")
                    await asyncio.sleep(check_interval * 60)

        except KeyboardInterrupt:
            console.print("\n")
            print_info("Shutting down bot...")
            await self.watcher.stop_all("user_shutdown")
            print_success("Bot stopped. Goodbye! 👋")

    async def cmd_ui(self, port: int = 8189):
        """Launch the web dashboard."""
        import webbrowser
        from src.web.server import start_server

        url = f"http://127.0.0.1:{port}"
        print_info(f"Starting web dashboard at {url}")

        runner = await start_server(self, port=port)

        webbrowser.open(url)
        print_success(f"Dashboard running at {url}")
        console.print("  Press [bold]Ctrl+C[/bold] to stop.\n")

        try:
            while True:
                await asyncio.sleep(3600)
        except KeyboardInterrupt:
            console.print("\n")
            print_info("Shutting down dashboard...")
            await runner.cleanup()
            print_success("Dashboard stopped.")

    # ── Notification Callbacks ─────────────────────────────────────

    async def _on_drop_earned_callback(self, drop, campaign_name, game_name, **_):
        notif_config = self.config.get("notifications", {})
        if notif_config.get("on_drop_earned", True):
            await self._notify(
                "notify_drop_earned",
                drop_name=drop.name,
                game_name=game_name,
                campaign_name=campaign_name,
            )

    async def _on_drop_claimed_callback(self, drop, campaign_name, game_name, **_):
        notif_config = self.config.get("notifications", {})
        if notif_config.get("on_drop_claimed", True):
            await self._notify(
                "notify_drop_claimed",
                drop_name=drop.name,
                game_name=game_name,
            )


# ── CLI Entry Point ────────────────────────────────────────────────

async def main():
    bot = TwitchDropsBot()
    await bot.initialize()

    try:
        args = sys.argv[1:]
        if not args:
            print_banner()
            console.print("  [bold]Usage:[/bold] python -m src.main <command> [args]\n")
            console.print("  [bold magenta]Authentication:[/bold magenta]")
            console.print("    login                  Log in to Twitch")
            console.print("    logout                 Clear credentials\n")
            console.print("  [bold magenta]Game Tracking:[/bold magenta]")
            console.print("    add-game <name>        Track a game")
            console.print("    remove-game <name>     Stop tracking")
            console.print("    list-games             Show tracked games\n")
            console.print("  [bold magenta]Drops:[/bold magenta]")
            console.print("    check-drops            Check available drops")
            console.print("    watch                  Watch for a specific drop")
            console.print("    watch-auto             Auto-watch all drops")
            console.print("    status                 Show watch status")
            console.print("    claim                  Claim completed drops")
            console.print("    inventory              Show drop inventory")
            console.print("    history                Show drop history\n")
            console.print("  [bold magenta]Notifications:[/bold magenta]")
            console.print("    config-email           Set up email alerts")
            console.print("    config-discord [url]   Set up Discord alerts")
            console.print("    test-notify            Test notifications\n")
            console.print("  [bold magenta]Full Bot:[/bold magenta]")
            console.print("    run                    Run the full bot")
            console.print("    ui                     Launch web dashboard\n")
            return

        command = args[0].lower()
        extra_args = args[1:]

        match command:
            case "login":
                await bot.cmd_login()
            case "logout":
                await bot.cmd_logout()
            case "add-game":
                if extra_args:
                    await bot.cmd_add_game(" ".join(extra_args))
                else:
                    print_error("Usage: add-game <game name>")
            case "remove-game":
                if extra_args:
                    await bot.cmd_remove_game(" ".join(extra_args))
                else:
                    print_error("Usage: remove-game <game name>")
            case "list-games":
                await bot.cmd_list_games()
            case "check-drops":
                await bot.cmd_check_drops()
            case "watch":
                await bot.cmd_watch()
            case "watch-auto":
                await bot.cmd_watch_auto()
            case "status":
                await bot.cmd_status()
            case "claim":
                await bot.cmd_claim()
            case "inventory":
                await bot.cmd_inventory()
            case "history":
                await bot.cmd_history()
            case "config-email":
                await bot.cmd_config_email()
            case "config-discord":
                url = extra_args[0] if extra_args else None
                await bot.cmd_config_discord(url)
            case "test-notify":
                await bot.cmd_test_notify()
            case "run":
                await bot.cmd_run()
            case "ui":
                await bot.cmd_ui()
            case _:
                print_error(f"Unknown command: {command}")
                print_info("Run without arguments to see available commands.")

    finally:
        await bot.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
