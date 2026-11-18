import pytest

from qpgm.core.models import (
    SCHEMA_VERSION,
    ConfigLocation,
    Game,
    Library,
    LibraryError,
    LocationKind,
)


@pytest.fixture
def lib() -> Library:
    lib = Library()
    lib.add_setup("Handheld")
    lib.add_setup("eGPU Dock")
    lib.add_game(
        Game(
            name="Cyberpunk 2077",
            steam_app_id=1091500,
            config_locations=[ConfigLocation("C:/Users/me/AppData/Local/CD Projekt Red")],
        )
    )
    return lib


def test_setup_names_are_trimmed_and_unique_case_insensitively(lib):
    assert lib.add_setup("  Laptop on Battery ").name == "Laptop on Battery"
    with pytest.raises(LibraryError):
        lib.add_setup("handheld")
    with pytest.raises(LibraryError):
        lib.add_setup("   ")


def test_rename_setup_allows_same_name_but_not_a_clash(lib):
    handheld = lib.setup_by_name("Handheld")
    lib.rename_setup(handheld.id, "HANDHELD")
    assert handheld.name == "HANDHELD"
    with pytest.raises(LibraryError):
        lib.rename_setup(handheld.id, "egpu dock")


def test_duplicate_steam_app_id_rejected(lib):
    with pytest.raises(LibraryError):
        lib.add_game(Game(name="Cyberpunk again", steam_app_id=1091500))


def test_record_snapshot_replaces_older_record(lib):
    game = lib.games[0]
    setup = lib.setups[0]
    first = lib.record_snapshot(game.id, setup.id)
    second = lib.record_snapshot(game.id, setup.id)
    assert lib.snapshots == [second]
    assert second.saved_at >= first.saved_at


def test_record_snapshot_requires_known_game_and_setup(lib):
    with pytest.raises(LibraryError):
        lib.record_snapshot("nope", lib.setups[0].id)
    with pytest.raises(LibraryError):
        lib.record_snapshot(lib.games[0].id, "nope")


def test_removing_setup_drops_its_snapshots_and_active_flag(lib):
    game, setup = lib.games[0], lib.setups[0]
    lib.record_snapshot(game.id, setup.id)
    lib.record_snapshot(game.id, lib.setups[1].id)
    lib.settings.active_setup_id = setup.id
    lib.remove_setup(setup.id)
    assert [s.setup_id for s in lib.snapshots] == [lib.setups[0].id]
    assert lib.settings.active_setup_id is None


def test_removing_game_drops_its_snapshots(lib):
    game = lib.games[0]
    lib.record_snapshot(game.id, lib.setups[0].id)
    lib.remove_game(game.id)
    assert lib.games == [] and lib.snapshots == []


def test_round_trip(lib):
    game = lib.games[0]
    game.config_locations.append(ConfigLocation("/tmp/cfg", LocationKind.DIRECTORY))
    lib.record_snapshot(game.id, lib.setups[1].id)
    lib.settings.active_setup_id = lib.setups[1].id
    lib.settings.save_on_switch = False
    data = lib.to_dict()
    assert data["schema_version"] == SCHEMA_VERSION
    assert Library.from_dict(data) == lib


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(schema_version=999),
        lambda d: d.pop("schema_version"),
        lambda d: d["setups"].append(dict(d["setups"][0])),  # duplicate id
        lambda d: d["setups"][1].update(name="HANDHELD"),  # duplicate name
        lambda d: d["snapshots"].append(
            {
                "game_id": "ghost",
                "setup_id": d["setups"][0]["id"],
                "saved_at": "2026-01-01T00:00:00+00:00",
            }
        ),
        lambda d: d["settings"].update(active_setup_id="ghost"),
        lambda d: d["games"][0].update(steam_app_id="1091500"),
        lambda d: d["games"][0].pop("name"),
        lambda d: d["games"][0]["config_locations"][0].update(kind="symlink"),
        lambda d: d.update(settings="oops"),
    ],
)
def test_invalid_data_is_rejected(lib, mutate):
    data = lib.to_dict()
    mutate(data)
    with pytest.raises(LibraryError):
        Library.from_dict(data)
