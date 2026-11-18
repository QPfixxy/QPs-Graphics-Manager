"""Reader for Valve's text KeyValues format (VDF / ACF).

Steam keeps its library list (libraryfolders.vdf) and one appmanifest_<id>.acf per
installed game in this format:

    "AppState"
    {
        "appid"       "1091500"
        "name"        "Cyberpunk 2077"
        "installdir"  "Cyberpunk 2077"
    }

Handles quoted and unquoted tokens, backslash escapes, // comments and the
[$WIN32]-style conditionals (which are ignored). Binary VDF isn't supported.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

_ESCAPES = {"\\": "\\", '"': '"', "n": "\n", "t": "\t"}
_OPEN, _CLOSE, _STR = "{", "}", "str"


class VdfError(ValueError):
    pass


def _tokens(text: str) -> Iterator[tuple[str, str]]:
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end < 0 else end + 1
        elif c in "{}":
            yield c, c
            i += 1
        elif c == '"':
            i += 1
            buf: list[str] = []
            while True:
                if i >= n:
                    raise VdfError("unterminated string")
                c = text[i]
                if c == "\\" and i + 1 < n:
                    nxt = text[i + 1]
                    buf.append(_ESCAPES.get(nxt, "\\" + nxt))
                    i += 2
                elif c == '"':
                    i += 1
                    break
                else:
                    buf.append(c)
                    i += 1
            yield _STR, "".join(buf)
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in '{}"':
                j += 1
            tok = text[i:j]
            i = j
            if tok.startswith("[") and tok.endswith("]"):
                continue  # platform conditional, e.g. [$WIN32]
            yield _STR, tok


def loads(text: str) -> dict[str, Any]:
    """Parse VDF text into nested dicts of strings. Later duplicate keys win."""
    toks = list(_tokens(text.lstrip("﻿")))
    pos = 0

    def parse_object(top: bool) -> dict[str, Any]:
        nonlocal pos
        obj: dict[str, Any] = {}
        while pos < len(toks):
            kind, key = toks[pos]
            if kind == _CLOSE:
                if top:
                    raise VdfError("unexpected '}'")
                pos += 1
                return obj
            if kind == _OPEN:
                raise VdfError("unexpected '{' where a key was expected")
            pos += 1
            if pos >= len(toks):
                raise VdfError(f"missing value for {key!r}")
            kind, value = toks[pos]
            pos += 1
            if kind == _OPEN:
                obj[key] = parse_object(top=False)
            elif kind == _STR:
                obj[key] = value
            else:
                raise VdfError(f"unexpected '}}' after key {key!r}")
        if not top:
            raise VdfError("missing '}'")
        return obj

    return parse_object(top=True)


def load(path: Path) -> dict[str, Any]:
    return loads(Path(path).read_text(encoding="utf-8", errors="replace"))


def get(d: dict[str, Any], key: str, default: Any = None) -> Any:
    """Case-insensitive lookup; Steam isn't consistent ("LibraryFolders" vs "libraryfolders")."""
    if key in d:
        return d[key]
    lower = key.lower()
    for k, v in d.items():
        if k.lower() == lower:
            return v
    return default
