"""PySide6 desktop app (Phase 1). Icons live in resources/icons/."""

from importlib.resources import files


def icon_path(name: str) -> str:
    """Path to a bundled icon, e.g. icon_path("app.png")."""
    return str(files(__name__) / "resources" / "icons" / name)
