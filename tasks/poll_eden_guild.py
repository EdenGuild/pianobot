"""Poll Eden two minutes; apply membership/raid/war/XP deltas; refresh DB."""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from heapq import heapify, heappop, heappush
from typing import TYPE_CHECKING
from uuid import UUID

from api import NotFoundError, WynncraftError
from database import eden, guild_membership, guild_metrics
from tasks.guild_processor import NewRaid, process_guild
from utils import webhooks

if TYPE_CHECKING:
    from client import Pianobot

log = logging.getLogger(__name__)


async def poll_eden_guild(bot: Pianobot) -> None:
    """One Eden poll cycle: detect deltas, persist rewards/metrics, notify Discord."""
    try:
        guild = await bot.api.get_uuid_guild(bot.eden_wynn_uuid)
    except (NotFoundError, WynncraftError) as exc:
        log.warning("Eden guild fetch failed: %s", exc)
        return

    prev_memberships = {
        m.uuid: m for m in await guild_membership.by_guild(bot.pool, guild.uuid)
    }
    result = await process_guild(
        bot.pool, guild, prev_memberships, bot.eden_poll_state, bot.raid_id_cache
    )

    if result.new_raids:
        emeralds_rate = await eden.get_rate(bot.pool, "emeralds_per_raid", 1024)
        aspects_rate = await eden.get_rate(bot.pool, "aspects_per_raid", 1)
        for raid in result.new_raids:
            await eden.ensure_reward_balance(bot.pool, raid.uuid)
            await eden.record_raid_completion(
                bot.pool, raid.uuid, emeralds_rate, aspects_rate
            )

    for member in guild.members:
        if member.weekly_completed is None or member.weekly_streak is None:
            continue
        await eden.upsert_weekly_objective(
            bot.pool, member.uuid, member.weekly_completed, member.weekly_streak
        )

    await guild_metrics.record(bot.pool, [guild])

    for join in result.joins:
        await webhooks.send_eden_guild_join(bot, join)
    for rename in result.renames:
        await webhooks.send_eden_rename(bot, rename)
    for change in result.rank_changes:
        await webhooks.send_eden_rank_change(bot, change)
    for leave in result.leaves:
        await webhooks.send_eden_guild_leave(bot, leave)

    if result.new_raids:
        await _dispatch_raid_groups(bot, result.new_raids, guild.level)


async def _dispatch_raid_groups(
    bot: Pianobot, new_raids: list[NewRaid], guild_level: int
) -> None:
    """Post one webhook embed per full party of four, per raid name."""
    eden_xp_percent = await guild_metrics.last_xp_percent(bot.pool, bot.eden_wynn_uuid)

    by_raid: dict[str, list[NewRaid]] = defaultdict(list)
    for raid in new_raids:
        by_raid[raid.raid_name].append(raid)

    for raid_name in sorted(by_raid.keys()):
        by_uuid: dict[UUID, deque[NewRaid]] = defaultdict(deque)
        for raid in by_raid[raid_name]:
            by_uuid[raid.uuid].append(raid)
        heap = [(-len(q), q[0].username.lower(), uuid) for uuid, q in by_uuid.items()]
        heapify(heap)

        while heap:
            group: list[NewRaid] = []
            leftover = []
            while len(group) < 4 and heap:
                neg_rem, name, uuid = heappop(heap)
                group.append(by_uuid[uuid].popleft())
                if -neg_rem > 1:
                    leftover.append((neg_rem + 1, name, uuid))
            for item in leftover:
                heappush(heap, item)
            if len(group) == 4:
                usernames = [r.username for r in group]
                await webhooks.send_eden_raid_completed(
                    bot, raid_name, usernames, guild_level, eden_xp_percent or 0
                )
            else:
                log.warning(
                    "%d players for %s not in a party of 4 - all new raids: %s",
                    len(group),
                    raid_name,
                    new_raids,
                )
