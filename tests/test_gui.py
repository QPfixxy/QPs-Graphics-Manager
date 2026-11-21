"""Drive the real window (offscreen). Prompts are answered by replacing MainWindow's helpers."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QLabel, QPushButton  # noqa: E402

from rigswap.core.models import ConfigLocation, Game, LocationKind  # noqa: E402
from rigswap.core.store import LibraryStore  # noqa: E402
from rigswap.gui.main_window import GAME_ID_ROLE, STATUS_ROLE, MainWindow  # noqa: E402
from rigswap.gui.state import AppState, SnapState  # noqa: E402


class Prompts:
    def __init__(self):
        self.answers = []  # queued answers for ask/get_text/pick_path/run_dialog
        self.messages = []

    def next(self, default=None):
        return self.answers.pop(0) if self.answers else default


@pytest.fixture
def env(qtbot, tmp_path, monkeypatch):
    running: list[str] = []
    state = AppState(store=LibraryStore(tmp_path / "data"), steam_roots=[],
                     running=lambda games: [g for g in games if g.name in running])
    window = MainWindow(state)
    qtbot.addWidget(window)
    prompts = Prompts()
    monkeypatch.setattr(window, "ask", lambda *a: prompts.next(True))
    monkeypatch.setattr(window, "get_text", lambda *a: prompts.next())
    monkeypatch.setattr(window, "pick_path", lambda *a: prompts.next())
    monkeypatch.setattr(window, "run_dialog", lambda d: prompts.next(True))
    monkeypatch.setattr(window, "inform", lambda t, m: prompts.messages.append(("info", m)))
    monkeypatch.setattr(window, "warn", lambda t, m: prompts.messages.append(("warn", m)))
    # Never touch the real start-at-login settings from tests
    autostart_calls = []
    monkeypatch.setattr(window, "autostart_enabled", lambda: True)
    monkeypatch.setattr(window, "set_autostart", lambda on: autostart_calls.append(on))

    class Env:
        pass

    e = Env()
    e.state, e.window, e.prompts, e.running, e.tmp = state, window, prompts, running, tmp_path
    return e


def grid_rows(window):
    return [window.proxy.index(r, 0) for r in range(window.proxy.rowCount())]


def make_game(env, name="Hades", content="low"):
    cfg = env.tmp / "live" / name / "settings.ini"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(content)
    game = env.state.add_game(Game(name=name, executables=[f"{name}.exe"],
                                   config_locations=[ConfigLocation(str(cfg))]))
    return game, cfg


def test_empty_state_then_add_setup_and_game(env):
    w = env.window
    assert w.stack.currentWidget() is w.empty
    env.prompts.answers = ["Handheld"]
    w.add_setup()
    assert [s.name for s in env.state.lib.setups] == ["Handheld"]

    def fill(dialog):
        dialog.name.setText("Elden Ring")
        dialog.exes.setText("eldenring.exe, start_protected_game.exe")
        return True

    w.run_dialog = fill
    w.add_game()
    assert w.stack.currentWidget() is w.grid
    game = env.state.lib.games[0]
    assert game.executables == ["eldenring.exe", "start_protected_game.exe"]
    assert w.detail.game_id == game.id  # new game is selected
    assert grid_rows(w)[0].data(STATUS_ROLE) == ("No config files yet", "none")


def test_add_folder_location_asks_for_patterns(env):
    game, _ = make_game(env)
    folder = env.tmp / "cfgdir"
    folder.mkdir()

    def patterns(dialog):
        dialog.exclude.setText("saves/*, *.sav")
        return True

    env.prompts.answers = [str(folder)]
    env.window.run_dialog = patterns
    env.window.add_location(game, LocationKind.DIRECTORY)
    loc = game.config_locations[-1]
    assert loc.kind == LocationKind.DIRECTORY and loc.exclude == ["saves/*", "*.sav"]


def test_switch_by_clicking_setup_button(env, qtbot):
    hh = env.state.add_setup("Handheld")
    dock = env.state.add_setup("eGPU Dock")
    game, cfg = make_game(env, content="720p")
    env.state.save_snapshot(game, hh)
    cfg.write_text("4k")
    env.state.save_snapshot(game, dock)
    assert grid_rows(env.window)[0].data(STATUS_ROLE) == ("Ready for all 2 setups", "ok")

    buttons = {b.text(): b for b in env.window.findChildren(QPushButton, "SetupButton")}
    buttons["Handheld"].click()
    assert cfg.read_text() == "720p"
    assert env.state.active_setup().name == "Handheld"
    assert env.window.undo_button.isEnabled()

    # The game is running: switching is refused with a clear message, nothing changes.
    env.running.append("Hades")
    buttons = {b.text(): b for b in env.window.findChildren(QPushButton, "SetupButton")}
    buttons["eGPU Dock"].click()
    assert cfg.read_text() == "720p"
    assert env.state.active_setup().name == "Handheld"
    kind, msg = env.prompts.messages[-1]
    assert kind == "warn" and "Close these games first" in msg and "Hades" in msg
    checked = [b.text() for b in env.window.findChildren(QPushButton, "SetupButton")
               if b.isChecked()]
    assert checked == ["Handheld"]

    env.running.clear()
    env.window.undo()
    assert cfg.read_text() == "4k"


def test_stale_and_unchecked_games_are_explained(env):
    hh = env.state.add_setup("Handheld")
    env.state.add_setup("eGPU Dock")
    game, cfg = make_game(env)
    env.state.save_snapshot(game, hh)
    env.state.add_location(game, ConfigLocation(str(env.tmp / "extra.ini")))
    assert env.state.snapshot_state(game, hh)[0] == SnapState.STALE
    assert grid_rows(env.window)[0].data(STATUS_ROLE)[1] == "warn"
    nameless = env.state.add_game(Game(name="Mystery",
                                       config_locations=[ConfigLocation(str(cfg) + ".2")]))
    env.state.save_snapshot(nameless, hh)  # so switching touches it
    env.window.switch(hh)
    kind, msg = env.prompts.messages[-1]
    assert kind == "info" and "Hades" in msg and "Mystery" in msg
    assert nameless.id in [r.data(GAME_ID_ROLE) for r in grid_rows(env.window)]


def test_search_filters_grid(env, qtbot):
    make_game(env, "Hades")
    make_game(env, "Hollow Knight")
    make_game(env, "Celeste")
    qtbot.keyClicks(env.window.search, "ho")
    names = [r.data() for r in grid_rows(env.window)]
    assert names == ["Hollow Knight"]
    env.window.search.clear()
    assert [r.data() for r in grid_rows(env.window)] == ["Celeste", "Hades", "Hollow Knight"]


def test_remove_game_and_setup(env):
    hh = env.state.add_setup("Handheld")
    game, cfg = make_game(env)
    env.state.save_snapshot(game, hh)
    env.prompts.answers = [False]
    env.window.remove_game(game)  # user said no
    assert env.state.lib.games
    env.window.remove_game(game)
    assert not env.state.lib.games and cfg.exists()
    env.window.remove_setup(hh)
    assert not env.state.lib.setups


def test_failed_change_reloads_from_disk(env):
    env.state.add_setup("Handheld")
    env.prompts.answers = ["handheld"]  # duplicate name
    env.window.add_setup()
    assert env.prompts.messages[-1][0] == "warn"
    assert [s.name for s in env.state.lib.setups] == ["Handheld"]


def test_detail_panel_shows_selected_game(env, qtbot):
    hh = env.state.add_setup("Handheld")
    game, _ = make_game(env)
    env.state.save_snapshot(game, hh)
    env.window.select_game(game.id)
    labels = [lbl.text() for lbl in env.window.detail.findChildren(QLabel)]
    assert "Hades" in labels
    assert any(t.startswith("Saved ") for t in labels)
    assert any("Hades.exe" in t for t in labels)
    assert env.window.detail.locations.count() == 1


# --- Phase 2: detection, locked setups, restore, startup check ------------------------

MANIFEST_YAML = """
Elden Ring:
  files:
    "<winAppData>/EldenRing/GraphicsConfig.xml":
      tags: [config]
  registry:
    HKEY_CURRENT_USER/Software/FromSoftware:
      tags: [config]
Hades:
  files:
    "<winDocuments>/Saved Games/Hades":
      tags: [config, save]
"""


@pytest.fixture
def with_db(env, monkeypatch):
    """Detection sees a Windows-style home; 'downloading' installs a small manifest."""
    import json

    from rigswap.core import detect, manifest

    home = env.tmp / "home"
    real = detect.context_for
    monkeypatch.setattr(detect, "context_for", lambda game, **kw: real(
        game, platform="win32", env={"APPDATA": str(home / "AppData" / "Roaming")},
        home=home, documents=home / "Documents"))
    yml = env.tmp / "m.yaml"
    yml.write_text(MANIFEST_YAML)
    downloads = []

    def fake_download(then):
        downloads.append(1)
        index = manifest.build_index(manifest.parse_yaml(yml))
        manifest.index_file(env.state.root).parent.mkdir(parents=True, exist_ok=True)
        manifest.index_file(env.state.root).write_text(json.dumps(index))
        env.state.manifest_downloaded()
        then()

    monkeypatch.setattr(env.window, "download_manifest", fake_download)
    env.home, env.downloads = home, downloads
    return env


def test_find_automatically_downloads_then_adds(with_db):
    e = with_db
    gfx = e.home / "AppData" / "Roaming" / "EldenRing" / "GraphicsConfig.xml"
    gfx.parent.mkdir(parents=True)
    gfx.write_text("x")
    game = e.state.add_game(Game(name="Elden Ring"))
    e.prompts.answers = [True]  # yes, download the database
    e.window.find_config(game)
    assert e.downloads == [1] and e.state.lib.settings.use_manifest is True
    assert [loc.path for loc in game.config_locations] == [str(gfx)]
    kind, msg = e.prompts.messages[-1]
    assert "Added" in msg and "registry" in msg
    e.window.find_config(game)  # second time: no download, nothing new
    assert e.downloads == [1] and "already added" in e.prompts.messages[-1][1]


def test_mixed_folder_is_only_added_when_asked(with_db):
    e = with_db
    saves = e.home / "Documents" / "Saved Games" / "Hades"
    saves.mkdir(parents=True)
    game = e.state.add_game(Game(name="Hades"))
    e.prompts.answers = [True, False]  # download yes; add the mixed folder: no
    e.window.find_config(game)
    assert game.config_locations == []

    def patterns(dialog):
        dialog.include.setText("Profile*.sav")
        return True

    e.window.run_dialog = patterns
    e.prompts.answers = [True]  # add it this time
    e.window.find_config(game)
    loc = game.config_locations[0]
    assert loc.kind == LocationKind.DIRECTORY and loc.include == ["Profile*.sav"]


def test_declining_the_database_on_import_is_remembered(with_db, monkeypatch):
    e = with_db
    from rigswap.core import steam

    games = [steam.SteamGame(1145360, "Hades", e.tmp / "lib" / "Hades", e.tmp / "lib",
                             e.tmp / "Steam")]
    monkeypatch.setattr(steam, "find_steam_installs", lambda: [steam.SteamInstall(e.tmp)])
    monkeypatch.setattr(steam, "discover_games", lambda installs=None: games)
    e.prompts.answers = [True, False]  # import the ticked games; database: no thanks
    e.window.import_games()
    assert e.state.lib.settings.use_manifest is False and e.downloads == []
    e.state.remove_game(e.state.lib.games[0])
    e.prompts.answers = []  # would default to True if asked
    e.window.import_games()
    assert e.downloads == []  # not asked again


def test_locked_setup_drift_restore_and_startup_check(env):
    hh = env.state.add_setup("Handheld")
    game, cfg = make_game(env, content="720p")
    env.state.save_snapshot(game, hh)
    env.window.switch(hh)
    env.window.set_locked(hh, True)
    assert env.state.lib.setups[0].locked
    cfg.write_text("changed by a game update")

    env.window.select_game(game.id)
    labels = [lbl.text() for lbl in env.window.detail.findChildren(QLabel)]
    assert "Changed since saved" in labels and any("locked" in t for t in labels)
    assert any(b.text() == "Restore saved" for b in env.window.detail.findChildren(QPushButton))

    env.prompts.answers = [False]
    env.window.startup_check()  # user says not now
    assert cfg.read_text() == "changed by a game update"
    env.window.startup_check()  # default answer: yes
    assert cfg.read_text() == "720p"
    env.window.undo()
    assert cfg.read_text() == "changed by a game update"
    env.window.restore(game, hh)
    assert cfg.read_text() == "720p"

    env.window.set_locked(hh, False)
    cfg.write_text("tweaked")
    called = []
    env.window.ask = lambda *a: called.append(a) or True
    env.window.startup_check()  # unlocked: drift is expected, no prompt
    assert called == []


# --- Phase 3: automatic switching -------------------------------------------------------

def test_conditions_dialog_and_auto_switching(env):
    from rigswap.core.models import Conditions
    from rigswap.gui.main_window import ConditionsDialog
    from rigswap.hardware import Gpu, HardwareState

    hh = env.state.add_setup("Handheld")
    dock = env.state.add_setup("eGPU Dock")
    game, cfg = make_game(env, content="720p")
    env.state.save_snapshot(game, hh)
    cfg.write_text("4k")
    env.state.save_snapshot(game, dock)
    env.window.switch(hh)

    igpu = Gpu("AMD Radeon 780M", "AMD", False)
    egpu = Gpu("NVIDIA GeForce RTX 4070", "NVIDIA", True)
    hw_now = {"state": HardwareState(gpus=(igpu,), on_ac=False)}
    env.window.read_hardware = lambda: hw_now["state"]

    # Auto-switch can't be turned on before any setup has conditions
    env.window.set_auto_switch(True)
    assert not env.state.lib.settings.auto_switch
    assert "Use automatically when" in env.prompts.messages[-1][1]

    # The dialog lists the GPUs it sees and previews whether the rule matches now
    dialog = ConditionsDialog(env.window, dock, HardwareState(gpus=(igpu, egpu)))
    assert [dialog.gpu.itemText(i) for i in range(dialog.gpu.count())] == \
        ["", "AMD Radeon 780M", "NVIDIA GeForce RTX 4070"]
    dialog.egpu.setCurrentIndex(1)  # Connected
    assert dialog.preview.text() == "Right now: this matches."
    dialog.egpu.setCurrentIndex(2)
    assert dialog.preview.text() == "Right now: this doesn't match."
    dialog.egpu.setCurrentIndex(0)
    assert "only used when you pick it" in dialog.preview.text()

    def set_dock_rule(d):
        d.egpu.setCurrentIndex(1)
        return True

    env.window.run_dialog = set_dock_rule
    env.prompts.answers = [True]  # turn on automatic switching now? yes
    env.window.edit_conditions(dock)
    assert dock.when == Conditions(external_gpu=True)
    assert env.state.lib.settings.auto_switch and env.window.auto_button.isChecked()
    assert env.window.auto_timer.isActive()

    # Plug in the eGPU while the game is running: wait, then switch when it closes
    env.running.append("Hades")
    hw_now["state"] = HardwareState(gpus=(igpu, egpu), on_ac=True)
    env.window.auto_tick()
    env.window.auto_tick()
    assert "when you close Hades" in env.window.statusBar().currentMessage()
    assert cfg.read_text() == "720p"
    env.running.clear()
    env.window.auto_tick()
    assert cfg.read_text() == "4k" and env.state.active_setup().name == "eGPU Dock"
    assert "Switched to eGPU Dock" in env.window.statusBar().currentMessage()

    env.window.set_auto_switch(False)
    assert not env.window.auto_timer.isActive()


def test_setup_order_can_be_changed(env):
    a = env.state.add_setup("A")
    env.state.add_setup("B")
    env.state.move_setup(a, 1)
    assert [s.name for s in env.state.lib.setups] == ["B", "A"]
    buttons = [b.text() for b in env.window.findChildren(QPushButton, "SetupButton")]
    assert buttons == ["B", "A"]


def test_import_dialog_ticks_new_suggested_games(env, monkeypatch):
    from rigswap import launchers
    from rigswap.gui.main_window import ImportDialog
    from rigswap.launchers import FoundGame

    have = FoundGame("Hades", "steam", "1145360", str(env.tmp / "Hades"), steam_app_id=1145360)
    env.state.import_found([have])
    found = [have,
             FoundGame("Control", "epic", "Calluna", str(env.tmp / "Control"),
                       wine_prefix=str(env.tmp / "pfx")),
             FoundGame("Some Tool", "gamebar", "t", str(env.tmp / "Tool"), suggested=False)]
    dialog = ImportDialog(env.window, found, lambda g: launchers.is_in_library(env.state.lib, g))
    labels = [dialog.list.item(i).text() for i in range(dialog.list.count())]
    assert "already added" in labels[0] and "Epic Games · Wine" in labels[1]
    assert [g.name for g in dialog.chosen()] == ["Control"]  # Game Bar find starts unticked

    monkeypatch.setattr(env.state, "discover", lambda: found)
    env.window.with_manifest = lambda then, asked_by_user: None
    env.window.import_games()
    assert sorted(g.name for g in env.state.lib.games) == ["Control", "Hades"]
