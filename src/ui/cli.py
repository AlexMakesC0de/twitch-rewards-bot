"""
CLI Interface

Rich interactive command-line interface for the Twitch Drops Bot.
Uses the Rich library for beautiful terminal output.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt, Confirm
from rich.text import Text
from rich import box

console = Console()
logger = logging.getLogger(__name__)


# ── Display Helpers ────────────────────────────────────────────────

def print_banner():
    """Print the bot banner."""
    banner = Text()
    banner.append("🎮 ", style="bold")
    banner.append("Twitch Drops Bot", style="bold magenta")
    banner.append(" — Never miss a drop again!", style="dim")
    console.print(Panel(banner, border_style="magenta", padding=(1, 2)))


def print_success(message: str):
    console.print(f"  ✅ {message}", style="green")


def print_error(message: str):
    console.print(f"  ❌ {message}", style="red")


def print_warning(message: str):
    console.print(f"  ⚠️  {message}", style="yellow")


def print_info(message: str):
    console.print(f"  ℹ️  {message}", style="blue")


# ── Campaign Display ───────────────────────────────────────────────

def display_campaigns(campaigns: list, title: str = "Drop Campaigns"):
    """Display drop campaigns in a rich table."""
    if not campaigns:
        print_warning("No campaigns found.")
        return

    table = Table(
        title=f"🎯 {title}",
        box=box.ROUNDED,
        border_style="magenta",
        title_style="bold magenta",
        show_lines=True,
    )
    table.add_column("#", style="dim", width=3, justify="center")
    table.add_column("Campaign", style="bold white", max_width=30)
    table.add_column("Game", style="magenta", max_width=20)
    table.add_column("Rewards", style="green", max_width=35)
    table.add_column("Time Required", style="cyan", justify="center")
    table.add_column("Ends", style="yellow", max_width=14)
    table.add_column("Status", justify="center")

    for i, campaign in enumerate(campaigns, 1):
        rewards = []
        total_minutes = 0
        for drop in campaign.drops:
            for b in drop.benefits:
                rewards.append(b.name)
            total_minutes += drop.required_minutes

        reward_str = "\n".join(rewards[:3]) if rewards else "—"
        if len(rewards) > 3:
            reward_str += f"\n+{len(rewards) - 3} more"

        time_str = format_minutes(total_minutes)
        end_str = campaign.end_at.strftime("%b %d, %Y") if campaign.end_at else "—"

        status_emoji = "🟢" if campaign.is_active else "🔴"
        status = f"{status_emoji} {campaign.status}"

        table.add_row(str(i), campaign.name, campaign.game_display_name, reward_str, time_str, end_str, status)

    console.print(table)
    console.print()


def display_drops_detail(campaigns: list):
    """Display detailed drop information."""
    for campaign in campaigns:
        console.print(
            Panel(
                f"[bold]{campaign.name}[/bold]\n"
                f"[magenta]{campaign.game_display_name}[/magenta]",
                border_style="magenta",
                padding=(0, 2),
            )
        )

        for j, drop in enumerate(campaign.drops, 1):
            rewards = ", ".join(b.name for b in drop.benefits) or "Reward"
            pct = drop.progress_percent

            bar_filled = int(pct / 5)
            bar_empty = 20 - bar_filled
            bar = f"[green]{'█' * bar_filled}[/green][dim]{'░' * bar_empty}[/dim]"

            console.print(
                f"  {j}. [bold]{drop.name}[/bold]\n"
                f"     🎁 {rewards}\n"
                f"     {bar} {pct:.1f}% ({drop.current_minutes}/{drop.required_minutes} min)\n"
            )


# ── Watch Status ───────────────────────────────────────────────────

def display_watch_status(sessions: list[dict]):
    """Display current watch session status."""
    if not sessions:
        print_info("Not currently watching any streams.")
        return

    table = Table(
        title="📺 Active Watch Sessions",
        box=box.ROUNDED,
        border_style="cyan",
        title_style="bold cyan",
    )
    table.add_column("Drop", style="bold white", max_width=25)
    table.add_column("Game", style="magenta")
    table.add_column("Channel", style="yellow")
    table.add_column("Progress", max_width=30)
    table.add_column("ETA", style="cyan", justify="center")

    for s in sessions:
        pct = s["progress_percent"]
        bar_filled = int(pct / 5)
        bar_empty = 20 - bar_filled
        bar = f"[green]{'█' * bar_filled}[/green][dim]{'░' * bar_empty}[/dim] {pct:.1f}%"

        eta = format_minutes(s["remaining_minutes"])
        benefits_str = ", ".join(s.get("benefits", [])) or s["drop_name"]

        table.add_row(benefits_str, s["game"], s["channel"], bar, eta)

    console.print(table)
    console.print()


# ── Inventory Display ──────────────────────────────────────────────

def display_inventory(inventory: dict):
    """Display drop inventory."""
    in_progress = inventory.get("in_progress", [])
    completed = inventory.get("completed", [])

    if in_progress:
        table = Table(
            title="🔄 Drops In Progress",
            box=box.ROUNDED,
            border_style="yellow",
        )
        table.add_column("Drop", style="bold white")
        table.add_column("Game", style="magenta")
        table.add_column("Progress", max_width=30)
        table.add_column("Status", justify="center")

        for item in in_progress:
            drop = item["drop"]
            pct = drop.progress_percent
            bar_filled = int(pct / 5)
            bar_empty = 20 - bar_filled
            bar = f"[green]{'█' * bar_filled}[/green][dim]{'░' * bar_empty}[/dim] {pct:.1f}%"

            if drop.is_claimed:
                status = "✅ Claimed"
            elif drop.is_complete:
                status = "🎉 Ready to claim!"
            else:
                status = f"⏳ {drop.minutes_remaining} min left"

            table.add_row(drop.name, item["game_name"], bar, status)

        console.print(table)
        console.print()

    if completed:
        table = Table(
            title="🏆 Earned Drops",
            box=box.ROUNDED,
            border_style="green",
        )
        table.add_column("Item", style="bold white")
        table.add_column("Game", style="magenta")
        table.add_column("Count", style="cyan", justify="center")

        for item in completed[:20]:
            table.add_row(item["name"], item["game"], str(item["count"]))

        console.print(table)
        if len(completed) > 20:
            print_info(f"Showing 20 of {len(completed)} items")
    elif not in_progress:
        print_info("Your drop inventory is empty.")


# ── Game List ──────────────────────────────────────────────────────

def display_tracked_games(games: list[dict]):
    """Display tracked games."""
    if not games:
        print_warning("No games being tracked. Use 'add-game <name>' to start tracking.")
        return

    table = Table(
        title="🎮 Tracked Games",
        box=box.ROUNDED,
        border_style="magenta",
    )
    table.add_column("#", style="dim", width=3)
    table.add_column("Game", style="bold magenta")
    table.add_column("Twitch ID", style="dim")
    table.add_column("Added", style="dim")

    for i, game in enumerate(games, 1):
        table.add_row(
            str(i),
            game.get("display_name") or game["game_name"],
            game.get("twitch_game_id") or "—",
            game.get("added_at", "—")[:10],
        )

    console.print(table)
    console.print()


# ── Drop History ───────────────────────────────────────────────────

def display_history(history: list[dict]):
    """Display drop history."""
    if not history:
        print_info("No drop history yet.")
        return

    table = Table(
        title="📜 Drop History",
        box=box.ROUNDED,
        border_style="blue",
    )
    table.add_column("Drop", style="bold white", max_width=25)
    table.add_column("Game", style="magenta")
    table.add_column("Earned", style="yellow")
    table.add_column("Claimed", style="green")

    for item in history[:30]:
        earned = item.get("earned_at", "—")[:16] if item.get("earned_at") else "—"
        claimed = item.get("claimed_at", "—")[:16] if item.get("claimed_at") else "Pending"
        table.add_row(
            item.get("benefit_name") or item.get("drop_name", "—"),
            item.get("game_name", "—"),
            earned,
            claimed,
        )

    console.print(table)
    console.print()


# ── Interactive Selection ──────────────────────────────────────────

def select_drop(watchable: list[dict]) -> dict | None:
    """Let the user interactively select a drop to watch for."""
    if not watchable:
        print_warning("No watchable drops available.")
        return None

    table = Table(
        title="🎯 Available Drops to Watch",
        box=box.ROUNDED,
        border_style="magenta",
    )
    table.add_column("#", style="dim", width=3)
    table.add_column("Reward", style="bold white", max_width=30)
    table.add_column("Game", style="magenta")
    table.add_column("Time Required", style="cyan")
    table.add_column("Progress", max_width=25)

    for i, item in enumerate(watchable, 1):
        drop = item["drop"]
        rewards = ", ".join(b.name for b in drop.benefits) or drop.name
        current = item["current_minutes"]
        required = drop.required_minutes
        remaining = item["remaining_minutes"]

        pct = ((current / required) * 100) if required > 0 else 100
        bar_filled = int(pct / 5)
        bar_empty = 20 - bar_filled
        bar = f"[green]{'█' * bar_filled}[/green][dim]{'░' * bar_empty}[/dim] {pct:.0f}%"

        table.add_row(str(i), rewards, item["game_name"], format_minutes(remaining), bar)

    console.print(table)
    console.print()

    choice = Prompt.ask(
        "  Select a drop to watch [number or 'q' to cancel]",
        default="1",
    )

    if choice.lower() in ("q", "quit", "cancel"):
        return None

    try:
        idx = int(choice) - 1
        if 0 <= idx < len(watchable):
            return watchable[idx]
        else:
            print_error("Invalid selection.")
            return None
    except ValueError:
        print_error("Invalid input. Enter a number.")
        return None


# ── Utilities ──────────────────────────────────────────────────────

def format_minutes(minutes: int) -> str:
    """Format minutes into a human-readable string."""
    if minutes <= 0:
        return "Done!"
    hours = minutes // 60
    mins = minutes % 60
    if hours > 0:
        return f"{hours}h {mins}m"
    return f"{mins}m"


def confirm_action(message: str) -> bool:
    """Ask for confirmation."""
    return Confirm.ask(f"  {message}")
