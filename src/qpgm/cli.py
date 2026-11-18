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
    args = parser.parse_args(argv)

    if args.command == "info":
        return _info()
    if args.command == "steam":
        return _steam(args.do_import)
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
