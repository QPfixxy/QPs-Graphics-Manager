"""The GUI's view of the library: every change goes through here.

Widgets never touch the core directly. Each method changes the library, saves it
and emits `changed`. If an operation fails, the library is reloaded from disk so
the window never shows half-applied state.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from rigswap.core import detect, manifest, steam, swap
from rigswap.core.artwork import Art, find_art
from rigswap.core.models import ConfigLocation, Game, Setup
from rigswap.core.store import LibraryStore


class SnapState(Enum):
    MISSING = "missing"
    SAVED = "saved"
    STALE = "stale"  # config locations changed since it was saved


class AppState(QObject):
    changed = Signal()

    def __init__(self, store: LibraryStore | None = None,
                 steam_roots: list[Path] | None = None,
                 running: swap.RunningCheck | None = None) -> None:
        super().__init__()
        self.store = store or LibraryStore()
        self.lib = self.store.load()
        self._running = running
        self._steam_roots = steam_roots
        self._art: dict[str, Art | None] = {}
        self._manifest: manifest.Manifest | None = None

    @property
    def root(self) -> Path:
        return self.store.root

    @property
    def steam_roots(self) -> list[Path]:
        if self._steam_roots is None:
            self._steam_roots = [i.root for i in steam.find_steam_installs()]
        return self._steam_roots

    @contextmanager
    def _change(self):
        try:
            yield
        except BaseException:
            self.lib = self.store.load()
            self._art.clear()
            self.changed.emit()
            raise
        self.store.save(self.lib)
        self._art.clear()
        self.changed.emit()

    # --- queries ----------------------------------------------------------------

    def active_setup(self) -> Setup | None:
        sid = self.lib.settings.active_setup_id
        return self.lib.setup(sid) if sid else None

    def snapshot_state(self, game: Game, setup: Setup) -> tuple[SnapState, datetime | None]:
        snap = self.lib.snapshot(game.id, setup.id)
        if snap is None:
            return SnapState.MISSING, None
        if not swap.snapshot_matches(self.root, game, setup.id):
            return SnapState.STALE, snap.saved_at
        return SnapState.SAVED, snap.saved_at

    def art(self, game: Game) -> Art | None:
        if game.id not in self._art:
            self._art[game.id] = find_art(game, self.steam_roots)
        return self._art[game.id]

    def can_undo(self) -> bool:
        return swap.can_undo(self.root)

    # --- setups -----------------------------------------------------------------

    def add_setup(self, name: str) -> Setup:
        with self._change():
            return self.lib.add_setup(name)

    def rename_setup(self, setup: Setup, name: str) -> None:
        with self._change():
            self.lib.rename_setup(setup.id, name)

    def remove_setup(self, setup: Setup) -> None:
        with self._change():
            self.lib.remove_setup(setup.id)
        swap.delete_setup_data(self.root, setup.id)

    def switch(self, setup: Setup) -> swap.SwitchResult:
        with self._change():
            return swap.switch_setup(self.root, self.lib, setup.id, running=self._running)

    def undo(self) -> swap.UndoResult | None:
        with self._change():
            return swap.undo_last(self.root, self.lib, running=self._running)

    # --- games ------------------------------------------------------------------

    def import_steam(self) -> steam.ImportResult:
        with self._change():
            self._steam_roots = None
            installs = steam.find_steam_installs()
            self._steam_roots = [i.root for i in installs]
            return steam.import_games(self.lib, steam.discover_games(installs))

    def add_game(self, game: Game) -> Game:
        with self._change():
            return self.lib.add_game(game)

    def update_game(self, game: Game, edit: Callable[[Game], None]) -> None:
        with self._change():
            edit(game)

    def remove_game(self, game: Game) -> None:
        with self._change():
            self.lib.remove_game(game.id)
        swap.delete_game_data(self.root, game.id)

    def add_location(self, game: Game, location: ConfigLocation) -> None:
        with self._change():
            self.lib.add_config_location(game.id, location)

    def remove_location(self, game: Game, index: int) -> None:
        with self._change():
            self.lib.remove_config_location(game.id, index)

    def set_patterns(self, game: Game, index: int, include: list[str], exclude: list[str]) -> None:
        with self._change():
            loc = game.config_locations[index]
            loc.include, loc.exclude = include, exclude

    def save_snapshot(self, game: Game, setup: Setup) -> None:
        with self._change():
            swap.save_snapshot(self.root, self.lib, game, setup.id, running=self._running)

    # --- locked setups, restore, changes --------------------------------------------

    def set_locked(self, setup: Setup, locked: bool) -> None:
        with self._change():
            setup.locked = locked

    def changed_files(self, game: Game, setup: Setup) -> list[Path]:
        return swap.changed_files(self.root, self.lib, game, setup.id)

    def restore(self, games: list[Game], setup: Setup) -> str:
        with self._change():
            return swap.restore_games(self.root, self.lib, games, setup.id,
                                      running=self._running)

    def locked_drift(self) -> tuple[Setup | None, list[Game]]:
        """Games whose live config no longer matches a *locked* active setup."""
        setup = self.active_setup()
        if setup is None or not setup.locked:
            return setup, []
        return setup, [g for g in self.lib.games if self.changed_files(g, setup)]

    # --- the config database (Ludusavi manifest) --------------------------------------

    def manifest(self) -> manifest.Manifest | None:
        if self._manifest is None:
            try:
                self._manifest = manifest.load(self.root)
            except manifest.ManifestError:
                self._manifest = None
        return self._manifest

    def manifest_downloaded(self) -> None:
        """Call after manifest.update() so the new copy is used."""
        self._manifest = None

    def set_use_manifest(self, value: bool) -> None:
        with self._change():
            self.lib.settings.use_manifest = value

    def usable_manifest(self):
        return self.manifest() if self.lib.settings.use_manifest is not False else None

    def detect(self, game: Game) -> detect.Detection | None:
        """The game database (if enabled and downloaded), then engine rules."""
        return detect.detect_game(game, self.usable_manifest())

    def add_locations(self, game: Game, locations: list[ConfigLocation]) -> None:
        with self._change():
            for loc in locations:
                self.lib.add_config_location(game.id, loc)

    def auto_detect(self, games: list[Game]) -> dict[str, int]:
        """Add safe config locations for each game; returns {game id: number added}."""
        man = self.usable_manifest()
        added: dict[str, int] = {}
        with self._change():
            for game in games:
                found = detect.detect_game(game, man)
                if found:
                    added[game.id] = len(detect.add_detected(self.lib, game, found))
        return added

    # --- automatic switching ------------------------------------------------------------

    def set_auto_switch(self, on: bool) -> None:
        with self._change():
            self.lib.settings.auto_switch = on

    def set_conditions(self, setup: Setup, when) -> None:
        with self._change():
            setup.when = when

    def move_setup(self, setup: Setup, delta: int) -> None:
        with self._change():
            index = self.lib.setups.index(setup)
            self.lib.move_setup(setup.id, index + delta)

    # --- all launchers ------------------------------------------------------------------

    def discover(self):
        from rigswap import launchers

        return launchers.discover()

    def import_found(self, found) -> list[Game]:
        from rigswap import launchers

        with self._change():
            result = launchers.import_games(self.lib, found)
        return result.added
