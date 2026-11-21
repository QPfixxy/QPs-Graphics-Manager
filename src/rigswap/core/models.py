"""Data model.

- A **Setup** is a hardware situation ("Handheld", "eGPU Dock"), defined once.
- A **Game** has one or more config locations (files or whole folders).
- A **Snapshot** records that a game has saved configs for a setup. The files
  themselves live on disk under paths.snapshot_dir(root, game_id, setup_id).
- The **Library** holds all of it plus app settings, and is what gets saved.

IDs are random hex strings so games and setups can be renamed freely.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from fnmatch import fnmatch
from typing import Any

SCHEMA_VERSION = 1


class LibraryError(Exception):
    """Invalid library data or an invalid operation on the library."""


def new_id() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


class LocationKind(StrEnum):
    FILE = "file"
    DIRECTORY = "directory"


@dataclass
class ConfigLocation:
    """A config file, or a folder of them.

    For folders, `include` / `exclude` are glob patterns matched against each file's
    path relative to the folder (e.g. "*.ini", "saves/*"). An empty include means
    every file. Use exclude to keep save files out when a game mixes them in.
    """

    path: str
    kind: LocationKind = LocationKind.FILE
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "kind": self.kind.value,
                "include": list(self.include), "exclude": list(self.exclude)}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ConfigLocation:
        return cls(path=_req_str(d, "path"), kind=LocationKind(d.get("kind", "file")),
                   include=_str_list(d, "include"), exclude=_str_list(d, "exclude"))

    def matches(self, relative: str) -> bool:
        """Does a file at this folder-relative path (forward slashes) belong to the config?"""
        name = relative.rsplit("/", 1)[-1]

        def hit(patterns: list[str]) -> bool:
            return any(fnmatch(relative, p) or fnmatch(name, p) for p in patterns)

        return (not self.include or hit(self.include)) and not hit(self.exclude)


class Power(StrEnum):
    AC = "ac"
    BATTERY = "battery"


@dataclass
class Conditions:
    """When a setup should be used automatically. Every condition that is set must hold;
    unset (None) conditions are ignored. No conditions at all = never automatic."""

    external_gpu: bool | None = None  # True: an external GPU is connected; False: none is
    gpu_name: str | None = None  # some connected GPU's name contains this (any case)
    power: Power | None = None
    external_display: bool | None = None

    def is_empty(self) -> bool:
        return (self.external_gpu is None and not self.gpu_name and self.power is None
                and self.external_display is None)

    def describe(self) -> str:
        parts = []
        if self.external_gpu is not None:
            parts.append("an external GPU is "
                         + ("connected" if self.external_gpu else "not connected"))
        if self.gpu_name:
            parts.append(f"a GPU named like '{self.gpu_name}' is connected")
        if self.power is not None:
            parts.append("plugged in" if self.power == Power.AC else "on battery")
        if self.external_display is not None:
            parts.append("an external display is "
                         + ("connected" if self.external_display else "not connected"))
        return " and ".join(parts) if parts else "never (switch by hand)"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.external_gpu is not None:
            d["external_gpu"] = self.external_gpu
        if self.gpu_name:
            d["gpu_name"] = self.gpu_name
        if self.power is not None:
            d["power"] = self.power.value
        if self.external_display is not None:
            d["external_display"] = self.external_display
        return d

    @classmethod
    def from_dict(cls, d: Any) -> Conditions:
        if not isinstance(d, dict):
            raise LibraryError("setup conditions must be an object")

        def opt_bool(key: str) -> bool | None:
            v = d.get(key)
            if v is not None and not isinstance(v, bool):
                raise LibraryError(f"{key!r} must be true or false")
            return v

        name = d.get("gpu_name")
        if name is not None and not isinstance(name, str):
            raise LibraryError("'gpu_name' must be text")
        power = d.get("power")
        return cls(external_gpu=opt_bool("external_gpu"), gpu_name=name or None,
                   power=Power(power) if power is not None else None,
                   external_display=opt_bool("external_display"))


@dataclass
class Setup:
    name: str
    # A locked setup's snapshots are never overwritten by "save when switching away".
    locked: bool = False
    # When to switch to this setup automatically (Phase 3).
    when: Conditions = field(default_factory=Conditions)
    id: str = field(default_factory=new_id)

    def to_dict(self) -> dict[str, Any]:
        d = {"id": self.id, "name": self.name, "locked": self.locked}
        if not self.when.is_empty():
            d["when"] = self.when.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Setup:
        return cls(id=_req_str(d, "id"), name=_req_str(d, "name"),
                   locked=bool(d.get("locked", False)),
                   when=Conditions.from_dict(d.get("when", {})))


@dataclass
class Game:
    name: str
    config_locations: list[ConfigLocation] = field(default_factory=list)
    steam_app_id: int | None = None
    install_dir: str | None = None
    # Linux/Proton only: the game's Wine prefix (steamapps/compatdata/<appid>/pfx).
    proton_prefix: str | None = None
    # Extra ways to tell the game is running: full paths, or bare file names such as
    # "Cyberpunk2077.exe" (which also match a Proton/Wine command line).
    executables: list[str] = field(default_factory=list)
    # Custom poster image chosen by the user (otherwise Steam's cached art is used).
    poster: str | None = None
    id: str = field(default_factory=new_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "steam_app_id": self.steam_app_id,
            "install_dir": self.install_dir,
            "proton_prefix": self.proton_prefix,
            "executables": list(self.executables),
            "poster": self.poster,
            "config_locations": [loc.to_dict() for loc in self.config_locations],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Game:
        app_id = d.get("steam_app_id")
        if app_id is not None and not isinstance(app_id, int):
            raise LibraryError(f"steam_app_id must be an integer, got {app_id!r}")
        return cls(
            id=_req_str(d, "id"),
            name=_req_str(d, "name"),
            steam_app_id=app_id,
            install_dir=d.get("install_dir"),
            proton_prefix=d.get("proton_prefix"),
            executables=_str_list(d, "executables"),
            poster=d.get("poster"),
            config_locations=[ConfigLocation.from_dict(x) for x in d.get("config_locations", [])],
        )


@dataclass
class Snapshot:
    game_id: str
    setup_id: str
    saved_at: datetime = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "game_id": self.game_id,
            "setup_id": self.setup_id,
            "saved_at": self.saved_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Snapshot:
        try:
            saved_at = datetime.fromisoformat(_req_str(d, "saved_at"))
        except ValueError as e:
            raise LibraryError(f"bad saved_at: {e}") from e
        return cls(game_id=_req_str(d, "game_id"), setup_id=_req_str(d, "setup_id"),
                   saved_at=saved_at)


@dataclass
class Settings:
    active_setup_id: str | None = None
    # Save the live config back into the outgoing setup's snapshot when switching away.
    save_on_switch: bool = True
    # Use the Ludusavi manifest to find config files: None = not asked yet.
    use_manifest: bool | None = None
    # Switch setups automatically when the hardware changes (Phase 3).
    auto_switch: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"active_setup_id": self.active_setup_id, "save_on_switch": self.save_on_switch,
                "use_manifest": self.use_manifest, "auto_switch": self.auto_switch}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Settings:
        use = d.get("use_manifest")
        return cls(active_setup_id=d.get("active_setup_id"),
                   save_on_switch=bool(d.get("save_on_switch", True)),
                   use_manifest=use if isinstance(use, bool) else None,
                   auto_switch=bool(d.get("auto_switch", False)))


@dataclass
class Library:
    setups: list[Setup] = field(default_factory=list)
    games: list[Game] = field(default_factory=list)
    snapshots: list[Snapshot] = field(default_factory=list)
    settings: Settings = field(default_factory=Settings)

    # --- setups ---------------------------------------------------------

    def setup(self, setup_id: str) -> Setup:
        for s in self.setups:
            if s.id == setup_id:
                return s
        raise LibraryError(f"no setup with id {setup_id}")

    def setup_by_name(self, name: str) -> Setup | None:
        key = name.strip().casefold()
        return next((s for s in self.setups if s.name.casefold() == key), None)

    def add_setup(self, name: str) -> Setup:
        name = self._check_setup_name(name)
        setup = Setup(name=name)
        self.setups.append(setup)
        return setup

    def rename_setup(self, setup_id: str, new_name: str) -> None:
        setup = self.setup(setup_id)
        setup.name = self._check_setup_name(new_name, ignore_id=setup_id)

    def move_setup(self, setup_id: str, index: int) -> None:
        """Reorder setups; automatic switching checks them from first to last."""
        setup = self.setup(setup_id)
        self.setups.remove(setup)
        self.setups.insert(max(0, min(index, len(self.setups))), setup)

    def remove_setup(self, setup_id: str) -> None:
        """Remove a setup and its snapshot records. Deleting the files is the caller's job."""
        self.setups.remove(self.setup(setup_id))
        self.snapshots = [s for s in self.snapshots if s.setup_id != setup_id]
        if self.settings.active_setup_id == setup_id:
            self.settings.active_setup_id = None

    def _check_setup_name(self, name: str, ignore_id: str | None = None) -> str:
        name = name.strip()
        if not name:
            raise LibraryError("setup name can't be empty")
        existing = self.setup_by_name(name)
        if existing and existing.id != ignore_id:
            raise LibraryError(f"a setup named {existing.name!r} already exists")
        return name

    # --- games ----------------------------------------------------------

    def game(self, game_id: str) -> Game:
        for g in self.games:
            if g.id == game_id:
                return g
        raise LibraryError(f"no game with id {game_id}")

    def game_by_steam_app_id(self, app_id: int) -> Game | None:
        return next((g for g in self.games if g.steam_app_id == app_id), None)

    def add_game(self, game: Game) -> Game:
        if not game.name.strip():
            raise LibraryError("game name can't be empty")
        if any(g.id == game.id for g in self.games):
            raise LibraryError(f"game id {game.id} already in library")
        if game.steam_app_id is not None and self.game_by_steam_app_id(game.steam_app_id):
            raise LibraryError(f"Steam app {game.steam_app_id} is already in the library")
        self.games.append(game)
        return game

    def find_game(self, query: str) -> Game:
        """Find a game by exact name (any case), Steam app id, or the start of its id."""
        q = query.strip()
        if not q:
            raise LibraryError("no game given")
        by_name = [g for g in self.games if g.name.casefold() == q.casefold()]
        if len(by_name) == 1:
            return by_name[0]
        if q.isdigit():
            g = self.game_by_steam_app_id(int(q))
            if g:
                return g
        by_id = [g for g in self.games if g.id.startswith(q.lower())] if len(q) >= 4 else []
        matches = by_name or by_id
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise LibraryError(f"no game matches {query!r}")
        listing = ", ".join(f"{g.name} ({g.id[:8]})" for g in matches)
        raise LibraryError(f"{query!r} matches several games: {listing}; use the id")

    def add_config_location(self, game_id: str, location: ConfigLocation) -> None:
        game = self.game(game_id)
        if any(_same_path(loc.path, location.path) for loc in game.config_locations):
            raise LibraryError(f"{location.path} is already a config location of {game.name}")
        game.config_locations.append(location)

    def remove_config_location(self, game_id: str, index: int) -> ConfigLocation:
        game = self.game(game_id)
        if not 0 <= index < len(game.config_locations):
            raise LibraryError(f"{game.name} has no config location #{index + 1}")
        return game.config_locations.pop(index)

    def remove_game(self, game_id: str) -> None:
        """Remove a game and its snapshot records. Deleting the files is the caller's job."""
        self.games.remove(self.game(game_id))
        self.snapshots = [s for s in self.snapshots if s.game_id != game_id]

    # --- snapshots ------------------------------------------------------

    def snapshot(self, game_id: str, setup_id: str) -> Snapshot | None:
        return next(
            (s for s in self.snapshots if s.game_id == game_id and s.setup_id == setup_id), None
        )

    def record_snapshot(self, game_id: str, setup_id: str) -> Snapshot:
        """Mark that game_id now has saved configs for setup_id (replacing any older record)."""
        self.game(game_id)
        self.setup(setup_id)
        self.snapshots = [
            s for s in self.snapshots if not (s.game_id == game_id and s.setup_id == setup_id)
        ]
        snap = Snapshot(game_id=game_id, setup_id=setup_id)
        self.snapshots.append(snap)
        return snap

    # --- serialisation --------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "settings": self.settings.to_dict(),
            "setups": [s.to_dict() for s in self.setups],
            "games": [g.to_dict() for g in self.games],
            "snapshots": [s.to_dict() for s in self.snapshots],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Library:
        if not isinstance(d, dict):
            raise LibraryError("library data must be a JSON object")
        version = d.get("schema_version")
        if version != SCHEMA_VERSION:
            raise LibraryError(
                f"library schema version {version!r} is not supported "
                f"(this build reads version {SCHEMA_VERSION})"
            )
        try:
            lib = cls(
                settings=Settings.from_dict(d.get("settings", {})),
                setups=[Setup.from_dict(x) for x in d.get("setups", [])],
                games=[Game.from_dict(x) for x in d.get("games", [])],
                snapshots=[Snapshot.from_dict(x) for x in d.get("snapshots", [])],
            )
        except (TypeError, ValueError, AttributeError) as e:
            raise LibraryError(f"malformed library data: {e}") from e
        lib._validate()
        return lib

    def _validate(self) -> None:
        setup_ids = {s.id for s in self.setups}
        game_ids = {g.id for g in self.games}
        if len(setup_ids) != len(self.setups):
            raise LibraryError("duplicate setup ids")
        if len(game_ids) != len(self.games):
            raise LibraryError("duplicate game ids")
        names = [s.name.casefold() for s in self.setups]
        if len(set(names)) != len(names):
            raise LibraryError("duplicate setup names")
        for snap in self.snapshots:
            if snap.game_id not in game_ids or snap.setup_id not in setup_ids:
                raise LibraryError(
                    f"snapshot refers to unknown game/setup ({snap.game_id}/{snap.setup_id})"
                )
        active = self.settings.active_setup_id
        if active is not None and active not in setup_ids:
            raise LibraryError(f"active setup {active} doesn't exist")


def _req_str(d: dict[str, Any], key: str) -> str:
    value = d.get(key)
    if not isinstance(value, str) or not value:
        raise LibraryError(f"missing or invalid {key!r}")
    return value


def _str_list(d: dict[str, Any], key: str) -> list[str]:
    value = d.get(key, [])
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise LibraryError(f"{key!r} must be a list of strings")
    return list(value)


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))
