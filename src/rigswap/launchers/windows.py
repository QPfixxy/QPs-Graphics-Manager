"""Windows launchers: Epic Games, GOG and the Xbox Game Bar's list.

- Epic: one JSON `.item` manifest per installed app in
  %PROGRAMDATA%\\Epic\\EpicGamesLauncher\\Data\\Manifests. DLC and unfinished
  installs are skipped.
- GOG: HKLM\\SOFTWARE\\(WOW6432Node\\)GOG.com\\Games\\<id>, values gameName, path, exe;
  entries with dependsOn set are DLC.
- Game Bar: HKCU\\System\\GameConfigStore\\Children\\*\\MatchedExeFullPath, the games
  Windows itself has recognised, from any launcher. It also lists some non-games, so
  these are offered unticked.
"""

from __future__ import annotations

import json
import os
from pathlib import Path, PureWindowsPath
from types import ModuleType

from rigswap.launchers import FoundGame, exists

EPIC_MANIFESTS = r"Epic\EpicGamesLauncher\Data\Manifests"
GOG_KEYS = (r"SOFTWARE\WOW6432Node\GOG.com\Games", r"SOFTWARE\GOG.com\Games")
GAMEBAR_KEY = r"System\GameConfigStore\Children"
# Folder names that are part of a game's layout rather than its name
GENERIC_DIRS = {"bin", "bin64", "bin32", "binaries", "win64", "win32", "x64", "x86", "x86_64",
                "game", "release", "shipping", "retail", "client", "64bit", "32bit", "exe",
                "wingdk", "windows", "launcher"}
GENERIC_PRODUCTS = {"unrealgame", "unreal engine", "unity", "unity player", "game", "launcher",
                    "bootstrap"}


# --- Epic ---------------------------------------------------------------------------


def epic(manifest_dir: Path | None = None) -> list[FoundGame]:
    base = manifest_dir or Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / EPIC_MANIFESTS
    games = []
    for item in sorted(Path(base).glob("*.item")):
        try:
            data = json.loads(item.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        app = data.get("AppName")
        main = data.get("MainGameAppName")
        categories = data.get("AppCategories") or []
        install = data.get("InstallLocation")
        name = data.get("DisplayName")
        if (not app or not name or data.get("bIsIncompleteInstall")
                or (main and main != app) or (categories and "games" not in categories)
                or not exists(install)):
            continue
        exe = data.get("LaunchExecutable")
        games.append(FoundGame(name=name, store="epic", store_id=app, install_dir=install,
                               executables=(os.path.normpath(os.path.join(install, exe)),)
                               if exe else ()))
    return games


# --- GOG ----------------------------------------------------------------------------


def gog(winreg: ModuleType | None = None) -> list[FoundGame]:
    winreg = winreg or _winreg()
    games, seen = [], set()
    for path in GOG_KEYS:
        for sub, values in _subkeys(winreg, winreg.HKEY_LOCAL_MACHINE, path):
            game_id = str(values.get("gameID") or sub)
            install = values.get("path")
            if game_id in seen or values.get("dependsOn") or not exists(install):
                continue
            seen.add(game_id)
            exe = values.get("exe")
            games.append(FoundGame(name=values.get("gameName") or Path(install).name,
                                   store="gog", store_id=game_id, install_dir=install,
                                   executables=(exe,) if exe else ()))
    return games


# --- Xbox Game Bar's list ------------------------------------------------------------


def gamebar(winreg: ModuleType | None = None, product_name=None) -> list[FoundGame]:
    winreg = winreg or _winreg()
    product_name = product_name or _product_name
    windir = os.path.normcase(os.environ.get("WINDIR", r"C:\Windows"))
    games, seen = [], set()
    for _sub, values in _subkeys(winreg, winreg.HKEY_CURRENT_USER, GAMEBAR_KEY):
        exe = values.get("MatchedExeFullPath")
        if not exe or not os.path.isfile(exe):
            continue
        key = os.path.normcase(exe)
        if key in seen or key.startswith(windir + os.sep):
            continue
        seen.add(key)
        root = guess_game_root(exe)
        name = product_name(exe)
        if not name or name.casefold() in GENERIC_PRODUCTS:
            name = PureWindowsPath(root).name
        games.append(FoundGame(name=name, store="gamebar", store_id=key, install_dir=root,
                               executables=(exe,), suggested=False))
    return games


def guess_game_root(exe: str) -> str:
    """The game's own folder for an executable buried in bin/Binaries/Win64 folders."""
    path = PureWindowsPath(exe)
    parts = list(path.parent.parts)
    lowered = [p.lower() for p in parts]
    if "binaries" in lowered:  # Unreal: <Game>\<Project>\Binaries\Win64\x.exe
        i = lowered.index("binaries")
        parts = parts[: max(1, i - 1)]
    while len(parts) > 1 and parts[-1].lower() in GENERIC_DIRS:
        parts.pop()
    return str(PureWindowsPath(*parts))


def _product_name(exe: str) -> str | None:
    """ProductName from the executable's version information, if it has one."""
    try:
        import ctypes
        from ctypes import wintypes

        version = ctypes.windll.version  # type: ignore[attr-defined]
        size = version.GetFileVersionInfoSizeW(exe, None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(exe, 0, size, buf):
            return None
        ptr, length = ctypes.c_void_p(), wintypes.UINT()
        if not version.VerQueryValueW(buf, r"\VarFileInfo\Translation", ctypes.byref(ptr),
                                      ctypes.byref(length)) or length.value < 4:
            return None
        lang, codepage = ctypes.cast(ptr, ctypes.POINTER(wintypes.WORD * 2)).contents
        query = rf"\StringFileInfo\{lang:04x}{codepage:04x}\ProductName"
        if not version.VerQueryValueW(buf, query, ctypes.byref(ptr), ctypes.byref(length)):
            return None
        name = ctypes.wstring_at(ptr, length.value).rstrip("\x00").strip()
        return name or None
    except Exception:
        return None


# --- registry helpers --------------------------------------------------------------


def _winreg() -> ModuleType:
    import winreg

    return winreg


def _subkeys(winreg: ModuleType, hive, path: str):
    """(subkey name, {value name: value}) for each subkey; nothing if the key is missing."""
    try:
        parent = winreg.OpenKey(hive, path)
    except OSError:
        return
    with parent:
        i = 0
        while True:
            try:
                name = winreg.EnumKey(parent, i)
            except OSError:
                break
            i += 1
            try:
                with winreg.OpenKey(parent, name) as key:
                    values = {}
                    j = 0
                    while True:
                        try:
                            vname, value, _type = winreg.EnumValue(key, j)
                        except OSError:
                            break
                        values[vname] = value
                        j += 1
            except OSError:
                continue
            yield name, values
