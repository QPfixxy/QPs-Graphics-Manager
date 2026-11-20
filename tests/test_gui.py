"""Drive the real window (offscreen). Prompts are answered by replacing MainWindow's helpers."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QLabel, QPushButton  # noqa: E402

from qpgm.core.models import ConfigLocation, Game, LocationKind  # noqa: E402
from qpgm.core.store import LibraryStore  # noqa: E402
from qpgm.gui.main_window import GAME_ID_ROLE, STATUS_ROLE, MainWindow  # noqa: E402
from qpgm.gui.state import AppState, SnapState  # noqa: E402


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
