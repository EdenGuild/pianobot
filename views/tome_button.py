"""Persistent "Join queue" button and helper to re-render the queue."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from discord import ButtonStyle, DiscordException, Interaction, TextChannel, ui

from database import tomes
from utils import format_time_since
from views.paginator import send_table

if TYPE_CHECKING:
    from client import Pianobot


RECEIVED_CAP = 16
PENDING_CAP = 3


class TomeButtonView(ui.View):
    """Persistent view holding the single "Join queue" button."""

    def __init__(self, bot: Pianobot) -> None:
        """Persist forever; we need the bot for pool access inside the callback."""
        super().__init__(timeout=None)
        self.bot = bot

    @ui.button(
        style=ButtonStyle.green,
        label="Join queue",
        custom_id="guild-tome-button",
    )
    async def join(
        self, interaction: Interaction, _: ui.Button[TomeButtonView]
    ) -> None:
        """Enforce per-user caps, enqueue the request, and refresh the queue view."""
        discord_id = interaction.user.id
        pending, granted, last_request = await tomes.stats_for(
            self.bot.pool, discord_id
        )
        if pending >= PENDING_CAP:
            await interaction.response.send_message(
                f"You already have {PENDING_CAP} tome requests in the queue.",
                ephemeral=True,
            )
            return
        if granted >= RECEIVED_CAP:
            await interaction.response.send_message(
                f"You have already received {RECEIVED_CAP} tomes"
                " and cannot queue up again.",
                ephemeral=True,
            )
            return
        if last_request and last_request + timedelta(days=7) > datetime.now(UTC):
            await interaction.response.send_message(
                "You have already requested a tome in the last week.",
                ephemeral=True,
            )
            return
        await tomes.add_request(self.bot.pool, discord_id)
        await interaction.response.send_message(
            "Successfully queued up for a guild tome!", ephemeral=True
        )
        await post_queue(
            self.bot,
            f"{interaction.user.display_name} queued up for a guild tome."
            "\nCurrently pending tomes:",
        )


async def post_queue(bot: Pianobot, header: str) -> None:
    """Re-render the pending-tome queue into the configured log channel."""
    if not (tome_channel_id := os.getenv("TOME_CHANNEL_ID")):
        return
    if not isinstance(channel := bot.get_channel(int(tome_channel_id)), TextChannel):
        return

    guild = bot.get_guild(int(os.environ["EDEN_DISCORD_ID"]))
    if guild is None:
        return

    pending = await tomes.pending(bot.pool)
    rows: list[list[str]] = []
    for discord_id, (pending_count, granted, latest) in reversed(pending.items()):
        try:
            member = await guild.fetch_member(discord_id)
        except DiscordException:
            continue
        display = member.display_name.lstrip("♔♕♜♝♞♙◉ ")[:24]
        rows.append(
            [
                display,
                str(pending_count),
                str(granted),
                f"{format_time_since(latest)[1]} ago",
            ]
        )

    if not rows:
        await channel.send(f"{header} None!")
        return

    columns = {"Discord Name": 28, "Requested": 11, "Received": 10, "Requested At": 18}
    await send_table(
        channel.send,
        columns,
        rows,
        flippable=False,
        separator_rows=0,
        leading_text=header,
    )
