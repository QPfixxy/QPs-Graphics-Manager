"""Find config files from how a game is built, without any database.

Recognised engines and where they keep graphics settings:

- Unreal Engine 4/5: a <Project> folder with Content/Paks or Binaries. Settings in
  <base>/<Project>/Saved/Config/<Windows|WindowsNoEditor|WinGDK>/, where <base> is
  %LOCALAPPDATA%, Documents\\My Games, or the install folder itself; on native Linux
  ~/.config/Epic/<Project>/Saved/Config/<Linux|LinuxNoEditor>/. Only the graphics files
  are taken: GameUserSettings.ini, Engine.ini, Scalability.ini.
- Unity: UnityPlayer.dll or a <Game>_Data folder; company and product names come from
  <Game>_Data/app.info. Player preferences live in the registry key
  HKCU\\Software\\<Company>\\<Product> (Windows or inside a Wine prefix), or in
  ~/.config/unity3d/<Company>/<Product>/prefs on native Linux. They mix settings and
  saves, so only Unity's own display values (Screenmanager*, UnityGraphicsQuality*) are
  added automatically; the whole key is only suggested.
- Source: a mod folder with gameinfo.txt; settings in <mod>/cfg/video.txt.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rigswap.core.models import ConfigLocation, Game, LocationKind

UNREAL_FILES = ["GameUserSettings.ini", "Engine.ini", "Scalability.ini"]
UNREAL_WINDOWS_DIRS = ["Windows", "WindowsNoEditor", "WinGDK"]
UNREAL_LINUX_DIRS = ["Linux", "LinuxNoEditor"]
UNITY_DISPLAY_VALUES = ["Screenmanager*", "UnityGraphicsQuality*"]


@dataclass(frozen=True)
class EngineInfo:
    engine: str  # "Unreal Engine" | "Unity" | "Source"
    projects: tuple[str, ...] = ()  # Unreal project folder names, Source mod folders
    company: str | None = None  # Unity
    product: str | None = None  # Unity


@dataclass
class EngineResult:
    engine: str
    found: list[ConfigLocation]
    missing: list[str]  # where settings would be once the game has run
    notes: list[str]  # things RigSwap deliberately didn't add, and why


def _dirs(path: Path) -> list[Path]:
    try:
        return sorted(p for p in path.iterdir() if p.is_dir())
    except OSError:
        return []


def identify(install_dir: str | None) -> EngineInfo | None:
    """Which engine a game uses, from its install folder (None if unknown)."""
    if not install_dir:
        return None
    root = Path(install_dir)
    if not root.is_dir():
        return None

    # Unreal: <Project>/Content/Paks or <Project>/Binaries, next to an Engine folder
    projects = [d.name for d in _dirs(root)
                if d.name.lower() != "engine"
                and ((d / "Content" / "Paks").is_dir() or (d / "Binaries").is_dir())]
    if projects and ((root / "Engine").is_dir()
                     or any((root / p / "Content" / "Paks").is_dir() for p in projects)):
        return EngineInfo("Unreal Engine", tuple(projects))

    # Unity: UnityPlayer.dll / <Game>_Data with app.info or Unity's data files
    for data in [d for d in _dirs(root) if d.name.endswith("_Data")]:
        app_info = data / "app.info"
        if app_info.is_file() or (data / "globalgamemanagers").exists() or \
                (data / "data.unity3d").exists():
            company = product = None
            try:
                lines = app_info.read_text(encoding="utf-8", errors="replace").splitlines()
                company = lines[0].strip() or None if len(lines) > 0 else None
                product = lines[1].strip() or None if len(lines) > 1 else None
            except OSError:
                pass
            return EngineInfo("Unity", company=company, product=product)
    if (root / "UnityPlayer.dll").is_file():
        return EngineInfo("Unity")

    # Source: <mod>/gameinfo.txt
    mods = [d.name for d in _dirs(root) if (d / "gameinfo.txt").is_file()]
    if mods:
        return EngineInfo("Source", tuple(mods))
    return None


def locations(game: Game, info: EngineInfo, values: dict[str, str], os_name: str,
              reg_backend=None) -> EngineResult:
    """Config locations for a recognised engine.

    `values` are detection placeholders (winLocalAppData, winDocuments, xdgConfig, ...)
    for where the game runs; `os_name` is "windows" (also for Proton/Wine) or "linux".
    """
    result = EngineResult(info.engine, [], [], [])
    if info.engine == "Unreal Engine":
        _unreal(game, info, values, os_name, result)
    elif info.engine == "Unity":
        _unity(info, values, os_name, reg_backend, result)
    elif info.engine == "Source" and game.install_dir:
        for mod in info.projects:
            video = Path(game.install_dir) / mod / "cfg" / "video.txt"
            if video.is_file():
                result.found.append(ConfigLocation(str(video)))
            else:
                result.missing.append(str(video))
    return result


def _unreal(game: Game, info: EngineInfo, values: dict[str, str], os_name: str,
            result: EngineResult) -> None:
    for project in info.projects:
        if os_name == "windows":
            bases = [Path(values[k]) / project for k in ("winLocalAppData",) if k in values]
            if "winDocuments" in values:
                bases.append(Path(values["winDocuments"]) / "My Games" / project)
            platforms = UNREAL_WINDOWS_DIRS
        else:
            bases = [Path(values["xdgConfig"]) / "Epic" / project] if "xdgConfig" in values \
                else []
            platforms = UNREAL_LINUX_DIRS
        if game.install_dir:
            bases.append(Path(game.install_dir) / project)
        hits = [b / "Saved" / "Config" / p for b in bases for p in platforms
                if (b / "Saved" / "Config" / p).is_dir()]
        for hit in hits:
            result.found.append(ConfigLocation(str(hit), LocationKind.DIRECTORY,
                                               include=list(UNREAL_FILES)))
        if not hits and bases:
            result.missing.append(str(bases[0] / "Saved" / "Config" / platforms[0]))


def _unity(info: EngineInfo, values: dict[str, str], os_name: str, reg_backend,
           result: EngineResult) -> None:
    if not (info.company and info.product):
        return  # without app.info we can't tell where its preferences are
    if os_name == "windows":
        if reg_backend is None:
            return
        key = f"HKEY_CURRENT_USER\\Software\\{info.company}\\{info.product}"
        if reg_backend.exists(key):
            # Only Unity's own display values: the rest of the key can hold saves.
            # "Choose values…" widens it for game-specific graphics settings.
            result.found.append(ConfigLocation(key, LocationKind.REGISTRY,
                                               include=list(UNITY_DISPLAY_VALUES)))
        else:
            result.missing.append(key)
    elif "xdgConfig" in values:
        prefs = Path(values["xdgConfig"]) / "unity3d" / info.company / info.product / "prefs"
        if prefs.is_file():
            result.notes.append(
                f"Unity keeps this game's settings and saves together in one file ({prefs}); "
                "RigSwap doesn't add it automatically, because switching would also roll "
                "your progress back.")
        else:
            result.missing.append(str(prefs))
