import io
import json
import urllib.error

import pytest

from rigswap.core import detect, manifest
from rigswap.core.models import ConfigLocation, Game, LocationKind

YAML = """
Elden Ring:
  files:
    "<winAppData>/EldenRing/<storeUserId>":
      tags: [save]
      when: [{os: windows}]
    "<winAppData>/EldenRing/GraphicsConfig.xml":
      tags: [config]
      when: [{os: windows}]
  id:
    steamExtra: [2778580]
  installDir:
    ELDEN RING: {}
  steam:
    id: 1245620
Valheim:
  files:
    "<xdgConfig>/unity3d/IronGate/Valheim":
      tags: [save]
      when: [{os: linux}]
    "<xdgConfig>/unity3d/IronGate/Valheim/prefs":
      tags: [config]
      when: [{os: linux}]
    "<root>/userdata/<storeUserId>/892970":
      tags: [config, save]
      when: [{store: steam}]
  registry:
    HKEY_CURRENT_USER/Software/IronGate/Valheim:
      tags: [config]
  steam:
    id: 892970
Mixed Game:
  files:
    "<winDocuments>/My Games/Mixed":
      tags: [config, save]
Folder Game:
  files:
    "<winLocalAppData>/FolderGame/Config":
      tags: [config]
    "<winLocalAppData>/FolderGame/Config/Engine.ini":
      tags: [config]
    "<base>/settings/*.cfg":
      tags: [config]
Save Only:
  files:
    "<home>/saves":
      tags: [save]
Old Name:
  alias: Folder Game
"""


@pytest.fixture
def man(tmp_path):
    path = tmp_path / "m.yaml"
    path.write_text(YAML)
    return manifest.Manifest(manifest.build_index(manifest.parse_yaml(path), etag='"abc"'))


def touch(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_index_keeps_only_config_games_and_aliases(man):
    assert len(man) == 4  # "Save Only" dropped, alias isn't a game
    assert man.find(steam_app_id=1245620).name == "Elden Ring"
    assert man.find(steam_app_id=2778580).name == "Elden Ring"  # steamExtra
    assert man.find(name="elden ring").name == "Elden Ring"
    assert man.find(install_dir="/x/steamapps/common/ELDEN RING").name == "Elden Ring"
    assert man.find(name="Old Name").name == "Folder Game"
    assert man.find(name="Nothing") is None


def test_windows_native(man, tmp_path):
    home = tmp_path / "home"
    gfx = touch(home / "AppData" / "Roaming" / "EldenRing" / "GraphicsConfig.xml")
    touch(home / "AppData" / "Roaming" / "EldenRing" / "7656119" / "ER0000.sl2")
    game = Game(name="ELDEN RING", steam_app_id=1245620)
    ctx = detect.context_for(game, platform="win32",
                             env={"APPDATA": str(home / "AppData" / "Roaming")}, home=home,
                             documents=home / "Documents")
    d = detect.detect(game, man.find(steam_app_id=1245620), ctx)
    assert d.found == [ConfigLocation(str(gfx), LocationKind.FILE)]
    assert d.mixed == []  # the save folder is save-only, not mixed


def test_proton_prefix_is_case_insensitive(man, tmp_path):
    pfx = tmp_path / "compatdata" / "1245620" / "pfx"
    # Real Proton prefixes don't always match the manifest's capitalisation
    gfx = touch(pfx / "drive_c" / "users" / "steamuser" / "AppData" / "Roaming" / "eldenring"
                / "GraphicsConfig.xml")
    game = Game(name="ELDEN RING", steam_app_id=1245620, wine_prefix=str(pfx),
                install_dir=str(tmp_path / "steamapps" / "common" / "ELDEN RING"))
    ctx = detect.context_for(game, platform="linux", env={}, home=tmp_path / "linuxhome")
    assert ctx.os_name == "windows" and ctx.case_insensitive
    d = detect.detect(game, man.find(steam_app_id=1245620), ctx)
    assert [loc.path for loc in d.found] == [str(gfx)]


def test_native_linux_mixed_and_registry(man, tmp_path):
    home = tmp_path / "home"
    prefs = touch(home / ".config" / "unity3d" / "IronGate" / "Valheim" / "prefs")
    lib = tmp_path / "Steam"
    userdata = lib / "userdata" / "12345" / "892970"
    touch(userdata / "remote" / "x")
    game = Game(name="Valheim", steam_app_id=892970,
                install_dir=str(lib / "steamapps" / "common" / "Valheim"))
    ctx = detect.context_for(game, platform="linux", env={}, home=home)
    d = detect.detect(game, man.find(steam_app_id=892970), ctx)
    assert d.found == [ConfigLocation(str(prefs), LocationKind.FILE)]
    assert d.mixed == [str(userdata)]  # config+save: suggested, never auto-added
    assert d.registry == ["HKEY_CURRENT_USER\\Software\\IronGate\\Valheim"]


def test_folder_wins_over_files_inside_and_globs(man, tmp_path):
    home = tmp_path / "home"
    cfg = home / "AppData" / "Local" / "FolderGame" / "Config"
    touch(cfg / "Engine.ini")
    base = tmp_path / "games" / "FolderGame"
    a, b = touch(base / "settings" / "a.cfg"), touch(base / "settings" / "b.cfg")
    game = Game(name="Folder Game", install_dir=str(base))
    ctx = detect.context_for(game, platform="win32", env={}, home=home, documents=home / "Docs")
    d = detect.detect(game, man.find(name="Folder Game"), ctx)
    assert d.found == [ConfigLocation(str(cfg), LocationKind.DIRECTORY),
                       ConfigLocation(str(a)), ConfigLocation(str(b))]
    # Already-managed paths (or paths inside a managed folder) aren't suggested again
    game.config_locations = [ConfigLocation(str(cfg), LocationKind.DIRECTORY),
                             ConfigLocation(str(a))]
    assert detect.new_locations(game, d) == [ConfigLocation(str(b))]


def test_missing_paths_are_reported(man, tmp_path):
    game = Game(name="ELDEN RING", steam_app_id=1245620)
    ctx = detect.context_for(game, platform="win32", env={}, home=tmp_path, documents=tmp_path)
    d = detect.detect(game, man.find(steam_app_id=1245620), ctx)
    assert d.found == [] and len(d.missing) == 1 and d.missing[0].endswith("GraphicsConfig.xml")


def test_paths_needing_unknown_placeholders_are_skipped(man, tmp_path):
    # Manual game without install dir: <base>/<root> paths can't be expanded
    game = Game(name="Folder Game")
    ctx = detect.context_for(game, platform="win32", env={}, home=tmp_path, documents=tmp_path)
    d = detect.detect(game, man.find(name="Folder Game"), ctx)
    assert not any("settings" in m for m in d.missing)


def test_placeholder_values_are_glob_escaped(tmp_path):
    weird = tmp_path / "Games [x64]"
    f = touch(weird / "game.cfg")
    ctx = detect.Context("windows", None, {"base": str(weird)})
    assert detect.matches(detect.expand("<base>/*.cfg", ctx), ctx) == [f]


class FakeResponse(io.BytesIO):
    def __init__(self, body, etag):
        super().__init__(body)
        self.status = 200
        self.headers = {"ETag": etag, "Content-Length": str(len(body))}


def test_update_downloads_then_uses_etag(tmp_path):
    calls = []

    def opener(req):
        calls.append(req.get_header("If-none-match"))
        if req.get_header("If-none-match") == '"v1"':
            raise urllib.error.HTTPError(req.full_url, 304, "Not Modified", {}, None)
        return FakeResponse(YAML.encode(), '"v1"')

    progress = []
    r1 = manifest.update(tmp_path, opener=opener, progress=lambda d, t: progress.append((d, t)))
    assert r1.changed and r1.games == 4 and progress[-1][0] == progress[-1][1]
    assert manifest.load(tmp_path).etag == '"v1"'
    r2 = manifest.update(tmp_path, opener=opener)
    assert not r2.changed and r2.games == 4
    assert calls == [None, '"v1"']
    assert [p.name for p in manifest.index_file(tmp_path).parent.iterdir()] == ["index.json"]


def test_update_errors_keep_old_cache(tmp_path):
    def ok(req):
        return FakeResponse(YAML.encode(), '"v1"')

    def broken(req):
        return FakeResponse(b"games: [unclosed", '"v2"')

    def offline(req):
        raise urllib.error.URLError("no network")

    manifest.update(tmp_path, opener=ok)
    for bad in (broken, offline):
        with pytest.raises(manifest.ManifestError):
            manifest.update(tmp_path, opener=bad)
        assert manifest.load(tmp_path).etag == '"v1"'


def test_load_missing_and_old_format(tmp_path):
    assert manifest.load(tmp_path) is None
    manifest.index_file(tmp_path).parent.mkdir(parents=True)
    manifest.index_file(tmp_path).write_text(json.dumps({"v": 0, "games": {}}))
    assert manifest.load(tmp_path) is None


def test_plain_wine_prefix_uses_the_linux_user_folder(man, tmp_path, monkeypatch):
    """Heroic/Lutris prefixes name the Windows user after the Linux user, not steamuser."""
    monkeypatch.setattr(detect, "_user_name", lambda: "alex")
    pfx = tmp_path / "Prefixes" / "Elden Ring"
    gfx = touch(pfx / "drive_c" / "users" / "alex" / "AppData" / "Roaming" / "EldenRing"
                / "GraphicsConfig.xml")
    (pfx / "drive_c" / "users" / "Public").mkdir()
    game = Game(name="Elden Ring", wine_prefix=str(pfx), store="epic")
    ctx = detect.context_for(game, platform="linux", env={}, home=tmp_path)
    assert ctx.values["osUserName"] == "alex" and ctx.store == "epic"
    d = detect.detect(game, man.find(name="Elden Ring"), ctx)
    assert [loc.path for loc in d.found] == [str(gfx)]
