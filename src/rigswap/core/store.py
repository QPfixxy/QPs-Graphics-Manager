"""Load and save the library as JSON in the data folder."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from rigswap.core import paths
from rigswap.core.fsutil import atomic_write_text
from rigswap.core.models import Library, LibraryError


class LibraryStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else paths.data_dir()

    @property
    def file(self) -> Path:
        return paths.library_file(self.root)

    @property
    def backup_file(self) -> Path:
        return self.file.with_suffix(".json.bak")

    def load(self) -> Library:
        """Load the library. A missing file means a fresh, empty library.

        A corrupt file raises LibraryError and is left untouched, so the user's
        data is never silently replaced.
        """
        try:
            text = self.file.read_text(encoding="utf-8")
        except FileNotFoundError:
            return Library()
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise LibraryError(f"{self.file} is not valid JSON: {e}") from e
        return Library.from_dict(data)

    def save(self, library: Library) -> None:
        """Save atomically, keeping the previous version as library.json.bak."""
        text = json.dumps(library.to_dict(), indent=2, ensure_ascii=False) + "\n"
        if self.file.exists():
            shutil.copy2(self.file, self.backup_file)
        atomic_write_text(self.file, text)
