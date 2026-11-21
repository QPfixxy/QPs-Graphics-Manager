"""Swapping registry-stored settings inside a Wine prefix (runs on every OS)."""

import json

import pytest

from rigswap import cli
from rigswap.core import detect, manifest, paths, processes, swap
from rigswap.core.models import ConfigLocation, Game, Library, LocationKind
from rigswap.core.registry import WineRegistry

KEY = "HKEY_CURRENT_USER\\Software\\IronGate\\Valheim"
USER_REG = (
    "WINE REGISTRY Version 2\n"
    ";; All keys relative to \\\\User\\\\S-1-5-21-0-0-0-1000\n\n"
    "#arch=win64\n\n"
    "[Software\\\\IronGate\\\\Valheim] 1700000000\n"
    "#time=1da0f1e2c3b4a50\n"
    '"GraphicsQuality_h1"=dword:00000001\n'
    '"SaveProgress_h2"=dword:00000005\n\n'
    "[Software\\\\Wine] 1700000002\n"
    '"Version"="win10"\n'
)


def not_running(games):
    return []


def set_value(prefix, name, wine_text):
    WineRegistry(prefix).write(KEY, [("", name, {"token": f'"{name}"', "wine": wine_text})])


def value(prefix, name):
    v = (WineRegistry(prefix).read(KEY) or {}).get("", {}).get(name)
    return v["wine"] if v else None


@pytest.fixture
def env(tmp_path):
    prefix = tmp_path / "pfx"
    prefix.mkdir()
    (prefix / "user.reg").write_text(USER_REG)
    root = tmp_path / "data"
    lib = Library()
    hh, dock = lib.add_setup("Handheld"), lib.add_setup("eGPU Dock")
    game = lib.add_game(Game(name="Valheim", wine_prefix=str(prefix), executables=["valheim.exe"],
                             config_locations=[ConfigLocation(KEY, LocationKind.REGISTRY,
                                                              exclude=["*Save*"])]))
    swap.save_snapshot(root, lib, game, hh.id, running=not_running)
    set_value(prefix, "GraphicsQuality_h1", "dword:00000003")
    swap.save_snapshot(root, lib, game, dock.id, running=not_running)
    lib.settings.active_setup_id = dock.id
    return root, lib, game, prefix, hh, dock


def test_switch_sets_values_and_keeps_excluded_saves(env):
    root, lib, game, prefix, hh, dock = env
    set_value(prefix, "SaveProgress_h2", "dword:00000009")  # progress made while docked
    swap.switch_setup(root, lib, hh.id, running=not_running)
    assert value(prefix, "GraphicsQuality_h1") == "dword:00000001"
    assert value(prefix, "SaveProgress_h2") == "dword:00000009"  # excluded: untouched
    text = (prefix / "user.reg").read_text()
    assert '[Software\\\\Wine] 1700000002\n"Version"="win10"' in text  # other keys untouched
    swap.switch_setup(root, lib, dock.id, running=not_running)
    assert value(prefix, "GraphicsQuality_h1") == "dword:00000003"


def test_changed_values_are_reported_and_restorable(env):
    root, lib, game, prefix, hh, dock = env
    assert swap.changed_files(root, lib, game, dock.id) == []
    set_value(prefix, "GraphicsQuality_h1", "dword:00000000")
    assert swap.changed_files(root, lib, game, dock.id) == [f"{KEY}\\GraphicsQuality_h1"]
    set_value(prefix, "SaveProgress_h2", "dword:00000077")  # excluded: never reported
    assert len(swap.changed_files(root, lib, game, dock.id)) == 1
    swap.restore_game(root, lib, game, dock.id, running=not_running)
    assert value(prefix, "GraphicsQuality_h1") == "dword:00000003"
    swap.undo_last(root, lib, running=not_running)
    assert value(prefix, "GraphicsQuality_h1") == "dword:00000000"


def test_undo_deletes_values_the_switch_created(env):
    root, lib, game, prefix, hh, dock = env
    WineRegistry(prefix).write(KEY, [("", "GraphicsQuality_h1", None)])
    swap.switch_setup(root, lib, hh.id, running=not_running)
    assert value(prefix, "GraphicsQuality_h1") == "dword:00000001"
    swap.undo_last(root, lib, running=not_running)
    assert value(prefix, "GraphicsQuality_h1") is None


def test_running_wineserver_blocks_the_swap(env):
    root, lib, game, prefix, hh, dock = env
    busy = {processes._norm(str(prefix))}  # wineserver_prefixes() returns normalised paths
    assert processes.running_games([game], processes=[], wine_prefixes=busy) == [game]
    assert processes.running_games([game], processes=[], wine_prefixes=set()) == []
    with pytest.raises(swap.GameRunningError):
        swap.switch_setup(root, lib, hh.id, running=lambda games: processes.running_games(
            games, processes=[], wine_prefixes={processes._norm(str(prefix))}))
    assert value(prefix, "GraphicsQuality_h1") == "dword:00000003"


def test_snapshot_from_another_registry_counts_as_out_of_date(env):
    root, lib, game, prefix, hh, dock = env
    reg_file = paths.snapshot_dir(root, game.id, hh.id) / "loc0" / "registry.json"
    data = json.loads(reg_file.read_text())
    data["backend"] = "windows"
    reg_file.write_text(json.dumps(data))
    result = swap.switch_setup(root, lib, hh.id, running=not_running)
    assert result.stale == [game]
    assert value(prefix, "GraphicsQuality_h1") == "dword:00000003"


def test_detection_finds_registry_keys_in_the_prefix(tmp_path):
    prefix = tmp_path / "pfx"
    prefix.mkdir()
    (prefix / "user.reg").write_text(USER_REG)
    yml = tmp_path / "m.yaml"
    yml.write_text(
        "Valheim:\n  registry:\n"
        "    HKEY_CURRENT_USER/Software/IronGate/Valheim:\n      tags: [config]\n"
        "Mixed:\n  registry:\n"
        "    HKEY_CURRENT_USER/Software/IronGate/Valheim:\n      tags: [config, save]\n"
        "Later:\n  registry:\n"
        "    HKEY_CURRENT_USER/Software/Later/Game:\n      tags: [config]\n")
    man = manifest.Manifest(manifest.build_index(manifest.parse_yaml(yml)))
    for name, field, expected in [("Valheim", "found", [ConfigLocation(KEY,
                                                                       LocationKind.REGISTRY)]),
                                  ("Mixed", "mixed_registry", [KEY]),
                                  ("Later", "missing",
                                   ["HKEY_CURRENT_USER\\Software\\Later\\Game"])]:
        game = Game(name=name, wine_prefix=str(prefix))
        ctx = detect.context_for(game, platform="linux", env={}, home=tmp_path)
        d = detect.detect(game, man.find(name=name), ctx, WineRegistry(prefix))
        assert getattr(d, field) == expected, name


def test_cli_add_registry_key(monkeypatch, tmp_path, capsys):
    """A game without a Wine prefix on a computer without a Windows registry has no registry."""
    monkeypatch.setattr(detect.sys, "platform", "linux")
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(tmp_path / "data"))
    monkeypatch.setattr(processes, "system_processes", lambda: iter([]))
    prefix = tmp_path / "pfx"
    prefix.mkdir()
    (prefix / "user.reg").write_text(USER_REG)
    cli.main(["game", "add", "Valheim", "--exe", "valheim.exe"])
    capsys.readouterr()
    assert cli.main(["config", "add", "Valheim", "HKCU/Software/IronGate/Valheim",
                     "--registry"]) == 1
    assert "no registry here" in capsys.readouterr().err
    store_lib = cli.LibraryStore()
    lib = store_lib.load()
    lib.games[0].wine_prefix = str(prefix)
    store_lib.save(lib)
    assert cli.main(["config", "add", "Valheim", "HKCU/Software/IronGate/Valheim",
                     "--registry", "--exclude", "*Save*"]) == 0
    assert f"Added registry key {KEY} to Valheim." in capsys.readouterr().out
    cli.main(["game", "show", "Valheim"])
    assert f"[registry key] {KEY}  exclude *Save*" in capsys.readouterr().out
