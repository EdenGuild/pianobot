"""Pianobot discord.py client and entry point."""

import logging
import os
from uuid import UUID

from aiohttp import ClientSession
from asyncpg import Pool, create_pool
from discord import Client, Intents, Object, app_commands

from api import WynncraftClient
from commands import (
    EdenCommands,
    GuildCommands,
    LeaderboardCommands,
    ManageCommands,
    PlayerCommands,
    WynnCommands,
)
from tasks import GuildPollState, start_scheduler
from views import TomeButtonView

log = logging.getLogger(__name__)


class Pianobot(Client):
    """Discord client with API, DB pool, and scheduler hooked up at startup."""

    pool: Pool
    session: ClientSession
    api: WynncraftClient

    def __init__(self) -> None:
        """Set up intents and runtime state; everything async lives in setup_hook."""
        super().__init__(intents=Intents.default())
        self.tree = app_commands.CommandTree(self)
        self.eden_poll_state = GuildPollState()
        self.tracked_poll_state = GuildPollState()
        self.raid_id_cache: dict[str, int] = {}
        self.eden_raid_xp_counter: dict[int, int] = {}
        self.eden_wynn_uuid = UUID(os.environ["EDEN_WYNN_UUID"])
        self._scheduler_started = False

    async def setup_hook(self) -> None:
        """Open pool, apply schema, register commands, sync the tree."""
        self.pool = await create_pool(
            os.environ["PIANOBOT_DB_URL"], server_settings={"TimeZone": "UTC"}
        )
        self.session = ClientSession()
        self.api = WynncraftClient(self.session, os.getenv("WYNN_API_TOKEN"))

        if tome_message_id := os.getenv("TOME_MESSAGE_ID"):
            self.add_view(TomeButtonView(self), message_id=int(tome_message_id))

        eden = Object(int(os.environ["EDEN_DISCORD_ID"]))
        self.tree.add_command(GuildCommands(self))
        self.tree.add_command(LeaderboardCommands(self))
        self.tree.add_command(PlayerCommands(self))
        self.tree.add_command(WynnCommands(self))
        self.tree.add_command(EdenCommands(self), guild=eden)
        self.tree.add_command(ManageCommands(self), guild=eden)
        await self.tree.sync()
        await self.tree.sync(guild=eden)

    async def on_ready(self) -> None:
        """Start the scheduler on the first ready event (ignores reconnects)."""
        log.info("Booted up")
        if not self._scheduler_started:
            self._scheduler_started = True
            start_scheduler(self)

    async def close(self) -> None:
        """Close the HTTP session and DB pool, then tear down the client."""
        try:
            if getattr(self, "pool", None):
                await self.pool.close()
            if getattr(self, "session", None):
                await self.session.close()
        finally:
            await super().close()


if __name__ == "__main__":
    logging.getLogger("discord.gateway").setLevel(logging.WARNING)
    Pianobot().run(os.environ["DISCORD_TOKEN"], root_logger=True)
