import os
import subprocess
import sys
import time

import pytest

from rigswap.core.models import Game
from rigswap.core.processes import running_games


def game(name, install_dir):
    return Game(name=name, install_dir=str(install_dir))


def test_exe_inside_install_dir(tmp_path):
    foo = game("Foo", tmp_path / "Foo")
    foobar = game("FooBar", tmp_path / "FooBar")
    procs = [(str(tmp_path / "FooBar" / "bin" / "foobar.exe"), [])]
    assert running_games([foo, foobar], procs) == [foobar]  # no prefix false positive


def test_cmdline_path_counts(tmp_path):
    g = game("Bar", tmp_path / "Bar")
    procs = [("/usr/bin/wine64-preloader", ["wine", str(tmp_path / "Bar" / "bar.exe")])]
    assert running_games([g], procs) == [g]


@pytest.mark.skipif(os.sep != "/", reason="Wine paths only occur on Linux")
def test_wine_drive_z_path():
    g = game("Hades", "/home/me/.steam/steam/steamapps/common/Hades")
    procs = [("/opt/proton/files/bin/wine64-preloader",
              [r"Z:\home\me\.steam\steam\steamapps\common\Hades\x64\Hades.exe"])]
    assert running_games([g], procs) == [g]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows paths are case-insensitive")
def test_windows_case_insensitive():
    g = game("Elden", r"C:\Program Files (x86)\Steam\steamapps\common\ELDEN RING")
    procs = [(r"c:\program files (x86)\steam\steamapps\common\elden ring\Game\eldenring.exe", [])]
    assert running_games([g], procs) == [g]


def test_games_without_install_dir_are_ignored():
    assert running_games([Game(name="Manual")], [("/x", [])]) == []


def test_real_process_is_detected(tmp_path):
    """Start a real process whose command line points into a fake install folder."""
    install = tmp_path / "SomeGame"
    install.mkdir()
    marker = install / "game.exe"
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", str(marker)])
    try:
        g = game("SomeGame", install)
        deadline = time.time() + 5
        found = []
        while time.time() < deadline and not found:
            found = running_games([g])
        assert found == [g]
    finally:
        proc.kill()
        proc.wait()
    assert running_games([g]) == []


def test_bare_executable_name_matches_exe_and_wine_cmdline():
    g = Game(name="Elden", executables=["eldenring.exe"])
    assert running_games([g], [(r"C:\Games\ELDEN RING\Game\EldenRing.exe", [])]) == [g]
    wine = ("/usr/bin/wine64-preloader", [r"Z:\mnt\games\ELDEN RING\Game\eldenring.exe"])
    assert running_games([g], [wine]) == [g]
    assert running_games([g], [("/usr/bin/eldenring-launcher", [])]) == []


def test_full_executable_path_matches_exactly(tmp_path):
    exe = tmp_path / "Game" / "game.exe"
    g = Game(name="G", executables=[str(exe)])
    assert running_games([g], [(str(exe), [])]) == [g]
    assert running_games([g], [(str(tmp_path / "Other" / "game.exe"), [])]) == []
