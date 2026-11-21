"""What hardware is connected right now: GPUs (and which are external), power, displays.

Each OS has its own reader (windows.py, linux.py); everything here is read-only and
needs no administrator rights. Values a platform can't tell are None, so rules that
depend on them simply don't match.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

VENDORS = {"10de": "NVIDIA", "1002": "AMD", "1022": "AMD", "8086": "Intel"}


@dataclass(frozen=True)
class Gpu:
    name: str
    vendor: str
    # True: the OS says it can be unplugged (eGPU over Thunderbolt/USB4/OCuLink).
    # False: built in. None: the OS doesn't say.
    external: bool | None
    pci_id: str | None = None  # "10de:2786"
    key: str = ""  # stable id: Windows device instance id or Linux PCI address


@dataclass(frozen=True)
class HardwareState:
    gpus: tuple[Gpu, ...] = ()
    on_ac: bool | None = None  # None: no battery info (desktops, VMs)
    external_displays: int | None = None

    @property
    def external_gpus(self) -> tuple[Gpu, ...]:
        return tuple(g for g in self.gpus if g.external)

    def describe(self) -> list[str]:
        lines = []
        for g in self.gpus:
            where = {True: "external", False: "built in", None: "unknown"}[g.external]
            lines.append(f"GPU: {g.name} ({where})")
        if not self.gpus:
            lines.append("GPU: none found")
        lines.append("Power: " + {True: "plugged in", False: "on battery",
                                  None: "unknown"}[self.on_ac])
        lines.append("External displays: "
                     + ("unknown" if self.external_displays is None
                        else str(self.external_displays)))
        return lines


def vendor_name(vendor_id: str | None) -> str:
    return VENDORS.get((vendor_id or "").lower().removeprefix("0x"), "Unknown")


def read_state() -> HardwareState:
    """The current hardware state for this OS."""
    if sys.platform == "win32":
        from rigswap.hardware import windows

        return windows.read_state()
    if sys.platform.startswith("linux"):
        from rigswap.hardware import linux

        return linux.read_state()
    return HardwareState()
