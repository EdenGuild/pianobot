"""`/manage` Eden-only manager-gated slash group."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal
from uuid import UUID

from asyncpg import Pool
from discord import Interaction, Member, app_commands

from database import eden, players, tomes
from views import ConfirmResetAllView, post_queue

if TYPE_CHECKING:
    from client import Pianobot


RewardKind = Literal["emeralds", "aspects"]
_RATE_KEYS: dict[RewardKind, str] = {
    "emeralds": "emeralds_per_raid",
    "aspects": "aspects_per_raid",
}


async def _resolve(pool: Pool, query: str) -> UUID | None:
    try:
        return UUID(query)
    except ValueError:
        return await players.resolve_username(pool, query)


class _TomeGroup(app_commands.Group, name="tome"):
    """Manage the Eden guild tome queue."""

    def __init__(self, bot: Pianobot) -> None:
        """Bind the subgroup to the owning bot for pool access."""
        super().__init__()
        self.bot = bot

    @app_commands.command()
    async def list(self, interaction: Interaction) -> None:
        """Show all Discord members currently in the tome queue."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        pending = await tomes.pending(self.bot.pool)
        if not pending:
            await interaction.followup.send("The tome queue is empty.")
            return
        lines: list[str] = []
        for discord_id, (p_count, granted, latest) in pending.items():
            member = interaction.guild and interaction.guild.get_member(discord_id)
            name = member.display_name if member else str(discord_id)
            ts = f"<t:{int(latest.timestamp())}:R>" if latest else "?"
            lines.append(
                f"**{name}** — {p_count} pending, {granted} granted total (last: {ts})"
            )
        await interaction.followup.send("\n".join(lines))

    @app_commands.command()
    async def grant(self, interaction: Interaction, user: Member) -> None:
        """Mark the user's oldest pending request as granted."""
        if await tomes.resolve_oldest(self.bot.pool, user.id, "granted"):
            await interaction.response.send_message(
                f"{user.display_name} has been removed from the tome queue.",
                ephemeral=True,
            )
            await post_queue(
                self.bot,
                f"{interaction.user.mention} granted {user.display_name}'s request.\n"
                "Currently pending tomes:",
            )
        else:
            await interaction.response.send_message(
                f"{user.display_name} is not queued up."
            )

    @app_commands.command()
    async def deny(self, interaction: Interaction, user: Member) -> None:
        """Mark the user's oldest pending request as denied."""
        if await tomes.resolve_oldest(self.bot.pool, user.id, "denied"):
            await interaction.response.send_message(
                f"{user.display_name} has been removed from the tome queue.",
                ephemeral=True,
            )
            await post_queue(
                self.bot,
                f"{interaction.user.mention} denied {user.display_name}'s request.\n"
                "Currently pending tomes:",
            )
        else:
            await interaction.response.send_message(
                f"{user.display_name} is not queued up."
            )


@app_commands.default_permissions(administrator=True)
class ManageCommands(app_commands.Group, name="manage"):
    """Manager-only Eden actions: reward resets, rates, blocks, tome queue."""

    def __init__(self, bot: Pianobot) -> None:
        """Bind the group (and its tome subgroup) to the owning bot."""
        super().__init__()
        self.bot = bot
        self.add_command(_TomeGroup(bot))

    @app_commands.command()
    @app_commands.describe(
        kind="Which reward to reset", username='Username, UUID, or "all"'
    )
    async def reset(
        self, interaction: Interaction, kind: RewardKind, username: str
    ) -> None:
        """Reset a member's pending balance (or all members')."""
        member = interaction.user
        if not isinstance(member, Member):
            await interaction.response.send_message("Only executable in guilds.")
            return
        if kind == "emeralds" and not member.guild_permissions.administrator:
            await interaction.response.send_message("Emerald commands for admins only.")
            return

        if username.lower() == "all":
            await interaction.response.send_message(
                f"Are you sure you want to reset **all** pending {kind} balances?",
                view=ConfirmResetAllView(kind, self.bot),
            )
            return
        await interaction.response.defer(thinking=True)
        if (uuid := await _resolve(self.bot.pool, username)) is None:
            await interaction.followup.send(f"No member named `{username}` found.")
            return
        await eden.reset_pending(self.bot.pool, uuid, kind)
        await interaction.followup.send(
            f"Pending {kind} of `{username}` have been reset."
        )

    @app_commands.command()
    @app_commands.describe(kind="Which reward rate to set", amount="Per-raid amount")
    async def rate(
        self, interaction: Interaction, kind: RewardKind, amount: int
    ) -> None:
        """Set the per-raid rate for emeralds or aspects."""
        member = interaction.user
        if not isinstance(member, Member):
            await interaction.response.send_message("Only executable in guilds.")
            return
        if kind == "emeralds" and not member.guild_permissions.administrator:
            await interaction.response.send_message("Emerald commands for admins only.")
            return
        if amount < 0:
            await interaction.response.send_message("Amount cannot be negative.")
            return

        await eden.set_rate(self.bot.pool, _RATE_KEYS[kind], amount)
        unit = eden.display_unit(kind)
        unit_name = "LE" if kind == "emeralds" else "aspects"
        per_raid = round(amount / unit, 4)
        await interaction.response.send_message(
            f"{kind.capitalize()} rate set to `{amount}` per raid"
            f" (`{per_raid}` {unit_name} per raid)."
        )

    @app_commands.command()
    @app_commands.describe(kind="Which reward to block", username="Username or UUID")
    async def block(
        self, interaction: Interaction, kind: RewardKind, username: str
    ) -> None:
        """Stop a member from receiving the chosen raid reward."""
        await interaction.response.defer(thinking=True)
        if (uuid := await _resolve(self.bot.pool, username)) is None:
            await interaction.followup.send(f"No member named `{username}` found.")
            return
        await eden.set_blocked(self.bot.pool, uuid, kind, True)
        await interaction.followup.send(f"`{username}` is no longer receiving {kind}.")

    @app_commands.command()
    async def rates(self, interaction: Interaction) -> None:
        """Show the current per-raid reward rates for emeralds and aspects."""
        em_rate = await eden.get_rate(self.bot.pool, _RATE_KEYS["emeralds"])
        as_rate = await eden.get_rate(self.bot.pool, _RATE_KEYS["aspects"])
        em_unit = eden.display_unit("emeralds")
        as_unit = eden.display_unit("aspects")
        em_display = round(em_rate / em_unit, 4)
        as_display = round(as_rate / as_unit, 4)
        await interaction.response.send_message(
            f"**Reward rates per raid:**\n"
            f"Emeralds: `{em_rate}` units (`{em_display}` LE)\n"
            f"Aspects: `{as_rate}` units (`{as_display}` aspects)",
            ephemeral=True,
        )

    @app_commands.command()
    @app_commands.describe(kind="Which reward to unblock", username="Username or UUID")
    async def unblock(
        self, interaction: Interaction, kind: RewardKind, username: str
    ) -> None:
        """Allow a previously blocked member to receive the chosen reward again."""
        await interaction.response.defer(thinking=True)
        if (uuid := await _resolve(self.bot.pool, username)) is None:
            await interaction.followup.send(f"No member named `{username}` found.")
            return
        await eden.set_blocked(self.bot.pool, uuid, kind, False)
        await interaction.followup.send(f"`{username}` is receiving {kind} again.")
