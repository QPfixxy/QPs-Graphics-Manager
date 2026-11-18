"""Is a game running? Swapping configs under a running game is refused.

A game counts as running when any process's executable, or any path in its command
line, is inside the game's install folder. The command-line check is what catches
Proton games: their process is Wine, with the game's .exe as an argument, often
written as a Wine path like Z:\\home\\me\\.steam\\...\\game.exe.

Games without an install folder (added by hand) can't be checked yet.
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


def running_games(games: Iterable[Game], processes: Iterable[ProcInfo] | None = None) -> list[Game]:
    """The games in `games` that have a process running from their install folder."""
    dirs = {g.id: _norm(g.install_dir) for g in games if g.install_dir}
    if not dirs:
        return []
    by_id = {g.id: g for g in games}
    found: set[str] = set()
    for exe, cmdline in processes if processes is not None else system_processes():
        for path in _candidate_paths(exe, cmdline):
            p = _norm(path)
            for game_id, d in dirs.items():
                if game_id not in found and (p == d or p.startswith(d + os.sep)):
                    found.add(game_id)
        if len(found) == len(dirs):
            break
    return [by_id[i] for i in dirs if i in found]
