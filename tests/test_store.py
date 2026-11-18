import json

import pytest

from qpgm.core.fsutil import atomic_write_text
from qpgm.core.models import Game, Library, LibraryError
from qpgm.core.store import LibraryStore


def test_missing_file_gives_empty_library(tmp_path):
    assert LibraryStore(tmp_path).load() == Library()


def test_save_and_load(tmp_path):
    store = LibraryStore(tmp_path)
    lib = Library()
    lib.add_setup("Handheld")
    lib.add_game(Game(name="Elden Ring", steam_app_id=1245620))
    store.save(lib)
    assert store.load() == lib


def test_save_keeps_previous_version_as_backup(tmp_path):
    store = LibraryStore(tmp_path)
    lib = Library()
    lib.add_setup("Handheld")
    store.save(lib)
    lib.add_setup("eGPU Dock")
    store.save(lib)
    backup = json.loads(store.backup_file.read_text(encoding="utf-8"))
    assert [s["name"] for s in backup["setups"]] == ["Handheld"]


def test_corrupt_file_raises_and_is_left_alone(tmp_path):
    store = LibraryStore(tmp_path)
    store.file.write_text("{ not json", encoding="utf-8")
    with pytest.raises(LibraryError):
        store.load()
    assert store.file.read_text(encoding="utf-8") == "{ not json"


def test_non_ascii_names_survive(tmp_path):
    store = LibraryStore(tmp_path)
    lib = Library()
    lib.add_game(Game(name="ニーア オートマタ"))
    store.save(lib)
    assert store.load().games[0].name == "ニーア オートマタ"


def test_atomic_write_leaves_no_temp_files(tmp_path):
    target = tmp_path / "sub" / "file.txt"
    atomic_write_text(target, "one")
    atomic_write_text(target, "two")
    assert target.read_text() == "two"
    assert [p.name for p in target.parent.iterdir()] == ["file.txt"]
