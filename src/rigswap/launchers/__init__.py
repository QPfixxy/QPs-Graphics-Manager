"""Find installed games from every launcher, and add them to the library.

- Steam: rigswap.core.steam (Windows and Linux).
- Windows: Epic Games (launcher manifests), GOG (registry) and, as a catch-all, the
  Xbox Game Bar's own list of games Windows has recognised (any launcher).
- Linux: Heroic (GOG, Epic and Amazon games) and Lutris.

Game Bar finds aren't suggested by default: the list also contains apps that aren't
games, so they're offered unticked.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from rigswap.core.models import Game, Library


@dataclass(frozen=True)
class FoundGame:
    name: str
    store: str  # "steam", "epic", "gog", "amazon", "lutris", "gamebar"
    store_id: str
    install_dir: str | None
    wine_prefix: str | None = None
    executables: tuple[str, ...] = ()
    steam_app_id: int | None = None
    suggested: bool = True  # pre-ticked when offered for import


@dataclass
class ImportResult:
    added: list[Game] = field(default_factory=list)
    updated: list[Game] = field(default_factory=list)


def _norm(path: str | None) -> str | None:
    return os.path.normcase(os.path.normpath(path)) if path else None


def discover(platform: str = sys.platform) -> list[FoundGame]:
    """Every installed game RigSwap can find on this machine, without duplicates."""
    from rigswap.core import steam

    found: list[FoundGame] = [
        FoundGame(name=g.name, store="steam", store_id=str(g.app_id),
                  install_dir=str(g.install_dir),
                  wine_prefix=str(g.proton_prefix) if g.proton_prefix else None,
                  steam_app_id=g.app_id)
        for g in steam.discover_games()
    ]
    if platform == "win32":
        from rigswap.launchers import windows

        for reader in (windows.epic, windows.gog, windows.gamebar):
            found.extend(_safe(reader))
    elif platform.startswith("linux"):
        from rigswap.launchers import linux

        for reader in (linux.heroic, linux.lutris):
            found.extend(_safe(reader))
    return dedupe(found)


def _safe(reader) -> list[FoundGame]:
    """One broken launcher file mustn't stop the others from being found."""
    try:
        return list(reader())
    except Exception:
        return []


def dedupe(found: list[FoundGame]) -> list[FoundGame]:
    """Drop repeats: the same store id twice, or a Game Bar find inside a known game."""
    out: list[FoundGame] = []
    seen_ids: set[tuple[str, str]] = set()
    dirs = [_norm(g.install_dir) for g in found if g.store != "gamebar" and g.install_dir]
    for g in found:
        key = (g.store, g.store_id)
        if key in seen_ids:
            continue
        if g.store == "gamebar":
            exe_dirs = [_norm(e) for e in g.executables] + [_norm(g.install_dir)]
            if any(e and d and (e == d or e.startswith(d + os.sep))
                   for e in exe_dirs for d in dirs):
                continue
        seen_ids.add(key)
        out.append(g)
    return sorted(out, key=lambda g: g.name.casefold())


def is_in_library(lib: Library, g: FoundGame) -> bool:
    return _match(lib, g) is not None


def _match(lib: Library, g: FoundGame) -> Game | None:
    for game in lib.games:
        if game.store == g.store and game.store_id == g.store_id:
            return game
        if g.steam_app_id is not None and game.steam_app_id == g.steam_app_id:
            return game
    target = _norm(g.install_dir)
    if target:
        for game in lib.games:
            if _norm(game.install_dir) == target:
                return game
    return None


def import_games(lib: Library, found: list[FoundGame]) -> ImportResult:
    """Add new games; refresh install paths of ones already in the library.

    Existing games keep their name, config locations and saved configs.
    """
    result = ImportResult()
    for g in found:
        existing = _match(lib, g)
        if existing is None:
            game = Game(name=g.name, store=g.store, store_id=g.store_id,
                        install_dir=g.install_dir, wine_prefix=g.wine_prefix,
                        executables=list(g.executables), steam_app_id=g.steam_app_id)
            result.added.append(lib.add_game(game))
            continue
        if existing.store != g.store or existing.store_id != g.store_id:
            continue  # same folder, different origin (e.g. added by hand): leave it
        changed = False
        if g.install_dir and existing.install_dir != g.install_dir:
            existing.install_dir = g.install_dir
            changed = True
        if existing.wine_prefix != g.wine_prefix:
            existing.wine_prefix = g.wine_prefix
            changed = True
        if g.executables and not existing.executables:
            existing.executables = list(g.executables)
            changed = True
        if changed:
            result.updated.append(existing)
    return result


def exists(path: str | None) -> bool:
    return bool(path) and Path(path).is_dir()
