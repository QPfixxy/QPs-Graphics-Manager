"""Registry-stored settings (common in Unity games), on Windows and inside Wine prefixes.

A registry location is a key such as HKEY_CURRENT_USER\\Software\\IronGate\\Valheim. Its
values (in it and all sub-keys) are read as a tree:

    {"": {"name": value, ...}, "Sub\\Key": {...}}

where each value is a small JSON-able dict, so snapshots can be stored as JSON.

Backends:
- WindowsRegistry: the real registry, through Python's built-in winreg; values keep
  their exact type.
- WineRegistry: the text files Wine keeps in a prefix (user.reg for HKEY_CURRENT_USER,
  system.reg for HKEY_LOCAL_MACHINE). Value lines are kept exactly as Wine wrote them
  and only the lines that change are touched; the file is rewritten atomically. Wine
  must not be running in that prefix (its wineserver would write its own copy back).
"""

from __future__ import annotations

import base64
import contextlib
import re
import time
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from rigswap.core.fsutil import atomic_write_bytes

HIVES = {
    "HKEY_CURRENT_USER": "HKEY_CURRENT_USER", "HKCU": "HKEY_CURRENT_USER",
    "HKEY_LOCAL_MACHINE": "HKEY_LOCAL_MACHINE", "HKLM": "HKEY_LOCAL_MACHINE",
}
WINE_FILES = {"HKEY_CURRENT_USER": "user.reg", "HKEY_LOCAL_MACHINE": "system.reg"}

Value = dict[str, Any]
Tree = dict[str, dict[str, Value]]  # sub-key (relative, "" = the key itself) -> name -> value


class RegistryError(Exception):
    pass


def parse_key(path: str) -> tuple[str, str]:
    """'HKCU/Software/X' or 'HKEY_CURRENT_USER\\Software\\X' -> (hive, 'Software\\X')."""
    parts = [p for p in re.split(r"[\\/]+", path.strip()) if p]
    if not parts or parts[0].upper() not in HIVES:
        raise RegistryError(f"{path!r} isn't a registry key under HKEY_CURRENT_USER or "
                            "HKEY_LOCAL_MACHINE")
    if len(parts) < 2:
        raise RegistryError(f"{path!r} is a whole hive; pick a key inside it")
    return HIVES[parts[0].upper()], "\\".join(parts[1:])


def normalize_key(path: str) -> str:
    hive, sub = parse_key(path)
    return f"{hive}\\{sub}"


def value_path(subkey: str, name: str) -> str:
    """How a value is shown and matched by include/exclude patterns: 'Sub/Key/Name'."""
    shown = name or "(Default)"
    return f"{subkey.replace(chr(92), '/')}/{shown}" if subkey else shown


# --- Windows -----------------------------------------------------------------------


class WindowsRegistry:
    kind = "windows"

    def __init__(self, winreg: ModuleType | None = None) -> None:
        if winreg is None:
            import winreg as _w

            winreg = _w
        self.w = winreg

    def _hive(self, hive: str):
        return {"HKEY_CURRENT_USER": self.w.HKEY_CURRENT_USER,
                "HKEY_LOCAL_MACHINE": self.w.HKEY_LOCAL_MACHINE}[hive]

    def exists(self, key: str) -> bool:
        hive, sub = parse_key(key)
        try:
            with self.w.OpenKey(self._hive(hive), sub):
                return True
        except OSError:
            return False

    def subkeys(self, key: str) -> list[str]:
        """Names of the key's direct sub-keys (empty if the key doesn't exist)."""
        hive, sub = parse_key(key)
        names: list[str] = []
        try:
            with self.w.OpenKey(self._hive(hive), sub) as k:
                i = 0
                while True:
                    try:
                        names.append(self.w.EnumKey(k, i))
                    except OSError:
                        break
                    i += 1
        except OSError:
            return []
        return names

    def read(self, key: str) -> Tree | None:
        hive, sub = parse_key(key)
        tree: Tree = {}
        try:
            self._read_into(self._hive(hive), sub, "", tree)
        except FileNotFoundError:
            return None
        except OSError as e:
            raise RegistryError(f"can't read {key}: {e}") from e
        return tree

    def _read_into(self, hive, path: str, rel: str, tree: Tree) -> None:
        with self.w.OpenKey(hive, path) as k:
            values: dict[str, Value] = {}
            i = 0
            while True:
                try:
                    name, data, typ = self.w.EnumValue(k, i)
                except OSError:
                    break
                values[name] = _encode(typ, data)
                i += 1
            tree[rel] = values
            subs = []
            i = 0
            while True:
                try:
                    subs.append(self.w.EnumKey(k, i))
                except OSError:
                    break
                i += 1
        for name in subs:
            self._read_into(hive, f"{path}\\{name}", f"{rel}\\{name}" if rel else name, tree)

    def write(self, key: str, changes: list[tuple[str, str, Value | None]]) -> None:
        """Set (or, for None, delete) values: (sub-key, name, value)."""
        hive, sub = parse_key(key)
        h = self._hive(hive)
        for rel, name, value in changes:
            path = f"{sub}\\{rel}" if rel else sub
            try:
                if value is None:
                    with (self.w.OpenKey(h, path, 0, self.w.KEY_SET_VALUE) as k,
                          contextlib.suppress(FileNotFoundError)):
                        self.w.DeleteValue(k, name)
                else:
                    with self.w.CreateKeyEx(h, path, 0, self.w.KEY_SET_VALUE) as k:
                        typ, data = _decode(value)
                        self.w.SetValueEx(k, name, 0, typ, data)
            except FileNotFoundError:
                continue  # deleting from a key that's gone: nothing to do
            except OSError as e:
                raise RegistryError(f"can't write {path}\\{name}: {e}") from e


def _encode(typ: int, data: Any) -> Value:
    if isinstance(data, bytes):
        return {"type": typ, "b64": base64.b64encode(data).decode("ascii")}
    if data is None:
        return {"type": typ, "b64": ""}
    return {"type": typ, "data": data}


def _decode(value: Value) -> tuple[int, Any]:
    if "b64" in value:
        return int(value["type"]), base64.b64decode(value["b64"]) if value["b64"] else None
    return int(value["type"]), value["data"]


# --- Wine prefixes ----------------------------------------------------------------

_SECTION = re.compile(r"^\[(?P<key>.*)\](?P<rest>.*)$")


@dataclass
class _Section:
    header: str  # the "[...] 1700000000" line
    key: str  # unescaped, e.g. Software\\IronGate\\Valheim
    lines: list[str]  # everything after the header: metadata and value entries


def _unescape_key(text: str) -> str:
    return text.replace("\\\\", "\\")


def _escape_key(key: str) -> str:
    return key.replace("\\", "\\\\")


def _entries(lines: list[str]) -> list[tuple[str | None, str]]:
    """Group a section's lines into (value-name token, full text) entries.

    Metadata lines (#time=...) get None as their name. A value whose text ends with a
    backslash continues on the next line (long hex data).
    """
    out: list[tuple[str | None, str]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith(("\"", "@")):
            text = [line]
            while text[-1].endswith("\\") and i + 1 < len(lines):
                i += 1
                text.append(lines[i])
            out.append((_name_token(line), "\n".join(text)))
        else:
            out.append((None, line))
        i += 1
    return out


def _name_token(line: str) -> str:
    """The value's name exactly as written: '@' or the quoted name with its escapes."""
    if line.startswith("@"):
        return "@"
    i = 1
    while i < len(line):
        if line[i] == "\\":
            i += 2
            continue
        if line[i] == '"':
            return line[: i + 1]
        i += 1
    return line


def _display_name(token: str) -> str:
    if token == "@":
        return ""
    return re.sub(r'\\(.)', r"\1", token[1:-1])


class WineRegistry:
    kind = "wine"

    def __init__(self, prefix: Path) -> None:
        self.prefix = Path(prefix)

    def _file(self, hive: str) -> Path:
        return self.prefix / WINE_FILES[hive]

    def _load(self, hive: str) -> tuple[list[str], list[_Section]]:
        try:
            text = self._file(hive).read_text(encoding="utf-8", errors="surrogateescape")
        except FileNotFoundError:
            return [], []
        head: list[str] = []
        sections: list[_Section] = []
        for line in text.split("\n"):
            m = _SECTION.match(line)
            if m:
                sections.append(_Section(line, _unescape_key(m.group("key")), []))
            elif sections:
                sections[-1].lines.append(line)
            else:
                head.append(line)
        return head, sections

    @staticmethod
    def _under(section_key: str, sub: str) -> str | None:
        """The section's path relative to `sub`, or None if it isn't inside it."""
        a, b = section_key.casefold(), sub.casefold()
        if a == b:
            return ""
        if a.startswith(b + "\\"):
            return section_key[len(sub) + 1:]
        return None

    def exists(self, key: str) -> bool:
        hive, sub = parse_key(key)
        return any(self._under(s.key, sub) is not None for s in self._load(hive)[1])

    def read(self, key: str) -> Tree | None:
        hive, sub = parse_key(key)
        tree: Tree = {}
        for section in self._load(hive)[1]:
            rel = self._under(section.key, sub)
            if rel is None:
                continue
            values = tree.setdefault(rel, {})
            for token, text in _entries(section.lines):
                if token is not None:
                    # keep the text after "name=" exactly as Wine wrote it
                    values[_display_name(token)] = {"wine": text[len(token):].lstrip("="),
                                                    "token": token}
        return tree or None

    def write(self, key: str, changes: list[tuple[str, str, Value | None]]) -> None:
        hive, sub = parse_key(key)
        path = self._file(hive)
        head, sections = self._load(hive)
        if not sections and not path.exists():
            raise RegistryError(f"{path} doesn't exist (has the game been run in this prefix?)")
        by_rel: dict[str, _Section] = {}
        for s in sections:
            rel = self._under(s.key, sub)
            if rel is not None:
                by_rel.setdefault(rel.casefold(), s)
        for rel, name, value in changes:
            section = by_rel.get(rel.casefold())
            if section is None:
                if value is None:
                    continue
                full = f"{sub}\\{rel}" if rel else sub
                section = _Section(f"[{_escape_key(full)}] {int(time.time())}", full, [])
                sections.append(section)
                by_rel[rel.casefold()] = section
            entries = _entries(section.lines)
            token = value["token"] if value else _find_token(entries, name)
            kept = [(t, txt) for t, txt in entries if t != token]
            if value is not None:
                line = ("@=" if token == "@" else f"{token}=") + value["wine"]
                # keep a trailing blank line (Wine separates sections with one)
                insert_at = len(kept)
                while insert_at and kept[insert_at - 1][1] == "":
                    insert_at -= 1
                kept.insert(insert_at, (token, line))
            section.lines = [txt for _t, txt in kept]
            if section.lines and section.lines[-1] != "" and section is not sections[-1]:
                section.lines.append("")
        parts = head[:]
        for s in sections:
            parts.append(s.header)
            parts.extend(s.lines)
        text = "\n".join(parts)
        if not text.endswith("\n"):
            text += "\n"
        # Bytes we couldn't decode were kept as-is when reading; write them back unchanged.
        atomic_write_bytes(path, text.encode("utf-8", errors="surrogateescape"))


def _find_token(entries: list[tuple[str | None, str]], name: str) -> str | None:
    for token, _text in entries:
        if token is not None and _display_name(token) == name:
            return token
    return None


def backend_for(wine_prefix: str | None, platform: str):
    """The registry a game's settings live in: Windows', or its Wine prefix's files."""
    if wine_prefix:
        return WineRegistry(Path(wine_prefix))
    if platform == "win32":
        return WindowsRegistry()
    raise RegistryError("registry settings need Windows, or a game that runs in a Wine/Proton "
                        "prefix")
