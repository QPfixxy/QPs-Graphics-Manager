"""Locked setups, restore, changed-file checks, and detection through the CLI."""

import json

import pytest

from rigswap import cli
from rigswap.core import manifest, paths, processes, steam, swap
from rigswap.core.models import ConfigLocation, Game, Library, Setup


def not_running(games):
    return []


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "data"
    cfg = tmp_path / "live" / "settings.ini"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("720p")
    lib = Library()
    hh, dock = lib.add_setup("Handheld"), lib.add_setup("eGPU Dock")
    g = lib.add_game(Game(name="G", executables=["g.exe"],
                          config_locations=[ConfigLocation(str(cfg))]))
    swap.save_snapshot(root, lib, g, hh.id, running=not_running)
    cfg.write_text("4k")
    swap.save_snapshot(root, lib, g, dock.id, running=not_running)
    lib.settings.active_setup_id = dock.id
    return root, lib, g, cfg, hh, dock


def test_locked_setup_is_not_saved_over(env):
    root, lib, g, cfg, hh, dock = env
    dock.locked = True
    cfg.write_text("4k-tweaked")
    swap.switch_setup(root, lib, hh.id, running=not_running)
    swap.switch_setup(root, lib, dock.id, running=not_running)
    assert cfg.read_text() == "4k"  # the tweak wasn't kept: Dock is locked
    # explicit saves still work
    cfg.write_text("4k-v2")
    swap.save_snapshot(root, lib, g, dock.id, running=not_running)
    swap.switch_setup(root, lib, hh.id, running=not_running)
    swap.switch_setup(root, lib, dock.id, running=not_running)
    assert cfg.read_text() == "4k-v2"


def test_locked_round_trips():
    lib = Library(setups=[Setup(name="A", locked=True)])
    lib.settings.use_manifest = True
    again = Library.from_dict(lib.to_dict())
    assert again.setups[0].locked and again.settings.use_manifest is True
    assert Library.from_dict({**lib.to_dict(), "settings": {}}).settings.use_manifest is None


def test_changed_files_and_restore_with_undo(env):
    root, lib, g, cfg, hh, dock = env
    assert swap.changed_files(root, lib, g, dock.id) == []
    cfg.write_text("overwritten by a game update")
    assert swap.changed_files(root, lib, g, dock.id) == [cfg]
    swap.restore_game(root, lib, g, dock.id, running=not_running)
    assert cfg.read_text() == "4k"
    assert lib.settings.active_setup_id == dock.id
    swap.undo_last(root, lib, running=not_running)
    assert cfg.read_text() == "overwritten by a game update"
    assert lib.settings.active_setup_id == dock.id  # restore doesn't change the setup
    cfg.unlink()
    assert swap.changed_files(root, lib, g, dock.id) == [cfg]
    with pytest.raises(swap.GameRunningError):
        swap.restore_game(root, lib, g, dock.id, running=lambda games: list(games))


def test_restore_needs_a_snapshot(env):
    root, lib, g, cfg, hh, dock = env
    laptop = lib.add_setup("Laptop")
    with pytest.raises(swap.SwapError):
        swap.restore_game(root, lib, g, laptop.id, running=not_running)
    assert swap.changed_files(root, lib, g, laptop.id) == []


# --- CLI ----------------------------------------------------------------------------

YAML = """
Hades:
  files:
    "<winDocuments>/Saved Games/Hades":
      tags: [config, save]
  steam:
    id: 1145360
Elden Ring:
  files:
    "<winAppData>/EldenRing/GraphicsConfig.xml":
      tags: [config]
  registry:
    HKEY_CURRENT_USER/Software/FromSoftware:
      tags: [config]
  steam:
    id: 1245620
"""


@pytest.fixture
def run(monkeypatch, tmp_path, capsys):
    data = tmp_path / "data"
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(data))
    monkeypatch.setattr(processes, "system_processes", lambda: iter([]))
    yml = tmp_path / "m.yaml"
    yml.write_text(YAML)

    def install_manifest():
        index = manifest.build_index(manifest.parse_yaml(yml), etag='"t"')
        manifest.index_file(data).parent.mkdir(parents=True, exist_ok=True)
        manifest.index_file(data).write_text(json.dumps(index))

    def _run(*argv):
        code = cli.main(list(argv))
        out = capsys.readouterr()
        return code, out.out + out.err

    _run.install_manifest = install_manifest
    return _run


@pytest.fixture
def fake_windows_home(monkeypatch, tmp_path):
    """Make detection look at a Windows-style home under tmp_path."""
    from rigswap.core import detect

    home = tmp_path / "home"
    real = detect.context_for

    def ctx(game, **kw):
        return real(game, platform="win32", env={"APPDATA": str(home / "AppData" / "Roaming")},
                    home=home, documents=home / "Documents")

    monkeypatch.setattr(detect, "context_for", ctx)
    return home


def test_detect_cli(run, fake_windows_home, monkeypatch):
    from rigswap.core import detect

    # as on a computer without a Windows registry (the test is about the message)
    monkeypatch.setattr(detect, "registry_backend", lambda game: None)
    gfx = fake_windows_home / "AppData" / "Roaming" / "EldenRing" / "GraphicsConfig.xml"
    gfx.parent.mkdir(parents=True)
    gfx.write_text("x")
    hades = fake_windows_home / "Documents" / "Saved Games" / "Hades"
    hades.mkdir(parents=True)
    code, out = run("detect", "--all")
    assert code == 1 and "manifest update" in out
    run.install_manifest()
    run("game", "add", "Elden Ring")
    run("game", "add", "Hades")
    code, out = run("detect", "--all")
    assert f"+ file: {gfx}" in out and "also holds saves, not added" in out
    assert "can't be swapped on this computer" in out and "--add" in out
    code, out = run("detect", "Elden Ring", "--add")
    assert "Added 1 config location(s)" in out
    code, out = run("game", "show", "Elden Ring")
    assert str(gfx) in out
    code, out = run("detect", "Hades", "--add")
    assert "Added 0" in out  # the mixed folder is never added automatically
    run("game", "add", "Unknown Indie")
    assert "not in the database" in run("detect", "Unknown Indie")[1]


def test_steam_import_detects_when_manifest_cached(run, monkeypatch, tmp_path, fake_windows_home):
    gfx = fake_windows_home / "AppData" / "Roaming" / "EldenRing" / "GraphicsConfig.xml"
    gfx.parent.mkdir(parents=True)
    gfx.write_text("x")
    game = steam.SteamGame(1245620, "ELDEN RING", tmp_path / "lib" / "ELDEN RING",
                           tmp_path / "lib", tmp_path / "Steam")
    install = steam.SteamInstall(tmp_path / "Steam")
    monkeypatch.setattr(steam, "find_steam_installs", lambda: [install])
    monkeypatch.setattr(steam, "library_folders", lambda root: [])
    monkeypatch.setattr(steam, "discover_games", lambda installs: [game])
    code, out = run("steam", "--import")
    assert "rigswap manifest update" in out  # tip, nothing downloaded yet
    run("game", "remove", "ELDEN RING")
    run.install_manifest()
    code, out = run("steam", "--import")
    assert "Found config files for 1 of 1 new game(s)" in out


def test_lock_status_restore_cli(run, tmp_path):
    cfg = tmp_path / "g.ini"
    cfg.write_text("a")
    run("setup", "add", "Dock")
    run("game", "add", "G", "--exe", "g.exe")
    run("config", "add", "G", str(cfg))
    run("save", "Dock", "G")
    run("switch", "Dock")
    assert "Every game matches" in run("status")[1]
    assert "Locked Dock" in run("setup", "lock", "dock")[1]
    assert "locked" in run("setup", "list")[1]
    cfg.write_text("changed by a game update")
    code, out = run("status")
    assert "G: 1 file(s) differ" in out and "rigswap restore" in out
    code, out = run("restore", "G")
    assert code == 0 and cfg.read_text() == "a"
    run("undo")
    assert cfg.read_text() == "changed by a game update"
    assert "Unlocked" in run("setup", "unlock", "Dock")[1]
