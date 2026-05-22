-- ============================================================================
-- IDENTITY
-- ============================================================================

-- Every player we've ever observed in any tracked guild.
CREATE TABLE players
(
    uuid          UUID PRIMARY KEY,
    username      TEXT        NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_players_username ON players (username);

CREATE TABLE player_username_history
(
    id           BIGSERIAL PRIMARY KEY,
    uuid         UUID        NOT NULL REFERENCES players (uuid),
    old_username TEXT        NOT NULL,
    new_username TEXT        NOT NULL,
    changed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_player_username_history_uuid ON player_username_history (uuid, changed_at);

-- Guild registry: uuid is canonical; name and prefix may change.
CREATE TABLE guild_names
(
    uuid       UUID PRIMARY KEY,
    name       TEXT NOT NULL,
    prefix     TEXT,
    founded_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ
);
CREATE INDEX idx_guild_names_name ON guild_names (name);
CREATE INDEX idx_guild_names_prefix ON guild_names (prefix);

-- Append-only audit log of guild lifecycle changes detected by the guild-list sync.
CREATE TABLE guild_events
(
    id          BIGSERIAL PRIMARY KEY,
    guild_uuid  UUID        NOT NULL,
    event_type  TEXT        NOT NULL CHECK (event_type IN
                                            ('created', 'deleted', 'renamed',
                                             'retagged')),
    old_name    TEXT,
    new_name    TEXT,
    old_prefix  TEXT,
    new_prefix  TEXT,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_guild_events_guild ON guild_events (guild_uuid, occurred_at);
CREATE INDEX idx_guild_events_type ON guild_events (event_type, occurred_at);

-- The subset of guilds we actively poll every 5 minutes.
CREATE TABLE tracked_guilds
(
    uuid UUID PRIMARY KEY REFERENCES guild_names (uuid)
);

-- ============================================================================
-- GUILD MEMBERSHIP: current state + history
-- ============================================================================

-- One row per player who is currently in some tracked guild.
CREATE TABLE guild_memberships
(
    uuid           UUID PRIMARY KEY REFERENCES players (uuid),
    guild_uuid     UUID        NOT NULL REFERENCES guild_names (uuid),
    rank           TEXT        NOT NULL,
    joined_at      TIMESTAMPTZ NOT NULL,
    contributed_xp BIGINT      NOT NULL DEFAULT 0
);
CREATE INDEX idx_guild_memberships_guild ON guild_memberships (guild_uuid);

-- Append-only log: every join, leave, and rank change we ever detected.
CREATE TABLE guild_membership_events
(
    id          BIGSERIAL PRIMARY KEY,
    uuid        UUID        NOT NULL REFERENCES players (uuid),
    guild_uuid  UUID        NOT NULL REFERENCES guild_names (uuid),
    event_type  TEXT        NOT NULL CHECK (event_type IN ('join', 'leave', 'rank_change')),
    old_rank    TEXT,
    new_rank    TEXT,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_guild_membership_events_uuid ON guild_membership_events (uuid, occurred_at);
CREATE INDEX idx_guild_membership_events_guild ON guild_membership_events (guild_uuid, occurred_at);

-- ============================================================================
-- RAIDS, WARS, XP — including the guild a player was in at that moment.
-- ============================================================================

CREATE TABLE raid_names
(
    id   SMALLSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

INSERT INTO raid_names (name)
VALUES ('Nest of the Grootslangs'),
       ('Orphion''s Nexus of Light'),
       ('The Canyon Colossus'),
       ('The Nameless Anomaly'),
       ('The Wartorn Palace');

CREATE TABLE raid_completions
(
    id           SERIAL PRIMARY KEY,
    uuid         UUID        NOT NULL REFERENCES players (uuid),
    guild_uuid   UUID        NOT NULL REFERENCES guild_names (uuid),
    raid_id      SMALLINT    NOT NULL REFERENCES raid_names (id),
    completed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_raid_completions_completed_at ON raid_completions (completed_at);
CREATE INDEX idx_raid_completions_uuid ON raid_completions (uuid, completed_at);
CREATE INDEX idx_raid_completions_guild ON raid_completions (guild_uuid, completed_at);

CREATE TABLE war_completions
(
    id           SERIAL PRIMARY KEY,
    uuid         UUID        NOT NULL REFERENCES players (uuid),
    guild_uuid   UUID        NOT NULL REFERENCES guild_names (uuid),
    completed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_war_completions_completed_at ON war_completions (completed_at);
CREATE INDEX idx_war_completions_uuid ON war_completions (uuid, completed_at);
CREATE INDEX idx_war_completions_guild ON war_completions (guild_uuid, completed_at);

-- One row per (player, day, guild) summing the XP that player contributed that day.
CREATE TABLE player_xp_daily
(
    uuid       UUID   NOT NULL REFERENCES players (uuid),
    day        DATE   NOT NULL DEFAULT CURRENT_DATE,
    guild_uuid UUID   NOT NULL REFERENCES guild_names (uuid),
    xp_gained  BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (uuid, day, guild_uuid)
);
CREATE INDEX idx_player_xp_daily_day ON player_xp_daily (day);
CREATE INDEX idx_player_xp_daily_guild ON player_xp_daily (guild_uuid, day);

-- ============================================================================
-- GUILD-LEVEL METRIC SNAPSHOTS — one row per guild per poll (5 min).
-- ============================================================================

CREATE TABLE guild_metric_snapshots
(
    guild_uuid   UUID        NOT NULL REFERENCES guild_names (uuid),
    taken_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    level        SMALLINT,
    xp_percent   SMALLINT,
    total_raids  INTEGER,
    wars         INTEGER,
    territories  SMALLINT,
    member_total SMALLINT,
    online_count SMALLINT,
    PRIMARY KEY (guild_uuid, taken_at)
);
CREATE INDEX idx_guild_metric_snapshots_taken_at ON guild_metric_snapshots (taken_at);

-- ============================================================================
-- PER-PLAYER DAILY STAT GAINS — increasing globalData counters.
-- ============================================================================

CREATE TABLE player_stats_daily
(
    uuid               UUID     NOT NULL REFERENCES players (uuid),
    guild_uuid         UUID     NOT NULL REFERENCES guild_names (uuid),
    day                DATE     NOT NULL DEFAULT CURRENT_DATE,
    total_level        SMALLINT NOT NULL DEFAULT 0,
    completed_quests   SMALLINT NOT NULL DEFAULT 0,
    total_dungeons     INTEGER  NOT NULL DEFAULT 0,
    total_raids        INTEGER  NOT NULL DEFAULT 0,
    playtime_hours     INTEGER  NOT NULL DEFAULT 0,
    content_completion SMALLINT NOT NULL DEFAULT 0,
    mobs_killed        BIGINT   NOT NULL DEFAULT 0,
    chests_found       INTEGER  NOT NULL DEFAULT 0,
    world_events       INTEGER  NOT NULL DEFAULT 0,
    lootruns           INTEGER  NOT NULL DEFAULT 0,
    wars               INTEGER  NOT NULL DEFAULT 0,
    caves              INTEGER  NOT NULL DEFAULT 0,
    pvp_kills          INTEGER  NOT NULL DEFAULT 0,
    pvp_deaths         INTEGER  NOT NULL DEFAULT 0,
    PRIMARY KEY (uuid, day, guild_uuid)
);
CREATE INDEX idx_player_stats_daily_day ON player_stats_daily (day);
CREATE INDEX idx_player_stats_daily_guild_day ON player_stats_daily (guild_uuid, day);

-- ============================================================================
-- PER-PLAYER GLOBAL STATS — current values from globalData on the guild poll.
-- ============================================================================

CREATE TABLE player_global_stats
(
    uuid               UUID PRIMARY KEY REFERENCES players (uuid),
    total_level        SMALLINT,
    completed_quests   SMALLINT,
    total_dungeons     INTEGER,
    total_raids        INTEGER,
    playtime_hours     INTEGER,
    content_completion SMALLINT,
    mobs_killed        BIGINT,
    chests_found       INTEGER,
    world_events       INTEGER,
    lootruns           INTEGER,
    wars               INTEGER,
    caves              INTEGER,
    pvp_kills          INTEGER,
    pvp_deaths         INTEGER,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- EDEN-ONLY: weekly objective.
-- ============================================================================

CREATE TABLE eden_weekly_objective
(
    uuid      UUID     NOT NULL REFERENCES players (uuid),
    iso_year  SMALLINT NOT NULL DEFAULT (EXTRACT(isoyear FROM NOW())::SMALLINT),
    iso_week  SMALLINT NOT NULL DEFAULT (EXTRACT(week FROM NOW())::SMALLINT),
    completed BOOLEAN  NOT NULL,
    streak    SMALLINT NOT NULL,
    PRIMARY KEY (uuid, iso_year, iso_week)
);
CREATE INDEX idx_eden_weekly_objective_week ON eden_weekly_objective (iso_year, iso_week);

-- ============================================================================
-- EDEN-ONLY: 5-minute XP snapshots for XP-diff webhook in poll_tracked_guilds.
-- ============================================================================

CREATE TABLE eden_xp_snapshots
(
    uuid           UUID        NOT NULL REFERENCES players (uuid),
    taken_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    contributed_xp BIGINT      NOT NULL,
    PRIMARY KEY (uuid, taken_at)
);
CREATE INDEX idx_eden_xp_snapshots_taken_at ON eden_xp_snapshots (taken_at);

-- ============================================================================
-- EDEN-ONLY: daily activity, award cycles, reward balances + rates.
-- ============================================================================

CREATE TABLE player_daily_activity
(
    uuid    UUID     NOT NULL,
    day     DATE     NOT NULL DEFAULT CURRENT_DATE,
    minutes SMALLINT NOT NULL DEFAULT 0,
    PRIMARY KEY (uuid, day)
);
CREATE INDEX idx_player_daily_activity_day ON player_daily_activity (day);

CREATE TABLE reward_balances
(
    uuid             UUID PRIMARY KEY REFERENCES players (uuid),
    pending_emeralds BIGINT   NOT NULL DEFAULT 0,
    pending_aspects  SMALLINT NOT NULL DEFAULT 0,
    emeralds_blocked BOOLEAN  NOT NULL DEFAULT FALSE,
    aspects_blocked  BOOLEAN  NOT NULL DEFAULT FALSE
);

CREATE TABLE reward_rates
(
    key   TEXT PRIMARY KEY,
    value BIGINT NOT NULL
);

INSERT INTO reward_rates (key, value)
VALUES ('emeralds_per_raid', 1024),
       ('aspects_per_raid', 1);

CREATE TABLE tome_requests
(
    id           SERIAL PRIMARY KEY,
    discord_id   BIGINT      NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at  TIMESTAMPTZ,
    resolution   TEXT CHECK (resolution IN ('granted', 'denied'))
);
CREATE INDEX idx_tome_requests_discord_id ON tome_requests (discord_id, resolved_at);
CREATE INDEX idx_tome_requests_pending ON tome_requests (requested_at) WHERE resolved_at IS NULL;

CREATE TABLE award_cycles
(
    code         TEXT PRIMARY KEY,
    started_at   TIMESTAMPTZ NOT NULL,
    ended_at     TIMESTAMPTZ,
    finalized_at TIMESTAMPTZ
);

-- ============================================================================
-- WORLDS / ONLINE / TERRITORIES
-- ============================================================================

CREATE TABLE player_last_seen
(
    uuid         UUID PRIMARY KEY,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE worlds
(
    name       TEXT PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE online_player_snapshots
(
    taken_at TIMESTAMPTZ NOT NULL DEFAULT (date_trunc('minute', NOW())),
    world    TEXT        NOT NULL,
    count    SMALLINT    NOT NULL,
    PRIMARY KEY (taken_at, world)
);
CREATE INDEX idx_online_player_snapshots_world ON online_player_snapshots (world, taken_at);

CREATE TABLE territories
(
    name       TEXT PRIMARY KEY,
    guild_uuid UUID REFERENCES guild_names (uuid),
    acquired   TIMESTAMPTZ NOT NULL
);

-- Append-only log when a territory's owning guild changes (or first observed owner).
CREATE TABLE territory_ownership_events
(
    id             BIGSERIAL PRIMARY KEY,
    territory_name TEXT        NOT NULL,
    old_guild_uuid UUID REFERENCES guild_names (uuid),
    new_guild_uuid UUID REFERENCES guild_names (uuid),
    acquired_at    TIMESTAMPTZ NOT NULL,
    recorded_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_territory_ownership_events_name
    ON territory_ownership_events (territory_name, recorded_at);
CREATE INDEX idx_territory_ownership_events_new_guild
    ON territory_ownership_events (new_guild_uuid, recorded_at);
