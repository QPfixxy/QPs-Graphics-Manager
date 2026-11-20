import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from rigswap import cli
from rigswap.core import paths, steam
from rigswap.core.models import Game, Library
from rigswap.core.steam import SteamInstall


def _q(s: str) -> str:
    return s.replace("\\", "\\\\")


def write_manifest(library: Path, app_id: int, name: str, installdir: str | None = None,
                   state_flags: int | None = 4, make_dir: bool = True, prefix: bool = False):
    installdir = installdir or name
    steamapps = library / "steamapps"
    steamapps.mkdir(parents=True, exist_ok=True)
    flags = f'"StateFlags" "{state_flags}"' if state_flags is not None else ""
    (steamapps / f"appmanifest_{app_id}.acf").write_text(
        f'"AppState"\n{{\n "appid" "{app_id}"\n "name" "{name}"\n'
        f' "installdir" "{installdir}"\n {flags}\n}}\n',
        encoding="utf-8",
    )
    if make_dir:
        (steamapps / "common" / installdir).mkdir(parents=True, exist_ok=True)
    if prefix:
        (steamapps / "compatdata" / str(app_id) / "pfx" / "drive_c").mkdir(parents=True)


def write_libraryfolders(root: Path, libraries: list[Path], old_format: bool = False):
    steamapps = root / "steamapps"
    steamapps.mkdir(parents=True, exist_ok=True)
    if old_format:
        body = "".join(f' "{i + 1}" "{_q(str(p))}"\n' for i, p in enumerate(libraries))
        text = f'"LibraryFolders"\n{{\n "TimeNextStatsReport" "123"\n{body}}}\n'
    else:
        body = "".join(
            f' "{i}"\n {{\n  "path" "{_q(str(p))}"\n  "apps" {{ }}\n }}\n'
            for i, p in enumerate([root, *libraries])
        )
        text = f'"libraryfolders"\n{{\n{body}}}\n'
    (steamapps / "libraryfolders.vdf").write_text(text, encoding="utf-8")


@pytest.fixture
def steam_root(tmp_path) -> Path:
    root = tmp_path / "Steam"
    second = tmp_path / "SSD2" / "SteamLibrary"
    missing = tmp_path / "unplugged-drive" / "SteamLibrary"
    write_libraryfolders(root, [second, missing])
    write_manifest(root, 1091500, "Cyberpunk 2077", prefix=True)
    write_manifest(root, 1245620, "ELDEN RING")
    write_manifest(root, 1493710, "Proton Experimental")  # tool, by id
    write_manifest(root, 2348590, "Proton 8.0")  # tool, by name
    write_manifest(root, 1628350, "Steam Linux Runtime 3.0 (sniper)")
    write_manifest(root, 228980, "Steamworks Common Redistributables")
    write_manifest(root, 555, "Half Downloaded", state_flags=1026)
    write_manifest(root, 556, "Dir Deleted", make_dir=False)
    (root / "steamapps" / "appmanifest_999.acf").write_text("garbage {", encoding="utf-8")
    write_manifest(second, 1174180, "Red Dead Redemption 2", prefix=True)
    write_manifest(second, 620, "Portal 2", state_flags=None)
    return root


def test_library_folders_skips_missing_and_duplicates(steam_root, tmp_path):
    libs = steam.library_folders(steam_root)
    assert libs == [steam_root, tmp_path / "SSD2" / "SteamLibrary"]


def test_old_libraryfolders_format(tmp_path):
    root = tmp_path / "Steam"
    other = tmp_path / "D" / "SteamLibrary"
    (other / "steamapps").mkdir(parents=True)
    write_libraryfolders(root, [other], old_format=True)
    assert steam.library_folders(root) == [root, other]


def test_libraryfolders_in_config_dir(tmp_path):
    root = tmp_path / "Steam"
    other = tmp_path / "Lib"
    (other / "steamapps").mkdir(parents=True)
    (root / "steamapps").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "config" / "libraryfolders.vdf").write_text(
        f'"libraryfolders" {{ "1" {{ "path" "{_q(str(other))}" }} }}', encoding="utf-8"
    )
    assert steam.library_folders(root) == [root, other]


def test_discover_games(steam_root, tmp_path):
    games = steam.discover_games([SteamInstall(steam_root)])
    assert [(g.app_id, g.name) for g in games] == [
        (1091500, "Cyberpunk 2077"),
        (1245620, "ELDEN RING"),
        (620, "Portal 2"),
        (1174180, "Red Dead Redemption 2"),
    ]
    by_id = {g.app_id: g for g in games}
    rdr2 = by_id[1174180]
    assert rdr2.library == tmp_path / "SSD2" / "SteamLibrary"
    assert rdr2.install_dir == rdr2.library / "steamapps" / "common" / "Red Dead Redemption 2"
    assert rdr2.proton_prefix == rdr2.library / "steamapps" / "compatdata" / "1174180" / "pfx"
    assert rdr2.uses_proton
    assert not by_id[1245620].uses_proton


def test_first_install_wins_for_duplicate_app(tmp_path):
    native, flat = tmp_path / "native", tmp_path / "flatpak"
    write_manifest(native, 620, "Portal 2")
    write_manifest(flat, 620, "Portal 2")
    write_manifest(flat, 400, "Portal")
    games = steam.discover_games([SteamInstall(native), SteamInstall(flat, flatpak=True)])
    assert [(g.app_id, g.flatpak) for g in games] == [(400, True), (620, False)]


def test_linux_candidates_include_flatpak_and_xdg(tmp_path):
    cands = steam.candidate_roots(home=tmp_path, platform="linux",
                                  env={"XDG_DATA_HOME": str(tmp_path / "xdg")})
    roots = [c.root for c in cands]
    assert roots[0] == tmp_path / "xdg" / "Steam"
    flatpak = [c.root for c in cands if c.flatpak]
    assert tmp_path / ".var/app/com.valvesoftware.Steam/.local/share/Steam" in flatpak


def test_find_installs_dedupes_symlinks(tmp_path):
    real = tmp_path / ".local" / "share" / "Steam"
    (real / "steamapps").mkdir(parents=True)
    link = tmp_path / ".steam" / "steam"
    link.parent.mkdir()
    try:
        os.symlink(real, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted here")
    cands = steam.candidate_roots(home=tmp_path, platform="linux", env={})
    assert steam.find_steam_installs(cands) == [SteamInstall(real)]


def test_windows_candidates_from_registry(tmp_path):
    class FakeKey:
        def __init__(self, value):
            self.value = value

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    values = {("HKCU", r"Software\Valve\Steam"): "c:/program files (x86)/steam"}

    def open_key(hive, subkey):
        if (hive, subkey) not in values:
            raise OSError
        return FakeKey(values[(hive, subkey)])

    fake = SimpleNamespace(
        HKEY_CURRENT_USER="HKCU", HKEY_LOCAL_MACHINE="HKLM",
        OpenKey=open_key, QueryValueEx=lambda key, name: (key.value, 1),
    )
    cands = steam.candidate_roots(platform="win32", env={"ProgramFiles(x86)": r"D:\PF86"},
                                  winreg=fake)
    assert [c.root for c in cands] == [Path("c:/program files (x86)/steam"),
                                       Path(r"D:\PF86") / "Steam"]


def test_import_adds_new_and_updates_moved(steam_root, tmp_path):
    lib = Library()
    existing = lib.add_game(Game(name="My Cyberpunk", steam_app_id=1091500,
                                 install_dir="/old/place"))
    games = steam.discover_games([SteamInstall(steam_root)])
    result = steam.import_games(lib, games)
    assert result.updated == [existing]
    assert existing.name == "My Cyberpunk"  # user's name kept
    assert existing.install_dir == str(games[0].install_dir)
    assert existing.proton_prefix == str(games[0].proton_prefix)
    assert sorted(g.steam_app_id for g in result.added) == [620, 1174180, 1245620]
    again = steam.import_games(lib, games)
    assert again.added == [] and again.updated == []


def test_cli_steam_import(monkeypatch, tmp_path, steam_root, capsys):
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(tmp_path / "data"))
    monkeypatch.setattr(steam, "find_steam_installs", lambda: [SteamInstall(steam_root)])
    assert cli.main(["steam", "--import"]) == 0
    out = capsys.readouterr().out
    assert "Cyberpunk 2077 [Proton]" in out and "4 game(s)" in out
    assert "Imported: 4 new, 0 updated" in out
    assert cli.main(["info"]) == 0
    assert "Games: 4" in capsys.readouterr().out
