import os
import stat
from pathlib import Path

import pytest

from rigswap.core import paths, swap
from rigswap.core.models import ConfigLocation, Game, Library, LocationKind


def not_running(games):
    return []


@pytest.fixture
def env(tmp_path):
    """A library with two games: one config file, and one config folder that also holds saves."""
    root = tmp_path / "data"
    live = tmp_path / "live"
    cp_cfg = live / "Cyberpunk" / "UserSettings.json"
    cp_cfg.parent.mkdir(parents=True)
    cp_cfg.write_text("handheld-720p")
    rdr_dir = live / "RDR2" / "Settings"
    rdr_dir.mkdir(parents=True)
    (rdr_dir / "system.xml").write_text("low")
    (rdr_dir / "saves").mkdir()
    (rdr_dir / "saves" / "slot1.sav").write_text("chapter 2")

    lib = Library()
    handheld = lib.add_setup("Handheld")
    dock = lib.add_setup("eGPU Dock")
    cp = lib.add_game(Game(name="Cyberpunk 2077", install_dir=str(tmp_path / "games" / "cp"),
                           config_locations=[ConfigLocation(str(cp_cfg))]))
    rdr_loc = ConfigLocation(str(rdr_dir), LocationKind.DIRECTORY, exclude=["saves/*"])
    rdr = lib.add_game(Game(name="RDR2", install_dir=str(tmp_path / "games" / "rdr"),
                            config_locations=[rdr_loc]))
    lib.add_game(Game(name="No configs yet"))

    # Save the handheld state, then make the dock state and save that too.
    for g in (cp, rdr):
        swap.save_snapshot(root, lib, g, handheld.id, running=not_running)
    cp_cfg.write_text("dock-4k")
    (rdr_dir / "system.xml").write_text("ultra")
    for g in (cp, rdr):
        swap.save_snapshot(root, lib, g, dock.id, running=not_running)
    lib.settings.active_setup_id = dock.id

    class Env:
        pass

    e = Env()
    e.root, e.lib, e.handheld, e.dock, e.cp, e.rdr = root, lib, handheld, dock, cp, rdr
    e.cp_cfg, e.rdr_dir = cp_cfg, rdr_dir
    return e


def live_state(e):
    return (e.cp_cfg.read_text(), (e.rdr_dir / "system.xml").read_text(),
            (e.rdr_dir / "saves" / "slot1.sav").read_text())


def test_switch_applies_snapshots(env):
    result = swap.switch_setup(env.root, env.lib, env.handheld.id, running=not_running)
    assert live_state(env) == ("handheld-720p", "low", "chapter 2")
    assert env.lib.settings.active_setup_id == env.handheld.id
    assert sorted(os.listdir(env.rdr_dir)) == ["saves", "system.xml"]  # no temp files left
    assert {g.name for g in result.applied} == {"Cyberpunk 2077", "RDR2"}
    assert result.without_snapshot == []  # "No configs yet" has no locations, so isn't counted


def test_folder_apply_never_touches_other_files(env):
    """Saves in a config folder are newer than the snapshot; they must survive a switch."""
    (env.rdr_dir / "saves" / "slot1.sav").write_text("chapter 5")  # existed at snapshot time
    (env.rdr_dir / "saves" / "slot2.sav").write_text("new save")
    (env.rdr_dir / "new_option.xml").write_text("made by a game update")
    swap.switch_setup(env.root, env.lib, env.handheld.id, running=not_running)
    assert (env.rdr_dir / "system.xml").read_text() == "low"
    assert (env.rdr_dir / "saves" / "slot1.sav").read_text() == "chapter 5"
    assert (env.rdr_dir / "saves" / "slot2.sav").read_text() == "new save"
    assert (env.rdr_dir / "new_option.xml").read_text() == "made by a game update"
    snap = paths.snapshot_dir(env.root, env.rdr.id, env.handheld.id)
    assert not (snap / "loc0" / "saves").exists()


def test_include_patterns(tmp_path):
    root, folder = tmp_path / "data", tmp_path / "cfg"
    (folder / "sub").mkdir(parents=True)
    for name in ("a.ini", "sub/b.ini", "c.sav"):
        (folder / name).write_text("1")
    lib = Library()
    s = lib.add_setup("S")
    g = lib.add_game(Game(name="G", config_locations=[
        ConfigLocation(str(folder), LocationKind.DIRECTORY, include=["*.ini"])]))
    swap.save_snapshot(root, lib, g, s.id, running=not_running)
    snap = paths.snapshot_dir(root, g.id, s.id) / "loc0"
    files = sorted(p.relative_to(snap).as_posix() for p in snap.rglob("*") if p.is_file())
    assert files == ["a.ini", "sub/b.ini"]


def test_changing_patterns_counts_as_changed_location(env):
    env.rdr.config_locations[0].exclude = []
    result = swap.switch_setup(env.root, env.lib, env.handheld.id, running=not_running)
    assert result.stale == [env.rdr]
    assert (env.rdr_dir / "system.xml").read_text() == "ultra"


def test_save_on_switch_keeps_tweaks_made_in_a_setup(env):
    env.cp_cfg.write_text("dock-4k-with-dlss")  # tweaked while docked
    swap.switch_setup(env.root, env.lib, env.handheld.id, running=not_running)
    assert env.cp_cfg.read_text() == "handheld-720p"
    swap.switch_setup(env.root, env.lib, env.dock.id, running=not_running)
    assert env.cp_cfg.read_text() == "dock-4k-with-dlss"


def test_save_on_switch_off(env):
    env.lib.settings.save_on_switch = False
    env.cp_cfg.write_text("dock-4k-with-dlss")
    swap.switch_setup(env.root, env.lib, env.handheld.id, running=not_running)
    swap.switch_setup(env.root, env.lib, env.dock.id, running=not_running)
    assert env.cp_cfg.read_text() == "dock-4k"


def test_running_game_blocks_whole_switch(env):
    before = live_state(env)
    with pytest.raises(swap.GameRunningError) as exc:
        swap.switch_setup(env.root, env.lib, env.handheld.id,
                          running=lambda games: [g for g in games if g.name == "RDR2"])
    assert [g.name for g in exc.value.games] == ["RDR2"]
    assert "RDR2" in str(exc.value)
    assert live_state(env) == before
    assert env.lib.settings.active_setup_id == env.dock.id
    assert not paths.backups_dir(env.root).exists()


def test_failure_part_way_rolls_back(env, monkeypatch):
    before = live_state(env)
    real_copy = swap.atomic_copy
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:  # first game written, second fails
            raise OSError("disk full")
        real_copy(src, dst)

    monkeypatch.setattr(swap, "atomic_copy", flaky)
    with pytest.raises(OSError, match="disk full"):
        swap.switch_setup(env.root, env.lib, env.handheld.id, running=not_running)
    monkeypatch.setattr(swap, "atomic_copy", real_copy)
    assert live_state(env) == before
    assert env.lib.settings.active_setup_id == env.dock.id
    assert list(paths.backups_dir(env.root).iterdir()) == []


def test_undo_restores_files_and_active_setup(env):
    env.cp_cfg.write_text("dock-4k-with-dlss")
    swap.switch_setup(env.root, env.lib, env.handheld.id, running=not_running)
    result = swap.undo_last(env.root, env.lib, running=not_running)
    assert result is not None and {g.name for g in result.restored} == {"Cyberpunk 2077", "RDR2"}
    assert live_state(env) == ("dock-4k-with-dlss", "ultra", "chapter 2")
    assert env.lib.settings.active_setup_id == env.dock.id
    assert swap.undo_last(env.root, env.lib, running=not_running) is None


def test_undo_deletes_files_the_switch_created(env):
    # The handheld snapshot has a file that doesn't exist live any more.
    (env.rdr_dir / "system.xml").unlink()
    swap.switch_setup(env.root, env.lib, env.handheld.id, running=not_running)
    assert (env.rdr_dir / "system.xml").read_text() == "low"
    swap.undo_last(env.root, env.lib, running=not_running)
    assert not (env.rdr_dir / "system.xml").exists()


def test_undo_refuses_while_running(env):
    swap.switch_setup(env.root, env.lib, env.handheld.id, running=not_running)
    with pytest.raises(swap.GameRunningError):
        swap.undo_last(env.root, env.lib, running=lambda games: list(games)[:1])
    assert live_state(env)[0] == "handheld-720p"


def test_changed_locations_skip_only_that_game(env, tmp_path):
    env.cp.config_locations = [ConfigLocation(str(tmp_path / "elsewhere.json"))]
    result = swap.switch_setup(env.root, env.lib, env.handheld.id, running=not_running)
    assert result.stale == [env.cp] and result.applied == [env.rdr]
    assert env.cp_cfg.read_text() == "dock-4k"  # untouched
    assert not (tmp_path / "elsewhere.json").exists()
    assert (env.rdr_dir / "system.xml").read_text() == "low"
    assert env.lib.settings.active_setup_id == env.handheld.id


def test_missing_live_file_at_save_time_leaves_live_alone(env, tmp_path):
    later = tmp_path / "live" / "later.ini"
    game = env.lib.add_game(Game(name="Later", config_locations=[ConfigLocation(str(later))]))
    swap.save_snapshot(env.root, env.lib, game, env.handheld.id, running=not_running)
    later.write_text("created by the game afterwards")
    swap.switch_setup(env.root, env.lib, env.handheld.id, running=not_running)
    assert later.read_text() == "created by the game afterwards"


def test_read_only_config_is_replaced(env):
    os.chmod(env.cp_cfg, stat.S_IREAD)
    swap.switch_setup(env.root, env.lib, env.handheld.id, running=not_running)
    assert env.cp_cfg.read_text() == "handheld-720p"


def test_resaving_snapshot_leaves_no_staging_folders(env):
    swap.save_snapshot(env.root, env.lib, env.cp, env.dock.id, running=not_running)
    names = sorted(p.name for p in (paths.snapshots_dir(env.root) / env.cp.id).iterdir())
    assert names == sorted([env.handheld.id, env.dock.id])
    assert len([s for s in env.lib.snapshots if s.game_id == env.cp.id]) == 2


def test_save_snapshot_refuses_while_running(env):
    with pytest.raises(swap.GameRunningError):
        swap.save_snapshot(env.root, env.lib, env.cp, env.dock.id, running=lambda g: list(g))


def test_backups_are_pruned(env, monkeypatch):
    monkeypatch.setattr(swap, "MAX_BACKUPS", 3)
    for _ in range(4):
        swap.switch_setup(env.root, env.lib, env.handheld.id, running=not_running)
        swap.switch_setup(env.root, env.lib, env.dock.id, running=not_running)
    assert len(list(paths.backups_dir(env.root).iterdir())) == 3


def test_game_without_snapshot_for_target_is_reported(env, tmp_path):
    cfg = tmp_path / "live" / "solo.ini"
    cfg.write_text("x")
    game = env.lib.add_game(Game(name="Solo", config_locations=[ConfigLocation(str(cfg))]))
    laptop = env.lib.add_setup("Laptop")
    result = swap.switch_setup(env.root, env.lib, laptop.id, running=not_running)
    assert game in result.without_snapshot and result.applied == []
    # its live config was still saved into the outgoing setup
    assert env.lib.snapshot(game.id, env.dock.id) is not None


def test_file_location_that_is_a_folder_is_an_error(env, tmp_path):
    bad = tmp_path / "live" / "actually_a_dir"
    bad.mkdir()
    game = env.lib.add_game(Game(name="Bad", config_locations=[ConfigLocation(str(bad))]))
    with pytest.raises(swap.SwapError):
        swap.save_snapshot(env.root, env.lib, game, env.dock.id, running=not_running)
    assert not Path(paths.snapshot_dir(env.root, game.id, env.dock.id)).exists()


def test_unchecked_games_are_reported(env):
    env.cp.install_dir = None
    result = swap.switch_setup(env.root, env.lib, env.handheld.id, running=not_running)
    assert result.unchecked == [env.cp]
    env.cp.executables = ["Cyberpunk2077.exe"]
    result = swap.switch_setup(env.root, env.lib, env.dock.id, running=not_running)
    assert result.unchecked == []


def test_delete_game_and_setup_data(env):
    swap.delete_setup_data(env.root, env.handheld.id)
    for g in (env.cp, env.rdr):
        assert not paths.snapshot_dir(env.root, g.id, env.handheld.id).exists()
        assert paths.snapshot_dir(env.root, g.id, env.dock.id).exists()
    swap.delete_game_data(env.root, env.cp.id)
    assert not (paths.snapshots_dir(env.root) / env.cp.id).exists()
