"""Shared per-guild processing used by poll_eden_guild and poll_tracked_guilds.

Computes membership/rank/name changes, raid/war completions, XP per day, global stats.
Guild raid *completions* are reconciled once per guild against ``guild.total_raids``
while accounting for hidden-stat players when the numbers and XP line up.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from uuid import UUID

from asyncpg import Pool

from api import Guild, GuildMember
from database import (
    completions,
    global_stats,
    guild_membership,
    guilds,
    players,
    raids,
    stats_daily,
    xp_daily,
)

log = logging.getLogger(__name__)


# Per-player guild XP from one guild raid slot (quarter of total raid XP).
# Wynncraft uses the same curve as guild level XP; see webhooks embed footer.
def guild_raid_quarter_xp_reward(guild_level: int) -> float:
    """XP contributed to the guild by one player for one guild raid completion."""
    return (100 / 3) * (1.15**guild_level - 1)


def guild_raid_total_xp_reward(guild_level: int) -> float:
    """Total guild XP granted for one full four-player guild raid."""
    return 4 * guild_raid_quarter_xp_reward(guild_level)


@dataclass(slots=True)
class GuildPollState:
    """Latest state across polls so we can compute deltas vs the previous poll."""

    raid_counts: dict[UUID, dict[str, int]] = field(default_factory=dict)
    war_counts: dict[UUID, int] = field(default_factory=dict)
    guild_prev_total_raids: dict[UUID, int] = field(default_factory=dict)
    # Hidden-stat players with a large enough XP delta but no guild.total_raids delta
    owed_raid_slots: dict[UUID, tuple[str, int]] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class NewRaid:
    """One raid completion just inserted, returned for UI dispatch."""

    uuid: UUID
    username: str
    raid_name: str
    guild_uuid: UUID


@dataclass(slots=True, frozen=True)
class JoinEvent:
    """A player who is in this guild's API response but wasn't a member before."""

    member: GuildMember


@dataclass(slots=True, frozen=True)
class LeaveEvent:
    """A player whose previous membership pointed to this guild but who is gone."""

    uuid: UUID
    username: str
    prev: guild_membership.Membership


@dataclass(slots=True, frozen=True)
class RankChangeEvent:
    """A same-guild member who got re-ranked since the last poll."""

    member: GuildMember
    old_rank: str


@dataclass(slots=True, frozen=True)
class RenameEvent:
    """A player whose username changed since the last time we saw them."""

    member: GuildMember
    old_username: str


@dataclass(slots=True)
class GuildPollResult:
    """Everything detected during one process_guild call."""

    new_raids: list[NewRaid] = field(default_factory=list)
    joins: list[JoinEvent] = field(default_factory=list)
    leaves: list[LeaveEvent] = field(default_factory=list)
    rank_changes: list[RankChangeEvent] = field(default_factory=list)
    renames: list[RenameEvent] = field(default_factory=list)


async def process_guild(
    pool: Pool,
    guild: Guild,
    prev_memberships: dict[UUID, guild_membership.Membership],
    state: GuildPollState,
    raid_ids: dict[str, int],
) -> GuildPollResult:
    """Apply every detected delta for one polled guild."""
    await guilds.upsert(pool, guild.uuid, guild.name)

    api_members = {m.uuid: m for m in guild.members}
    all_uuids = list(set(api_members.keys()) | set(prev_memberships.keys()))
    prev_usernames = await players.usernames_by_uuid(pool, all_uuids)
    result = GuildPollResult()

    for uuid, prev in prev_memberships.items():
        if prev.guild_uuid != guild.uuid or uuid in api_members:
            continue
        current = await guild_membership.get(pool, uuid)
        if current is not None and current.guild_uuid != guild.uuid:
            continue
        username = prev_usernames.get(uuid, "")
        await guild_membership.record_event(
            pool, uuid, guild.uuid, "leave", old_rank=prev.rank
        )
        await guild_membership.remove(pool, uuid)
        result.leaves.append(LeaveEvent(uuid, username, prev))

    for member in guild.members:
        await _process_member_core(
            pool,
            guild.uuid,
            member,
            prev_memberships.get(member.uuid),
            prev_usernames.get(member.uuid),
            state,
            result,
        )

    await _reconcile_guild_raids(pool, guild, prev_memberships, state, raid_ids, result)

    for member in guild.members:
        if member.raid_counts:
            prev_counts = state.raid_counts.get(member.uuid, {})
            state.raid_counts[member.uuid] = {
                raid_name: max(count, prev_counts.get(raid_name, 0))
                for raid_name, count in member.raid_counts.items()
            }
        else:
            state.raid_counts.pop(member.uuid, None)
        if member.total_level is not None:
            state.war_counts[member.uuid] = member.wars

    for uuid, prev in prev_memberships.items():
        if prev.guild_uuid != guild.uuid or uuid in api_members:
            continue
        state.raid_counts.pop(uuid, None)
        state.war_counts.pop(uuid, None)
        state.owed_raid_slots.pop(uuid, None)

    state.guild_prev_total_raids[guild.uuid] = guild.total_raids

    return result


async def _process_member_core(
    pool: Pool,
    guild_uuid: UUID,
    member: GuildMember,
    prev_membership: guild_membership.Membership | None,
    prev_username: str | None,
    state: GuildPollState,
    result: GuildPollResult,
) -> None:
    """Membership, XP, wars, global stats — not guild raid completions."""
    await players.upsert(pool, member.uuid, member.username)
    if prev_username is not None and prev_username != member.username:
        result.renames.append(RenameEvent(member, prev_username))

    if prev_membership is None or prev_membership.guild_uuid != guild_uuid:
        if prev_membership is not None:
            await guild_membership.record_event(
                pool,
                member.uuid,
                prev_membership.guild_uuid,
                "leave",
                old_rank=prev_membership.rank,
            )
        await guild_membership.record_event(
            pool, member.uuid, guild_uuid, "join", new_rank=member.rank
        )
        await guild_membership.upsert(
            pool,
            member.uuid,
            guild_uuid,
            member.rank,
            member.joined_at,
            member.contributed_xp,
        )
        result.joins.append(JoinEvent(member))
        state.raid_counts.pop(member.uuid, None)
        state.owed_raid_slots.pop(member.uuid, None)
    else:
        if prev_membership.rank != member.rank:
            await guild_membership.record_event(
                pool,
                member.uuid,
                guild_uuid,
                "rank_change",
                old_rank=prev_membership.rank,
                new_rank=member.rank,
            )
            await guild_membership.update_rank(pool, member.uuid, member.rank)
            result.rank_changes.append(RankChangeEvent(member, prev_membership.rank))
        xp_delta = member.contributed_xp - prev_membership.contributed_xp
        if xp_delta > 0:
            await xp_daily.add_xp(pool, member.uuid, guild_uuid, xp_delta)
        if xp_delta != 0:
            await guild_membership.update_xp(pool, member.uuid, member.contributed_xp)

    war_diff = member.wars - state.war_counts.get(member.uuid, member.wars)
    if war_diff > 0:
        await completions.add_wars(pool, member.uuid, guild_uuid, war_diff)

    raw_stats: dict[str, int | None] = {
        "total_level": member.total_level,
        "completed_quests": member.completed_quests,
        "total_dungeons": member.total_dungeons,
        "total_raids": member.total_raids,
        "playtime_hours": int(member.playtime) if member.playtime is not None else None,
        "content_completion": member.content_completion,
        "mobs_killed": member.mobs_killed,
        "chests_found": member.chests_found,
        "world_events": member.world_events,
        "lootruns": member.lootruns,
        "wars": member.wars,
        "caves": member.caves,
        "pvp_kills": member.pvp_kills,
        "pvp_deaths": member.pvp_deaths,
    }
    prev = await global_stats.get(pool, member.uuid)
    await global_stats.upsert(pool, member.uuid, raw_stats)
    if prev is not None:
        deltas = {
            col: new_val - old_val
            for col in stats_daily.DELTA_COLS
            if (new_val := raw_stats[col]) is not None
            and (old_val := prev[col]) is not None
            and new_val > old_val
        }
        if deltas:
            await stats_daily.add_delta(pool, member.uuid, guild_uuid, deltas)


async def _reconcile_guild_raids(
    pool: Pool,
    guild: Guild,
    prev_memberships: dict[UUID, guild_membership.Membership],
    state: GuildPollState,
    raid_ids: dict[str, int],
    result: GuildPollResult,
) -> None:
    """Insert guild raid completions using per-member counters + guild total."""
    prev_guild_total = state.guild_prev_total_raids.get(guild.uuid, guild.total_raids)
    d_guild = max(0, guild.total_raids - prev_guild_total)
    quarter_xp = guild_raid_quarter_xp_reward(guild.level)
    quarter_xp_int = max(1, int(quarter_xp))
    member_server: dict[UUID, str | None] = {m.uuid: m.server for m in guild.members}

    # Save XP jumps from guild raids for level-100+ guilds
    if guild.level >= 100 and quarter_xp > 0:
        for member in guild.members:
            if member.raid_counts and state.raid_counts.get(member.uuid) is not None:
                continue
            prev_m = prev_memberships.get(member.uuid)
            if prev_m is None or prev_m.guild_uuid != guild.uuid:
                continue
            xp_delta = member.contributed_xp - prev_m.contributed_xp
            if xp_delta <= 0:
                continue
            slots = xp_delta // quarter_xp_int
            if slots <= 0:
                continue
            _, current_owed = state.owed_raid_slots.get(
                member.uuid, (member.username, 0)
            )
            state.owed_raid_slots[member.uuid] = (member.username, current_owed + slots)
            log.debug(
                "XP queue: %s +%d slot(s) to %d owed"
                " (xp_delta=%d, threshold=%d) [guild=%s level=%d]",
                member.username,
                slots,
                current_owed + slots,
                xp_delta,
                quarter_xp_int,
                guild.name,
                guild.level,
            )

    if d_guild == 0:
        return

    # Players whose per-raid counters are exposed.
    visible: list[tuple[UUID, str, str]] = []
    for member in guild.members:
        if not member.raid_counts:
            continue
        prev_raids = state.raid_counts.get(member.uuid)
        if prev_raids is None:
            continue
        for raid_name, new_count in member.raid_counts.items():
            if raid_name not in prev_raids:
                continue
            for _ in range(max(0, new_count - prev_raids.get(raid_name, 0))):
                visible.append((member.uuid, member.username, raid_name))

    s = len(visible)
    if s > 4 * d_guild:
        log.warning(
            "Guild raid mismatch (guild=%s uuid=%s): visible=%s > 4×delta=%s "
            "(prev=%s curr=%s). Inserting visible anyway.",
            guild.name,
            guild.uuid,
            s,
            4 * d_guild,
            prev_guild_total,
            guild.total_raids,
        )

    hidden_slots = max(0, 4 * d_guild - s)
    inferred: list[tuple[UUID, str, str | None]] = []  # (uuid, username, server)

    if hidden_slots > 0:
        if guild.level >= 100 and quarter_xp > 0:
            remaining = hidden_slots
            for uuid, (username, owed) in sorted(
                state.owed_raid_slots.items(), key=lambda t: -t[1][1]
            ):
                if remaining <= 0:
                    break
                take = min(remaining, owed)
                for _ in range(take):
                    inferred.append((uuid, username, member_server.get(uuid)))
                new_owed = owed - take
                if new_owed > 0:
                    state.owed_raid_slots[uuid] = (username, new_owed)
                else:
                    del state.owed_raid_slots[uuid]
                remaining -= take
        else:
            # Below level 100, use current-poll XP delta only, ranked by size.
            candidates: list[tuple[UUID, str, int]] = []
            for member in guild.members:
                if (
                    member.raid_counts
                    and state.raid_counts.get(member.uuid) is not None
                ):
                    continue
                prev_m = prev_memberships.get(member.uuid)
                if prev_m is None or prev_m.guild_uuid != guild.uuid:
                    continue
                xp_delta = member.contributed_xp - prev_m.contributed_xp
                if xp_delta > 0:
                    candidates.append((member.uuid, member.username, xp_delta))
            candidates.sort(key=lambda t: -t[2])
            remaining = hidden_slots
            for uuid, username, _ in candidates:
                if remaining <= 0:
                    break
                inferred.append((uuid, username, member_server.get(uuid)))
                remaining -= 1

    for uuid, username, raid_name in visible:
        await _insert_one_raid(
            pool, guild.uuid, uuid, username, raid_name, raid_ids, result
        )

    # Assign inferred players to incomplete visible buckets, preferring same server.
    # Usually, players with raids hidden also hide online status, so not too important.
    by_raid: dict[str, list[tuple[UUID, str]]] = defaultdict(list)
    for uuid, username, raid_name in visible:
        by_raid[raid_name].append((uuid, username))

    # Consensus server per bucket: most common non-None server among visible members.
    bucket_server: dict[str, str | None] = {}
    for raid_name, bucket in by_raid.items():
        servers = [sv for uid, _ in bucket if (sv := member_server.get(uid))]
        bucket_server[raid_name] = (
            max(set(servers), key=servers.count) if servers else None
        )

    inferred_q = list(inferred)
    for raid_name in sorted(
        by_raid, key=lambda n: (-(len(by_raid[n]) % 4), -len(by_raid[n]), n)
    ):
        bucket = by_raid[raid_name]
        target_server = bucket_server.get(raid_name)
        while len(bucket) % 4 != 0 and inferred_q:
            # Prefer a player currently on the same server as the visible raiders.
            best_idx = 0
            if target_server:
                for i, (_, _, srv) in enumerate(inferred_q):
                    if srv == target_server:
                        best_idx = i
                        break
            uuid, username, server = inferred_q.pop(best_idx)
            await _insert_one_raid(
                pool, guild.uuid, uuid, username, raid_name, raid_ids, result
            )
            if target_server:
                if server == target_server:
                    log.debug(
                        "Guild %s: Inferred %s to %s (server match: %s)",
                        guild.name,
                        username,
                        raid_name,
                        server,
                    )
                else:
                    log.debug(
                        "Guild %s: Inferred %s to %s (mismatch player=%s bucket=%s)",
                        guild.name,
                        username,
                        raid_name,
                        server or "offline",
                        target_server,
                    )
            else:
                log.debug(
                    "Guild %s: Inferred %s to %s (no server info)",
                    guild.name,
                    username,
                    raid_name,
                )
            bucket.append((uuid, username))

    if inferred_q:
        log.warning(
            "Guild %s: %d inferred players could not be resolved",
            guild.name,
            len(inferred_q),
        )

    # Log per-raid attribution summary.
    visible_counts: dict[str, int] = {}
    for _, _, r in visible:
        visible_counts[r] = visible_counts.get(r, 0) + 1
    for raid_name, bucket in by_raid.items():
        full_groups = len(bucket) // 4
        leftover = len(bucket) % 4
        v = visible_counts.get(raid_name, 0)
        inf = len(bucket) - v
        if full_groups:
            log.debug(
                "Guild %s: %d complete group(s) of %s (%d visible, %d inferred).",
                guild.name,
                full_groups,
                raid_name,
                v,
                inf,
            )
        if leftover:
            names = ", ".join(username for _, username in bucket[-leftover:])
            log.debug(
                "Guild %s: %d player(s) in incomplete %s group",
                guild.name,
                leftover,
                raid_name,
                names,
            )


async def _insert_one_raid(
    pool: Pool,
    guild_uuid: UUID,
    uuid: UUID,
    username: str,
    raid_name: str,
    raid_ids: dict[str, int],
    result: GuildPollResult,
) -> None:
    if (raid_id := raid_ids.get(raid_name)) is None:
        raid_id = await raids.get_or_create_id(pool, raid_name)
        raid_ids[raid_name] = raid_id
    await completions.add_raid(pool, uuid, guild_uuid, raid_id)
    result.new_raids.append(NewRaid(uuid, username, raid_name, guild_uuid))
