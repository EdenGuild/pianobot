"""Poll the territory endpoint and persist ownership + Eden alerts."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from api import WynncraftError
from database import guilds, world_state
from utils import webhooks

if TYPE_CHECKING:
    from client import Pianobot


log = logging.getLogger(__name__)


async def poll_territories(bot: Pianobot) -> None:
    """Diff current territories against stored state; notify on Eden-related changes."""
    try:
        api_terrs = await bot.api.get_territories()
    except WynncraftError as exc:
        log.warning("Territory fetch failed: %s", exc)
        return

    upserts = {
        t.guild_uuid: t.guild_name
        for t in api_terrs
        if t.guild_uuid is not None and t.guild_name is not None
    }
    if upserts:
        await guilds.bulk_upsert(bot.pool, upserts)

    known_names = await guilds.name_by_uuid(bot.pool)
    stored_terrs = await world_state.all_territories(bot.pool)

    for terr in api_terrs:
        if (current := stored_terrs.get(terr.name)) is None:
            await world_state.add_territory(
                bot.pool, terr.name, terr.guild_uuid, terr.acquired_at
            )
            await world_state.record_territory_ownership_change(
                bot.pool,
                terr.name,
                None,
                terr.guild_uuid,
                terr.acquired_at,
            )
            continue
        if current.guild_uuid == terr.guild_uuid:
            continue
        await world_state.record_territory_ownership_change(
            bot.pool,
            terr.name,
            current.guild_uuid,
            terr.guild_uuid,
            terr.acquired_at,
        )
        await world_state.update_territory(
            bot.pool, terr.name, terr.guild_uuid, terr.acquired_at
        )
        if bot.eden_wynn_uuid in (current.guild_uuid, terr.guild_uuid):
            await webhooks.send_territory_change(
                bot,
                territory=terr.name,
                old_guild=known_names.get(current.guild_uuid)
                if current.guild_uuid
                else None,
                new_guild=terr.guild_name,
                held_since=current.acquired,
                all_snapshots=api_terrs,
                captured=bot.eden_wynn_uuid == terr.guild_uuid,
            )
