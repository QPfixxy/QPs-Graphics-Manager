"""File helpers that never leave a half-written file behind."""

from __future__ import annotations

import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write to a temp file in the same folder, fsync it, then rename over the target.

    The rename is atomic on both Windows and Linux, so a crash leaves either the
    old file or the new one, never a mix.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as tmp:
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    atomic_write_bytes(path, text.encode(encoding))


def atomic_copy(src: Path, dst: Path) -> None:
    """Copy src over dst atomically, keeping src's timestamps and mode.

    A read-only dst (some people lock configs so games can't change them) is made
    writable first; the copy then gets src's mode, so read-only comes back if the
    saved copy had it.
    """
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=dst.parent, prefix=f".{dst.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as tmp, open(src, "rb") as f:
            shutil.copyfileobj(f, tmp)
            tmp.flush()
            os.fsync(tmp.fileno())
        shutil.copystat(src, tmp_name)
        if dst.exists() and not os.access(dst, os.W_OK):
            os.chmod(dst, stat.S_IWRITE | stat.S_IREAD)
        os.replace(tmp_name, dst)
    except BaseException:
        tmp_path = Path(tmp_name)
        if tmp_path.exists():
            os.chmod(tmp_path, stat.S_IWRITE | stat.S_IREAD)
            tmp_path.unlink()
        raise


def rmtree(path: Path) -> None:
    """shutil.rmtree that also removes read-only files (needed on Windows)."""

    def on_error(func, p, _exc):
        os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
        func(p)

    if not Path(path).exists():
        return
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=on_error)
    else:
        shutil.rmtree(path, onerror=on_error)
