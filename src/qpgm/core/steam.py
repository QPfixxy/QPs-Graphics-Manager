"""Find Steam installs, their library folders and the games installed in them.

Where Steam lives:
- Windows: the registry (HKCU\\Software\\Valve\\Steam\\SteamPath), then Program Files (x86).
- Linux:   ~/.local/share/Steam (and the ~/.steam/steam symlink), Flatpak Steam under
           ~/.var/app/com.valvesoftware.Steam, and Snap Steam.
- macOS:   ~/Library/Application Support/Steam (handy for development).

Each install lists its libraries in steamapps/libraryfolders.vdf, and each library
has one steamapps/appmanifest_<appid>.acf per installed game. On Linux, a game that
runs under Proton has a Wine prefix at <library>/steamapps/compatdata/<appid>/pfx;
its config files live inside that prefix.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from qpgm.core import vdf
from qpgm.core.models import Game, Library

FLATPAK_ID = "com.valvesoftware.Steam"

# Steam "apps" that are runtimes/tools, not games.
_TOOL_APP_IDS = {
    228980,  # Steamworks Common Redistributables
    1070560,  # Steam Linux Runtime 1.0 (scout)
    1391110,  # Steam Linux Runtime 2.0 (soldier)
    1628350,  # Steam Linux Runtime 3.0 (sniper)
    1493710,  # Proton Experimental
    250820,  # SteamVR
}
_TOOL_NAME = re.compile(
    r"^(Proton\b|Steam Linux Runtime|Steamworks Common Redistributables|SteamVR\b)",
    re.IGNORECASE,
)
_STATE_FULLY_INSTALLED = 4


@dataclass(frozen=True)
class SteamInstall:
    root: Path
    flatpak: bool = False


@dataclass(frozen=True)
class SteamGame:
    app_id: int
    name: str
    install_dir: Path
    library: Path
    steam_root: Path
    proton_prefix: Path | None = None
    flatpak: bool = False

    @property
    def uses_proton(self) -> bool:
        return self.proton_prefix is not None


# --- locating Steam -------------------------------------------------------


def candidate_roots(
    home: Path | None = None,
    platform: str = sys.platform,
    env: dict[str, str] | None = None,
    winreg: ModuleType | None = None,
) -> list[SteamInstall]:
    """Places Steam may be installed, most likely first. They may not exist."""
    home = Path(home) if home is not None else Path.home()
    env = dict(os.environ) if env is None else env
    if platform == "win32":
        roots = [SteamInstall(p) for p in _windows_registry_roots(winreg)]
        pf86 = env.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        roots.append(SteamInstall(Path(pf86) / "Steam"))
        return roots
    if platform == "darwin":
        return [SteamInstall(home / "Library" / "Application Support" / "Steam")]
    xdg_data = Path(env.get("XDG_DATA_HOME") or home / ".local" / "share")
    flatpak_base = home / ".var" / "app" / FLATPAK_ID
    return [
        SteamInstall(xdg_data / "Steam"),
        SteamInstall(home / ".local" / "share" / "Steam"),
        SteamInstall(home / ".steam" / "steam"),
        SteamInstall(home / ".steam" / "root"),
        SteamInstall(flatpak_base / ".local" / "share" / "Steam", flatpak=True),
        SteamInstall(flatpak_base / "data" / "Steam", flatpak=True),
        SteamInstall(home / "snap" / "steam" / "common" / ".local" / "share" / "Steam"),
    ]


def _windows_registry_roots(winreg: ModuleType | None = None) -> list[Path]:
    if winreg is None:
        try:
            import winreg  # type: ignore[no-redef]
        except ImportError:
            return []
    keys = [
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
    ]
    found = []
    for hive, subkey, value in keys:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                path, _ = winreg.QueryValueEx(key, value)
        except OSError:
            continue
        if path:
            found.append(Path(path))
    return found


def find_steam_installs(candidates: list[SteamInstall] | None = None) -> list[SteamInstall]:
    """Existing Steam installs, with symlinked duplicates (~/.steam/steam) removed."""
    if candidates is None:
        candidates = candidate_roots()
    seen: dict[Path, SteamInstall] = {}
    for cand in candidates:
        if not (cand.root / "steamapps").is_dir():
            continue
        key = _real(cand.root)
        if key in seen:
            if cand.flatpak and not seen[key].flatpak:
                seen[key] = SteamInstall(seen[key].root, flatpak=True)
            continue
        seen[key] = cand
    return list(seen.values())


# --- libraries and games ---------------------------------------------------


def library_folders(root: Path) -> list[Path]:
    """Library folders for one Steam install. The install folder itself is always first."""
    libs = [Path(root)]
    for vdf_path in (root / "steamapps" / "libraryfolders.vdf",
                     root / "config" / "libraryfolders.vdf"):
        if vdf_path.is_file():
            try:
                data = vdf.load(vdf_path)
            except (OSError, vdf.VdfError):
                continue
            libs.extend(_paths_from_libraryfolders(data))
            break
    unique: dict[Path, Path] = {}
    for lib in libs:
        if (lib / "steamapps").is_dir():
            unique.setdefault(_real(lib), lib)
    return list(unique.values())


def _paths_from_libraryfolders(data: dict) -> list[Path]:
    section = vdf.get(data, "libraryfolders")
    if not isinstance(section, dict):
        return []
    paths = []
    for key, value in section.items():
        if not key.isdigit():
            continue  # e.g. "TimeNextStatsReport", "contentstatsid"
        # current format: "0" { "path" "..." }; old format: "1" "D:\\SteamLibrary"
        path = vdf.get(value, "path") if isinstance(value, dict) else value
        if isinstance(path, str) and path:
            paths.append(Path(path))
    return paths


def games_in_library(library: Path, install: SteamInstall) -> list[SteamGame]:
    steamapps = library / "steamapps"
    games = []
    for manifest in sorted(steamapps.glob("appmanifest_*.acf")):
        game = _read_manifest(manifest, library, install)
        if game is not None:
            games.append(game)
    return games


def _read_manifest(manifest: Path, library: Path, install: SteamInstall) -> SteamGame | None:
    try:
        state = vdf.get(vdf.load(manifest), "AppState")
    except (OSError, vdf.VdfError):
        return None
    if not isinstance(state, dict):
        return None
    try:
        app_id = int(vdf.get(state, "appid", ""))
    except ValueError:
        return None
    name = (vdf.get(state, "name") or "").strip()
    installdir = vdf.get(state, "installdir")
    if not name or not isinstance(installdir, str) or not installdir:
        return None
    if app_id in _TOOL_APP_IDS or _TOOL_NAME.match(name):
        return None
    install_dir = library / "steamapps" / "common" / installdir
    flags = vdf.get(state, "StateFlags")
    if flags is not None:
        try:
            if not int(flags) & _STATE_FULLY_INSTALLED:
                return None
        except ValueError:
            pass
    if not install_dir.is_dir():
        return None
    prefix = library / "steamapps" / "compatdata" / str(app_id) / "pfx"
    return SteamGame(
        app_id=app_id,
        name=name,
        install_dir=install_dir,
        library=library,
        steam_root=install.root,
        proton_prefix=prefix if prefix.is_dir() else None,
        flatpak=install.flatpak,
    )


def discover_games(installs: list[SteamInstall] | None = None) -> list[SteamGame]:
    """All installed Steam games across all installs and libraries, sorted by name.

    If the same app shows up twice (e.g. native and Flatpak Steam), the first wins.
    """
    if installs is None:
        installs = find_steam_installs()
    by_id: dict[int, SteamGame] = {}
    for install in installs:
        for lib in library_folders(install.root):
            for game in games_in_library(lib, install):
                by_id.setdefault(game.app_id, game)
    return sorted(by_id.values(), key=lambda g: g.name.casefold())


# --- library import ----------------------------------------------------------


@dataclass
class ImportResult:
    added: list[Game]
    updated: list[Game]


def import_games(library: Library, games: list[SteamGame]) -> ImportResult:
    """Add new Steam games to the library and refresh paths of ones already in it.

    Existing games keep their name, config locations and snapshots; only
    install_dir and proton_prefix are updated (a game may have moved library).
    """
    result = ImportResult(added=[], updated=[])
    for sg in games:
        install_dir = str(sg.install_dir)
        prefix = str(sg.proton_prefix) if sg.proton_prefix else None
        existing = library.game_by_steam_app_id(sg.app_id)
        if existing is None:
            game = Game(name=sg.name, steam_app_id=sg.app_id, install_dir=install_dir,
                        proton_prefix=prefix)
            result.added.append(library.add_game(game))
        elif (existing.install_dir, existing.proton_prefix) != (install_dir, prefix):
            existing.install_dir = install_dir
            existing.proton_prefix = prefix
            result.updated.append(existing)
    return result


def _real(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path.absolute()
