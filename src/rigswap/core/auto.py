"""Automatic switching: pick the setup that matches the hardware, and switch to it.

Used by `rigswap watch` and the app's tray mode. Call AutoSwitcher.tick() every few
seconds. It:

- picks the first setup (top to bottom) whose conditions match the hardware;
- waits until that answer is the same for `settle_ticks` checks in a row, because
  plugging in an eGPU makes the GPU appear in stages;
- never switches under a running game: it waits, says which game it's waiting for,
  and switches once the game closes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from rigswap.core.models import Conditions, Library, Power, Setup
from rigswap.core.swap import GameRunningError, SwitchResult
from rigswap.hardware import HardwareState


def matches(when: Conditions, state: HardwareState) -> bool:
    if when.is_empty():
        return False
    if when.external_gpu is not None:
        if any(g.external is None for g in state.gpus) and not state.external_gpus:
            return False  # can't tell whether a GPU is external
        if bool(state.external_gpus) != when.external_gpu:
            return False
    if when.gpu_name:
        needle = when.gpu_name.casefold()
        if not any(needle in g.name.casefold() for g in state.gpus):
            return False
    if when.power is not None and (state.on_ac is None
                                   or state.on_ac != (when.power == Power.AC)):
        return False
    if when.external_display is not None:
        if state.external_displays is None:
            return False
        if (state.external_displays > 0) != when.external_display:
            return False
    return True


def choose_setup(lib: Library, state: HardwareState) -> Setup | None:
    """The first setup whose conditions match, or None (then nothing changes)."""
    return next((s for s in lib.setups if matches(s.when, state)), None)


@dataclass
class Switched:
    setup: Setup
    result: SwitchResult


@dataclass
class Waiting:
    setup: Setup
    games: list  # list[Game] that must close first


@dataclass
class Failed:
    setup: Setup
    error: Exception


Event = Switched | Waiting | Failed


class AutoSwitcher:
    def __init__(self, get_library: Callable[[], Library],
                 switch: Callable[[Setup], SwitchResult],
                 read_state: Callable[[], HardwareState], *, settle_ticks: int = 2) -> None:
        self._get_library = get_library
        self._switch = switch
        self._read_state = read_state
        self._settle = max(1, settle_ticks)
        self._candidate: str | None = None
        self._seen = 0
        self._last_wait: tuple[str, frozenset[str]] | None = None
        self._failed: str | None = None
        self.last_state: HardwareState | None = None

    def tick(self) -> Event | None:
        """Check the hardware once; returns what happened (None = nothing new)."""
        lib = self._get_library()
        state = self._read_state()
        self.last_state = state
        target = choose_setup(lib, state)
        if target is None or target.id == lib.settings.active_setup_id:
            self._candidate, self._seen, self._last_wait, self._failed = None, 0, None, None
            return None
        if target.id != self._candidate:
            self._candidate, self._seen, self._last_wait, self._failed = target.id, 0, None, None
        self._seen += 1
        if self._seen < self._settle or self._failed == target.id:
            return None
        try:
            result = self._switch(target)
        except GameRunningError as e:
            key = (target.id, frozenset(g.id for g in e.games))
            if key == self._last_wait:
                return None  # still waiting for the same games: don't repeat ourselves
            self._last_wait = key
            return Waiting(target, list(e.games))
        except Exception as e:  # report once; retried when the hardware changes again
            self._failed = target.id
            return Failed(target, e)
        self._candidate, self._seen, self._last_wait = None, 0, None
        return Switched(target, result)
