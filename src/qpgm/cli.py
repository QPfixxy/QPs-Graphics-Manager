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
    args = parser.parse_args(argv)

    if args.command == "info":
        return _info()
    parser.print_help()
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
