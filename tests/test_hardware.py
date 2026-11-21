import sys

import pytest

from rigswap import hardware
from rigswap.hardware import Gpu, HardwareState, linux, windows

PCI_IDS = """\
# comment
10de  NVIDIA Corporation
\t2786  AD104 [GeForce RTX 4070]
\t\t1043 8888  some subsystem
\t1f95  TU117M [GeForce GTX 1650 Ti Mobile]
1002  Advanced Micro Devices, Inc. [AMD/ATI]
\t15bf  Phoenix1
8086  Intel Corporation
\t7d55  Meteor Lake-P [Intel Arc Graphics]
"""


linux_only = pytest.mark.skipif(sys.platform == "win32",
                                reason="fake /sys trees use ':' in names and symlinks")


def w(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n")


def make_gpu(sys_root, card, slot, vendor, device, removable=None):
    dev = sys_root / "devices" / "pci0000:00" / slot
    w(dev / "vendor", f"0x{vendor}")
    w(dev / "device", f"0x{device}")
    if removable:
        w(dev / "removable", removable)
    link = sys_root / "class" / "drm" / card
    link.mkdir(parents=True, exist_ok=True)
    (link / "device").symlink_to(dev)


@pytest.fixture
def laptop_with_egpu(tmp_path):
    root = tmp_path / "sys"
    make_gpu(root, "card0", "0000:00:02.0", "8086", "7d55", "fixed")
    make_gpu(root, "card1", "0000:2c:00.0", "10de", "2786", "removable")
    w(root / "class" / "drm" / "card0-eDP-1" / "status", "connected")
    w(root / "class" / "drm" / "card1-DP-3" / "status", "connected")
    w(root / "class" / "drm" / "card1-HDMI-A-1" / "status", "disconnected")
    w(root / "class" / "power_supply" / "ADP1" / "type", "Mains")
    w(root / "class" / "power_supply" / "ADP1" / "online", "1")
    w(root / "class" / "power_supply" / "BAT0" / "type", "Battery")
    ids = tmp_path / "pci.ids"
    ids.write_text(PCI_IDS)
    return root, ids


@linux_only
def test_linux_laptop_with_egpu(laptop_with_egpu):
    root, ids = laptop_with_egpu
    state = linux.read_state(root, ids)
    assert [(g.name, g.external, g.pci_id, g.key) for g in state.gpus] == [
        ("Intel Arc Graphics", False, "8086:7d55", "0000:00:02.0"),
        ("NVIDIA GeForce RTX 4070", True, "10de:2786", "0000:2c:00.0"),
    ]
    assert state.on_ac is True and state.external_displays == 1
    assert [g.name for g in state.external_gpus] == ["NVIDIA GeForce RTX 4070"]


@linux_only
def test_linux_on_battery_and_unknowns(tmp_path):
    root = tmp_path / "sys"
    make_gpu(root, "card0", "0000:04:00.0", "1002", "15bf")  # old kernel: no removable attr
    w(root / "class" / "power_supply" / "ADP1" / "type", "Mains")
    w(root / "class" / "power_supply" / "ADP1" / "online", "0")
    w(root / "class" / "power_supply" / "BAT0" / "type", "Battery")
    state = linux.read_state(root, tmp_path / "missing.ids")
    assert state.gpus[0].external is None
    assert state.gpus[0].name == "AMD GPU (1002:15bf)"
    assert state.on_ac is False and state.external_displays is None


@linux_only
def test_linux_desktop_or_vm_has_no_power_info(tmp_path):
    root = tmp_path / "sys"
    make_gpu(root, "card0", "0000:00:01.0", "1af4", "1050")  # virtio GPU
    w(root / "class" / "drm" / "card0-Virtual-1" / "status", "connected")
    state = linux.read_state(root, tmp_path / "none")
    assert state.on_ac is None and state.external_displays == 0
    assert state.gpus[0].vendor == "Unknown"


def test_windows_helpers():
    assert windows.parse_hardware_id(["PCI\\VEN_10DE&DEV_2786&SUBSYS_88881043&REV_A1",
                                      "PCI\\VEN_10DE&DEV_2786"]) == "10de:2786"
    assert windows.parse_hardware_id(["ROOT\\BasicDisplay"]) is None
    assert windows.external_from_policy(1) is False
    assert windows.external_from_policy(3) is True and windows.external_from_policy(2) is True
    assert windows.external_from_policy(None) is None
    # internal panel, embedded DP, then an HDMI (5) and a DisplayPort (10) monitor
    assert windows.count_external_outputs([0x80000000, 11, 5, 10]) == 2


def test_describe():
    state = HardwareState(gpus=(Gpu("RTX 4070", "NVIDIA", True),), on_ac=False,
                          external_displays=None)
    assert state.describe() == ["GPU: RTX 4070 (external)", "Power: on battery",
                                "External displays: unknown"]


def test_read_state_on_this_machine():
    """Smoke test: the real reader for this OS runs without errors."""
    state = hardware.read_state()
    assert isinstance(state, HardwareState)
    if sys.platform == "win32":
        assert state.gpus  # every Windows machine has a display adapter
