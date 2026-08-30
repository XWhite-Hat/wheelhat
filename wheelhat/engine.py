"""The spin engine.

The server picks the winner, not the browser. Every overlay is then told which
index to land on, so two browser sources showing the same wheel always agree,
and the actions fire even if no overlay is open at all.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from . import db
from .actions.executor import ExecContext, execute_chain
from .hub import hub
from .models import Slice, SpinRecord, Wheel, new_id

log = logging.getLogger("wheelhat.engine")

_rng = random.SystemRandom()


class SpinRejected(RuntimeError):
    """A spin could not start; the message explains why."""


@dataclass
class ActiveSpin:
    spin_id: str
    wheel_id: str
    winner: str
    started_at: float
    ends_at: float
    task: Optional[asyncio.Task] = field(default=None, repr=False)


def slice_payload(slices: list[Slice]) -> list[dict[str, Any]]:
    return [
        {
            "id": s.id,
            "label": s.label,
            "weight": max(s.weight, 0.0001),
            "color": s.color,
            "text_color": s.text_color,
            "image": s.image.model_dump(),
        }
        for s in slices
    ]


def render_payload(wheel: Wheel) -> dict[str, Any]:
    """Everything an overlay needs to draw the wheel at rest."""
    return {
        "wheel_id": wheel.id,
        "name": wheel.name,
        "slices": slice_payload(wheel.spinnable()),
        "appearance": wheel.appearance.model_dump(),
        "spin": wheel.spin.model_dump(),
        "updated_at": wheel.updated_at,
    }


class SpinEngine:
    def __init__(self) -> None:
        self._active: dict[str, ActiveSpin] = {}
        self._last_spin_at: dict[str, float] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ status

    def is_spinning(self, wheel_id: str) -> bool:
        return wheel_id in self._active

    def active_spin(self, wheel_id: str) -> Optional[ActiveSpin]:
        return self._active.get(wheel_id)

    def cooldown_remaining(self, wheel: Wheel) -> float:
        if wheel.spin.cooldown_seconds <= 0:
            return 0.0
        last = self._last_spin_at.get(wheel.id, 0.0)
        return max(0.0, last + wheel.spin.cooldown_seconds - time.time())

    # -------------------------------------------------------------------- spin

    def pick(self, wheel: Wheel) -> Slice:
        candidates = wheel.spinnable()
        if not candidates:
            raise SpinRejected(
                f"'{wheel.name}' has no slices that can win right now "
                "(all disabled, zero weight or on cooldown)."
            )
        weights = [max(s.weight, 0.0001) for s in candidates]
        return _rng.choices(candidates, weights=weights, k=1)[0]

    async def spin(
        self,
        wheel_id: str,
        *,
        source: str = "manual",
        variables: Optional[dict[str, Any]] = None,
        force_slice_id: str = "",
        skip_actions: bool = False,
        ignore_cooldown: bool = False,
    ) -> dict[str, Any]:
        wheel = db.get_wheel(wheel_id)
        if wheel is None:
            raise SpinRejected(f"Wheel '{wheel_id}' no longer exists.")
        if not wheel.enabled and source != "manual":
            raise SpinRejected(f"'{wheel.name}' is disabled.")

        async with self._lock:
            if wheel_id in self._active:
                raise SpinRejected(f"'{wheel.name}' is already spinning.")
            if not ignore_cooldown:
                remaining = self.cooldown_remaining(wheel)
                if remaining > 0:
                    raise SpinRejected(
                        f"'{wheel.name}' is on cooldown for another {remaining:.0f}s."
                    )

            candidates = wheel.spinnable()
            if force_slice_id:
                winner = wheel.slice_by_id(force_slice_id)
                if winner is None:
                    raise SpinRejected("That slice is not on the wheel any more.")
                if winner not in candidates:
                    candidates = candidates + [winner]
            else:
                winner = self.pick(wheel)

            try:
                target_index = candidates.index(winner)
            except ValueError:
                target_index = 0

            spin_id = new_id("spin_")
            duration = max(500, int(wheel.spin.duration_ms))
            delay = max(0, int(wheel.spin.action_delay_ms))
            now = time.time()
            active = ActiveSpin(
                spin_id=spin_id,
                wheel_id=wheel_id,
                winner=winner.label,
                started_at=now,
                ends_at=now + (duration + delay) / 1000,
            )
            self._active[wheel_id] = active
            self._last_spin_at[wheel_id] = now

        turns = _rng.uniform(wheel.spin.min_turns, max(wheel.spin.min_turns, wheel.spin.max_turns))
        payload = {
            "type": "spin_start",
            "spin_id": spin_id,
            "wheel_id": wheel_id,
            "wheel_name": wheel.name,
            "slices": slice_payload(candidates),
            "target_index": target_index,
            "winner": winner.label,
            "winner_id": winner.id,
            "duration_ms": duration,
            "turns": turns,
            "easing": wheel.spin.easing,
            "appearance": wheel.appearance.model_dump(),
            "source": source,
            "actor": (variables or {}).get("user", ""),
            "started_at": now,
        }
        await hub.broadcast_all(wheel_id, payload)

        active.task = asyncio.create_task(
            self._finish(
                wheel=wheel,
                winner=winner,
                spin_id=spin_id,
                source=source,
                variables=variables or {},
                duration_ms=duration,
                delay_ms=delay,
                skip_actions=skip_actions,
            ),
            name=f"spin-{spin_id}",
        )

        return {
            "spin_id": spin_id,
            "wheel_id": wheel_id,
            "winner": winner.label,
            "winner_id": winner.id,
            "target_index": target_index,
            "duration_ms": duration,
            "resolves_in_ms": duration + delay,
        }

    async def _finish(
        self,
        *,
        wheel: Wheel,
        winner: Slice,
        spin_id: str,
        source: str,
        variables: dict[str, Any],
        duration_ms: int,
        delay_ms: int,
        skip_actions: bool,
    ) -> None:
        try:
            await asyncio.sleep(duration_ms / 1000)

            record = db.add_spin(
                SpinRecord(
                    wheel_id=wheel.id,
                    wheel_name=wheel.name,
                    slice_id=winner.id,
                    label=winner.label,
                    source=source,
                    actor=str(variables.get("user", "")),
                )
            )
            await hub.broadcast_all(
                wheel.id,
                {
                    "type": "spin_result",
                    "spin_id": spin_id,
                    "wheel_id": wheel.id,
                    "winner": winner.label,
                    "winner_id": winner.id,
                    "source": source,
                    "actor": str(variables.get("user", "")),
                    "history_id": record.id,
                },
            )

            self._apply_win_effects(wheel.id, winner.id)

            if delay_ms:
                await asyncio.sleep(delay_ms / 1000)

            if skip_actions:
                return

            ctx = ExecContext(
                wheel_id=wheel.id,
                wheel_name=wheel.name,
                slice_id=winner.id,
                winner=winner.label,
                source=source,
                variables=variables,
            )
            if wheel.pre_actions:
                await execute_chain(
                    [a.model_dump() for a in wheel.pre_actions], ctx, label="pre"
                )
            if winner.actions:
                await execute_chain(
                    [a.model_dump() for a in winner.actions], ctx, label=f"slice:{winner.label}"
                )
            if wheel.post_actions:
                await execute_chain(
                    [a.model_dump() for a in wheel.post_actions], ctx, label="post"
                )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Spin %s failed while resolving", spin_id)
        finally:
            self._active.pop(wheel.id, None)
            await hub.broadcast_control(
                {"type": "spin_finished", "spin_id": spin_id, "wheel_id": wheel.id}
            )

    def _apply_win_effects(self, wheel_id: str, winner_id: str) -> None:
        """Tick cooldowns and eliminations, then persist."""
        current = db.get_wheel(wheel_id)
        if current is None:
            return
        changed = False
        for item in current.slices:
            if item.cooldown_remaining > 0:
                item.cooldown_remaining -= 1
                changed = True
            if item.id == winner_id:
                item.won_count += 1
                if item.remove_on_win:
                    item.enabled = False
                elif item.cooldown_spins > 0:
                    item.cooldown_remaining = item.cooldown_spins
                changed = True
        if changed:
            db.save_wheel(current)

    async def cancel(self, wheel_id: str) -> bool:
        active = self._active.pop(wheel_id, None)
        if active is None:
            return False
        if active.task:
            active.task.cancel()
        await hub.broadcast_all(
            wheel_id, {"type": "spin_cancelled", "spin_id": active.spin_id, "wheel_id": wheel_id}
        )
        return True

    async def shutdown(self) -> None:
        for wheel_id in list(self._active):
            await self.cancel(wheel_id)


engine = SpinEngine()
