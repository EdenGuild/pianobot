"""Daily maintenance: thin long-tail snapshots + refresh the tracked-guild set."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING
from uuid import UUID

from api import WynncraftError
from database import eden, guild_metrics, snapshots, tracked_guilds

if TYPE_CHECKING:
    from client import Pianobot


log = logging.getLogger(__name__)


async def cleanup_snapshots(bot: Pianobot) -> None:
    """Thin long-tail snapshot rows across every time-series table."""
    await guild_metrics.cleanup(bot.pool)
    await eden.cleanup_xp(bot.pool)
    await snapshots.cleanup_daily(bot.pool)
    await snapshots.cleanup_online_counts(bot.pool)


async def refresh_tracked_guilds(bot: Pianobot) -> None:
    """Pull top 100 of each guild leaderboard and update the tracked set."""
    merged: dict[UUID, tuple[str, str]] = {}
    for lb in ("guildLevel", "guildTerritories", "guildWars", "guildTotalRaids"):
        try:
            entries = await bot.api.get_guild_leaderboard(lb)
        except WynncraftError as exc:
            log.warning("Leaderboard %s fetch failed: %s", lb, exc)
            continue
        for entry in entries:
            if entry.uuid is None:
                continue
            merged[entry.uuid] = (entry.name, entry.prefix)
    if not merged:
        return
    keep_uuids = [bot.eden_wynn_uuid]

    added, removed = 0, 0
    for attempt in range(3):
        try:
            added, removed = await tracked_guilds.refresh(bot.pool, merged, keep_uuids)
            break
        except Exception as exc:
            if attempt == 2:
                raise
            log.warning("Refreshing tracked guilds raised %s", exc)
            await asyncio.sleep(0.2 * (attempt + 1))

    if added > 0 or removed > 0:
        log.info(
            "Tracked guilds changed: %d entries (+%d, -%d)",
            len(merged),
            added,
            removed,
        )
