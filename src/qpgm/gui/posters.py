"""Poster cards: Steam art cropped to a 2:3 card, or a generated placeholder."""

from __future__ import annotations

import hashlib
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPixmap

from qpgm.core.artwork import Art

CARD_W, CARD_H = 150, 225
RADIUS = 10


class PosterCache:
    def __init__(self) -> None:
        self._cache: dict[tuple, QPixmap] = {}

    def card(self, name: str, art: Art | None, scale: float = 1.0, dpr: float = 1.0) -> QPixmap:
        key = (name, art.path if art else None, _mtime(art.path) if art else None, scale, dpr)
        pix = self._cache.get(key)
        if pix is None:
            pix = _render(name, art, scale, dpr)
            self._cache[key] = pix
        return pix

    def clear(self) -> None:
        self._cache.clear()


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _render(name: str, art: Art | None, scale: float, dpr: float) -> QPixmap:
    """Paint in logical pixels (w x h) onto a pixmap with dpr times the resolution."""
    w, h = round(CARD_W * scale), round(CARD_H * scale)
    out = QPixmap(round(w * dpr), round(h * dpr))
    out.setDevicePixelRatio(dpr)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(0, 0, w, h), RADIUS * scale, RADIUS * scale)
    p.setClipPath(clip)

    source = QPixmap(str(art.path)) if art else QPixmap()
    if not source.isNull() and art.portrait:
        dw, dh = round(w * dpr), round(h * dpr)
        scaled = source.scaled(dw, dh, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                               Qt.TransformationMode.SmoothTransformation)
        scaled = scaled.copy((scaled.width() - dw) // 2, (scaled.height() - dh) // 2, dw, dh)
        scaled.setDevicePixelRatio(dpr)
        p.drawPixmap(0, 0, scaled)
    else:
        top, bottom = _colors(name)
        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0, top)
        grad.setColorAt(1, bottom)
        p.fillRect(0, 0, w, h, grad)
        if not source.isNull():  # wide header: fit across the middle
            scaled = source.scaledToWidth(round(w * dpr),
                                          Qt.TransformationMode.SmoothTransformation)
            scaled.setDevicePixelRatio(dpr)
            p.drawPixmap(0, round((h - scaled.height() / dpr) / 2), scaled)
        else:
            p.setPen(QColor(255, 255, 255, 230))
            font = QFont()
            font.setPixelSize(round(46 * scale))
            font.setBold(True)
            p.setFont(font)
            p.drawText(QRectF(0, 0, w, h * 0.8), Qt.AlignmentFlag.AlignCenter, _initials(name))
            font.setPixelSize(round(13 * scale))
            font.setBold(False)
            p.setFont(font)
            p.setPen(QColor(255, 255, 255, 190))
            p.drawText(QRectF(10 * scale, h * 0.62, w - 20 * scale, h * 0.35),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
                       | Qt.TextFlag.TextWordWrap, name)
    p.end()
    return out


def _initials(name: str) -> str:
    words = [w for w in name.replace(":", " ").split() if w[:1].isalnum()]
    return "".join(w[0] for w in words[:2]).upper() or "?"


def _colors(name: str) -> tuple[QColor, QColor]:
    hue = int(hashlib.sha1(name.encode()).hexdigest()[:4], 16) % 360
    return QColor.fromHsv(hue, 120, 150), QColor.fromHsv((hue + 30) % 360, 160, 70)
