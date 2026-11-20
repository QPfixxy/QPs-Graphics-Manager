"""Command-line interface: `rigswap switch "eGPU Dock"` and friends.

Games and setups are named on the command line by name (any case); games can also
be named by Steam app id or the start of their id (shown by `rigswap game list`).
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from pathlib import Path

from rigswap import __version__
from rigswap.core.models import ConfigLocation, Game, Library, LibraryError, LocationKind, Setup
from rigswap.core.store import LibraryStore


class CliError(Exception):
    pass


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    handler: Callable[[argparse.Namespace], int] | None = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    from rigswap.core.swap import SwapError

    try:
        return handler(args)
    except (CliError, LibraryError, SwapError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rigswap", description="RigSwap")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    def cmd(parent, name, handler, help_text):
        p = parent.add_parser(name, help=help_text, description=help_text)
        p.set_defaults(handler=handler)
        return p

    cmd(sub, "info", _info, "show where data is stored and what's in the library")
    cmd(sub, "gui", _gui, "open the app window")

    p = cmd(sub, "steam", _steam, "list installed Steam games")
    p.add_argument("--import", dest="do_import", action="store_true",
                   help="add them to the library")

    setup = sub.add_parser("setup", help="manage setups (Handheld, eGPU Dock, ...)")
    setup_sub = setup.add_subparsers(dest="action", metavar="ACTION", required=True)
    cmd(setup_sub, "list", _setup_list, "list setups (* = active)")
    p = cmd(setup_sub, "add", _setup_add, "add a setup")
    p.add_argument("name")
    p = cmd(setup_sub, "rename", _setup_rename, "rename a setup")
    p.add_argument("name")
    p.add_argument("new_name")
    p = cmd(setup_sub, "remove", _setup_remove, "remove a setup and its saved configs")
    p.add_argument("name")
    p.add_argument("--yes", action="store_true", help="also delete its saved configs")

    game = sub.add_parser("game", help="manage games")
    game_sub = game.add_subparsers(dest="action", metavar="ACTION", required=True)
    cmd(game_sub, "list", _game_list, "list games")
    p = cmd(game_sub, "add", _game_add, "add a game by hand")
    p.add_argument("name")
    p.add_argument("--install-dir", help="the game's install folder (used to see if it's running)")
    p.add_argument("--exe", action="append", default=[], metavar="EXE",
                   help="game executable: full path or file name, e.g. eldenring.exe (repeatable)")
    p = cmd(game_sub, "show", _game_show, "show a game's details, config locations and snapshots")
    p.add_argument("game")
    p = cmd(game_sub, "edit", _game_edit, "change a game's name, install folder or executables")
    p.add_argument("game")
    p.add_argument("--name")
    p.add_argument("--install-dir")
    p.add_argument("--add-exe", action="append", default=[], metavar="EXE")
    p.add_argument("--remove-exe", action="append", default=[], metavar="EXE")
    p = cmd(game_sub, "remove", _game_remove, "remove a game and its saved configs")
    p.add_argument("game")
    p.add_argument("--yes", action="store_true", help="also delete its saved configs")

    config = sub.add_parser("config", help="manage a game's config files and folders")
    config_sub = config.add_subparsers(dest="action", metavar="ACTION", required=True)
    p = cmd(config_sub, "add", _config_add, "add a config file or folder to a game")
    p.add_argument("game")
    p.add_argument("path")
    kind = p.add_mutually_exclusive_group()
    kind.add_argument("--file", action="store_true",
                      help="it's a file (needed if it doesn't exist yet)")
    kind.add_argument("--folder", action="store_true", help="it's a folder")
    p.add_argument("--include", action="append", default=[], metavar="GLOB",
                   help="folders only: just these files, e.g. '*.ini' (repeatable)")
    p.add_argument("--exclude", action="append", default=[], metavar="GLOB",
                   help="folders only: never these files, e.g. 'saves/*' (repeatable)")
    p = cmd(config_sub, "remove", _config_remove,
            "remove a config location (its number from rigswap game show)")
    p.add_argument("game")
    p.add_argument("number", type=int)

    p = cmd(sub, "save", _save, "save a game's current config as its snapshot for a setup")
    p.add_argument("setup")
    p.add_argument("game", nargs="?", help="omit with --all")
    p.add_argument("--all", action="store_true", help="every game that has config locations")

    p = cmd(sub, "switch", _switch, "apply every game's config for a setup")
    p.add_argument("setup")

    cmd(sub, "undo", _undo, "put back the configs from before the last switch")
    return parser


# --- helpers ----------------------------------------------------------------------


def _open() -> tuple[LibraryStore, Library]:
    store = LibraryStore()
    return store, store.load()


def _find_setup(lib: Library, name: str) -> Setup:
    setup = lib.setup_by_name(name)
    if setup is None:
        names = ", ".join(s.name for s in lib.setups) or "none yet"
        raise CliError(f"no setup named {name!r} (setups: {names})")
    return setup


def _abspath(path: str) -> str:
    return os.path.abspath(os.path.expandvars(os.path.expanduser(path)))


def _kind_word(kind: LocationKind) -> str:
    return "folder" if kind == LocationKind.DIRECTORY else "file"


def _active_name(lib: Library) -> str:
    active = lib.settings.active_setup_id
    return lib.setup(active).name if active else "-"


# --- info / steam -----------------------------------------------------------------


def _gui(args) -> int:
    from rigswap.gui.app import main as gui_main

    return gui_main()


def _info(args) -> int:
    store, lib = _open()
    print(f"Data folder: {store.root}")
    print(f"Setups: {len(lib.setups)}  Games: {len(lib.games)}  Active setup: {_active_name(lib)}")
    return 0


def _steam(args) -> int:
    from rigswap.core import steam

    installs = steam.find_steam_installs()
    if not installs:
        print("No Steam install found.")
        return 0
    for inst in installs:
        print(f"Steam: {inst.root}" + (" (Flatpak)" if inst.flatpak else ""))
        for lib_dir in steam.library_folders(inst.root):
            print(f"  Library: {lib_dir}")
    games = steam.discover_games(installs)
    for g in games:
        tag = " [Proton]" if g.uses_proton else ""
        print(f"{g.app_id:>9}  {g.name}{tag}")
    print(f"{len(games)} game(s)")
    if args.do_import:
        store, lib = _open()
        result = steam.import_games(lib, games)
        store.save(lib)
        print(f"Imported: {len(result.added)} new, {len(result.updated)} updated")
    return 0


# --- setups -----------------------------------------------------------------------


def _setup_list(args) -> int:
    _, lib = _open()
    if not lib.setups:
        print('No setups yet. Add one with: rigswap setup add "Handheld"')
    for s in lib.setups:
        marker = "*" if s.id == lib.settings.active_setup_id else " "
        count = sum(1 for snap in lib.snapshots if snap.setup_id == s.id)
        print(f"{marker} {s.name}  ({count} game(s) saved)")
    return 0


def _setup_add(args) -> int:
    store, lib = _open()
    setup = lib.add_setup(args.name)
    store.save(lib)
    print(f"Added setup {setup.name}.")
    return 0


def _setup_rename(args) -> int:
    store, lib = _open()
    setup = _find_setup(lib, args.name)
    old = setup.name
    lib.rename_setup(setup.id, args.new_name)
    store.save(lib)
    print(f"Renamed {old} to {setup.name}.")
    return 0


def _setup_remove(args) -> int:
    from rigswap.core import swap

    store, lib = _open()
    setup = _find_setup(lib, args.name)
    saved = sum(1 for snap in lib.snapshots if snap.setup_id == setup.id)
    if saved and not args.yes:
        raise CliError(f"{setup.name} has saved configs for {saved} game(s); "
                       "add --yes to delete them too")
    lib.remove_setup(setup.id)
    store.save(lib)
    swap.delete_setup_data(store.root, setup.id)
    print(f"Removed setup {setup.name}" + (f" and {saved} saved config(s)." if saved else "."))
    return 0


# --- games ------------------------------------------------------------------------


def _game_list(args) -> int:
    _, lib = _open()
    if not lib.games:
        print("No games yet. Import Steam games with: rigswap steam --import, "
              'or add one with: rigswap game add "Name"')
    for g in sorted(lib.games, key=lambda g: g.name.casefold()):
        source = f"steam {g.steam_app_id}" if g.steam_app_id else "manual"
        saved = [lib.setup(s.setup_id).name for s in lib.snapshots if s.game_id == g.id]
        print(f"{g.id[:8]}  {g.name}  [{source}]  "
              f"{len(g.config_locations)} config location(s)"
              + (f", saved for: {', '.join(saved)}" if saved else ""))
    return 0


def _game_add(args) -> int:
    store, lib = _open()
    game = Game(name=args.name.strip(),
                install_dir=_abspath(args.install_dir) if args.install_dir else None,
                executables=[_exe(e) for e in args.exe])
    lib.add_game(game)
    store.save(lib)
    print(f"Added {game.name} ({game.id[:8]}).")
    if not (game.install_dir or game.executables):
        print("Tip: give it --install-dir or --exe so switching can check it isn't running.")
    print(f'Next: rigswap config add "{game.name}" <path to its config file or folder>')
    return 0


def _exe(value: str) -> str:
    """Keep bare file names as they are; make paths absolute."""
    if "/" in value or "\\" in value or value.startswith("~"):
        return _abspath(value)
    return value


def _game_show(args) -> int:
    _, lib = _open()
    g = lib.find_game(args.game)
    print(f"{g.name}  (id {g.id})")
    if g.steam_app_id:
        print(f"  Steam app: {g.steam_app_id}")
    print(f"  Install folder: {g.install_dir or '-'}")
    if g.proton_prefix:
        print(f"  Proton prefix: {g.proton_prefix}")
    print(f"  Executables: {', '.join(g.executables) or '-'}")
    print("  Config locations:" + ("" if g.config_locations else " none"))
    for n, loc in enumerate(g.config_locations, 1):
        line = f"    {n}. [{_kind_word(loc.kind)}] {loc.path}"
        if not Path(loc.path).exists():
            line += "  (doesn't exist yet)"
        if loc.include:
            line += f"  include {', '.join(loc.include)}"
        if loc.exclude:
            line += f"  exclude {', '.join(loc.exclude)}"
        print(line)
    snaps = [s for s in lib.snapshots if s.game_id == g.id]
    print("  Saved for:" + ("" if snaps else " no setups yet"))
    for s in snaps:
        print(f"    {lib.setup(s.setup_id).name}: {s.saved_at.astimezone():%Y-%m-%d %H:%M}")
    return 0


def _game_edit(args) -> int:
    store, lib = _open()
    g = lib.find_game(args.game)
    if args.name is not None:
        if not args.name.strip():
            raise CliError("the name can't be empty")
        g.name = args.name.strip()
    if args.install_dir is not None:
        g.install_dir = _abspath(args.install_dir) if args.install_dir else None
    for e in args.add_exe:
        if _exe(e) not in g.executables:
            g.executables.append(_exe(e))
    for e in args.remove_exe:
        if _exe(e) not in g.executables:
            raise CliError(f"{g.name} has no executable {e!r}")
        g.executables.remove(_exe(e))
    store.save(lib)
    print(f"Updated {g.name}.")
    return 0


def _game_remove(args) -> int:
    from rigswap.core import swap

    store, lib = _open()
    g = lib.find_game(args.game)
    saved = sum(1 for s in lib.snapshots if s.game_id == g.id)
    if saved and not args.yes:
        raise CliError(f"{g.name} has saved configs for {saved} setup(s); "
                       "add --yes to delete them too")
    lib.remove_game(g.id)
    store.save(lib)
    swap.delete_game_data(store.root, g.id)
    print(f"Removed {g.name}.")
    return 0


# --- config locations -----------------------------------------------------------


def _config_add(args) -> int:
    store, lib = _open()
    g = lib.find_game(args.game)
    path = Path(_abspath(args.path))
    if args.folder:
        kind = LocationKind.DIRECTORY
    elif args.file:
        kind = LocationKind.FILE
    elif path.is_dir():
        kind = LocationKind.DIRECTORY
    elif path.is_file():
        kind = LocationKind.FILE
    else:
        raise CliError(f"{path} doesn't exist; add --file or --folder to say which it will be")
    if kind == LocationKind.DIRECTORY and path.is_file():
        raise CliError(f"{path} is a file, not a folder")
    if kind == LocationKind.FILE and path.is_dir():
        raise CliError(f"{path} is a folder, not a file")
    if kind == LocationKind.FILE and (args.include or args.exclude):
        raise CliError("--include and --exclude only apply to folders")
    loc = ConfigLocation(str(path), kind, include=args.include, exclude=args.exclude)
    lib.add_config_location(g.id, loc)
    store.save(lib)
    print(f"Added {_kind_word(kind)} {path} to {g.name}.")
    _warn_stale(lib, g)
    return 0


def _config_remove(args) -> int:
    store, lib = _open()
    g = lib.find_game(args.game)
    loc = lib.remove_config_location(g.id, args.number - 1)
    store.save(lib)
    print(f"Removed {loc.path} from {g.name}. The file itself wasn't touched.")
    _warn_stale(lib, g)
    return 0


def _warn_stale(lib: Library, g: Game) -> None:
    names = [lib.setup(s.setup_id).name for s in lib.snapshots if s.game_id == g.id]
    if names:
        print(f"Note: {g.name}'s saved configs ({', '.join(names)}) were made with the old "
              "locations; switching skips them until you save them again.")


# --- save / switch / undo --------------------------------------------------------


def _save(args) -> int:
    from rigswap.core import swap

    store, lib = _open()
    setup = _find_setup(lib, args.setup)
    if args.all == bool(args.game):
        raise CliError("name one game, or use --all")
    games = [g for g in lib.games if g.config_locations] if args.all else [lib.find_game(args.game)]
    if not games:
        raise CliError("no games have config locations yet")
    for g in games:
        swap.save_snapshot(store.root, lib, g, setup.id)
        print(f"Saved {g.name} for {setup.name}.")
    store.save(lib)
    return 0


def _switch(args) -> int:
    from rigswap.core import swap

    store, lib = _open()
    setup = _find_setup(lib, args.setup)
    try:
        result = swap.switch_setup(store.root, lib, setup.id)
    except swap.SwapError as e:
        raise CliError(f"nothing changed: {e}") from e
    except OSError as e:
        raise CliError(f"switch failed and was rolled back: {e}") from e
    store.save(lib)
    print(f"Switched to {setup.name}: {len(result.applied)} game(s) applied, "
          f"{len(result.saved)} saved, {len(result.without_snapshot)} without a snapshot.")
    for g in result.stale:
        print(f"Skipped {g.name}: its config locations changed since it was saved for "
              f'{setup.name}. Run: rigswap save "{setup.name}" "{g.name}"')
    for g in result.unchecked:
        print(f"Warning: couldn't check whether {g.name} was running "
              "(give it --install-dir or --exe with rigswap game edit).")
    return 0


def _undo(args) -> int:
    from rigswap.core import swap

    store, lib = _open()
    result = swap.undo_last(store.root, lib)
    if result is None:
        print("Nothing to undo.")
        return 0
    store.save(lib)
    print(f"Undone: {len(result.restored)} game(s) restored. Active setup: {_active_name(lib)}")
    return 0
