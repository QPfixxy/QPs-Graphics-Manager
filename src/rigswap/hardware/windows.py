"""Windows: read GPUs, power and displays through Windows' own APIs (no admin needed).

- GPUs: SetupAPI lists display adapters. Each has a *removal policy*: Windows expects
  a built-in GPU never to be removed, while an eGPU (Thunderbolt, USB4, OCuLink) can
  be. That tells external from internal for any brand, without guessing from names.
- Power: GetSystemPowerStatus.
- Displays: QueryDisplayConfig gives each active display's connection type; built-in
  panels report "internal" or embedded DisplayPort.
"""

from __future__ import annotations

import re
import struct

from rigswap.hardware import Gpu, HardwareState, vendor_name

# CM_REMOVAL_POLICY_*: 1 = expect no removal (built in), 2 = orderly, 3 = surprise removal
REMOVAL_NONE = 1
# DISPLAYCONFIG_VIDEO_OUTPUT_TECHNOLOGY values that mean a built-in panel
INTERNAL_OUTPUTS = {0x80000000, 6, 11, 13}  # INTERNAL, LVDS, DISPLAYPORT_EMBEDDED, UDI_EMBEDDED
PATH_INFO_SIZE, MODE_INFO_SIZE, OUTPUT_TECH_OFFSET = 72, 64, 36


def parse_hardware_id(hardware_ids: list[str]) -> str | None:
    """'PCI\\VEN_10DE&DEV_2786&SUBSYS_...' -> '10de:2786'."""
    for hid in hardware_ids:
        m = re.search(r"VEN_([0-9A-Fa-f]{4})&DEV_([0-9A-Fa-f]{4})", hid)
        if m:
            return f"{m.group(1).lower()}:{m.group(2).lower()}"
    return None


def external_from_policy(policy: int | None) -> bool | None:
    if policy is None:
        return None
    return policy != REMOVAL_NONE


def count_external_outputs(technologies: list[int]) -> int:
    return sum(1 for t in technologies if t not in INTERNAL_OUTPUTS)


def read_state() -> HardwareState:
    return HardwareState(gpus=tuple(gpus()), on_ac=on_ac(), external_displays=external_displays())


# --- GPUs via SetupAPI ---------------------------------------------------------------


def gpus() -> list[Gpu]:
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    class SP_DEVINFO_DATA(ctypes.Structure):  # noqa: N801 - Windows name
        _fields_ = [("cbSize", wintypes.DWORD), ("ClassGuid", GUID),
                    ("DevInst", wintypes.DWORD), ("Reserved", ctypes.c_void_p)]

    setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
    get_class_devs = setupapi.SetupDiGetClassDevsW
    get_class_devs.restype = ctypes.c_void_p
    get_class_devs.argtypes = [ctypes.POINTER(GUID), wintypes.LPCWSTR, wintypes.HWND,
                               wintypes.DWORD]
    enum_info = setupapi.SetupDiEnumDeviceInfo
    enum_info.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(SP_DEVINFO_DATA)]
    enum_info.restype = wintypes.BOOL
    get_prop = setupapi.SetupDiGetDeviceRegistryPropertyW
    get_prop.argtypes = [ctypes.c_void_p, ctypes.POINTER(SP_DEVINFO_DATA), wintypes.DWORD,
                         ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p, wintypes.DWORD,
                         ctypes.POINTER(wintypes.DWORD)]
    get_prop.restype = wintypes.BOOL
    get_id = setupapi.SetupDiGetDeviceInstanceIdW
    get_id.argtypes = [ctypes.c_void_p, ctypes.POINTER(SP_DEVINFO_DATA), wintypes.LPWSTR,
                       wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    get_id.restype = wintypes.BOOL
    destroy = setupapi.SetupDiDestroyDeviceInfoList
    destroy.argtypes = [ctypes.c_void_p]

    # GUID_DEVCLASS_DISPLAY {4d36e968-e325-11ce-bfc1-08002be10318}
    display_class = GUID(0x4D36E968, 0xE325, 0x11CE,
                         (ctypes.c_ubyte * 8)(0xBF, 0xC1, 0x08, 0x00, 0x2B, 0xE1, 0x03, 0x18))
    DIGCF_PRESENT = 0x2  # noqa: N806
    SPDRP_DEVICEDESC, SPDRP_HARDWAREID, SPDRP_FRIENDLYNAME = 0x0, 0x1, 0xC  # noqa: N806
    SPDRP_REMOVAL_POLICY = 0x1F  # noqa: N806

    handle = get_class_devs(ctypes.byref(display_class), None, None, DIGCF_PRESENT)
    if handle in (None, ctypes.c_void_p(-1).value):
        return []

    def prop(info: SP_DEVINFO_DATA, which: int) -> bytes | None:
        needed = wintypes.DWORD(0)
        get_prop(handle, ctypes.byref(info), which, None, None, 0, ctypes.byref(needed))
        if not needed.value:
            return None
        buf = ctypes.create_string_buffer(needed.value)
        if not get_prop(handle, ctypes.byref(info), which, None, buf, needed.value, None):
            return None
        return buf.raw

    def text(raw: bytes | None) -> list[str]:
        if not raw:
            return []
        return [s for s in raw.decode("utf-16-le", errors="replace").split("\x00") if s]

    found = []
    try:
        index = 0
        while True:
            info = SP_DEVINFO_DATA()
            info.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
            if not enum_info(handle, index, ctypes.byref(info)):
                break
            index += 1
            name = (text(prop(info, SPDRP_FRIENDLYNAME)) or text(prop(info, SPDRP_DEVICEDESC))
                    or ["Display adapter"])[0]
            pci_id = parse_hardware_id(text(prop(info, SPDRP_HARDWAREID)))
            raw_policy = prop(info, SPDRP_REMOVAL_POLICY)
            policy = struct.unpack("<I", raw_policy[:4])[0] if raw_policy else None
            id_buf = ctypes.create_unicode_buffer(512)
            key = id_buf.value if get_id(handle, ctypes.byref(info), id_buf, 512, None) else name
            found.append(Gpu(name=name, vendor=vendor_name(pci_id.split(":")[0] if pci_id
                                                          else None),
                             external=external_from_policy(policy), pci_id=pci_id, key=key))
    finally:
        destroy(handle)
    return found


# --- power and displays ------------------------------------------------------------


def on_ac() -> bool | None:
    import ctypes

    class SYSTEM_POWER_STATUS(ctypes.Structure):  # noqa: N801 - Windows name
        _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                    ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                    ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]

    status = SYSTEM_POWER_STATUS()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
        return None
    return {0: False, 1: True}.get(status.ACLineStatus)


def external_displays() -> int | None:
    import ctypes

    user32 = ctypes.windll.user32
    QDC_ONLY_ACTIVE_PATHS, ERROR_INSUFFICIENT_BUFFER = 0x2, 122  # noqa: N806
    for _ in range(3):  # displays can change between the two calls
        n_paths, n_modes = ctypes.c_uint32(0), ctypes.c_uint32(0)
        if user32.GetDisplayConfigBufferSizes(QDC_ONLY_ACTIVE_PATHS, ctypes.byref(n_paths),
                                              ctypes.byref(n_modes)) != 0:
            return None
        paths = ctypes.create_string_buffer(PATH_INFO_SIZE * max(n_paths.value, 1))
        modes = ctypes.create_string_buffer(MODE_INFO_SIZE * max(n_modes.value, 1))
        rc = user32.QueryDisplayConfig(QDC_ONLY_ACTIVE_PATHS, ctypes.byref(n_paths), paths,
                                       ctypes.byref(n_modes), modes, None)
        if rc == ERROR_INSUFFICIENT_BUFFER:
            continue
        if rc != 0:
            return None
        techs = [struct.unpack_from("<I", paths.raw, i * PATH_INFO_SIZE + OUTPUT_TECH_OFFSET)[0]
                 for i in range(n_paths.value)]
        return count_external_outputs(techs)
    return None
