from qpgm.core.artwork import Art, find_art
from qpgm.core.models import Game


def touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"img")
    return path


def test_layouts_and_priority(tmp_path):
    root = tmp_path / "Steam"
    cache = root / "appcache" / "librarycache"
    flat = touch(cache / "10_library_600x900.jpg")
    v2024 = touch(cache / "20" / "library_600x900.jpg")
    hashed = touch(cache / "30" / "3dee2eb6" / "library_capsule.jpg")
    touch(cache / "30" / "df65cafa" / "library_header.jpg")
    wide_only = touch(cache / "40" / "header.jpg")
    touch(cache / "20" / "header.jpg")

    def art(app_id):
        return find_art(Game(name="g", steam_app_id=app_id), [root])

    assert art(10) == Art(flat, True)
    assert art(20) == Art(v2024, True)
    assert art(30) == Art(hashed, True)
    assert art(40) == Art(wide_only, False)
    assert art(50) is None


def test_custom_grid_and_user_poster_win(tmp_path):
    root = tmp_path / "Steam"
    touch(root / "appcache" / "librarycache" / "20" / "library_600x900.jpg")
    custom = touch(root / "userdata" / "1234" / "config" / "grid" / "20p.png")
    assert find_art(Game(name="g", steam_app_id=20), [root]) == Art(custom, True)
    mine = touch(tmp_path / "my poster.png")
    g = Game(name="g", steam_app_id=20, poster=str(mine))
    assert find_art(g, [root]) == Art(mine, True)
    assert find_art(Game(name="manual"), [root]) is None
    gone = Game(name="g", steam_app_id=20, poster=str(tmp_path / "deleted.png"))
    assert find_art(gone, [root]) == Art(custom, True)


def test_portrait_in_second_install_beats_wide_in_first(tmp_path):
    a, b = tmp_path / "A", tmp_path / "B"
    touch(a / "appcache" / "librarycache" / "7" / "header.jpg")
    poster = touch(b / "appcache" / "librarycache" / "7" / "library_600x900.jpg")
    assert find_art(Game(name="g", steam_app_id=7), [a, b]) == Art(poster, True)
