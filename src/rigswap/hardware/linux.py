"""Linux: read GPUs, power and displays from /sys (no root needed).

- GPUs: /sys/class/drm/cardN/device. A GPU behind a Thunderbolt/USB4 port is marked
  by the kernel's PCI `removable` attribute ("removable" vs "fixed", kernel 5.12+).
- Names: looked up in the system's pci.ids file when present.
- Power: /sys/class/power_supply/*, type "Mains" (or USB-C chargers), `online`.
- Displays: /sys/class/drm/cardN-<connector>/status; eDP/LVDS/DSI are built-in panels.
"""

from __future__ import annotations

from pathlib import Path

from rigswap.hardware import Gpu, HardwareState, vendor_name

PCI_IDS_PATHS = (Path("/usr/share/hwdata/pci.ids"), Path("/usr/share/misc/pci.ids"),
                 Path("/usr/share/pci.ids"))
INTERNAL_CONNECTORS = ("eDP", "LVDS", "DSI", "Virtual")


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


def read_state(sys_root: Path = Path("/sys"), pci_ids: Path | None = None) -> HardwareState:
    return HardwareState(gpus=tuple(gpus(sys_root, pci_ids)), on_ac=on_ac(sys_root),
                         external_displays=external_displays(sys_root))


def gpus(sys_root: Path = Path("/sys"), pci_ids: Path | None = None) -> list[Gpu]:
    found: dict[str, Gpu] = {}
    for card in sorted((sys_root / "class" / "drm").glob("card[0-9]*")):
        if "-" in card.name:
            continue  # a connector, not a GPU
        dev = card / "device"
        vendor = (_read(dev / "vendor") or "").lower().removeprefix("0x")
        device = (_read(dev / "device") or "").lower().removeprefix("0x")
        if not vendor:
            continue
        try:
            key = dev.resolve().name
        except OSError:
            key = card.name
        removable = _read(dev / "removable")
        external = {"removable": True, "fixed": False}.get(removable or "")
        pci_id = f"{vendor}:{device}" if device else vendor
        name = _pci_name(vendor, device, pci_ids) or f"{vendor_name(vendor)} GPU ({pci_id})"
        found.setdefault(key, Gpu(name=name, vendor=vendor_name(vendor), external=external,
                                  pci_id=pci_id, key=key))
    return list(found.values())


def on_ac(sys_root: Path = Path("/sys")) -> bool | None:
    supplies = sorted((sys_root / "class" / "power_supply").glob("*"))
    mains = [s for s in supplies if _read(s / "type") in ("Mains", "USB", "USB_C", "USB_PD")]
    has_battery = any(_read(s / "type") == "Battery" for s in supplies)
    if any(_read(s / "online") == "1" for s in mains):
        return True
    if has_battery:
        return False
    return None


def external_displays(sys_root: Path = Path("/sys")) -> int | None:
    connectors = [c for c in (sys_root / "class" / "drm").glob("card*-*")]
    if not connectors:
        return None
    count = 0
    for c in connectors:
        name = c.name.split("-", 1)[1]
        if _read(c / "status") == "connected" and not name.startswith(INTERNAL_CONNECTORS):
            count += 1
    return count


_pci_cache: dict[Path, dict[str, str]] = {}


def _pci_name(vendor: str, device: str, pci_ids: Path | None) -> str | None:
    """'NVIDIA GeForce RTX 4070' from pci.ids ('AD104 [GeForce RTX 4070]')."""
    paths = [pci_ids] if pci_ids else [p for p in PCI_IDS_PATHS if p.is_file()]
    if not paths or not device:
        return None
    table = _load_pci_ids(paths[0], vendor)
    raw = table.get(device)
    if not raw:
        return None
    if "[" in raw and raw.rstrip().endswith("]"):
        raw = raw[raw.index("[") + 1: raw.rindex("]")]
    short = vendor_name(vendor)
    return raw if short == "Unknown" or raw.startswith(short) else f"{short} {raw}"


def _load_pci_ids(path: Path, vendor: str) -> dict[str, str]:
    """Device names for one vendor (the file is ~1.4 MB; we only keep one vendor)."""
    cache_key = path / vendor
    if cache_key in _pci_cache:
        return _pci_cache[cache_key]
    table: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            in_vendor = False
            for line in f:
                if not line.strip() or line.startswith("#"):
                    continue
                if not line.startswith("\t"):
                    if in_vendor:
                        break
                    in_vendor = line[:4].lower() == vendor
                elif in_vendor and not line.startswith("\t\t"):
                    dev_id, _, name = line.strip().partition(" ")
                    table[dev_id.lower()] = name.strip()
    except OSError:
        pass
    _pci_cache[cache_key] = table
    return table
