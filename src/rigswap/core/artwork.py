"""Find poster art for games in Steam's local cache (no network needed).

Steam has used three cache layouts, and one game's folder can mix them:

    appcache/librarycache/<appid>_library_600x900.jpg          (old, flat)
    appcache/librarycache/<appid>/library_600x900.jpg          (2024)
    appcache/librarycache/<appid>/<hash>/library_capsule.jpg   (2025+)

Custom art the user set in Steam (userdata/<user>/config/grid/<appid>p.png) wins.
When there's no portrait poster, the wide header image is returned instead and
the GUI fits it into the card.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rigswap.core.models import Game

_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
_PORTRAIT_NAMES = ("library_600x900_2x.jpg", "library_600x900.jpg", "library_capsule.jpg")
_WIDE_NAMES = ("header.jpg", "library_header.jpg")


@dataclass(frozen=True)
class Art:
    path: Path
    portrait: bool


def find_art(game: Game, steam_roots: list[Path]) -> Art | None:
    if game.poster and Path(game.poster).is_file():
        return Art(Path(game.poster), portrait=True)
    if game.steam_app_id is None:
        return None
    app = str(game.steam_app_id)
    wide: Art | None = None
    for root in steam_roots:
        for grid in sorted((root / "userdata").glob("*/config/grid")):
            for ext in _IMAGE_EXTS:
                custom = grid / f"{app}p{ext}"
                if custom.is_file():
                    return Art(custom, portrait=True)
        cache = root / "appcache" / "librarycache"
        app_dir = cache / app
        for name in _PORTRAIT_NAMES:
            for candidate in (app_dir / name, *sorted(app_dir.glob(f"*/{name}"))):
                if candidate.is_file():
                    return Art(candidate, portrait=True)
        flat = cache / f"{app}_library_600x900.jpg"
        if flat.is_file():
            return Art(flat, portrait=True)
        if wide is None:
            for name in _WIDE_NAMES:
                for candidate in (app_dir / name, *sorted(app_dir.glob(f"*/{name}")),
                                  cache / f"{app}_header.jpg"):
                    if candidate.is_file():
                        wide = Art(candidate, portrait=False)
                        break
                if wide:
                    break
    return wide
