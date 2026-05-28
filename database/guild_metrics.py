"""Per-guild metric snapshots: one row per tracked guild per poll cycle."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from asyncpg import Pool

from api import Guild


@dataclass(slots=True, frozen=True)
class GuildMetrics:
    """Values captured per poll for one guild."""

    level: int | None
    xp_percent: int | None
    total_raids: int | None
    wars: int | None
    territories: int | None
    member_total: int | None
    online_count: int | None


async def record(pool: Pool, rows: list[Guild]) -> None:
    """Batch-insert one current-time snapshot for many guilds."""
    if not rows:
        return
    await pool.executemany(
        "INSERT INTO guild_metric_snapshots"
        " (guild_uuid, level, xp_percent, total_raids, wars,"
        "  territories, member_total, online_count)"
        " VALUES ($1, $2, $3, $4, $5, $6, $7, $8)"
        " ON CONFLICT (guild_uuid, taken_at) DO NOTHING",
        [
            (
                guild.uuid,
                guild.level,
                guild.xp_percent,
                guild.total_raids,
                guild.wars,
                guild.territories,
                guild.member_total,
                guild.online_total,
            )
            for guild in rows
        ],
    )


async def history(
    pool: Pool, guild_uuid: UUID, days: int
) -> list[tuple[datetime, GuildMetrics]]:
    """Time-series of metric snapshots for one guild over the last N days."""
    rows = await pool.fetch(
        "SELECT taken_at, level, xp_percent, total_raids, wars,"
        "       territories, member_total, online_count"
        " FROM guild_metric_snapshots"
        " WHERE guild_uuid = $1 AND taken_at > NOW() - MAKE_INTERVAL(days => $2)"
        " ORDER BY taken_at",
        guild_uuid,
        days,
    )
    return [(row[0], GuildMetrics(*row[1:])) for row in rows]


async def online_history(
    pool: Pool, guild_uuid: UUID, days: int
) -> dict[datetime, int]:
    """Online-count time-series for one guild over the last N days."""
    rows = await pool.fetch(
        "SELECT taken_at, online_count FROM guild_metric_snapshots"
        " WHERE guild_uuid = $1 AND taken_at > NOW() - MAKE_INTERVAL(days => $2)"
        " AND online_count IS NOT NULL ORDER BY taken_at",
        guild_uuid,
        days,
    )
    return {row[0]: row[1] for row in rows}


async def last_xp_percent(pool: Pool, guild_uuid: UUID) -> int | None:
    """Last xp percentage recorded for the given guild, 0-100."""
    xp_percent: int | None = await pool.fetchval(
        "SELECT xp_percent FROM guild_metric_snapshots"
        " WHERE guild_uuid = $1"
        " ORDER BY taken_at DESC LIMIT 1",
        guild_uuid,
    )
    return xp_percent


async def cleanup(pool: Pool) -> None:
    """Hourly-mean after 7 days, daily-mean after 30 days."""
    await pool.execute(
        "INSERT INTO guild_metric_snapshots"
        " (guild_uuid, taken_at, level, xp_percent, total_raids, wars,"
        "  territories, member_total, online_count)"
        " SELECT guild_uuid, date_trunc('hour', taken_at),"
        "        ROUND(AVG(level))::SMALLINT,"
        "        ROUND(AVG(xp_percent))::SMALLINT,"
        "        ROUND(AVG(total_raids))::INTEGER,"
        "        ROUND(AVG(wars))::INTEGER,"
        "        ROUND(AVG(territories))::SMALLINT,"
        "        ROUND(AVG(member_total))::SMALLINT,"
        "        ROUND(AVG(online_count))::SMALLINT"
        " FROM guild_metric_snapshots"
        " WHERE taken_at < NOW() - '7 days'::interval"
        "   AND taken_at >= NOW() - '30 days'::interval"
        " GROUP BY guild_uuid, date_trunc('hour', taken_at)"
        " ON CONFLICT (guild_uuid, taken_at) DO UPDATE SET"
        "   level = EXCLUDED.level, xp_percent = EXCLUDED.xp_percent,"
        "   total_raids = EXCLUDED.total_raids, wars = EXCLUDED.wars,"
        "   territories = EXCLUDED.territories, member_total = EXCLUDED.member_total,"
        "   online_count = EXCLUDED.online_count"
    )
    await pool.execute(
        "DELETE FROM guild_metric_snapshots"
        " WHERE taken_at < NOW() - '7 days'::interval"
        "   AND taken_at >= NOW() - '30 days'::interval"
        "   AND date_trunc('hour', taken_at) <> taken_at"
    )
    await pool.execute(
        "INSERT INTO guild_metric_snapshots"
        " (guild_uuid, taken_at, level, xp_percent, total_raids, wars,"
        "  territories, member_total, online_count)"
        " SELECT guild_uuid, date_trunc('day', taken_at),"
        "        ROUND(AVG(level))::SMALLINT,"
        "        ROUND(AVG(xp_percent))::SMALLINT,"
        "        ROUND(AVG(total_raids))::INTEGER,"
        "        ROUND(AVG(wars))::INTEGER,"
        "        ROUND(AVG(territories))::SMALLINT,"
        "        ROUND(AVG(member_total))::SMALLINT,"
        "        ROUND(AVG(online_count))::SMALLINT"
        " FROM guild_metric_snapshots"
        " WHERE taken_at < NOW() - '30 days'::interval"
        " GROUP BY guild_uuid, date_trunc('day', taken_at)"
        " ON CONFLICT (guild_uuid, taken_at) DO UPDATE SET"
        "   level = EXCLUDED.level, xp_percent = EXCLUDED.xp_percent,"
        "   total_raids = EXCLUDED.total_raids, wars = EXCLUDED.wars,"
        "   territories = EXCLUDED.territories, member_total = EXCLUDED.member_total,"
        "   online_count = EXCLUDED.online_count"
    )
    await pool.execute(
        "DELETE FROM guild_metric_snapshots"
        " WHERE taken_at < NOW() - '30 days'::interval"
        "   AND date_trunc('day', taken_at) <> taken_at"
    )
