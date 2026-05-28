"""Finalize Eden's award cycle as soon as the previous half-month closes."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from random import choices
from typing import TYPE_CHECKING

from database import completions, eden
from utils import get_cycle, get_cycle_window, get_prev_cycle, webhooks

if TYPE_CHECKING:
    from client import Pianobot


log = logging.getLogger(__name__)


async def award_cycle_tick(bot: Pianobot) -> None:
    """Register the active cycle and finalize any prior cycle still pending."""
    now = datetime.now(UTC)
    current_code = get_cycle(now)
    current_start, _ = get_cycle_window(now)
    await eden.ensure_cycle(bot.pool, current_code, current_start)

    prev_code = get_prev_cycle(now)
    if not await eden.cycle_pending_finalization(bot.pool, prev_code):
        return

    prev_end = current_start
    prev_start, _ = get_cycle_window(prev_end - timedelta(seconds=1))

    db_raids = await completions.raid_counts_by_username(
        bot.pool, prev_start, prev_end, guild_uuid=bot.eden_wynn_uuid
    )
    db_wars = await completions.war_counts_by_username(
        bot.pool, prev_start, prev_end, guild_uuid=bot.eden_wynn_uuid
    )
    db_xp = await eden.xp_diff_by_username(bot.pool, prev_start, prev_end)

    raid_results = sorted(db_raids.items(), key=lambda x: x[1], reverse=True)
    war_results = sorted(db_wars.items(), key=lambda x: x[1], reverse=True)
    xp_results = sorted(db_xp.items(), key=lambda x: x[1], reverse=True)

    raffle_winners, total_tickets = _draw_raffle(raid_results)
    await webhooks.send_promotion_cycle_awards(
        bot,
        cycle_code=prev_code,
        raid_results=raid_results,
        war_results=war_results,
        xp_results=xp_results,
        raffle_winners=raffle_winners,
        total_tickets=total_tickets,
    )
    await eden.finalize_cycle(bot.pool, prev_code, prev_end)
    log.info("Finalized cycle %s", prev_code)


def _draw_raffle(
    raid_results: list[tuple[str, int]], n: int = 5
) -> tuple[list[tuple[str, int, int]], int]:
    entries = [
        (name, amount // 5, amount) for name, amount in raid_results if amount > 50
    ]
    total_tickets = sum(e[1] for e in entries)
    winners: list[tuple[str, int, int]] = []
    for _ in range(min(n, len(entries))):
        if not entries:
            break
        names, weights = zip(*[(e[0], e[1]) for e in entries], strict=True)
        winner = choices(names, weights)[0]  # noqa: S311
        for i, entry in enumerate(entries):
            if entry[0] == winner:
                winners.append(entries.pop(i))
                break
    return winners, total_tickets
