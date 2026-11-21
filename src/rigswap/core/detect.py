"""Find a game's config files on disk using the Ludusavi manifest.

Placeholders are expanded for where the game actually runs:

- Windows: the real user folders (Documents via the shell, so OneDrive moves work).
- Proton (Linux, Windows game): the same folders inside the game's Wine prefix,
  `pfx/drive_c/users/steamuser/...`, matched case-insensitively because Windows paths
  aren't case-sensitive but Linux is.
- Native Linux: $XDG_CONFIG_HOME / $XDG_DATA_HOME.

Only paths tagged `config` and not `save` are added automatically. Paths tagged
both are folders that mix settings and saves: switching them would roll saves back,
so they are only suggested. Registry keys are reported (supported in Phase 4).
"""

from __future__ import annotations

import getpass
import glob
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from rigswap.core import registry
from rigswap.core.manifest import Manifest, ManifestGame
from rigswap.core.models import ConfigLocation, Game, Library, LocationKind

PROTON_USER = "steamuser"
_PLACEHOLDER = re.compile(r"<([A-Za-z]+)>")


@dataclass(frozen=True)
class Context:
    """Where a game runs: which OS rules apply and what each placeholder means."""

    os_name: str  # "windows" | "linux" | "mac", as the manifest spells it
    store: str | None
    values: dict[str, str]  # placeholder -> path (missing = can't expand)
    case_insensitive: bool = False


@dataclass
class Detection:
    manifest_name: str
    found: list[ConfigLocation] = field(default_factory=list)
    # tagged config AND save: folders that also hold saves
    mixed: list[str] = field(default_factory=list)
    # registry keys that hold settings and saves together
    mixed_registry: list[str] = field(default_factory=list)
    # registry keys that can't be swapped on this system (no Windows registry or prefix)
    registry: list[str] = field(default_factory=list)
    # config paths that apply here but don't exist yet (e.g. game never launched)
    missing: list[str] = field(default_factory=list)


# --- contexts -----------------------------------------------------------------------


def context_for(game: Game, *, platform: str = sys.platform,
                env: dict[str, str] | None = None, home: Path | None = None,
                documents: Path | None = None) -> Context:
    env = dict(os.environ) if env is None else env
    home = Path(home) if home is not None else Path.home()
    store = game.store or ("steam" if game.steam_app_id is not None else None)
    values: dict[str, str] = {}
    if game.install_dir:
        base = Path(game.install_dir)
        values["base"] = str(base)
        values["game"] = base.name
        if base.parent.name == "common" and base.parent.parent.name == "steamapps":
            values["root"] = str(base.parent.parent.parent)
    values["storeUserId"] = "*"

    if game.wine_prefix:
        drive_c = Path(game.wine_prefix) / "drive_c"
        user_name = prefix_user(Path(game.wine_prefix))
        user = drive_c / "users" / user_name
        values.update(_windows_dirs(user, user / "Documents"))
        values.update({
            "winPublic": str(drive_c / "users" / "Public"),
            "winProgramData": str(drive_c / "ProgramData"),
            "winDir": str(drive_c / "windows"),
            "osUserName": user_name,
        })
        return Context("windows", store, values, case_insensitive=True)

    values["home"] = str(home)
    values["osUserName"] = _user_name()
    if platform == "win32":
        values.update(_windows_dirs(home, documents or _windows_documents(home)))
        for key, var in (("winAppData", "APPDATA"), ("winLocalAppData", "LOCALAPPDATA"),
                         ("winPublic", "PUBLIC"), ("winProgramData", "PROGRAMDATA"),
                         ("winDir", "WINDIR")):
            if env.get(var):
                values[key] = env[var]
        return Context("windows", store, values)
    if platform == "darwin":
        return Context("mac", store, values)
    values["xdgConfig"] = env.get("XDG_CONFIG_HOME") or str(home / ".config")
    values["xdgData"] = env.get("XDG_DATA_HOME") or str(home / ".local" / "share")
    return Context("linux", store, values)


def prefix_user(prefix: Path) -> str:
    """The Windows user folder inside a Wine prefix: 'steamuser' for Proton, the
    Linux user name for plain Wine (Heroic, Lutris)."""
    users = prefix / "drive_c" / "users"
    for candidate in (PROTON_USER, _user_name()):
        if candidate and (users / candidate).is_dir():
            return candidate
    try:
        others = sorted(p.name for p in users.iterdir()
                        if p.is_dir() and p.name.lower() != "public")
    except OSError:
        others = []
    return others[0] if others else PROTON_USER


def _windows_dirs(home: Path, documents: Path) -> dict[str, str]:
    return {
        "home": str(home),
        "winAppData": str(home / "AppData" / "Roaming"),
        "winLocalAppData": str(home / "AppData" / "Local"),
        "winLocalAppDataLow": str(home / "AppData" / "LocalLow"),
        "winDocuments": str(documents),
    }


def _user_name() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return ""


def _windows_documents(home: Path) -> Path:
    """The real Documents folder (it may have been moved, e.g. into OneDrive)."""
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

        # FOLDERID_Documents {FDD39AD0-238F-46AF-ADB4-6C85480369C7}
        fid = GUID(0xFDD39AD0, 0x238F, 0x46AF,
                   (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
        out = ctypes.c_wchar_p()
        shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
        if shell32.SHGetKnownFolderPath(ctypes.byref(fid), 0, None, ctypes.byref(out)) == 0:
            path = out.value
            ctypes.windll.ole32.CoTaskMemFree(out)  # type: ignore[attr-defined]
            if path:
                return Path(path)
    except Exception:
        pass
    return home / "Documents"


# --- expansion and globbing ---------------------------------------------------------


def expand(rule_path: str, ctx: Context) -> str | None:
    """Substitute placeholders (escaped for globbing). None if one can't be expanded."""
    missing = False

    def sub(m: re.Match[str]) -> str:
        nonlocal missing
        key = m.group(1)
        if key not in ctx.values:
            missing = True
            return ""
        value = ctx.values[key]
        return value if key == "storeUserId" else glob.escape(value)

    expanded = _PLACEHOLDER.sub(sub, rule_path)
    if missing:
        return None
    return os.path.normpath(expanded)


def _case_insensitive(pattern: str, fixed_prefix: str) -> str:
    """Make letters after fixed_prefix match either case (outside [...] groups)."""
    head, tail = pattern[: len(fixed_prefix)], pattern[len(fixed_prefix):]
    out, in_bracket = [], False
    for ch in tail:
        if ch == "[":
            in_bracket = True
        elif ch == "]":
            in_bracket = False
        if ch.isalpha() and not in_bracket:
            out.append(f"[{ch.lower()}{ch.upper()}]")
        else:
            out.append(ch)
    return head + "".join(out)


def matches(pattern: str, ctx: Context) -> list[Path]:
    hits = glob.glob(pattern, recursive=True)
    if not hits and ctx.case_insensitive:
        # Keep the prefix up to drive_c exact (it's ours); relax the rest.
        anchor = pattern.find("drive_c")
        prefix = pattern[: anchor + len("drive_c")] if anchor >= 0 else ""
        hits = glob.glob(_case_insensitive(pattern, prefix), recursive=True)
    return sorted({_true_case(Path(h)) for h in hits})


def _true_case(path: Path) -> Path:
    """The path as spelled on disk (case-insensitive file systems echo the pattern's case)."""
    parts = list(path.parts)
    out = Path(parts[0])
    for part in parts[1:]:
        try:
            names = os.listdir(out)
        except OSError:
            return path
        if part not in names:
            folded = [n for n in names if n.casefold() == part.casefold()]
            if len(folded) == 1:
                part = folded[0]
        out = out / part
    return out


# --- detection ------------------------------------------------------------------------


def detect(game: Game, entry: ManifestGame, ctx: Context, reg_backend=None) -> Detection:
    result = Detection(manifest_name=entry.name)
    seen: set[str] = set()
    for rule in entry.files:
        if not rule.applies(ctx.os_name, ctx.store):
            continue
        pattern = expand(rule.path, ctx)
        if pattern is None:
            continue
        hits = matches(pattern, ctx)
        if not rule.config_only:
            result.mixed.extend(str(h) for h in hits if str(h) not in result.mixed)
            continue
        if not hits:
            result.missing.append(pattern)
            continue
        for hit in hits:
            key = os.path.normcase(str(hit))
            if key in seen:
                continue
            seen.add(key)
            kind = LocationKind.DIRECTORY if hit.is_dir() else LocationKind.FILE
            result.found.append(ConfigLocation(str(hit), kind))
    result.found = _drop_nested(result.found)
    if ctx.os_name != "windows":
        return result  # native Linux/Mac builds don't keep settings in a registry
    for rule in entry.registry:
        if not rule.applies(ctx.os_name, ctx.store):
            continue
        try:
            key = registry.normalize_key(rule.path)
        except registry.RegistryError:
            continue  # a hive RigSwap doesn't handle
        if reg_backend is None:
            result.registry.append(key)
        elif not reg_backend.exists(key):
            result.missing.append(key)
        elif rule.config_only:
            result.found.append(ConfigLocation(key, LocationKind.REGISTRY))
        else:
            result.mixed_registry.append(key)
    return result


def _drop_nested(locations: list[ConfigLocation]) -> list[ConfigLocation]:
    """If a folder is a location, files or folders inside it are redundant."""
    dirs = [os.path.normcase(loc.path) for loc in locations if loc.kind == LocationKind.DIRECTORY]
    keep = []
    for loc in locations:
        p = os.path.normcase(loc.path)
        if any(p != d and p.startswith(d + os.sep) for d in dirs):
            continue
        keep.append(loc)
    return keep


def _key(loc: ConfigLocation) -> str:
    """Compare paths the way the OS does; registry keys are never case-sensitive."""
    if loc.kind == LocationKind.REGISTRY:
        return loc.path.casefold()
    return os.path.normcase(loc.path)


def new_locations(game: Game, detection: Detection) -> list[ConfigLocation]:
    """Found locations the game doesn't already have (or sit inside one it has)."""
    have = [(_key(loc), loc.kind) for loc in game.config_locations]
    out = []
    for loc in detection.found:
        p = _key(loc)
        if any(p == h or (kind == LocationKind.DIRECTORY and p.startswith(h + os.sep))
               for h, kind in have):
            continue
        out.append(loc)
    return out


def detect_game(game: Game, man: Manifest, ctx: Context | None = None) -> Detection | None:
    """Detect a game's config files, or None if the manifest doesn't know the game."""
    entry = man.find(steam_app_id=game.steam_app_id, name=game.name,
                     install_dir=game.install_dir)
    if entry is None:
        return None
    return detect(game, entry, ctx or context_for(game), registry_backend(game))


def registry_backend(game: Game):
    """The registry this game's settings would live in here, or None if there isn't one."""
    try:
        return registry.backend_for(game.wine_prefix, sys.platform)
    except (registry.RegistryError, ImportError):
        return None


def add_detected(lib: Library, game: Game, detection: Detection) -> list[ConfigLocation]:
    """Add the safe (config-only) locations found; returns what was added."""
    added = []
    for loc in new_locations(game, detection):
        lib.add_config_location(game.id, loc)
        added.append(loc)
    return added
