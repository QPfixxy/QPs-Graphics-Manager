"""Linux launchers: Heroic (GOG, Epic, Amazon) and Lutris. Native and Flatpak installs.

Heroic keeps, under its config folder (~/.config/heroic or the Flatpak equivalent):
- GOG:    gog_store/installed.json  {"installed": [{appName, install_path, platform, ...}]}
          titles in store_cache/gog_library.json {"games": [{app_name, title}]}
- Epic:   legendaryConfig/legendary/installed.json  {app_name: {title, install_path, ...}}
- Amazon: nile_config/nile/installed.json [{id, path}], titles in library.json
- Each game's Wine prefix: GamesConfig/<appName>.json -> {appName: {"winePrefix": ...}}

Lutris keeps installed games in pga.db (SQLite, table `games`, installed = 1) and each
game's settings, including its Wine prefix, in games/<configpath>.yml.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from rigswap.launchers import FoundGame, exists

HEROIC_FLATPAK = "com.heroicgameslauncher.hgl"
LUTRIS_FLATPAK = "net.lutris.Lutris"


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None


def _home(home: Path | None) -> Path:
    return Path(home) if home is not None else Path.home()


def _expand_home(path: str, home: Path) -> Path:
    """'~/Games/x' -> <home>/Games/x, built as a real path rather than by text replacement."""
    if path == "~" or path.startswith(("~/", "~\\")):
        return home.joinpath(*Path(path[2:]).parts) if len(path) > 2 else home
    return Path(path)


# --- Heroic -------------------------------------------------------------------------


def heroic_roots(home: Path | None = None, env: dict[str, str] | None = None) -> list[Path]:
    home = _home(home)
    env = dict(os.environ) if env is None else env
    config = Path(env.get("XDG_CONFIG_HOME") or home / ".config")
    candidates = [config / "heroic", home / ".config" / "heroic",
                  home / ".var" / "app" / HEROIC_FLATPAK / "config" / "heroic"]
    out = []
    for c in candidates:
        if c.is_dir() and c.resolve() not in {o.resolve() for o in out}:
            out.append(c)
    return out


def heroic(home: Path | None = None, env: dict[str, str] | None = None) -> list[FoundGame]:
    home = _home(home)
    games: list[FoundGame] = []
    for root in heroic_roots(home, env):
        games += _heroic_gog(root, home) + _heroic_epic(root, home) + _heroic_amazon(root, home)
    return games


def _heroic_prefix(root: Path, app: str, platform: str | None, home: Path) -> str | None:
    if (platform or "").lower() not in ("windows", "win32"):
        return None  # native Linux game: no prefix
    data = _load_json(root / "GamesConfig" / f"{app}.json")
    if not isinstance(data, dict):
        return None
    settings = data.get(app) if isinstance(data.get(app), dict) else data
    prefix = settings.get("winePrefix") if isinstance(settings, dict) else None
    if not isinstance(prefix, str) or not prefix:
        return None
    path = _expand_home(prefix, home)
    return str(path) if path.is_dir() else None


def _exe(install: str, exe: Any) -> tuple[str, ...]:
    if not isinstance(exe, str) or not exe:
        return ()
    return (os.path.normpath(exe if os.path.isabs(exe) else os.path.join(install, exe)),)


def _heroic_gog(root: Path, home: Path) -> list[FoundGame]:
    installed = _load_json(root / "gog_store" / "installed.json")
    items = installed.get("installed") if isinstance(installed, dict) else None
    if not isinstance(items, list):
        return []
    library = _load_json(root / "store_cache" / "gog_library.json")
    titles = {}
    if isinstance(library, dict) and isinstance(library.get("games"), list):
        titles = {g.get("app_name"): g.get("title") for g in library["games"]
                  if isinstance(g, dict)}
    games = []
    for item in items:
        if not isinstance(item, dict) or item.get("is_dlc"):
            continue
        app, install = item.get("appName"), item.get("install_path")
        if not app or not exists(install):
            continue
        games.append(FoundGame(
            name=titles.get(app) or Path(install).name, store="gog", store_id=str(app),
            install_dir=install, wine_prefix=_heroic_prefix(root, app, item.get("platform"), home),
            executables=_exe(install, item.get("executable"))))
    return games


def _heroic_epic(root: Path, home: Path) -> list[FoundGame]:
    installed = _load_json(root / "legendaryConfig" / "legendary" / "installed.json")
    if not isinstance(installed, dict):
        return []
    games = []
    for app, item in installed.items():
        if not isinstance(item, dict) or item.get("is_dlc"):
            continue
        install = item.get("install_path")
        if not exists(install):
            continue
        games.append(FoundGame(
            name=item.get("title") or Path(install).name, store="epic", store_id=str(app),
            install_dir=install, wine_prefix=_heroic_prefix(root, app, item.get("platform"), home),
            executables=_exe(install, item.get("executable"))))
    return games


def _heroic_amazon(root: Path, home: Path) -> list[FoundGame]:
    base = root / "nile_config" / "nile"
    installed = _load_json(base / "installed.json")
    if not isinstance(installed, list):
        return []
    library = _load_json(base / "library.json")
    titles = {}
    if isinstance(library, list):
        for entry in library:
            product = entry.get("product") if isinstance(entry, dict) else None
            if isinstance(product, dict):
                titles[product.get("id")] = product.get("title")
    games = []
    for item in installed:
        if not isinstance(item, dict):
            continue
        app, install = item.get("id"), item.get("path")
        if not app or not exists(install):
            continue
        games.append(FoundGame(
            name=titles.get(app) or Path(install).name, store="amazon", store_id=str(app),
            install_dir=install, wine_prefix=_heroic_prefix(root, app, "windows", home)))
    return games


# --- Lutris -------------------------------------------------------------------------


def lutris_locations(home: Path | None = None,
                     env: dict[str, str] | None = None) -> list[tuple[Path, Path]]:
    """(pga.db, config folder) pairs for native and Flatpak Lutris."""
    home = _home(home)
    env = dict(os.environ) if env is None else env
    data = Path(env.get("XDG_DATA_HOME") or home / ".local" / "share") / "lutris"
    config = Path(env.get("XDG_CONFIG_HOME") or home / ".config") / "lutris"
    flat = home / ".var" / "app" / LUTRIS_FLATPAK
    pairs = []
    for data_dir, config_dir in ((data, config),
                                 (flat / "data" / "lutris", flat / "config" / "lutris")):
        db = data_dir / "pga.db"
        if db.is_file():
            # Lutris falls back to its data folder when the config folder doesn't exist
            pairs.append((db, config_dir if config_dir.is_dir() else data_dir))
    return pairs


def lutris(home: Path | None = None, env: dict[str, str] | None = None) -> list[FoundGame]:
    import yaml

    games = []
    for db, config_dir in lutris_locations(home, env):
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            try:
                rows = con.execute(
                    "SELECT name, slug, runner, directory, configpath FROM games "
                    "WHERE installed = 1").fetchall()
            finally:
                con.close()
        except sqlite3.Error:
            continue
        for name, slug, runner, directory, configpath in rows:
            if not slug or runner == "steam":
                continue  # Steam games come from Steam itself
            cfg: dict[str, Any] = {}
            if configpath:
                try:
                    loaded = yaml.safe_load((config_dir / "games" / f"{configpath}.yml")
                                            .read_text(encoding="utf-8"))
                    if isinstance(loaded, dict) and isinstance(loaded.get("game"), dict):
                        cfg = loaded["game"]
                except (OSError, yaml.YAMLError):
                    pass
            exe = cfg.get("exe") if isinstance(cfg.get("exe"), str) else None
            prefix = cfg.get("prefix") if isinstance(cfg.get("prefix"), str) else None
            if prefix:
                prefix = str(_expand_home(prefix, _home(home)))
            install = directory or (os.path.dirname(exe) if exe and os.path.isabs(exe) else None)
            if not exists(install):
                continue
            games.append(FoundGame(
                name=name or slug, store="lutris", store_id=slug, install_dir=install,
                wine_prefix=prefix if runner == "wine" and prefix and Path(prefix).is_dir()
                else None,
                executables=_exe(install, exe)))
    return games
