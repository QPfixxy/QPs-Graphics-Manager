import sys

import pytest

from rigswap.core import registry
from rigswap.core.registry import RegistryError, WineRegistry

USER_REG = r'''WINE REGISTRY Version 2
;; All keys relative to \\User\\S-1-5-21-0-0-0-1000

#arch=win64

[Software\\IronGate\\Valheim] 1700000000
#time=1da0f1e2c3b4a50
"Screenmanager Resolution Width_h182942802"=dword:00000780
"GraphicsQuality_h1234"=dword:00000003
"PlayerName_h99"=hex:41,6c,65,78,00
@="default value"

[Software\\IronGate\\Valheim\\Sub] 1700000001
#time=1da0f1e2c3b4a51
"Long_h1"=hex:00,01,02,03,04,05,06,07,08,09,0a,0b,0c,0d,0e,0f,10,11,12,13,14,15,\
  16,17,18,19
"Quote \"x\""="a\\b"

[Software\\Wine] 1700000002
#time=1da0f1e2c3b4a52
"Version"="win10"
'''

KEY = "HKEY_CURRENT_USER\\Software\\IronGate\\Valheim"


@pytest.fixture
def prefix(tmp_path):
    (tmp_path / "user.reg").write_text(USER_REG)
    return tmp_path


def test_parse_key_forms():
    assert registry.parse_key("HKCU/Software/IronGate/Valheim") == \
        ("HKEY_CURRENT_USER", "Software\\IronGate\\Valheim")
    assert registry.normalize_key("hklm\\SOFTWARE\\X") == "HKEY_LOCAL_MACHINE\\SOFTWARE\\X"
    for bad in ("HKEY_CLASSES_ROOT\\x", "Software\\X", "HKCU"):
        with pytest.raises(RegistryError):
            registry.parse_key(bad)


def test_wine_read(prefix):
    reg = WineRegistry(prefix)
    assert reg.exists(KEY) and reg.exists("HKCU/software/irongate/valheim")  # any case
    assert not reg.exists("HKCU\\Software\\Nothing")
    tree = reg.read(KEY)
    assert set(tree) == {"", "Sub"}
    assert tree[""]["GraphicsQuality_h1234"]["wine"] == "dword:00000003"
    assert tree[""][""]["wine"] == '"default value"'
    assert tree["Sub"]["Long_h1"]["wine"].endswith("16,17,18,19")
    assert tree["Sub"]['Quote "x"']["wine"] == '"a\\\\b"'
    assert reg.read("HKCU\\Software\\Nothing") is None


def test_wine_write_touches_only_changed_lines(prefix):
    reg = WineRegistry(prefix)
    tree = reg.read(KEY)
    quality = dict(tree[""]["GraphicsQuality_h1234"], wine="dword:00000000")
    reg.write(KEY, [("", "GraphicsQuality_h1234", quality),
                    ("", "PlayerName_h99", None),  # delete
                    ("Sub", "New_h2", {"token": '"New_h2"', "wine": "dword:00000001"}),
                    ("Brand\\New", "X", {"token": '"X"', "wine": '"y"'})])
    text = (prefix / "user.reg").read_text()
    assert '"GraphicsQuality_h1234"=dword:00000000' in text
    assert "PlayerName_h99" not in text
    assert '"Screenmanager Resolution Width_h182942802"=dword:00000780' in text
    assert "[Software\\\\Wine] 1700000002\n#time=1da0f1e2c3b4a52\n\"Version\"=\"win10\"" in text
    assert text.startswith("WINE REGISTRY Version 2\n;; All keys relative to")
    assert "16,17,18,19" in text  # the multi-line value survived
    assert "[Software\\\\IronGate\\\\Valheim\\\\Brand\\\\New]" in text
    after = reg.read(KEY)
    assert after["Sub"]["New_h2"]["wine"] == "dword:00000001"
    assert after["Brand\\New"]["X"]["wine"] == '"y"'
    assert "PlayerName_h99" not in after[""]
    # Writing the original values back restores them
    reg.write(KEY, [("", "GraphicsQuality_h1234", tree[""]["GraphicsQuality_h1234"])])
    assert reg.read(KEY)[""]["GraphicsQuality_h1234"]["wine"] == "dword:00000003"


def test_wine_write_needs_the_file(tmp_path):
    with pytest.raises(RegistryError, match="doesn't exist"):
        WineRegistry(tmp_path).write(KEY, [("", "x", {"token": '"x"', "wine": '"1"'})])


def test_value_path():
    assert registry.value_path("", "Quality") == "Quality"
    assert registry.value_path("Sub\\Key", "") == "Sub/Key/(Default)"


@pytest.mark.skipif(sys.platform != "win32", reason="real Windows registry")
def test_windows_registry_round_trip():
    import winreg

    key = "HKEY_CURRENT_USER\\Software\\RigSwapTest\\Game"
    reg = registry.WindowsRegistry()
    try:
        reg.write(key, [("", "Quality", {"type": winreg.REG_DWORD, "data": 3}),
                        ("", "Name", {"type": winreg.REG_SZ, "data": "Alex"}),
                        ("Sub", "Blob", {"type": winreg.REG_BINARY,
                                         "b64": "AQID"})])
        tree = reg.read(key)
        assert tree[""]["Quality"] == {"type": winreg.REG_DWORD, "data": 3}
        assert tree["Sub"]["Blob"] == {"type": winreg.REG_BINARY, "b64": "AQID"}
        reg.write(key, [("", "Name", None)])
        assert "Name" not in reg.read(key)[""]
    finally:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, "Software\\RigSwapTest\\Game\\Sub")
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, "Software\\RigSwapTest\\Game")
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, "Software\\RigSwapTest")
