import os
import time

import pytest

from rigswap import cli
from rigswap.core import detect, learn, paths, processes
from rigswap.core.models import ConfigLocation, Game, LocationKind
from rigswap.core.registry import WineRegistry


def touch(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def bump(path, text):
    """Change a file so both its size and modification time differ."""
    path.write_text(text)
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))


@pytest.fixture
def world(tmp_path):
    home = tmp_path / "home"
    install = tmp_path / "Games" / "Hollow Knight"
    game = Game(name="Hollow Knight", install_dir=str(install))
    ctx = detect.context_for(game, platform="win32",
                             env={"APPDATA": str(home / "AppData" / "Roaming"),
                                  "LOCALAPPDATA": str(home / "AppData" / "Local")},
                             home=home, documents=home / "Documents")
    files = {
        "game_cfg": touch(home / "AppData" / "LocalLow" / "Team Cherry" / "Hollow Knight"
                          / "user.ini", "res=720"),
        "browser": touch(home / "AppData" / "Local" / "Browser" / "Cache" / "data_1", "a"),
        "log": touch(home / "AppData" / "Roaming" / "Tool" / "tool.log", "a"),
        "other_app": touch(home / "AppData" / "Roaming" / "ChatApp" / "settings.json", "{}"),
        "managed": touch(home / "Documents" / "managed.ini", "a"),
        "big": touch(install / "huge.dat", "a"),
    }
    game.config_locations = [ConfigLocation(str(files["managed"]))]
    return game, ctx, files, install


def test_finds_the_game_setting_and_ranks_noise_low(world, monkeypatch):
    game, ctx, f, install = world
    before = learn.scan(game, ctx)
    assert str(f["browser"]) not in before.files  # pruned while scanning
    assert str(f["log"]) not in before.files
    bump(f["game_cfg"], "res=1080")
    bump(f["browser"], "bb")
    bump(f["log"], "bb")
    bump(f["other_app"], '{"x": 1}')
    bump(f["managed"], "b")
    touch(install / "options.cfg", "vsync=1")  # new file in the install folder
    monkeypatch.setattr(learn, "MAX_FILE_SIZE", 100)  # configs are small
    bump(f["big"], "x" * 500)
    after = learn.scan(game, ctx)
    found = learn.suggestions(game, before, after)
    likely = [s.location.path for s in found if s.likely]
    unlikely = [s.location.path for s in found if not s.likely]
    assert sorted(likely) == sorted([str(f["game_cfg"]), str(install / "options.cfg")])
    assert unlikely == [str(f["other_app"])]
    assert all(s.location.kind == LocationKind.FILE for s in found)


def test_scan_stops_at_the_file_limit(world, monkeypatch):
    game, ctx, f, install = world
    monkeypatch.setattr(learn, "MAX_FILES", 2)
    result = learn.scan(game, ctx)
    assert result.partial and len(result.files) == 2


USER_REG = (
    "WINE REGISTRY Version 2\n\n"
    "[Software\\\\Team Cherry\\\\Hollow Knight] 1700000000\n"
    '"Screenmanager Resolution Width_h182942802"=dword:00000500\n'
    '"Odd [name]*"=dword:00000001\n'
    '"SaveSlot_h2"=dword:00000001\n\n'
    "[Software\\\\Valve\\\\Steam] 1700000001\n"
    '"RunningAppID"=dword:00000000\n\n'
    "[Software\\\\Wine\\\\Direct3D] 1700000002\n"
    '"renderer"="gl"\n\n'
    "[Software\\\\SomeTool] 1700000003\n"
    '"LastRun"=dword:00000001\n'
)


def test_registry_changes_become_narrow_suggestions(tmp_path):
    pfx = tmp_path / "pfx"
    touch(pfx / "user.reg", USER_REG)
    reg = WineRegistry(pfx)
    game = Game(name="Hollow Knight", wine_prefix=str(pfx))
    ctx = detect.context_for(game, platform="linux", env={}, home=tmp_path)
    before = learn.scan(game, ctx, reg_backend=reg, scan_roots=[])
    key = "HKEY_CURRENT_USER\\Software\\Team Cherry\\Hollow Knight"
    reg.write(key, [("", "Screenmanager Resolution Width_h182942802",
                     {"token": '"Screenmanager Resolution Width_h182942802"',
                      "wine": "dword:00000780"}),
                    ("", "Odd [name]*", {"token": '"Odd [name]*"', "wine": "dword:00000002"})])
    reg.write("HKEY_CURRENT_USER\\Software\\Valve\\Steam",
              [("", "RunningAppID", {"token": '"RunningAppID"', "wine": "dword:00000367"})])
    reg.write("HKEY_CURRENT_USER\\Software\\Wine\\Direct3D",
              [("", "renderer", {"token": '"renderer"', "wine": '"vulkan"'})])
    reg.write("HKEY_CURRENT_USER\\Software\\SomeTool",
              [("", "LastRun", {"token": '"LastRun"', "wine": "dword:00000002"})])
    after = learn.scan(game, ctx, reg_backend=reg, scan_roots=[])
    found = learn.suggestions(game, before, after)
    assert [(s.location.path, s.likely) for s in found] == [
        (key, True), ("HKEY_CURRENT_USER\\Software\\SomeTool", False)]  # Steam, Wine skipped
    loc = found[0].location
    assert loc.kind == LocationKind.REGISTRY
    # Only the changed values are included, matched exactly; the save slot isn't
    assert loc.matches("Screenmanager Resolution Width_h182942802")
    assert loc.matches("Odd [name]*") and not loc.matches("Odd n*")
    assert not loc.matches("SaveSlot_h2")


def test_name_tokens_skip_generic_words():
    g = Game(name="The Witcher 3: Wild Hunt - Game of the Year Edition",
             install_dir="/games/The Witcher 3")
    assert learn.name_tokens(g) == {"witcher", "wild", "hunt", "year"}


def test_cli_learn(monkeypatch, tmp_path, capsys, world):
    game, ctx, f, install = world
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(tmp_path / "data"))
    monkeypatch.setattr(processes, "system_processes", lambda: iter([]))
    cli.main(["game", "add", "Hollow Knight", "--install-dir", str(install)])
    scans = iter([learn.Scan(files={str(f["game_cfg"]): (1, 1), str(f["other_app"]): (1, 1)}),
                  learn.Scan(files={str(f["game_cfg"]): (2, 2), str(f["other_app"]): (2, 2)})])
    monkeypatch.setattr(learn, "scan", lambda g: next(scans))
    answers = iter(["", ""])  # press Enter after playing; accept the ticked ones
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    capsys.readouterr()
    assert cli.main(["learn", "Hollow Knight"]) == 0
    out = capsys.readouterr().out
    assert "1. [x]" in out and "2. [ ]" in out and "Added 1 config location(s)" in out
    cli.main(["game", "show", "Hollow Knight"])
    shown = capsys.readouterr().out
    assert str(f["game_cfg"]) in shown and str(f["other_app"]) not in shown


def test_cli_learn_nothing_changed(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(tmp_path / "data"))
    cli.main(["game", "add", "G"])
    monkeypatch.setattr(learn, "scan", lambda g: learn.Scan())
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    capsys.readouterr()
    assert cli.main(["learn", "G"]) == 0
    assert "Nothing changed" in capsys.readouterr().out


def test_bump_really_changes_mtime(tmp_path):
    p = touch(tmp_path / "a.ini", "1")
    m = p.stat().st_mtime_ns
    time.sleep(0.01)
    bump(p, "22")
    assert p.stat().st_mtime_ns != m


def test_user_name_in_the_path_doesnt_count_as_the_game_name(tmp_path):
    """Found on a real VM: user 'test' made every file look like 'Learn Test Game's."""
    home = tmp_path / "test"  # the home folder is named like a word in the game's name
    game = Game(name="Learn Test Game")
    ctx = detect.context_for(game, platform="linux", env={}, home=home)
    mine = touch(home / ".config" / "learn-test-game" / "settings.ini", "a")
    other = touch(home / ".local" / "share" / "nautilus" / "tags" / "meta.ini", "a")
    before = learn.scan(game, ctx)
    bump(mine, "bb")
    bump(other, "bb")
    found = learn.suggestions(game, before, learn.scan(game, ctx))
    assert [(s.location.path, s.likely) for s in found] == [(str(mine), True),
                                                           (str(other), False)]
    assert "elsewhere" in found[1].reason
