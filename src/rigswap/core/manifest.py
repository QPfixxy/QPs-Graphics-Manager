"""The Ludusavi manifest: where thousands of games keep their files.

https://github.com/mtkennerly/ludusavi-manifest is compiled from PCGamingWiki. Each
path is tagged `save` and/or `config`; RigSwap only cares about `config`.

The manifest is downloaded at runtime (never bundled: its data comes from
PCGamingWiki under CC BY-NC-SA), using the ETag check its README asks tools to use.
The 17 MB YAML takes seconds to parse, so after each download we keep a compact
JSON index holding only games with config paths, which loads in a fraction of a second.
"""

from __future__ import annotations

import contextlib
import json
import re
import ssl
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rigswap import __version__
from rigswap.core.fsutil import atomic_write_text

MANIFEST_URL = (
    "https://raw.githubusercontent.com/mtkennerly/ludusavi-manifest/refs/heads/data/data/manifest.yaml"
)
INDEX_VERSION = 1
USER_AGENT = f"RigSwap/{__version__} (+https://github.com/QPfixxy/RigSwap)"


class ManifestError(Exception):
    pass


@dataclass(frozen=True)
class PathRule:
    """One `files` or `registry` entry from the manifest."""

    path: str
    tags: frozenset[str]
    when: tuple[tuple[str | None, str | None], ...]  # (os, store) pairs; empty = always

    @property
    def config_only(self) -> bool:
        return "config" in self.tags and "save" not in self.tags

    def applies(self, os_name: str, store: str | None) -> bool:
        if not self.when:
            return True
        return any((w_os is None or w_os == os_name) and (w_store is None or w_store == store)
                   for w_os, w_store in self.when)


@dataclass(frozen=True)
class ManifestGame:
    name: str
    files: tuple[PathRule, ...]
    registry: tuple[PathRule, ...]
    steam_ids: tuple[int, ...]
    install_dirs: tuple[str, ...]


def _norm(name: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", name.casefold())


class Manifest:
    def __init__(self, index: dict[str, Any]) -> None:
        self.etag: str | None = index.get("etag")
        self.fetched_at: str | None = index.get("fetched_at")
        self._games: dict[str, dict[str, Any]] = index["games"]
        self._by_steam: dict[int, str] = {}
        self._by_name: dict[str, str] = {}
        for name, g in self._games.items():
            for sid in g.get("steam", []):
                self._by_steam.setdefault(int(sid), name)
            self._by_name.setdefault(_norm(name), name)
            for d in g.get("installDir", []):
                self._by_name.setdefault(_norm(d), name)
        for alias, target in index.get("aliases", {}).items():
            if target in self._games:
                self._by_name.setdefault(_norm(alias), target)

    def __len__(self) -> int:
        return len(self._games)

    def _game(self, name: str) -> ManifestGame:
        g = self._games[name]

        def rules(items: list[dict[str, Any]]) -> tuple[PathRule, ...]:
            return tuple(
                PathRule(r["path"], frozenset(r.get("tags", [])),
                         tuple((w.get("os"), w.get("store")) for w in r.get("when", [])))
                for r in items
            )

        return ManifestGame(name=name, files=rules(g.get("files", [])),
                            registry=rules(g.get("registry", [])),
                            steam_ids=tuple(g.get("steam", [])),
                            install_dirs=tuple(g.get("installDir", [])))

    def find(self, *, steam_app_id: int | None = None, name: str | None = None,
             install_dir: str | None = None) -> ManifestGame | None:
        """Look a game up by Steam app id first, then by name or install folder name."""
        if steam_app_id is not None and steam_app_id in self._by_steam:
            return self._game(self._by_steam[steam_app_id])
        for candidate in (name, Path(install_dir).name if install_dir else None):
            if candidate:
                hit = self._by_name.get(_norm(candidate))
                if hit:
                    return self._game(hit)
        return None


# --- building the index from the YAML -----------------------------------------------


def build_index(raw: dict[str, Any], etag: str | None = None) -> dict[str, Any]:
    """Keep only what RigSwap needs: games with config-tagged files or registry keys."""
    games: dict[str, Any] = {}
    aliases: dict[str, str] = {}
    for name, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        if isinstance(entry.get("alias"), str):
            aliases[str(name)] = entry["alias"]
            continue
        files = _config_rules(entry.get("files"))
        registry = _config_rules(entry.get("registry"))
        if not files and not registry:
            continue
        steam = []
        if isinstance(entry.get("steam"), dict) and isinstance(entry["steam"].get("id"), int):
            steam.append(entry["steam"]["id"])
        ids = entry.get("id") if isinstance(entry.get("id"), dict) else {}
        extra = ids.get("steamExtra")
        if isinstance(extra, list):
            steam.extend(x for x in extra if isinstance(x, int))
        install_dirs = list((entry.get("installDir") or {}).keys()) \
            if isinstance(entry.get("installDir"), dict) else []
        games[str(name)] = {k: v for k, v in {
            "files": files, "registry": registry, "steam": steam,
            "installDir": [str(d) for d in install_dirs],
        }.items() if v}
    return {
        "v": INDEX_VERSION,
        "etag": etag,
        "fetched_at": datetime.now(UTC).isoformat(),
        "games": games,
        "aliases": aliases,
    }


def _config_rules(section: Any) -> list[dict[str, Any]]:
    if not isinstance(section, dict):
        return []
    out = []
    for path, spec in section.items():
        spec = spec if isinstance(spec, dict) else {}
        tags = [t for t in spec.get("tags", []) or [] if isinstance(t, str)]
        if "config" not in tags:
            continue
        rule: dict[str, Any] = {"path": str(path), "tags": sorted(tags)}
        when = [{k: w[k] for k in ("os", "store") if isinstance(w.get(k), str)}
                for w in spec.get("when", []) or [] if isinstance(w, dict)]
        if when:
            rule["when"] = when
        out.append(rule)
    return out


def parse_yaml(path: Path) -> dict[str, Any]:
    import yaml

    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    with open(path, "rb") as f:
        data = yaml.load(f, Loader=loader)  # noqa: S506 - safe loader
    if not isinstance(data, dict):
        raise ManifestError("manifest is not a mapping")
    return data


# --- cache on disk ---------------------------------------------------------------


def index_file(root: Path) -> Path:
    return root / "manifest" / "index.json"


def load(root: Path) -> Manifest | None:
    """The cached manifest, or None if it hasn't been downloaded yet."""
    try:
        index = json.loads(index_file(root).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        raise ManifestError(f"cached manifest is unreadable: {e}") from e
    if index.get("v") != INDEX_VERSION or "games" not in index:
        return None  # an older format: treat as not downloaded
    return Manifest(index)


@dataclass
class UpdateResult:
    changed: bool
    games: int


Opener = Callable[[urllib.request.Request], Any]


def _ssl_context() -> ssl.SSLContext:
    """Verify HTTPS with the operating system's own certificate checks.

    Python's default reads the Windows certificate store directly, which on a fresh
    install may not yet contain the roots Windows fetches on demand, so downloads fail
    with CERTIFICATE_VERIFY_FAILED. truststore asks the OS verifier instead.
    """
    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        return ssl.create_default_context()


def update(root: Path, *, url: str = MANIFEST_URL, opener: Opener | None = None,
           progress: Callable[[int, int | None], None] | None = None) -> UpdateResult:
    """Download the manifest if it changed (ETag check) and rebuild the index."""
    current = None
    with contextlib.suppress(ManifestError):
        current = load(root)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    if current and current.etag:
        req.add_header("If-None-Match", current.etag)
    open_url = opener or (lambda r: urllib.request.urlopen(r, timeout=60,
                                                            context=_ssl_context()))
    try:
        resp = open_url(req)
    except urllib.error.HTTPError as e:
        if e.code == 304 and current is not None:
            return UpdateResult(changed=False, games=len(current))
        raise ManifestError(f"download failed: HTTP {e.code}") from e
    except (urllib.error.URLError, OSError) as e:
        raise ManifestError(f"download failed: {e}") from e

    with resp:
        status = getattr(resp, "status", 200)
        if status == 304 and current is not None:
            return UpdateResult(changed=False, games=len(current))
        etag = resp.headers.get("ETag")
        total = resp.headers.get("Content-Length")
        total_n = int(total) if total and total.isdigit() else None
        index_file(root).parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=index_file(root).parent, suffix=".yaml",
                                         delete=False) as tmp:
            tmp_path = Path(tmp.name)
            done = 0
            while chunk := resp.read(1 << 16):
                tmp.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total_n)
    try:
        index = build_index(parse_yaml(tmp_path), etag)
    except Exception as e:
        raise ManifestError(f"downloaded manifest couldn't be read: {e}") from e
    finally:
        tmp_path.unlink(missing_ok=True)
    atomic_write_text(index_file(root), json.dumps(index, separators=(",", ":")))
    return UpdateResult(changed=True, games=len(index["games"]))
