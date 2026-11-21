import pytest

from rigswap import cli, hardware
from rigswap.core import auto, paths, processes, swap
from rigswap.core.models import Conditions, Game, Library, LibraryError, Power
from rigswap.hardware import Gpu, HardwareState

IGPU = Gpu("AMD Radeon 780M", "AMD", False)
EGPU = Gpu("NVIDIA GeForce RTX 4070", "NVIDIA", True)
MYSTERY = Gpu("Virtio GPU", "Unknown", None)


def hw(*gpus, ac=None, displays=None):
    return HardwareState(gpus=tuple(gpus), on_ac=ac, external_displays=displays)


@pytest.mark.parametrize("when,state,expected", [
    (Conditions(), hw(IGPU, EGPU), False),  # no conditions: never automatic
    (Conditions(external_gpu=True), hw(IGPU, EGPU), True),
    (Conditions(external_gpu=True), hw(IGPU), False),
    (Conditions(external_gpu=False), hw(IGPU), True),
    (Conditions(external_gpu=False), hw(IGPU, EGPU), False),
    (Conditions(external_gpu=False), hw(MYSTERY), False),  # can't tell: don't guess
    (Conditions(external_gpu=True), hw(MYSTERY, EGPU), True),
    (Conditions(gpu_name="rtx 4070"), hw(IGPU, EGPU), True),
    (Conditions(gpu_name="RTX 5090"), hw(IGPU, EGPU), False),
    (Conditions(power=Power.BATTERY), hw(IGPU, ac=False), True),
    (Conditions(power=Power.BATTERY), hw(IGPU, ac=True), False),
    (Conditions(power=Power.AC), hw(IGPU, ac=None), False),  # unknown power
    (Conditions(external_display=True), hw(IGPU, displays=1), True),
    (Conditions(external_display=True), hw(IGPU, displays=None), False),
    (Conditions(external_gpu=False, power=Power.BATTERY), hw(IGPU, ac=False), True),
    (Conditions(external_gpu=False, power=Power.BATTERY), hw(IGPU, ac=True), False),
])
def test_matches(when, state, expected):
    assert auto.matches(when, state) is expected


def test_choose_setup_is_top_to_bottom():
    lib = Library()
    dock = lib.add_setup("eGPU Dock")
    dock.when = Conditions(external_gpu=True)
    plugged = lib.add_setup("Plugged in")
    plugged.when = Conditions(power=Power.AC)
    assert auto.choose_setup(lib, hw(IGPU, EGPU, ac=True)) is dock
    lib.move_setup(plugged.id, 0)
    assert auto.choose_setup(lib, hw(IGPU, EGPU, ac=True)) is plugged
    assert auto.choose_setup(lib, hw(IGPU, ac=False)) is None


class Rig:
    """A library plus fake hardware and a fake 'switch' that records calls."""

    def __init__(self):
        self.lib = Library()
        self.handheld = self.lib.add_setup("Handheld")
        self.handheld.when = Conditions(external_gpu=False)
        self.dock = self.lib.add_setup("eGPU Dock")
        self.dock.when = Conditions(external_gpu=True)
        self.lib.settings.active_setup_id = self.handheld.id
        self.state = hw(IGPU)
        self.running: list[Game] = []
        self.fail_with: Exception | None = None
        self.switches = []

    def switch(self, setup):
        if self.running:
            raise swap.GameRunningError(self.running)
        if self.fail_with:
            raise self.fail_with
        self.switches.append(setup.name)
        self.lib.settings.active_setup_id = setup.id
        return swap.SwitchResult(setup, [], [], [], [], [], "op")

    def switcher(self, settle=2):
        return auto.AutoSwitcher(lambda: self.lib, self.switch, lambda: self.state,
                                 settle_ticks=settle)


def test_switches_after_hardware_settles():
    rig = Rig()
    s = rig.switcher()
    assert s.tick() is None  # already on Handheld
    rig.state = hw(IGPU, EGPU)
    assert s.tick() is None  # first sighting: wait for it to settle
    event = s.tick()
    assert isinstance(event, auto.Switched) and event.setup is rig.dock
    assert s.tick() is None and rig.switches == ["eGPU Dock"]


def test_flapping_hardware_resets_the_wait():
    rig = Rig()
    s = rig.switcher()
    rig.state = hw(IGPU, EGPU)
    s.tick()
    rig.state = hw(IGPU)  # eGPU dropped out again
    s.tick()
    rig.state = hw(IGPU, EGPU)
    assert s.tick() is None  # counting starts over
    assert isinstance(s.tick(), auto.Switched)


def test_waits_for_running_game_then_switches():
    rig = Rig()
    s = rig.switcher(settle=1)
    rig.running = [Game(name="Elden Ring")]
    rig.state = hw(IGPU, EGPU)
    event = s.tick()
    assert isinstance(event, auto.Waiting) and [g.name for g in event.games] == ["Elden Ring"]
    assert s.tick() is None  # same wait isn't reported again
    rig.running = []
    event = s.tick()
    assert isinstance(event, auto.Switched) and rig.switches == ["eGPU Dock"]


def test_failure_is_reported_once_and_retried_after_hardware_changes():
    rig = Rig()
    s = rig.switcher(settle=1)
    rig.fail_with = OSError("disk full")
    rig.state = hw(IGPU, EGPU)
    assert isinstance(s.tick(), auto.Failed)
    assert s.tick() is None  # not retried every few seconds
    rig.fail_with = None
    rig.state = hw(IGPU)
    s.tick()
    rig.state = hw(IGPU, EGPU)
    assert isinstance(s.tick(), auto.Switched)


def test_conditions_round_trip_and_validation():
    lib = Library()
    s = lib.add_setup("Dock")
    s.when = Conditions(external_gpu=True, gpu_name="RTX", power=Power.AC,
                        external_display=False)
    lib.settings.auto_switch = True
    again = Library.from_dict(lib.to_dict())
    assert again.setups[0].when == s.when and again.settings.auto_switch
    plain = Library()
    plain.add_setup("No rules")
    assert "when" not in plain.to_dict()["setups"][0]
    bad = lib.to_dict()
    bad["setups"][0]["when"]["external_gpu"] = "yes"
    with pytest.raises(LibraryError):
        Library.from_dict(bad)


# --- CLI ------------------------------------------------------------------------------


@pytest.fixture
def run(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(tmp_path / "data"))
    monkeypatch.setattr(processes, "system_processes", lambda: iter([]))

    def _run(*argv):
        code = cli.main(list(argv))
        out = capsys.readouterr()
        return code, out.out + out.err

    return _run


def test_when_hardware_and_watch_cli(run, monkeypatch, tmp_path):
    cfg = tmp_path / "g.ini"
    cfg.write_text("720p")
    run("setup", "add", "Handheld")
    run("setup", "add", "eGPU Dock")
    run("game", "add", "G", "--exe", "g.exe")
    run("config", "add", "G", str(cfg))
    run("save", "Handheld", "G")
    cfg.write_text("4k")
    run("save", "eGPU Dock", "G")
    run("switch", "Handheld")

    code, out = run("watch", "--once")
    assert code == 1 and "no setup has conditions" in out
    code, out = run("setup", "when", "eGPU Dock", "--external-gpu", "yes", "--power", "ac")
    assert "an external GPU is connected and plugged in" in out
    run("setup", "when", "Handheld", "--external-gpu", "no")
    code, out = run("setup", "move", "Handheld", "1")
    assert out.splitlines()[0].startswith("1. Handheld")

    monkeypatch.setattr(hardware, "read_state", lambda: hw(IGPU, EGPU, ac=True, displays=1))
    code, out = run("hardware")
    assert "NVIDIA GeForce RTX 4070 (external)" in out and "Matching setup: eGPU Dock" in out
    code, out = run("watch", "--once")
    assert "Switched to eGPU Dock" in out and cfg.read_text() == "4k"
    code, out = run("info")
    assert "Active setup: eGPU Dock" in out

    code, out = run("setup", "when", "eGPU Dock", "--clear")
    assert "never" in out


def test_watch_keeps_running_without_conditions(run, monkeypatch):
    """As a login service, watch must wait for conditions instead of exiting (and restarting)."""
    import time as time_mod

    run("setup", "add", "Handheld")
    monkeypatch.setattr(hardware, "read_state", lambda: hw(IGPU))
    ticks = []

    def fake_sleep(seconds):
        ticks.append(seconds)
        if len(ticks) == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(time_mod, "sleep", fake_sleep)
    code, out = run("watch")
    assert code == 0 and "nothing will switch until" in out and "Stopped." in out
    assert len(ticks) == 2
