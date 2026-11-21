import os

import pytest

from rigswap.core import detect, engines, manifest, swap
from rigswap.core.models import ConfigLocation, Game, Library, LocationKind
from rigswap.core.registry import WineRegistry


def touch(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def unreal_game(root, project="Hk_project", engine_folder=True):
    touch(root / project / "Binaries" / "Win64" / f"{project}-Win64-Shipping.exe")
    touch(root / project / "Content" / "Paks" / f"{project}-WindowsNoEditor.pak")
    if engine_folder:
        touch(root / "Engine" / "Binaries" / "ThirdParty" / "x.dll")
    return root


# --- identifying the engine -----------------------------------------------------


def test_identify(tmp_path):
    ue = unreal_game(tmp_path / "Stray")
    assert engines.identify(str(ue)) == engines.EngineInfo("Unreal Engine", ("Hk_project",))

    unity = tmp_path / "Valheim"
    touch(unity / "valheim_Data" / "app.info", "IronGate\nValheim\n")
    touch(unity / "UnityPlayer.dll")
    assert engines.identify(str(unity)) == engines.EngineInfo("Unity", company="IronGate",
                                                              product="Valheim")
    bare_unity = tmp_path / "NoInfo"
    touch(bare_unity / "UnityPlayer.dll")
    assert engines.identify(str(bare_unity)) == engines.EngineInfo("Unity")

    source = tmp_path / "Portal 2"
    touch(source / "portal2" / "gameinfo.txt")
    assert engines.identify(str(source)) == engines.EngineInfo("Source", ("portal2",))

    other = tmp_path / "Celeste"
    touch(other / "Celeste.exe")
    assert engines.identify(str(other)) is None
    assert engines.identify(None) is None and engines.identify(str(tmp_path / "gone")) is None


# --- where settings are ---------------------------------------------------------


def windows_ctx(game, home):
    return detect.context_for(game, platform="win32",
                              env={"LOCALAPPDATA": str(home / "AppData" / "Local")},
                              home=home, documents=home / "Documents")


def test_unreal_windows_and_my_games(tmp_path):
    home = tmp_path / "home"
    game = Game(name="Stray", install_dir=str(unreal_game(tmp_path / "Stray")))
    info = engines.identify(game.install_dir)
    ctx = windows_ctx(game, home)
    r = engines.locations(game, info, ctx.values, ctx.os_name)
    assert r.found == [] and r.missing[0].endswith(
        os.path.join("Hk_project", "Saved", "Config", "Windows"))
    cfg = home / "Documents" / "My Games" / "Hk_project" / "Saved" / "Config" / "WindowsNoEditor"
    touch(cfg / "GameUserSettings.ini")
    r = engines.locations(game, info, ctx.values, ctx.os_name)
    assert r.found == [ConfigLocation(str(cfg), LocationKind.DIRECTORY,
                                      include=engines.UNREAL_FILES)]


def test_unreal_in_a_proton_prefix_and_native_linux(tmp_path):
    install = unreal_game(tmp_path / "steamapps" / "common" / "Stray")
    pfx = tmp_path / "compatdata" / "1332010" / "pfx"
    cfg = touch(pfx / "drive_c" / "users" / "steamuser" / "AppData" / "Local" / "Hk_project"
                / "Saved" / "Config" / "Windows" / "GameUserSettings.ini").parent
    proton = Game(name="Stray", install_dir=str(install), wine_prefix=str(pfx), store="steam")
    d = detect.detect_game(proton, None, detect.context_for(proton, platform="linux", env={},
                                                            home=tmp_path / "home"))
    assert d.engine == "Unreal Engine" and [loc.path for loc in d.found] == [str(cfg)]

    native = Game(name="Stray", install_dir=str(install))
    home = tmp_path / "home"
    linux_cfg = touch(home / ".config" / "Epic" / "Hk_project" / "Saved" / "Config" / "Linux"
                      / "GameUserSettings.ini").parent
    d = detect.detect_game(native, None, detect.context_for(native, platform="linux", env={},
                                                            home=home))
    assert [loc.path for loc in d.found] == [str(linux_cfg)]


UNITY_REG = (
    "WINE REGISTRY Version 2\n\n"
    "[Software\\\\IronGate\\\\Valheim] 1700000000\n"
    '"Screenmanager Resolution Width_h182942802"=dword:00000780\n'
    '"UnityGraphicsQuality_h1669003810"=dword:00000003\n'
    '"SaveSlot_h2"=dword:00000001\n'
)


def test_unity_registry_in_a_prefix_takes_only_display_values(tmp_path):
    install = tmp_path / "Valheim"
    touch(install / "valheim_Data" / "app.info", "IronGate\nValheim\n")
    pfx = tmp_path / "pfx"
    touch(pfx / "user.reg", UNITY_REG)
    game = Game(name="Valheim", install_dir=str(install), wine_prefix=str(pfx), store="steam")
    ctx = detect.context_for(game, platform="linux", env={}, home=tmp_path)
    info = engines.identify(game.install_dir)
    r = engines.locations(game, info, ctx.values, ctx.os_name, WineRegistry(pfx))
    key = "HKEY_CURRENT_USER\\Software\\IronGate\\Valheim"
    assert r.found == [ConfigLocation(key, LocationKind.REGISTRY,
                                      include=engines.UNITY_DISPLAY_VALUES)]
    # A snapshot of it holds the display values but not the save slot
    lib = Library()
    s = lib.add_setup("A")
    game.config_locations = r.found
    lib.add_game(game)
    root = tmp_path / "data"
    swap.save_snapshot(root, lib, game, s.id, running=lambda g: [])
    snap = (root / "snapshots" / game.id / s.id / "loc0" / "registry.json").read_text()
    assert "UnityGraphicsQuality" in snap and "Screenmanager" in snap and "SaveSlot" not in snap


def test_unity_native_linux_prefs_is_only_a_note(tmp_path):
    install = tmp_path / "Valheim"
    touch(install / "valheim_Data" / "app.info", "IronGate\nValheim\n")
    home = tmp_path / "home"
    touch(home / ".config" / "unity3d" / "IronGate" / "Valheim" / "prefs")
    game = Game(name="Valheim", install_dir=str(install))
    d = detect.detect_game(game, None, detect.context_for(game, platform="linux", env={},
                                                          home=home))
    assert d.found == [] and "settings and saves together" in d.notes[0]


def test_source_video_txt(tmp_path):
    install = tmp_path / "Portal 2"
    touch(install / "portal2" / "gameinfo.txt")
    video = touch(install / "portal2" / "cfg" / "video.txt")
    game = Game(name="Portal 2", install_dir=str(install))
    d = detect.detect_game(game, None, detect.context_for(game, platform="linux", env={},
                                                          home=tmp_path))
    assert d.found == [ConfigLocation(str(video))] and detect.sources(d) == "Source rules"


def test_database_and_engine_results_merge_without_duplicates(tmp_path):
    home = tmp_path / "home"
    install = unreal_game(tmp_path / "Stray")
    cfg = touch(home / "AppData" / "Local" / "Hk_project" / "Saved" / "Config" / "Windows"
                / "GameUserSettings.ini").parent
    yml = tmp_path / "m.yaml"
    yml.write_text('Stray:\n  files:\n    "<winLocalAppData>/Hk_project/Saved/Config/Windows":\n'
                   "      tags: [config]\n")
    man = manifest.Manifest(manifest.build_index(manifest.parse_yaml(yml)))
    game = Game(name="Stray", install_dir=str(install))
    d = detect.detect_game(game, man, windows_ctx(game, home))
    assert [loc.path for loc in d.found] == [str(cfg)]
    assert detect.sources(d) == "game database: Stray, Unreal Engine rules"


@pytest.mark.parametrize("name", ["GameUserSettings.ini", "Engine.ini", "Scalability.ini"])
def test_unreal_patterns_take_graphics_files_only(name):
    loc = ConfigLocation("/x", LocationKind.DIRECTORY, include=engines.UNREAL_FILES)
    assert loc.matches(name) and not loc.matches("Input.ini") and not loc.matches("Game.ini")
