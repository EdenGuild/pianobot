"""Async scheduler spinning up one long-running coroutine per background task."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import TYPE_CHECKING

from tasks.award_cycle import award_cycle_tick
from tasks.maintenance import cleanup_snapshots, refresh_tracked_guilds
from tasks.poll_eden_guild import poll_eden_guild
from tasks.poll_guild_list import poll_guild_list
from tasks.poll_online import poll_online
from tasks.poll_tracked_guilds import poll_tracked_guilds
from tasks.territories import poll_territories

if TYPE_CHECKING:
    from client import Pianobot


log = logging.getLogger(__name__)


async def _run_periodically(
    bot: Pianobot, name: str, interval: float, fn: Callable[[Pianobot], Awaitable[None]]
) -> None:
    while not bot.is_closed():
        start = perf_counter()
        try:
            await fn(bot)
        except Exception:
            log.exception("Task %s failed", name)
        else:
            log.debug("Task %s done in %.2fs", name, perf_counter() - start)
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            return


def start_scheduler(bot: Pianobot) -> None:
    """Kick off every periodic task as its own asyncio.Task."""
    jobs = [
        ("poll_eden_guild", 30, poll_eden_guild),
        ("poll_territories", 30, poll_territories),
        ("poll_online", 60, poll_online),
        ("poll_tracked_guilds", 300, poll_tracked_guilds),
        ("award_cycle_tick", 300, award_cycle_tick),
        ("poll_guild_list", 3600, poll_guild_list),
        ("refresh_tracked_guilds", 86400, refresh_tracked_guilds),
        ("cleanup_snapshots", 86400, cleanup_snapshots),
    ]
    for name, interval, fn in jobs:
        bot.loop.create_task(
            _run_periodically(bot, name, interval, fn), name=f"pianobot-{name}"
        )
    log.debug("Started %d periodic tasks", len(jobs))
