"""Learn mode: find a game's settings by watching what changes.

1. scan() the places games keep settings (and the registry) before.
2. The user changes one graphics setting in the game, applies it and quits.
3. scan() again; suggestions() compares the two and proposes what changed.

Other programs write files too while this happens, so noise (caches, logs, temp files,
other apps' folders) is skipped, and suggestions are ranked: a config-type file in the
game's own folders, or with the game's name in its path, is ticked by default; anything
else is offered unticked.

Suggestions are deliberately narrow: each changed file is a file location (never its
whole folder), and registry changes become the parent key with only the changed values
included, so saves in the same place aren't swapped.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from rigswap.core import detect, registry
from rigswap.core.models import ConfigLocation, Game, LocationKind

CONFIG_EXTS = {".ini", ".cfg", ".conf", ".config", ".json", ".xml", ".txt", ".yaml", ".yml",
               ".toml", ".lua", ".prefs", ".vdf", ".settings", ".dat", ".sav_settings"}
NOISE_EXTS = {".log", ".tmp", ".temp", ".etl", ".dmp", ".lock", ".pid", ".pak", ".bin",
              ".cache", ".db-journal", ".db-wal", ".db-shm", ".ldb", ".sqlite-wal"}
# Folder names (any case) that are skipped while scanning: caches, logs, other apps.
PRUNE_DIRS = {"cache", "caches", "shadercache", "dxcache", "glcache", "gpucache", "code cache",
              "logs", "log", "temp", "tmp", "crashdumps", "crashes", "crash reports",
              "d3dscache", "nvidia", "nvidia corporation", "amd", "intel", "microsoft",
              "packages", "google", "mozilla", "discord", "spotify", "slack", "teams",
              "onedrive", "electron", "node_modules", ".git", "__pycache__", "rigswap",
              "steam", "htmlcache", "webcache", "indexeddb", "local storage",
              "session storage", "service worker", "trash", "flatpak", "thumbnails",
              "fontconfig", "pulse", "gvfs-metadata", "recently-used.xbel", "baloo"}
# Registry areas (relative to HKEY_CURRENT_USER\Software, lower case) that change on their own
REGISTRY_SKIP = ("microsoft", "classes", "policies", "wine", "valve\\steam", "nvidia corporation",
                 "intel", "amd", "google", "mozilla", "rigswap")
GENERIC_WORDS = {"the", "game", "games", "edition", "remastered", "definitive", "deluxe",
                 "complete", "goty", "ultimate", "and", "for", "launcher", "steam", "win64",
                 "windows", "x64", "client"}
MAX_FILE_SIZE = 5 * 1024 * 1024
MAX_FILES = 300_000
MAX_DEPTH = 8
SOFTWARE = "HKEY_CURRENT_USER\\Software"


@dataclass
class Scan:
    files: dict[str, tuple[int, int]] = field(default_factory=dict)  # path -> (size, mtime_ns)
    registry: dict[str, dict[str, str]] = field(default_factory=dict)  # sub-key -> name -> fp
    partial: bool = False  # stopped at MAX_FILES
    roots: list[str] = field(default_factory=list)  # the folders that were scanned


@dataclass
class Suggestion:
    location: ConfigLocation
    reason: str
    likely: bool  # ticked by default


# --- where to look ------------------------------------------------------------------


def roots(game: Game, ctx: detect.Context) -> list[Path]:
    """Folders worth scanning for this game, for where it runs."""
    v = ctx.values
    out: list[Path] = []
    if ctx.os_name == "windows":
        for key in ("winAppData", "winLocalAppData", "winLocalAppDataLow", "winDocuments"):
            if key in v:
                out.append(Path(v[key]))
        if "home" in v:
            out.append(Path(v["home"]) / "Saved Games")
    else:
        for key in ("xdgConfig", "xdgData"):
            if key in v:
                out.append(Path(v[key]))
    if game.install_dir:
        out.append(Path(game.install_dir))
    seen, unique = set(), []
    for p in out:
        key = os.path.normcase(str(p))
        if key not in seen and p.is_dir():
            seen.add(key)
            unique.append(p)
    return unique


# --- scanning -----------------------------------------------------------------------


def scan(game: Game, ctx: detect.Context | None = None, reg_backend=None,
         scan_roots: list[Path] | None = None) -> Scan:
    ctx = ctx or detect.context_for(game)
    result = Scan()
    for root in (scan_roots if scan_roots is not None else roots(game, ctx)):
        result.roots.append(str(root))
        if not _walk(root, result):
            break
    backend = reg_backend if reg_backend is not None else (
        detect.registry_backend(game) if ctx.os_name == "windows" else None)
    if backend is not None:
        result.registry = _scan_registry(backend)
    return result


def _walk(root: Path, result: Scan) -> bool:
    """Record (size, mtime) of files under root; False once MAX_FILES is reached."""
    stack = [(str(root), 0)]
    while stack:
        path, depth = stack.pop()
        try:
            entries = list(os.scandir(path))
        except OSError:
            continue
        for entry in entries:
            name = entry.name.lower()
            try:
                if entry.is_dir(follow_symlinks=False):
                    if depth < MAX_DEPTH and name not in PRUNE_DIRS:
                        stack.append((entry.path, depth + 1))
                elif entry.is_file(follow_symlinks=False):
                    if os.path.splitext(name)[1] in NOISE_EXTS:
                        continue
                    st = entry.stat(follow_symlinks=False)
                    if st.st_size <= MAX_FILE_SIZE:
                        result.files[entry.path] = (st.st_size, st.st_mtime_ns)
                        if len(result.files) >= MAX_FILES:
                            result.partial = True
                            return False
            except OSError:
                continue
    return True


def _skipped(rel: str) -> bool:
    low = rel.lower()
    return any(low == s or low.startswith(s + "\\") for s in REGISTRY_SKIP)


def _scan_registry(backend) -> dict[str, dict[str, str]]:
    try:
        if isinstance(backend, registry.WindowsRegistry):
            tree: registry.Tree = {}
            for child in backend.subkeys(SOFTWARE):
                if _skipped(child):
                    continue
                for rel, values in (backend.read(f"{SOFTWARE}\\{child}") or {}).items():
                    tree[f"{child}\\{rel}" if rel else child] = values
        else:
            tree = backend.read(SOFTWARE) or {}
    except registry.RegistryError:
        return {}
    return {rel: {name: json.dumps(value.get("wine", value), sort_keys=True)
                  for name, value in values.items()}
            for rel, values in tree.items() if rel and not _skipped(rel)}


# --- comparing ------------------------------------------------------------------------


def name_tokens(game: Game) -> set[str]:
    words = set()
    for text in (game.name, Path(game.install_dir).name if game.install_dir else ""):
        for w in re.split(r"[^0-9a-z]+", text.casefold()):
            if len(w) >= 3 and w not in GENERIC_WORDS:
                words.add(w)
    return words


def _below_root(path: str, scan_roots: list[str]) -> str:
    """The part of the path under the scanned folder it was found in.

    Only that part can say anything about the game: the folders above it (like the
    user's home, /home/<name>) would match a game whose name contains the user name.
    """
    best = ""
    for root in scan_roots:
        if _inside(path, root) and len(root) > len(best):
            best = root
    return path[len(best):] if best else path


def _mentions(path: str, tokens: set[str]) -> bool:
    flat = re.sub(r"[^0-9a-z]+", "", path.casefold())
    return any(t in flat for t in tokens)


def _inside(path: str, folder: str | None) -> bool:
    if not folder:
        return False
    p, f = os.path.normcase(os.path.normpath(path)), os.path.normcase(os.path.normpath(folder))
    return p == f or p.startswith(f + os.sep)


def _managed(game: Game, path: str) -> bool:
    for loc in game.config_locations:
        if loc.kind == LocationKind.FILE and os.path.normcase(loc.path) == os.path.normcase(path):
            return True
        if loc.kind == LocationKind.DIRECTORY and _inside(path, loc.path):
            return True
    return False


def _glob_escape(name: str) -> str:
    """A value name as a pattern that matches only itself."""
    return re.sub(r"([\[\]*?])", r"[\1]", name)


def suggestions(game: Game, before: Scan, after: Scan) -> list[Suggestion]:
    tokens = name_tokens(game)
    out: list[Suggestion] = []
    for path, meta in after.files.items():
        old = before.files.get(path)
        if old == meta or _managed(game, path):
            continue
        ext = os.path.splitext(path)[1].lower()
        own = _inside(path, game.install_dir) or _mentions(_below_root(path, after.roots),
                                                          tokens)
        likely = ext in CONFIG_EXTS and own
        what = "new file" if old is None else "changed"
        why = "in the game's folder or named after it" if own else "elsewhere"
        out.append(Suggestion(ConfigLocation(path), f"{what}, {why}", likely))
    for rel, values in after.registry.items():
        old_values = before.registry.get(rel, {})
        changed = sorted(n for n, fp in values.items() if old_values.get(n) != fp)
        if not changed:
            continue
        key = f"{SOFTWARE}\\{rel}"
        if any(loc.kind == LocationKind.REGISTRY and loc.path.casefold() == key.casefold()
               for loc in game.config_locations):
            continue
        likely = _mentions(rel, tokens)
        shown = ", ".join(n or "(Default)" for n in changed[:4]) + (
            f" and {len(changed) - 4} more" if len(changed) > 4 else "")
        out.append(Suggestion(
            ConfigLocation(key, LocationKind.REGISTRY, include=[_glob_escape(n or "(Default)")
                                                                for n in changed]),
            f"registry values changed: {shown}", likely))
    out.sort(key=lambda s: (not s.likely, s.location.path.casefold()))
    return out
