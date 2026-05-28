"""Poll every tracked guild (except Eden) every 5 minutes."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from api import Guild, NotFoundError, WynncraftError
from database import (
    eden,
    guild_membership,
    guild_metrics,
    players,
    tracked_guilds,
)
from database.tracked_guilds import TrackedGuild
from tasks.guild_processor import process_guild
from utils import display_full, display_short, webhooks

if TYPE_CHECKING:
    from client import Pianobot


log = logging.getLogger(__name__)


async def poll_tracked_guilds(bot: Pianobot) -> None:
    """Snapshot every non-Eden tracked guild + compute Eden XP diff for Discord."""
    tracked = await tracked_guilds.all_tracked(bot.pool)
    tracked = [g for g in tracked if g.uuid != bot.eden_wynn_uuid]

    sem = asyncio.Semaphore(4)

    async def fetch(guild: TrackedGuild) -> Guild | None:
        async with sem:
            try:
                return await bot.api.get_uuid_guild(guild.uuid)
            except (NotFoundError, WynncraftError) as exc:
                log.warning("Tracked guild %s fetch failed: %s", guild.name, exc)
                return None

    if fetched := [g for g in await asyncio.gather(*(fetch(g) for g in tracked)) if g]:
        prev_by_guild = await guild_membership.by_guilds(
            bot.pool, [g.uuid for g in fetched]
        )
        for guild in fetched:
            await process_guild(
                bot.pool,
                guild,
                prev_by_guild.get(guild.uuid, {}),
                bot.tracked_poll_state,
                bot.raid_id_cache,
            )
        await guild_metrics.record(bot.pool, fetched)

    await _record_eden_xp_snapshot(bot)
    await _post_xp_diff(bot)


async def _record_eden_xp_snapshot(bot: Pianobot) -> None:
    """Snapshot every current Eden member's contributed_xp at this poll time."""
    if members := await guild_membership.by_guild(bot.pool, bot.eden_wynn_uuid):
        await eden.record_xp(bot.pool, {m.uuid: m.contributed_xp for m in members})


async def _post_xp_diff(bot: Pianobot) -> None:
    latest = await eden.latest_two_xp_times(bot.pool)
    if len(latest) < 2:
        return
    latest_snapshot = await eden.xp_at(bot.pool, latest[0])
    prev_snapshot = await eden.xp_at(bot.pool, latest[1])
    delta_minutes = (latest[0] - latest[1]).total_seconds() / 60
    if delta_minutes <= 0:
        log.warning(
            "Skipping Eden XP diff due to non-positive interval: %.2f min",
            delta_minutes,
        )
        return

    uuid_to_username = await players.usernames_by_uuid(
        bot.pool, list(latest_snapshot.keys())
    )

    diffs: list[tuple[str, int]] = [
        (username, new_xp - old_xp)
        for uuid, new_xp in latest_snapshot.items()
        if (old_xp := prev_snapshot.get(uuid)) is not None
        and new_xp > old_xp
        and (username := uuid_to_username.get(uuid)) is not None
    ]
    if not diffs:
        return

    diffs.sort(key=lambda x: x[1], reverse=True)
    lines: list[str] = ["-" * 80]
    for i, (name, xp) in enumerate(diffs, start=1):
        lines.append(
            f"**#{i} {name}** — `{display_short(xp)} XP |"
            f" {display_short(xp / delta_minutes)} XP/min`"
        )
    total = sum(x for _, x in diffs)
    lines.append(f"**Total: ** `{display_full(total)} XP`")

    await webhooks.send_eden_xp_tracking_messages(bot, lines)
