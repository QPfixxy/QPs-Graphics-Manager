"""Command-line entry point. Grows into `qpgm switch "eGPU Dock"` and friends."""

from __future__ import annotations

import argparse
import sys

from qpgm import __version__
from qpgm.core.models import LibraryError
from qpgm.core.store import LibraryStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qpgm", description="QP's Graphics Manager")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("info", help="show where data is stored and what's in the library")
    steam_p = sub.add_parser("steam", help="list installed Steam games")
    steam_p.add_argument("--import", dest="do_import", action="store_true",
                         help="add them to the library")
    setup_p = sub.add_parser("setup", help="list or add setups")
    setup_p.add_argument("action", choices=["list", "add"])
    setup_p.add_argument("name", nargs="?")
    switch_p = sub.add_parser("switch", help="apply every game's config for a setup")
    switch_p.add_argument("setup", help="setup name")
    sub.add_parser("undo", help="undo the last switch")
    args = parser.parse_args(argv)

    if args.command == "info":
        return _info()
    if args.command == "steam":
        return _steam(args.do_import)
    if args.command == "setup":
        return _setup(args.action, args.name)
    if args.command == "switch":
        return _switch(args.setup)
    if args.command == "undo":
        return _undo()
    parser.print_help()
    return 0


def _steam(do_import: bool) -> int:
    from qpgm.core import steam

    installs = steam.find_steam_installs()
    if not installs:
        print("No Steam install found.")
        return 0
    for inst in installs:
        print(f"Steam: {inst.root}" + (" (Flatpak)" if inst.flatpak else ""))
        for lib in steam.library_folders(inst.root):
            print(f"  Library: {lib}")
    games = steam.discover_games(installs)
    for g in games:
        tag = " [Proton]" if g.uses_proton else ""
        print(f"{g.app_id:>9}  {g.name}{tag}")
    print(f"{len(games)} game(s)")
    if do_import:
        store = LibraryStore()
        try:
            lib = store.load()
        except LibraryError as e:
            print(f"Library can't be read: {e}", file=sys.stderr)
            return 1
        result = steam.import_games(lib, games)
        store.save(lib)
        print(f"Imported: {len(result.added)} new, {len(result.updated)} updated")
    return 0


def _info() -> int:
    store = LibraryStore()
    print(f"Data folder: {store.root}")
    try:
        lib = store.load()
    except LibraryError as e:
        print(f"Library can't be read: {e}", file=sys.stderr)
        return 1
    active = lib.setup(lib.settings.active_setup_id).name if lib.settings.active_setup_id else "-"
    print(f"Setups: {len(lib.setups)}  Games: {len(lib.games)}  Active setup: {active}")
    return 0


def _load(store: LibraryStore):
    try:
        return store.load()
    except LibraryError as e:
        print(f"Library can't be read: {e}", file=sys.stderr)
        return None


def _setup(action: str, name: str | None) -> int:
    store = LibraryStore()
    lib = _load(store)
    if lib is None:
        return 1
    if action == "list":
        for s in lib.setups:
            marker = "*" if s.id == lib.settings.active_setup_id else " "
            print(f"{marker} {s.name}")
        return 0
    if not name:
        print("Give the new setup a name.", file=sys.stderr)
        return 2
    try:
        lib.add_setup(name)
    except LibraryError as e:
        print(e, file=sys.stderr)
        return 1
    store.save(lib)
    return 0


def _switch(name: str) -> int:
    from qpgm.core import swap

    store = LibraryStore()
    lib = _load(store)
    if lib is None:
        return 1
    setup = lib.setup_by_name(name)
    if setup is None:
        print(f"No setup named {name!r}.", file=sys.stderr)
        return 1
    try:
        result = swap.switch_setup(store.root, lib, setup.id)
    except swap.SwapError as e:
        print(f"Nothing changed: {e}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"Switch failed and was rolled back: {e}", file=sys.stderr)
        return 1
    store.save(lib)
    print(f"Switched to {setup.name}: {len(result.applied)} game(s) applied, "
          f"{len(result.saved)} saved, {len(result.without_snapshot)} without a snapshot.")
    return 0


def _undo() -> int:
    from qpgm.core import swap

    store = LibraryStore()
    lib = _load(store)
    if lib is None:
        return 1
    try:
        result = swap.undo_last(store.root, lib)
    except swap.SwapError as e:
        print(f"Nothing changed: {e}", file=sys.stderr)
        return 1
    if result is None:
        print("Nothing to undo.")
        return 0
    store.save(lib)
    active = lib.setup(result.active_setup_id).name if result.active_setup_id else "-"
    print(f"Undone: {len(result.restored)} game(s) restored. Active setup: {active}")
    return 0
