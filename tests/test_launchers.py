import json
import sqlite3
from types import SimpleNamespace

import pytest

from rigswap import cli, launchers
from rigswap.core import paths, processes
from rigswap.core.models import Game, Library
from rigswap.launchers import FoundGame, linux, windows


def w(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data))
    return path


# --- Windows ------------------------------------------------------------------------


def test_epic_manifests(tmp_path):
    games = tmp_path / "Games"
    for d in ("Fortnite", "Control", "Half"):
        (games / d).mkdir(parents=True)
    m = tmp_path / "Manifests"
    w(m / "a.item", {"AppName": "Fortnite", "DisplayName": "Fortnite", "AppCategories": ["games"],
                     "InstallLocation": str(games / "Fortnite"),
                     "LaunchExecutable": "FortniteGame/Binaries/Win64/FortniteLauncher.exe"})
    w(m / "b.item", {"AppName": "Calluna", "DisplayName": "Control", "MainGameAppName": "Calluna",
                     "InstallLocation": str(games / "Control")})
    w(m / "dlc.item", {"AppName": "dlc1", "MainGameAppName": "Calluna", "DisplayName": "DLC",
                       "InstallLocation": str(games / "Control")})
    w(m / "partial.item", {"AppName": "x", "DisplayName": "Half", "bIsIncompleteInstall": True,
                           "InstallLocation": str(games / "Half")})
    w(m / "tool.item", {"AppName": "ue", "DisplayName": "Unreal Engine",
                        "AppCategories": ["engines"], "InstallLocation": str(games / "Half")})
    w(m / "broken.item", "{ not json")
    found = windows.epic(m)
    assert [(g.name, g.store_id) for g in found] == [("Fortnite", "Fortnite"),
                                                    ("Control", "Calluna")]
    assert found[0].executables[0].endswith("FortniteLauncher.exe")


class FakeReg:
    """Just enough of winreg: nested dict {hive: {path: {subkey: {value: data}}}}."""

    HKEY_LOCAL_MACHINE, HKEY_CURRENT_USER = "HKLM", "HKCU"

    def __init__(self, tree):
        self.tree = tree

    def OpenKey(self, parent, path):  # noqa: N802 - winreg's name
        if isinstance(parent, str):
            if path not in self.tree.get(parent, {}):
                raise OSError
            return SimpleNamespace(children=self.tree[parent][path], values={},
                                   __enter__=None)
        return Key(values=parent.children[path])

    def EnumKey(self, key, i):  # noqa: N802
        names = list(key.children)
        if i >= len(names):
            raise OSError
        return names[i]

    def EnumValue(self, key, i):  # noqa: N802
        items = list(key.values.items())
        if i >= len(items):
            raise OSError
        return items[i][0], items[i][1], 1


class Key(SimpleNamespace):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_reg(tree):
    reg = FakeReg(tree)
    real_open = reg.OpenKey

    def open_key(parent, path):
        k = real_open(parent, path)
        return Key(**vars(k)) if not isinstance(k, Key) else k

    reg.OpenKey = open_key
    return reg


def test_gog_registry(tmp_path):
    witcher = tmp_path / "GOG Games" / "The Witcher 3"
    witcher.mkdir(parents=True)
    reg = fake_reg({"HKLM": {windows.GOG_KEYS[0]: {
        "1207664663": {"gameName": "The Witcher 3", "path": str(witcher), "gameID": "1207664663",
                       "exe": str(witcher / "bin" / "x64" / "witcher3.exe")},
        "1207664664": {"gameName": "Hearts of Stone", "path": str(witcher), "gameID": "1207664664",
                       "dependsOn": "1207664663"},
        "1": {"gameName": "Uninstalled", "path": str(tmp_path / "gone")},
    }}})
    found = windows.gog(reg)
    assert [(g.name, g.store_id) for g in found] == [("The Witcher 3", "1207664663")]
    assert found[0].executables == (str(witcher / "bin" / "x64" / "witcher3.exe"),)


def test_gamebar(tmp_path, monkeypatch):
    exe = tmp_path / "Games" / "Hollow Knight" / "hollow_knight.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    ue = tmp_path / "Games" / "Stray" / "Hk_project" / "Binaries" / "Win64" / "Stray-Win64.exe"
    ue.parent.mkdir(parents=True)
    ue.write_text("")
    reg = fake_reg({"HKCU": {windows.GAMEBAR_KEY: {
        "a": {"MatchedExeFullPath": str(exe)},
        "b": {"MatchedExeFullPath": str(ue)},
        "c": {"MatchedExeFullPath": str(tmp_path / "missing.exe")},
    }}})
    names = {str(exe): "Hollow Knight", str(ue): "UnrealGame"}
    found = windows.gamebar(reg, product_name=lambda p: names.get(p))
    assert [(g.name, g.suggested) for g in found] == [("Hollow Knight", False), ("Stray", False)]


@pytest.mark.parametrize("exe,root", [
    (r"C:\Games\Stray\Hk_project\Binaries\Win64\Stray-Win64-Shipping.exe", r"C:\Games\Stray"),
    (r"D:\SteamLibrary\common\Hades\x64\Hades.exe", r"D:\SteamLibrary\common\Hades"),
    (r"C:\GOG Games\Witcher 3\bin\x64\witcher3.exe", r"C:\GOG Games\Witcher 3"),
    (r"C:\Games\Celeste\Celeste.exe", r"C:\Games\Celeste"),
])
def test_guess_game_root(exe, root):
    assert windows.guess_game_root(exe) == root


# --- Linux --------------------------------------------------------------------------


def test_heroic_gog_epic_amazon_with_prefixes(tmp_path):
    home = tmp_path / "home"
    root = home / ".config" / "heroic"
    gog_dir, epic_dir, amz_dir = (home / "Games" / "Heroic" / n for n in ("Witcher", "Control",
                                                                         "Amz"))
    native = home / "Games" / "Heroic" / "Celeste"
    for d in (gog_dir, epic_dir, amz_dir, native):
        d.mkdir(parents=True)
    pfx = home / "Games" / "Heroic" / "Prefixes" / "Control"
    (pfx / "drive_c").mkdir(parents=True)
    w(root / "gog_store" / "installed.json", {"installed": [
        {"appName": "1207664663", "install_path": str(gog_dir), "platform": "windows",
         "executable": "bin/x64/witcher3.exe"},
        {"appName": "1", "install_path": str(native), "platform": "linux"},
        {"appName": "dlc", "install_path": str(gog_dir), "platform": "windows", "is_dlc": True},
    ]})
    w(root / "store_cache" / "gog_library.json",
      {"games": [{"app_name": "1207664663", "title": "The Witcher 3"},
                 {"app_name": "1", "title": "Celeste"}]})
    w(root / "legendaryConfig" / "legendary" / "installed.json", {
        "Calluna": {"title": "Control", "install_path": str(epic_dir), "platform": "Windows",
                    "executable": "Control.exe"}})
    w(root / "GamesConfig" / "Calluna.json", {"Calluna": {"winePrefix": "~/Games/Heroic/"
                                                                        "Prefixes/Control"}})
    w(root / "nile_config" / "nile" / "installed.json", [{"id": "amzn1", "path": str(amz_dir)}])
    w(root / "nile_config" / "nile" / "library.json",
      [{"product": {"id": "amzn1", "title": "Amazon Game"}}])
    found = linux.heroic(home=home, env={})
    by = {g.name: g for g in found}
    assert set(by) == {"The Witcher 3", "Celeste", "Control", "Amazon Game"}
    assert by["Control"].wine_prefix == str(pfx) and by["Control"].store == "epic"
    assert by["Celeste"].wine_prefix is None  # native Linux build
    assert by["The Witcher 3"].executables == (str(gog_dir / "bin/x64/witcher3.exe"),)


def test_heroic_flatpak_location(tmp_path):
    home = tmp_path / "home"
    root = home / ".var" / "app" / "com.heroicgameslauncher.hgl" / "config" / "heroic"
    game = home / "Games" / "X"
    game.mkdir(parents=True)
    w(root / "legendaryConfig" / "legendary" / "installed.json",
      {"x": {"title": "X", "install_path": str(game), "platform": "Windows"}})
    assert [g.name for g in linux.heroic(home=home, env={})] == ["X"]


def test_lutris(tmp_path):
    home = tmp_path / "home"
    data = home / ".local" / "share" / "lutris"
    data.mkdir(parents=True)
    game_dir = home / "Games" / "diablo-iv"
    pfx = game_dir / "prefix"
    (pfx / "drive_c").mkdir(parents=True)
    con = sqlite3.connect(data / "pga.db")
    con.execute("CREATE TABLE games (id INTEGER, name TEXT, slug TEXT, runner TEXT, "
                "directory TEXT, installed INTEGER, configpath TEXT)")
    con.executemany("INSERT INTO games VALUES (?,?,?,?,?,?,?)", [
        (1, "Diablo IV", "diablo-iv", "wine", str(game_dir), 1, "diablo-iv-123"),
        (2, "Not installed", "gone", "wine", str(home / "nope"), 0, None),
        (3, "Steam game", "s", "steam", str(game_dir), 1, None),
    ])
    con.commit()
    con.close()
    w(home / ".config" / "lutris" / "games" / "diablo-iv-123.yml",
      "game:\n  exe: drive_c/Program Files (x86)/Diablo IV/Diablo IV Launcher.exe\n"
      f"  prefix: {pfx}\nwine:\n  version: lutris-GE\n")
    found = linux.lutris(home=home, env={})
    assert [(g.name, g.store, g.store_id, g.wine_prefix) for g in found] == [
        ("Diablo IV", "lutris", "diablo-iv", str(pfx))]
    assert found[0].executables[0].endswith("Diablo IV Launcher.exe")


# --- merging into the library -----------------------------------------------------


def test_dedupe_drops_gamebar_finds_inside_known_games(tmp_path):
    epic = FoundGame("Control", "epic", "Calluna", str(tmp_path / "Control"))
    dup = FoundGame("Control", "gamebar", "x", str(tmp_path / "Control"),
                    executables=(str(tmp_path / "Control" / "Control_DX12.exe"),),
                    suggested=False)
    other = FoundGame("Celeste", "gamebar", "y", str(tmp_path / "Celeste"), suggested=False)
    assert launchers.dedupe([epic, dup, other, epic]) == [other, epic]


def test_import_matches_store_ids_and_leaves_manual_games_alone(tmp_path):
    lib = Library()
    manual = lib.add_game(Game(name="My Control", install_dir=str(tmp_path / "Control")))
    old = lib.add_game(Game(name="Witcher (mine)", store="gog", store_id="1207664663",
                            install_dir=str(tmp_path / "old")))
    found = [FoundGame("Control", "epic", "Calluna", str(tmp_path / "Control")),
             FoundGame("The Witcher 3", "gog", "1207664663", str(tmp_path / "new"),
                       executables=("w.exe",)),
             FoundGame("Celeste", "gog", "1", str(tmp_path / "Celeste"))]
    result = launchers.import_games(lib, found)
    assert [g.name for g in result.added] == ["Celeste"]
    assert result.updated == [old] and old.name == "Witcher (mine)"
    assert old.install_dir == str(tmp_path / "new") and old.executables == ["w.exe"]
    assert manual.store is None  # same folder, but added by hand: untouched
    assert launchers.import_games(lib, found).added == []


def test_import_cli(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(tmp_path / "data"))
    monkeypatch.setattr(processes, "system_processes", lambda: iter([]))
    found = [FoundGame("Control", "epic", "Calluna", str(tmp_path / "Control")),
             FoundGame("Some Tool", "gamebar", "t", str(tmp_path / "Tool"), suggested=False)]
    monkeypatch.setattr(launchers, "discover", lambda: found)
    assert cli.main(["import", "--list"]) == 0
    out = capsys.readouterr().out
    assert "+ Control  (Epic Games)" in out and "  Some Tool  (found by Windows)" in out
    assert cli.main(["import"]) == 0
    out = capsys.readouterr().out
    assert "Imported: 1 new game(s)" in out and "--include-found-by-windows" in out
    assert cli.main(["import", "--include-found-by-windows"]) == 0
    assert "Imported: 1 new game(s)" in capsys.readouterr().out
    cli.main(["game", "list"])
    out = capsys.readouterr().out
    assert "Control" in out and "Some Tool" in out
