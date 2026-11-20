from pathlib import Path

from rigswap import cli
from rigswap.core import paths
from rigswap.gui import icon_path


def test_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(tmp_path))
    assert paths.data_dir() == tmp_path


def test_default_is_per_user_folder(monkeypatch):
    monkeypatch.delenv(paths.DATA_DIR_ENV, raising=False)
    d = paths.data_dir()
    assert d.name == paths.APP_DIR_NAME
    assert d.is_absolute()


def test_snapshot_dir_layout(tmp_path):
    assert paths.snapshot_dir(tmp_path, "g1", "s1") == tmp_path / "snapshots" / "g1" / "s1"


def test_cli_info(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(tmp_path))
    assert cli.main(["info"]) == 0
    out = capsys.readouterr().out
    assert str(tmp_path) in out and "Setups: 0" in out


def test_icons_are_packaged():
    assert Path(icon_path("app.png")).is_file()
