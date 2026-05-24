"""Guild uuid to name, prefix, founding-date registry and guild event log."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from asyncpg import Pool


@dataclass(slots=True, frozen=True)
class KnownGuild:
    """One row from ``guild_names``."""

    uuid: UUID
    name: str
    prefix: str | None
    founded_at: datetime | None
    deleted_at: datetime | None = None


@dataclass(slots=True, frozen=True)
class GuildEvent:
    """One row from the ``guild_events`` audit log."""

    id: int
    guild_uuid: UUID
    event_type: str
    old_name: str | None
    new_name: str | None
    old_prefix: str | None
    new_prefix: str | None
    occurred_at: datetime


async def name_by_uuid(pool: Pool) -> dict[UUID, str]:
    """Every known guild mapped uuid to name."""
    rows = await pool.fetch("SELECT uuid, name FROM guild_names")
    return {row[0]: row[1] for row in rows}


async def all_known(pool: Pool) -> list[KnownGuild]:
    """Every known guild sorted by name (used by autocomplete)."""
    rows = await pool.fetch(
        "SELECT uuid, name, prefix, founded_at, deleted_at"
        " FROM guild_names"
        " ORDER BY name"
    )
    return [KnownGuild(*row) for row in rows]


async def all_with_prefix(pool: Pool) -> dict[UUID, tuple[str, str | None]]:
    """Lightweight snapshot: every guild's current name and prefix."""
    rows = await pool.fetch(
        "SELECT uuid, name, prefix FROM guild_names WHERE deleted_at IS NULL"
    )
    return {row[0]: (row[1], row[2]) for row in rows}


async def upsert(pool: Pool, uuid: UUID, name: str, prefix: str | None = None) -> None:
    """Insert or update a guild by UUID."""
    await pool.execute(
        "INSERT INTO guild_names (uuid, name, prefix) VALUES ($1, $2, $3)"
        " ON CONFLICT (uuid) DO UPDATE SET"
        "   name   = EXCLUDED.name,"
        "   prefix = COALESCE(EXCLUDED.prefix, guild_names.prefix),"
        "   deleted_at = NULL",
        uuid,
        name,
        prefix,
    )


async def bulk_upsert(pool: Pool, guilds: dict[UUID, str]) -> None:
    """Upsert many ``(uuid, name)`` pairs in one round-trip."""
    if not guilds:
        return
    uuids = list(guilds.keys())
    names = [guilds[uuid] for uuid in uuids]
    await pool.execute(
        "INSERT INTO guild_names (uuid, name)"
        " SELECT u, n FROM UNNEST($1::UUID[], $2::TEXT[]) AS t(u, n)"
        " ON CONFLICT (uuid) DO UPDATE SET"
        "   name = EXCLUDED.name,"
        "   deleted_at = NULL",
        uuids,
        names,
    )


async def bulk_upsert_full(pool: Pool, guilds: dict[UUID, tuple[str, str]]) -> None:
    """Upsert name and prefix for many guilds, overwriting any stored prefix."""
    if not guilds:
        return
    uuids = list(guilds.keys())
    names, prefixes = zip(*guilds.values(), strict=True)
    await pool.execute(
        "INSERT INTO guild_names (uuid, name, prefix)"
        " SELECT * FROM UNNEST($1::UUID[], $2::TEXT[], $3::TEXT[])"
        " ON CONFLICT (uuid) DO UPDATE SET"
        "   name = EXCLUDED.name,"
        "   prefix = EXCLUDED.prefix,"
        "   deleted_at = NULL",
        uuids,
        list(names),
        list(prefixes),
    )


async def bulk_set_deleted(pool: Pool, uuids: list[UUID]) -> None:
    """Mark the specified guilds as deleted."""
    if not uuids:
        return
    await pool.execute(
        "UPDATE guild_names SET deleted_at = NOW() WHERE uuid = ANY($1::UUID[])",
        uuids,
    )


async def set_founded_at(pool: Pool, uuid: UUID, founded_at: datetime) -> None:
    """Write the guild's actual founding date (only if not already set)."""
    await pool.execute(
        "UPDATE guild_names SET founded_at = $2 WHERE uuid = $1 AND founded_at IS NULL",
        uuid,
        founded_at,
    )


async def insert_events(
    pool: Pool,
    events: list[tuple[UUID, str, str | None, str | None, str | None, str | None]],
) -> None:
    """Batch-insert guild lifecycle events (guild_uuid, type, old/new name/prefix)."""
    if not events:
        return
    await pool.executemany(
        "INSERT INTO guild_events"
        "  (guild_uuid, event_type, old_name, new_name, old_prefix, new_prefix)"
        " VALUES ($1, $2, $3, $4, $5, $6)",
        events,
    )


async def missing_founded_at(pool: Pool) -> list[UUID]:
    """UUIDs of guilds whose `founded_at` is still NULL, up to `limit` rows."""
    rows = await pool.fetch("SELECT uuid FROM guild_names WHERE founded_at IS NULL")
    return [row[0] for row in rows]


async def events_for_guild(
    pool: Pool, guild_uuid: UUID, limit: int = 50
) -> list[GuildEvent]:
    """Recent guild lifecycle events (newest first, capped at `limit`)."""
    rows = await pool.fetch(
        "SELECT id, guild_uuid, event_type, old_name, new_name,"
        " old_prefix, new_prefix, occurred_at"
        " FROM guild_events WHERE guild_uuid = $1 ORDER BY occurred_at DESC LIMIT $2",
        guild_uuid,
        limit,
    )
    return [GuildEvent(*row) for row in rows]
