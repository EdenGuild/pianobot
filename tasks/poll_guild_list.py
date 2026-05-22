"""Hourly sync of the full Wynncraft guild list into guild_names."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING
from uuid import UUID

from api import NotFoundError, WynncraftError
from database import guilds

if TYPE_CHECKING:
    from client import Pianobot

log = logging.getLogger(__name__)


async def poll_guild_list(bot: Pianobot) -> None:
    """Fetch the full guild list, sync into guild_names, and backfill founding dates."""
    try:
        incoming = await bot.api.get_guild_list()
    except WynncraftError as exc:
        log.warning("Guild list fetch failed: %s", exc)
        return

    if not incoming:
        log.warning("Guild list payload empty; skipping sync for this cycle")
        return

    await _sync(bot, incoming)
    await _backfill_founded_at(bot)


async def _sync(bot: Pianobot, incoming: dict[UUID, tuple[str, str]]) -> None:
    """Diff incoming guild list against the DB, emit events, and upsert all rows."""
    current = await guilds.all_with_prefix(bot.pool)

    incoming_set = set(incoming.keys())
    current_set = set(current.keys())

    events: list[tuple[UUID, str, str | None, str | None, str | None, str | None]] = []

    for uuid in current_set - incoming_set:
        old_name, old_prefix = current[uuid]
        events.append((uuid, "deleted", old_name, None, old_prefix, None))
        log.info("Guild deleted: %s [%s]", old_name, old_prefix)

    for uuid in incoming_set - current_set:
        new_name, new_prefix = incoming[uuid]
        events.append((uuid, "created", None, new_name, None, new_prefix))
        log.info("Guild created: %s [%s]", new_name, new_prefix)

    for uuid in incoming_set & current_set:
        old_name, old_prefix = current[uuid]
        new_name, new_prefix = incoming[uuid]
        if old_name != new_name:
            events.append((uuid, "renamed", old_name, new_name, None, None))
            log.info("Guild renamed: %s to %s", old_name, new_name)
        if (old_prefix or "") != (new_prefix or ""):
            events.append((uuid, "retagged", None, None, old_prefix, new_prefix))
            log.info("Guild retagged: %s [%s to %s]", new_name, old_prefix, new_prefix)

    await guilds.insert_events(bot.pool, events)
    deleted_uuids = list(current_set - incoming_set)
    if deleted_uuids:
        await guilds.bulk_set_deleted(bot.pool, deleted_uuids)
    await guilds.bulk_upsert_full(bot.pool, incoming)


async def _backfill_founded_at(bot: Pianobot) -> None:
    """Fill `founded_at` for guilds that are missing it."""
    for uuid in await guilds.missing_founded_at(bot.pool):
        try:
            guild = await bot.api.get_uuid_guild(uuid)
        except (NotFoundError, WynncraftError) as exc:
            log.warning("Could not fetch founding date for %s: %s", uuid, exc)
            continue
        await guilds.set_founded_at(bot.pool, uuid, guild.created_at)
        await asyncio.sleep(1)
