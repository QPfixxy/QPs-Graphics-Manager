"""The whole manual workflow through the CLI, the way a user would do it."""

import pytest

from qpgm import cli
from qpgm.core import paths, processes


@pytest.fixture
def run(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(tmp_path / "data"))
    monkeypatch.setattr(processes, "system_processes", lambda: iter([]))

    def _run(*argv):
        code = cli.main(list(argv))
        out = capsys.readouterr()
        return code, out.out + out.err

    return _run


def test_manual_game_workflow(run, tmp_path):
    cfg_dir = tmp_path / "Saved Games" / "MyGame"
    (cfg_dir / "saves").mkdir(parents=True)
    (cfg_dir / "video.ini").write_text("res=720p")
    (cfg_dir / "saves" / "slot1").write_text("level 3")
    single = tmp_path / "launcher.cfg"
    single.write_text("fps=30")

    assert run("setup", "add", "Handheld")[0] == 0
    assert run("setup", "add", "eGPU Dock")[0] == 0
    code, out = run("game", "add", "My Game", "--exe", "MyGame.exe")
    assert code == 0 and "Added My Game" in out
    assert run("config", "add", "my game", str(cfg_dir), "--exclude", "saves/*")[0] == 0
    assert run("config", "add", "my game", str(single))[0] == 0

    assert run("save", "Handheld", "My Game")[0] == 0
    (cfg_dir / "video.ini").write_text("res=4k")
    single.write_text("fps=120")
    assert run("save", "egpu dock", "--all")[0] == 0
    assert run("setup", "list")[1].count("1 game(s) saved") == 2

    code, out = run("switch", "Handheld")
    assert code == 0 and "1 game(s) applied" in out
    assert (cfg_dir / "video.ini").read_text() == "res=720p"
    assert single.read_text() == "fps=30"
    (cfg_dir / "saves" / "slot1").write_text("level 9")

    code, out = run("switch", "eGPU Dock")
    assert (cfg_dir / "video.ini").read_text() == "res=4k"
    assert (cfg_dir / "saves" / "slot1").read_text() == "level 9"

    code, out = run("game", "show", "My Game")
    assert "exclude saves/*" in out and "Handheld:" in out and "MyGame.exe" in out

    code, out = run("config", "remove", "My Game", "2")
    assert code == 0 and "switching skips them" in out
    code, out = run("switch", "Handheld")
    assert "Skipped My Game" in out
    assert (cfg_dir / "video.ini").read_text() == "res=4k"

    assert run("undo")[0] == 0


def test_running_game_blocks_switch(run, tmp_path, monkeypatch):
    cfg = tmp_path / "g.ini"
    cfg.write_text("a")
    run("setup", "add", "A")
    run("setup", "add", "B")
    run("game", "add", "G", "--exe", "g.exe")
    run("config", "add", "G", str(cfg))
    run("save", "A", "G")
    monkeypatch.setattr(processes, "system_processes",
                        lambda: iter([(r"C:\Games\G\G.EXE", [])]))
    code, out = run("switch", "A")
    assert code == 1 and "close these games first: G" in out
    # B has no snapshot and nothing is active yet, so switching there touches nothing
    assert run("switch", "B")[0] == 0


def test_errors_are_friendly(run, tmp_path):
    run("setup", "add", "A")
    assert run("switch", "Nope") == (1, "Error: no setup named 'Nope' (setups: A)\n")
    code, out = run("config", "add", "Ghost", str(tmp_path))
    assert code == 1 and "no game matches" in out
    run("game", "add", "G")
    code, out = run("config", "add", "G", str(tmp_path / "missing.ini"))
    assert code == 1 and "--file or --folder" in out
    code, out = run("config", "add", "G", str(tmp_path / "x.ini"), "--file", "--include", "*.ini")
    assert code == 1 and "only apply to folders" in out
    code, out = run("game", "add", "Solo")
    assert "Tip:" in out


def test_remove_needs_yes_when_configs_are_saved(run, tmp_path):
    cfg = tmp_path / "g.ini"
    cfg.write_text("a")
    run("setup", "add", "A")
    run("game", "add", "G", "--exe", "g.exe")
    run("config", "add", "G", str(cfg))
    run("save", "A", "G")
    code, out = run("game", "remove", "G")
    assert code == 1 and "--yes" in out
    assert run("game", "remove", "G", "--yes")[0] == 0
    assert run("setup", "remove", "A")[0] == 0  # nothing saved for it any more
    assert cfg.read_text() == "a"  # live file never touched
