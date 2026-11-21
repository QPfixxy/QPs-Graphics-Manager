"""Saving and applying config snapshots, safely.

Snapshots
    A full copy of a game's config locations, in snapshots/<game>/<setup>/:
    capture.json lists each location (path, kind, whether it existed) and loc<N>/
    holds the copy. New snapshots are built in a staging folder and renamed into
    place, so a crash never leaves a half-written one.

Applying
    Never deletes from the live config. A file location is replaced atomically;
    for a folder location only the files in the snapshot are written, and files
    the game creates later are left alone. Folder snapshots hold only files that
    match the location's include/exclude patterns: when a game keeps saves in its
    config folder, exclude them, or every switch would roll them back.

Backups and undo
    Every switch first copies exactly the live files it will overwrite into
    backups/<op>/<game>/ (and notes the files it will create). That backup is used
    to roll back automatically if anything fails part-way, and by undo_last().
    The newest MAX_BACKUPS operations are kept.

Every operation refuses to run while an affected game is running.
"""

from __future__ import annotations

import json
import shutil
import sys
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rigswap.core import paths, processes, registry
from rigswap.core.fsutil import atomic_copy, atomic_write_text, rmtree
from rigswap.core.models import Game, Library, LocationKind, Setup, Snapshot

CAPTURE_FILE = "capture.json"
RECORDS_FILE = "records.json"
OP_FILE = "op.json"
MAX_BACKUPS = 20

RunningCheck = Callable[[Iterable[Game]], list[Game]]


class SwapError(Exception):
    pass


class GameRunningError(SwapError):
    def __init__(self, games: list[Game]) -> None:
        self.games = games
        names = ", ".join(g.name for g in games)
        super().__init__(f"close these games first: {names}")


class SnapshotMismatchError(SwapError):
    """The game's config locations changed since the snapshot was saved."""


@dataclass
class SwitchResult:
    setup: Setup
    applied: list[Game]
    saved: list[Game]
    without_snapshot: list[Game]
    # snapshot no longer matches the game's config locations; skipped, save it again
    stale: list[Game]
    # no install folder or executables, so it couldn't be checked for running
    unchecked: list[Game]
    op_id: str


@dataclass
class UndoResult:
    op_id: str
    restored: list[Game]
    active_setup_id: str | None


# --- snapshots ----------------------------------------------------------------


def save_snapshot(root: Path, lib: Library, game: Game, setup_id: str,
                  *, running: RunningCheck | None = None) -> Snapshot:
    """Copy the game's live config into its snapshot for setup_id."""
    lib.setup(setup_id)
    if not game.config_locations:
        raise SwapError(f"{game.name} has no config locations")
    _refuse_if_running([game], running)
    _write_snapshot(root, game, setup_id)
    return lib.record_snapshot(game.id, setup_id)


def _write_snapshot(root: Path, game: Game, setup_id: str) -> None:
    final = paths.snapshot_dir(root, game.id, setup_id)
    staging = final.with_name(f".{final.name}.new")
    old = final.with_name(f".{final.name}.old")
    rmtree(staging)
    rmtree(old)
    try:
        _capture(game, staging)
    except BaseException:
        rmtree(staging)
        raise
    if final.exists():
        final.rename(old)
    staging.rename(final)
    rmtree(old)


REGISTRY_FILE = "registry.json"


@dataclass
class _RegOp:
    key: str
    subkey: str
    name: str
    value: dict[str, Any]


@dataclass
class _Plan:
    files: list[tuple[Path, Path]]  # (snapshot file, live target)
    registry: list[_RegOp]


def _backend(game: Game):
    return registry.backend_for(game.wine_prefix, sys.platform)


def _capture(game: Game, dest: Path) -> None:
    dest.mkdir(parents=True)
    entries = []
    for i, loc in enumerate(game.config_locations):
        live = Path(loc.path)
        slot = dest / f"loc{i}"
        if loc.kind == LocationKind.REGISTRY:
            try:
                backend = _backend(game)
                tree = backend.read(loc.path)
            except registry.RegistryError as e:
                raise SwapError(str(e)) from e
            present = tree is not None
            if present:
                kept = {sub: {name: v for name, v in values.items()
                              if loc.matches(registry.value_path(sub, name))}
                        for sub, values in tree.items()}
                slot.mkdir()
                atomic_write_text(slot / REGISTRY_FILE, json.dumps(
                    {"backend": backend.kind, "tree": kept}, indent=2))
            entries.append({**loc.to_dict(), "present": present})
            continue
        if loc.kind == LocationKind.FILE:
            if live.exists() and not live.is_file():
                raise SwapError(f"{live} should be a file but isn't")
            present = live.is_file()
            if present:
                slot.mkdir()
                shutil.copy2(live, slot / live.name)
        else:
            if live.exists() and not live.is_dir():
                raise SwapError(f"{live} should be a folder but isn't")
            present = live.is_dir()
            if present:
                slot.mkdir()
                for src in sorted(live.rglob("*")):
                    rel = src.relative_to(live)
                    if src.is_file() and not src.is_symlink() and loc.matches(rel.as_posix()):
                        (slot / rel).parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src, slot / rel)
        entries.append({**loc.to_dict(), "present": present})
    atomic_write_text(dest / CAPTURE_FILE, json.dumps({"locations": entries}, indent=2))


def _read_capture(snap_dir: Path) -> list[dict[str, Any]]:
    try:
        return json.loads((snap_dir / CAPTURE_FILE).read_text(encoding="utf-8"))["locations"]
    except (OSError, ValueError, KeyError) as e:
        raise SwapError(f"snapshot in {snap_dir} is unreadable: {e}") from e


def _matches(entries: list[dict[str, Any]], game: Game) -> bool:
    current = [loc.to_dict() for loc in game.config_locations]
    saved = [{k: e.get(k, []) for k in ("path", "kind", "include", "exclude")} for e in entries]
    return saved == current


def snapshot_matches(root: Path, game: Game, setup_id: str) -> bool:
    """False if the game's config locations changed since this snapshot was saved."""
    try:
        return _matches(_read_capture(paths.snapshot_dir(root, game.id, setup_id)), game)
    except SwapError:
        return False


def can_undo(root: Path) -> bool:
    return bool(_ops(root))


def _plan(game: Game, snap_dir: Path) -> _Plan:
    """What applying this snapshot will write: files to copy and registry values to set."""
    entries = _read_capture(snap_dir)
    if not _matches(entries, game):
        raise SnapshotMismatchError(
            f"{game.name}'s config locations changed since this snapshot was saved; "
            "save it again"
        )
    plan = _Plan(files=[], registry=[])
    for i, e in enumerate(entries):
        if not e["present"]:
            continue  # nothing was there when saved: leave the live side alone
        slot = snap_dir / f"loc{i}"
        if e["kind"] == LocationKind.REGISTRY.value:
            try:
                saved = json.loads((slot / REGISTRY_FILE).read_text(encoding="utf-8"))
                backend = _backend(game)
            except (OSError, ValueError) as err:
                raise SwapError(f"snapshot in {snap_dir} is unreadable: {err}") from err
            except registry.RegistryError as err:
                raise SnapshotMismatchError(str(err)) from err
            if saved.get("backend") != backend.kind:
                raise SnapshotMismatchError(
                    f"{game.name}'s registry snapshot was saved from a different registry "
                    "(Windows vs. a Wine prefix); save it again")
            for sub, values in saved.get("tree", {}).items():
                for name, value in values.items():
                    plan.registry.append(_RegOp(e["path"], sub, name, value))
            continue
        live = Path(e["path"])
        if e["kind"] == LocationKind.FILE.value:
            plan.files.append((slot / live.name, live))
        else:
            for src in sorted(slot.rglob("*")):
                if src.is_file() and not src.is_symlink():
                    plan.files.append((src, live / src.relative_to(slot)))
    return plan


# --- applying with backup -------------------------------------------------------


def _apply(game: Game, snap_dir: Path, backup_dir: Path) -> None:
    """Back up what will be overwritten, then write the snapshot over the live config."""
    plan = _plan(game, snap_dir)
    files_dir = backup_dir / "files"
    files_dir.mkdir(parents=True)
    records: list[dict[str, Any]] = []
    for n, (_, target) in enumerate(plan.files):
        if target.is_file():
            backup_name = f"files/{n}"
            shutil.copy2(target, backup_dir / backup_name)
        else:
            backup_name = None  # created by this apply; undo deletes it
        records.append({"target": str(target), "backup": backup_name})
    backend = _backend(game) if plan.registry else None
    current: dict[str, registry.Tree] = {}
    for op in plan.registry:
        if op.key not in current:
            current[op.key] = backend.read(op.key) or {}
        before = current[op.key].get(op.subkey, {}).get(op.name)
        records.append({"registry": op.key, "backend": backend.kind,
                        "prefix": game.wine_prefix, "subkey": op.subkey, "name": op.name,
                        "before": before})  # None: created by this apply; undo deletes it
    atomic_write_text(backup_dir / RECORDS_FILE, json.dumps(records, indent=2))
    for src, target in plan.files:
        atomic_copy(src, target)
    by_key: dict[str, list[tuple[str, str, dict[str, Any] | None]]] = {}
    for op in plan.registry:
        by_key.setdefault(op.key, []).append((op.subkey, op.name, op.value))
    try:
        for key, changes in by_key.items():
            backend.write(key, changes)
    except registry.RegistryError as e:
        raise SwapError(str(e)) from e


def _restore(backup_dir: Path) -> None:
    records_file = backup_dir / RECORDS_FILE
    if not records_file.exists():
        return  # failed before anything was written
    records = json.loads(records_file.read_text(encoding="utf-8"))
    reg_changes: dict[tuple[str, str | None, str], list] = {}
    for rec in records:
        if "registry" in rec:
            key = (rec["backend"], rec.get("prefix"), rec["registry"])
            reg_changes.setdefault(key, []).append((rec["subkey"], rec["name"], rec["before"]))
            continue
        target = Path(rec["target"])
        if rec["backup"]:
            atomic_copy(backup_dir / rec["backup"], target)
        elif target.is_file():
            target.unlink()
    for (kind, prefix, key), changes in reg_changes.items():
        backend = (registry.WineRegistry(Path(prefix)) if kind == "wine"
                   else registry.WindowsRegistry())
        backend.write(key, changes)


def switch_setup(root: Path, lib: Library, setup_id: str,
                 *, running: RunningCheck | None = None) -> SwitchResult:
    """Make setup_id active: apply every game's snapshot for it.

    With settings.save_on_switch, each game's live config is first saved into the
    outgoing setup's snapshot. If any affected game is running, nothing happens.
    If a write fails, games already switched are rolled back and the error re-raised.
    The caller saves the library afterwards.
    """
    setup = lib.setup(setup_id)
    previous = lib.settings.active_setup_id
    games = [g for g in lib.games if g.config_locations]
    with_snapshot = [g for g in games if lib.snapshot(g.id, setup_id)]
    snap_dirs = {g.id: paths.snapshot_dir(root, g.id, setup_id) for g in with_snapshot}
    to_apply, stale = [], []
    for g in with_snapshot:
        try:
            _plan(g, snap_dirs[g.id])  # fail early on unreadable snapshots
        except SnapshotMismatchError:
            stale.append(g)
        else:
            to_apply.append(g)
    save_back = (lib.settings.save_on_switch and previous and previous != setup_id
                 and not lib.setup(previous).locked)
    to_save = games if save_back else []
    affected = list({g.id: g for g in [*to_save, *to_apply]}.values())
    _refuse_if_running(affected, running)

    for g in to_save:
        _write_snapshot(root, g, previous)
        lib.record_snapshot(g.id, previous)

    op_id = _apply_with_backup(root, [(g, snap_dirs[g.id]) for g in to_apply],
                               kind="switch", previous=previous, new=setup_id)
    lib.settings.active_setup_id = setup_id
    return SwitchResult(
        setup=setup,
        applied=to_apply,
        saved=to_save,
        without_snapshot=[g for g in games if g not in with_snapshot],
        stale=stale,
        unchecked=[g for g in affected if not processes.can_check(g)],
        op_id=op_id,
    )


def _apply_with_backup(root: Path, items: list[tuple[Game, Path]], *, kind: str,
                       previous: str | None, new: str | None) -> str:
    """Apply snapshots as one undoable operation; on failure roll back and re-raise."""
    op_id = _new_op_id()
    op_dir = paths.backups_dir(root) / op_id
    op_dir.mkdir(parents=True)
    atomic_write_text(op_dir / OP_FILE, json.dumps({
        "id": op_id,
        "created": datetime.now(UTC).isoformat(),
        "kind": kind,
        "previous_active_setup_id": previous,
        "new_active_setup_id": new,
        "games": [g.id for g, _ in items],
    }, indent=2))
    started: list[Game] = []
    try:
        for g, snap_dir in items:
            started.append(g)
            _apply(g, snap_dir, op_dir / g.id)
    except BaseException:
        for g in reversed(started):
            _restore(op_dir / g.id)
        rmtree(op_dir)
        raise
    _prune_backups(root)
    return op_id


def restore_game(root: Path, lib: Library, game: Game, setup_id: str,
                 *, running: RunningCheck | None = None) -> str:
    """Put one game's saved config for setup_id back over its live config (undoable)."""
    return restore_games(root, lib, [game], setup_id, running=running)


def restore_games(root: Path, lib: Library, games: list[Game], setup_id: str,
                  *, running: RunningCheck | None = None) -> str:
    """Put several games' saved configs back as one undoable operation."""
    items = []
    for game in games:
        if lib.snapshot(game.id, setup_id) is None:
            raise SwapError(f"{game.name} has no saved config for {lib.setup(setup_id).name}")
        snap_dir = paths.snapshot_dir(root, game.id, setup_id)
        _plan(game, snap_dir)
        items.append((game, snap_dir))
    _refuse_if_running(games, running)
    active = lib.settings.active_setup_id
    return _apply_with_backup(root, items, kind="restore", previous=active, new=active)


def changed_files(root: Path, lib: Library, game: Game, setup_id: str) -> list[Path | str]:
    """Live files (and registry values, as text) that differ from the saved config.

    Empty when they match, or when there's nothing to compare (no snapshot, or it
    no longer matches the game's locations).
    """
    if lib.snapshot(game.id, setup_id) is None:
        return []
    try:
        plan = _plan(game, paths.snapshot_dir(root, game.id, setup_id))
    except SwapError:
        return []
    changed: list[Path | str] = []
    for src, target in plan.files:
        try:
            if not target.is_file() or not _same_bytes(src, target):
                changed.append(target)
        except OSError:
            changed.append(target)
    if plan.registry:
        try:
            backend = _backend(game)
            trees = {key: backend.read(key) or {} for key in {op.key for op in plan.registry}}
        except registry.RegistryError:
            return changed
        for op in plan.registry:
            now = trees[op.key].get(op.subkey, {}).get(op.name)
            if not _same_value(now, op.value):
                changed.append(f"{op.key}\\{registry.value_path(op.subkey, op.name)}")
    return changed


def _same_value(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    if a is None or b is None:
        return a is b
    if "wine" in a or "wine" in b:
        return a.get("wine") == b.get("wine")
    return a == b


def _same_bytes(a: Path, b: Path) -> bool:
    if a.stat().st_size != b.stat().st_size:
        return False
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while True:
            ca, cb = fa.read(1 << 16), fb.read(1 << 16)
            if ca != cb:
                return False
            if not ca:
                return True


def undo_last(root: Path, lib: Library,
              *, running: RunningCheck | None = None) -> UndoResult | None:
    """Put back the live configs from before the most recent switch. None if nothing to undo."""
    ops = _ops(root)
    if not ops:
        return None
    op_dir = ops[-1]
    op = json.loads((op_dir / OP_FILE).read_text(encoding="utf-8"))
    known = {g.id: g for g in lib.games}
    games = [known[i] for i in op["games"] if i in known]
    _refuse_if_running(games, running)
    for game_id in reversed(op["games"]):
        _restore(op_dir / game_id)
    previous = op.get("previous_active_setup_id")
    lib.settings.active_setup_id = previous if any(s.id == previous for s in lib.setups) else None
    rmtree(op_dir)
    return UndoResult(op_id=op["id"], restored=games, active_setup_id=lib.settings.active_setup_id)


# --- helpers ----------------------------------------------------------------------


def delete_game_data(root: Path, game_id: str) -> None:
    """Delete a game's snapshot files (after lib.remove_game). Backups are kept for undo."""
    rmtree(paths.snapshots_dir(root) / game_id)


def delete_setup_data(root: Path, setup_id: str) -> None:
    """Delete every game's snapshot files for a setup (after lib.remove_setup)."""
    base = paths.snapshots_dir(root)
    if base.is_dir():
        for game_dir in base.iterdir():
            rmtree(game_dir / setup_id)


def _refuse_if_running(games: list[Game], running: RunningCheck | None) -> None:
    check = running or processes.running_games
    busy = check(games)
    if busy:
        raise GameRunningError(busy)


def _new_op_id() -> str:
    return f"{datetime.now(UTC):%Y%m%dT%H%M%S%f}-{uuid.uuid4().hex[:6]}"


def _ops(root: Path) -> list[Path]:
    base = paths.backups_dir(root)
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if (p / OP_FILE).is_file())


def _prune_backups(root: Path) -> None:
    for old in _ops(root)[:-MAX_BACKUPS]:
        rmtree(old)
