"""Is a game running? Swapping configs under a running game is refused.

A game counts as running when any process's executable, or any path in its command
line, is inside the game's install folder. The command-line check is what catches
Proton games: their process is Wine, with the game's .exe as an argument, often
written as a Wine path like Z:\\home\\me\\.steam\\...\\game.exe.

A game's `executables` add more matches: a full path matches exactly, a bare file
name ("eldenring.exe") matches the file name of the process or of any command-line
path. Games with neither an install folder nor executables can't be checked.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Iterator

import psutil

from rigswap.core.models import Game, LocationKind

# (exe, cmdline) for one process.
ProcInfo = tuple[str | None, list[str]]

_WINE_UNIX_DRIVE = re.compile(r"^[zZ]:[\\/]")


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def _candidate_paths(exe: str | None, cmdline: list[str]) -> Iterator[str]:
    if exe:
        yield exe
    for arg in cmdline:
        if "/" not in arg and "\\" not in arg:
            continue
        if os.sep == "/" and _WINE_UNIX_DRIVE.match(arg):
            yield "/" + arg[3:].replace("\\", "/")
        else:
            yield arg


def system_processes() -> Iterator[ProcInfo]:
    own_pid = os.getpid()
    for proc in psutil.process_iter(["pid", "exe", "cmdline"]):
        info = proc.info
        if info.get("pid") == own_pid:
            continue
        yield info.get("exe"), info.get("cmdline") or []


def wineserver_prefixes() -> set[str]:
    """Wine prefixes that have a wineserver running right now (Linux)."""
    found: set[str] = set()
    for proc in psutil.process_iter(["name"]):
        if not (proc.info.get("name") or "").startswith("wineserver"):
            continue
        try:
            env = proc.environ()
        except (psutil.Error, OSError):
            continue
        if env.get("WINEPREFIX"):
            found.add(_norm(env["WINEPREFIX"]))
        if env.get("STEAM_COMPAT_DATA_PATH"):
            found.add(_norm(os.path.join(env["STEAM_COMPAT_DATA_PATH"], "pfx")))
    return found


def _uses_wine_registry(game: Game) -> bool:
    return bool(game.wine_prefix) and any(loc.kind == LocationKind.REGISTRY
                                          for loc in game.config_locations)


def can_check(game: Game) -> bool:
    return bool(game.install_dir or game.executables)


def _basename(path: str) -> str:
    # Case-insensitive everywhere: Wine command lines don't keep .exe capitalisation.
    return re.split(r"[\\/]", path.rstrip("\\/"))[-1].casefold()


def running_games(games: Iterable[Game], processes: Iterable[ProcInfo] | None = None,
                  wine_prefixes: set[str] | None = None) -> list[Game]:
    """The games in `games` that have a matching process running.

    A game whose settings live in its Wine prefix's registry also counts as running
    while that prefix's wineserver is up: Wine writes its registry files back when it
    shuts down, which would overwrite a swap.
    """
    games = list(games)
    wine_games = [g for g in games if _uses_wine_registry(g)]
    busy_wine: list[Game] = []
    if wine_games:
        prefixes = wineserver_prefixes() if wine_prefixes is None else wine_prefixes
        busy_wine = [g for g in wine_games if _norm(g.wine_prefix) in prefixes]
    games = [g for g in games if can_check(g) and g not in busy_wine]
    if not games:
        return busy_wine
    dirs = {g.id: _norm(g.install_dir) for g in games if g.install_dir}
    full = {g.id: {_norm(e) for e in g.executables if "/" in e or "\\" in e} for g in games}
    names = {g.id: {e.casefold() for e in g.executables if "/" not in e and "\\" not in e}
             for g in games}
    found: set[str] = set()
    for exe, cmdline in processes if processes is not None else system_processes():
        for path in _candidate_paths(exe, cmdline):
            p, base = _norm(path), _basename(path)
            for g in games:
                if g.id in found:
                    continue
                d = dirs.get(g.id)
                if (d and (p == d or p.startswith(d + os.sep))) or p in full[g.id] \
                        or base in names[g.id]:
                    found.add(g.id)
        if len(found) == len(games):
            break
    return busy_wine + [g for g in games if g.id in found]
