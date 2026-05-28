"""Discord webhook delivery for background tasks."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from discord import Embed, File, Webhook
from discord.utils import escape_markdown, format_dt

from api import GuildMember, NotFoundError, TerritorySnapshot, WynncraftError
from tasks.guild_processor import (
    JoinEvent,
    LeaveEvent,
    RankChangeEvent,
    RenameEvent,
    guild_raid_total_xp_reward,
)
from utils import display_full, display_short, format_time_since

if TYPE_CHECKING:
    from client import Pianobot

log = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"

AVATAR_URL = (
    "https://cdn.discordapp.com/avatars/861602324543307786/"
    "83f879567954aee29bc9fd534bc05b1f.webp"
)

RAID_COLORS = {
    "Nest of the Grootslangs": 0x00AA00,
    "Orphion's Nexus of Light": 0xFFAA00,
    "The Canyon Colossus": 0x00AAAA,
    "The Nameless Anomaly": 0x5555FF,
    "The Wartorn Palace": 0xAA0000,
}

RANK_ORDER = ("Recruit", "Recruiter", "Captain", "Strategist", "Chief", "Owner")


async def _send_member_webhook(bot: Pianobot, embed: Embed) -> None:
    """Deliver one embed to ``MEMBER_CHANNEL_WEBHOOK``."""
    if (url := os.getenv("MEMBER_CHANNEL_WEBHOOK")) is None:
        return
    webhook = Webhook.from_url(url, session=bot.session)
    try:
        await webhook.send(
            embed=embed, username="Eden Guild Log", avatar_url=AVATAR_URL
        )
    except Exception as exc:
        log.warning("Member webhook failed: %s", exc)


async def send_eden_guild_join(bot: Pianobot, event: JoinEvent) -> None:
    """Post a guild join notification."""
    member: GuildMember = event.member
    username = escape_markdown(member.username)
    description = f"{username} has joined Eden {format_dt(member.joined_at, 'R')}\n\n"
    try:
        player = await bot.api.get_player(member.uuid)
        first_join = format_dt(player.joined) if player.joined else "Unknown"
        description += (
            f"First join: {first_join}\n"
            f"Playtime: {round(player.playtime)} hours\n"
            f"Total level: {player.total_level}"
        )
    except (NotFoundError, WynncraftError) as exc:
        log.warning("Player fetch for join embed failed: %s", exc)

    embed = Embed(
        title=f"Guild Join: {member.username}",
        description=description,
        color=0x00FF00,
    )
    embed.set_thumbnail(url=f"https://mc-heads.net/avatar/{member.uuid.hex}")
    await _send_member_webhook(bot, embed)


async def send_eden_guild_leave(bot: Pianobot, event: LeaveEvent) -> None:
    """Post a guild leave notification."""
    username = escape_markdown(event.username)
    embed = Embed(
        title=f"Guild Leave: {username}",
        description=(
            f"{username} has left Eden!\n\n"
            f"Joined at: {format_dt(event.prev.joined_at)}\n"
            f"Last rank: {event.prev.rank}\n"
            f"XP contributed: {event.prev.contributed_xp}"
        ),
        color=0xFF0000,
    )
    embed.set_thumbnail(url=f"https://mc-heads.net/avatar/{event.uuid.hex}")
    await _send_member_webhook(bot, embed)


async def send_eden_rename(bot: Pianobot, event: RenameEvent) -> None:
    """Post a username change notification."""
    member = event.member
    old_name = escape_markdown(event.old_username)
    new_name = escape_markdown(member.username)
    embed = Embed(
        title=f"Name Change: {new_name}",
        description=(
            f"{old_name} has changed their name to {new_name}!\n\n"
            f"Guild rank: {member.rank}\n"
            f"Old name: {old_name}\n"
            f"New name: {new_name}"
        ),
        color=0x88FFFF,
    )
    embed.set_thumbnail(url=f"https://mc-heads.net/avatar/{member.uuid.hex}")
    await _send_member_webhook(bot, embed)


async def send_eden_rank_change(bot: Pianobot, event: RankChangeEvent) -> None:
    """Post a guild rank promotion or demotion notification."""
    member = event.member
    try:
        old_i = RANK_ORDER.index(event.old_rank)
        new_i = RANK_ORDER.index(member.rank)
        is_promotion = new_i > old_i
    except ValueError:
        is_promotion = True
    username = escape_markdown(member.username)
    embed = Embed(
        title=f"Guild {'promotion' if is_promotion else 'demotion'}: {username}",
        description=(
            f"{username} has been {'promoted' if is_promotion else 'demoted'}!\n\n"
            f"Old rank: {event.old_rank}\n"
            f"New rank: {member.rank}"
        ),
        color=0x88FF88 if is_promotion else 0xFF8888,
    )
    embed.set_thumbnail(url=f"https://mc-heads.net/avatar/{member.uuid.hex}")
    await _send_member_webhook(bot, embed)


async def send_eden_raid_completed(
    bot: Pianobot, raid_name: str, players: list[str], guild_level: int, xp_percent: int
) -> None:
    """Post one 4-player guild raid completion embed with assets."""
    if (url := os.getenv("RAID_CHANNEL_WEBHOOK")) is None:
        return

    if xp_percent not in bot.eden_raid_xp_counter:
        subtract_raids = 0
        bot.eden_raid_xp_counter.clear()
        bot.eden_raid_xp_counter[xp_percent] = 1
    else:
        subtract_raids = bot.eden_raid_xp_counter[xp_percent]
        bot.eden_raid_xp_counter[xp_percent] += 1
    raids_left = 1000 * (100 - xp_percent) - subtract_raids

    xp_reward = guild_raid_total_xp_reward(guild_level)
    embed = Embed(
        color=RAID_COLORS.get(raid_name) or 0x888888,
        title=":crossed_swords:   Guild Raid completed",
        description="\n".join(
            f":number_{i}:    {escape_markdown(name)}"
            for i, name in enumerate(players, start=1)
        ),
    )
    embed.set_author(name=raid_name)
    embed.set_footer(
        text=f"+2 Aspects, +2048 Emeralds, +{display_short(xp_reward)} XP\n"
        f"{raids_left} Guild Raids until guild level {guild_level + 1}",
        icon_url="attachment://aspect.png",
    )
    embed.set_thumbnail(url="attachment://raid.png")

    raid_asset = ASSETS_DIR / f"{raid_name}.png"
    if not raid_asset.exists():
        raid_asset = ASSETS_DIR / "unknown.png"
    files = [
        File(str(ASSETS_DIR / "aspect.png"), "aspect.png"),
        File(str(raid_asset), "raid.png"),
    ]
    webhook = Webhook.from_url(url, session=bot.session)
    try:
        await webhook.send(
            files=files,
            embed=embed,
            username="Eden Guild Raid Tracking",
            avatar_url=AVATAR_URL,
        )
    except Exception as exc:
        log.warning("Raid webhook failed: %s", exc)


def pack_lines_under_limit(lines: list[str], *, limit: int = 2000) -> list[str]:
    """Pack lines into newline-joined chunks, each at most `limit` characters."""
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in lines:
        added = len(line) + (1 if current else 0)
        if current and current_len + added > limit:
            chunks.append("\n".join(current))
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += added
    if current:
        chunks.append(escape_markdown("\n".join(current)))
    return chunks


async def send_eden_xp_tracking_messages(bot: Pianobot, lines: list[str]) -> None:
    """Post preformatted XP leaderboard lines to the XP webhook."""
    if (url := os.getenv("XP_CHANNEL_WEBHOOK")) is None:
        return
    webhook = Webhook.from_url(url, session=bot.session)
    try:
        for chunk in pack_lines_under_limit(lines, limit=2000):
            await webhook.send(
                chunk, username="Eden XP Tracking", avatar_url=AVATAR_URL
            )
    except Exception as exc:
        log.warning("XP webhook failed: %s", exc)


async def send_territory_change(
    bot: Pianobot,
    *,
    territory: str,
    old_guild: str | None,
    new_guild: str | None,
    held_since: datetime,
    all_snapshots: list[TerritorySnapshot],
    captured: bool,
) -> None:
    """Post a territory capture or loss embed."""
    if (url := os.getenv("TERRITORY_CHANNEL_WEBHOOK")) is None:
        return

    _, held_text = format_time_since(held_since)

    old_count = (
        sum(1 for t in all_snapshots if t.guild_name == old_guild) if old_guild else 0
    )
    new_count = (
        sum(1 for t in all_snapshots if t.guild_name == new_guild) if new_guild else 0
    )

    embed = Embed(
        color=0x00AA00 if captured else 0xAA0000,
        title=":crossed_swords:   Territory " + ("captured" if captured else "lost"),
        description=escape_markdown(
            f"{old_guild or 'Unclaimed'} ({old_count})\n"
            f":arrow_forward:  {new_guild or 'Unclaimed'} ({new_count})"
        ),
    )
    embed.set_author(name=territory)
    embed.set_footer(text=f"Held for {held_text}")

    webhook = Webhook.from_url(url, session=bot.session)
    try:
        await webhook.send(
            embed=embed, username="Eden Territory Tracking", avatar_url=AVATAR_URL
        )
    except Exception as exc:
        log.warning("Territory webhook failed: %s", exc)


async def send_promotion_cycle_awards(
    bot: Pianobot,
    *,
    cycle_code: str,
    raid_results: list[tuple[str, int]],
    war_results: list[tuple[str, int]],
    xp_results: list[tuple[str, int]],
    raffle_winners: list[tuple[str, int, int]],
    total_tickets: int,
) -> None:
    """Post promotion-cycle leaderboard and raffle results."""
    if (url := os.getenv("MEMBER_CHANNEL_WEBHOOK")) is None:
        return

    embed = Embed(title=f"Final award results for promotion cycle  `{cycle_code}`")
    for title, code, result in (
        ("Guild Raids", "gss", raid_results),
        ("Wars", "js", war_results),
        ("Guild XP", "less", xp_results),
    ):
        block = f"```{code}\n"
        for i, (name, amount) in enumerate(result[:9], start=1):
            if amount == 0:
                break
            if title == "Guild XP":
                block += f"{i}. {escape_markdown(name)} (+{display_full(amount)})\n"
            else:
                block += f"{i}. {escape_markdown(name)} (+{amount})\n"
        embed.add_field(name=title, value=block + "```", inline=False)

    header = f"Total tickets: {total_tickets}"
    block = f"```md\n{header}\n{'-' * len(header)}\n"
    for i, (name, tickets, raids) in enumerate(raffle_winners, start=1):
        block += f"{i}. {escape_markdown(name)} ({tickets} tickets for {raids} raids)\n"
    embed.add_field(name="Raid Raffle", value=block + "```", inline=False)

    webhook = Webhook.from_url(url, session=bot.session)
    try:
        await webhook.send(embed=embed, username="Eden Awards", avatar_url=AVATAR_URL)
    except Exception as exc:
        log.warning("Award webhook failed: %s", exc)
