"""Where RigSwap keeps its own data.

Everything lives in the OS's per-user data folder, never next to the app:

- Linux:   ~/.local/share/rigswap  (respects $XDG_DATA_HOME, so Flatpak works too)
- Windows: %LOCALAPPDATA%\\rigswap
- macOS:   ~/Library/Application Support/rigswap

Set RIGSWAP_DATA_DIR to override (tests, portable installs).
"""

from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_data_path

APP_DIR_NAME = "rigswap"
DATA_DIR_ENV = "RIGSWAP_DATA_DIR"


def data_dir() -> Path:
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override).expanduser()
    # Non-roaming on Windows on purpose: config snapshots are machine-specific.
    return user_data_path(APP_DIR_NAME, appauthor=False)


def library_file(root: Path) -> Path:
    return root / "library.json"


def snapshots_dir(root: Path) -> Path:
    return root / "snapshots"


def snapshot_dir(root: Path, game_id: str, setup_id: str) -> Path:
    """Folder holding one game's saved config files for one setup."""
    return snapshots_dir(root) / game_id / setup_id


def backups_dir(root: Path) -> Path:
    """Copies of live configs taken before each swap (used from Phase 1)."""
    return root / "backups"
