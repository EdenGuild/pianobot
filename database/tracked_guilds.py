"""Set of guilds (every 5 min) by guild uuid; name and prefix come from guild_names."""

from dataclasses import dataclass
from uuid import UUID

from asyncpg import Pool


@dataclass(slots=True, frozen=True)
class TrackedGuild:
    """A polled guild: uuid + current name + prefix (may be None before g-list sync)."""

    uuid: UUID
    name: str
    prefix: str | None


async def all_tracked(pool: Pool) -> list[TrackedGuild]:
    """Every tracked guild with its current name and prefix, sorted alphabetically."""
    rows = await pool.fetch(
        "SELECT t.uuid, g.name, g.prefix FROM tracked_guilds t"
        " JOIN guild_names g ON g.uuid = t.uuid"
        " ORDER BY g.name"
    )
    return [TrackedGuild(*row) for row in rows]


async def all_names(pool: Pool) -> list[str]:
    """Just the names of the tracked guilds (used by autocomplete)."""
    rows = await pool.fetch(
        "SELECT g.name FROM tracked_guilds t"
        " JOIN guild_names g ON g.uuid = t.uuid"
        " ORDER BY g.name"
    )
    return [row[0] for row in rows]


async def refresh(
    pool: Pool,
    guilds: dict[UUID, tuple[str, str]],
    keep_uuids: list[UUID] | None = None,
) -> None:
    """Replace the tracked set with a new one, preserving specified UUIDs."""
    keep = list(guilds.keys())
    if keep_uuids:
        keep.extend(keep_uuids)

    async with pool.acquire() as conn, conn.transaction():
        if guilds:
            uuids = list(guilds.keys())
            names = [guilds[uuid][0] for uuid in uuids]
            prefixes = [guilds[uuid][1] for uuid in uuids]
            await conn.execute(
                "INSERT INTO guild_names (uuid, name, prefix)"
                " SELECT * FROM UNNEST($1::UUID[], $2::TEXT[], $3::TEXT[])"
                " ON CONFLICT (uuid) DO UPDATE SET"
                "   name   = EXCLUDED.name,"
                "   prefix = COALESCE(EXCLUDED.prefix, guild_names.prefix),"
                "   deleted_at = NULL",
                uuids,
                names,
                prefixes,
            )
        await conn.execute(
            "DELETE FROM tracked_guilds WHERE uuid <> ALL($1)",
            keep,
        )
        if guilds:
            await conn.execute(
                "INSERT INTO tracked_guilds (uuid)"
                " SELECT u FROM UNNEST($1::UUID[]) AS t(u)"
                " ON CONFLICT (uuid) DO NOTHING",
                uuids,
            )


async def find(pool: Pool, query: str) -> list[TrackedGuild]:
    """Match by exact name, exact prefix, or partial name (case-insensitive)."""
    rows = await pool.fetch(
        "SELECT t.uuid, g.name, g.prefix FROM tracked_guilds t"
        " JOIN guild_names g ON g.uuid = t.uuid"
        " WHERE LOWER(g.name) = LOWER($1) OR LOWER(g.prefix) = LOWER($1)"
        "    OR LOWER(g.name) LIKE LOWER($2)"
        " ORDER BY"
        "   CASE WHEN LOWER(g.name) = LOWER($1) OR LOWER(g.prefix) = LOWER($1)"
        "        THEN 0 ELSE 1 END,"
        "   g.name",
        query,
        f"%{query}%",
    )
    return [TrackedGuild(*row) for row in rows]
