"""Views for `/manage reset`."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from discord import ButtonStyle, Interaction, ui

from database import eden

if TYPE_CHECKING:
    from client import Pianobot


class ConfirmResetAllView(ui.View):
    """One-shot ephemeral confirm view for `/manage reset <kind> all`."""

    def __init__(self, kind: Literal["emeralds", "aspects"], bot: Pianobot) -> None:
        """Initialize the view."""
        super().__init__(timeout=60)
        self.kind = kind
        self.bot = bot

    @ui.button(style=ButtonStyle.danger, label="Confirm reset all")
    async def confirm(
        self, interaction: Interaction, _: ui.Button[ConfirmResetAllView]
    ) -> None:
        """Apply the reset across every member, then disable the button."""
        await eden.reset_all_pending(self.bot.pool, self.kind, self.bot.eden_wynn_uuid)
        for child in self.children:
            if isinstance(child, ui.Button):
                child.disabled = True
        await interaction.response.edit_message(
            content=f"All pending {self.kind} balances have been reset.", view=None
        )
        self.stop()
