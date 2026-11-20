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

from qpgm.core.models import Game

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


def can_check(game: Game) -> bool:
    return bool(game.install_dir or game.executables)


def _basename(path: str) -> str:
    # Case-insensitive everywhere: Wine command lines don't keep .exe capitalisation.
    return re.split(r"[\\/]", path.rstrip("\\/"))[-1].casefold()


def running_games(games: Iterable[Game], processes: Iterable[ProcInfo] | None = None) -> list[Game]:
    """The games in `games` that have a matching process running."""
    games = [g for g in games if can_check(g)]
    if not games:
        return []
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
    return [g for g in games if g.id in found]
