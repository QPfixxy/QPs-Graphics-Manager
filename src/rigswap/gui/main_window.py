"""Main window: setup buttons on top, poster grid, details of the selected game."""

from __future__ import annotations

import html
import os
from pathlib import Path

from PySide6.QtCore import (
    QEvent,
    QModelIndex,
    QRect,
    QSize,
    QSortFilterProxyModel,
    Qt,
    QThread,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPen,
    QStandardItem,
    QStandardItemModel,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from rigswap import __version__, hardware
from rigswap.core import auto, processes, swap
from rigswap.core.models import (
    Conditions,
    ConfigLocation,
    Game,
    LibraryError,
    LocationKind,
    Power,
    Setup,
)
from rigswap.gui import icon_path
from rigswap.gui.posters import CARD_H, CARD_W, PosterCache
from rigswap.gui.state import AppState, SnapState

GAME_ID_ROLE = Qt.ItemDataRole.UserRole + 1
STATUS_ROLE = Qt.ItemDataRole.UserRole + 2

ACCENT = QColor("#4c7dff")
STATUS_COLORS = {"ok": QColor("#5ccf8a"), "warn": QColor("#f0a24a"), "none": QColor("#8a91a3")}


# --- poster grid ------------------------------------------------------------------


class CardDelegate(QStyledItemDelegate):
    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.window = window

    def sizeHint(self, option, index) -> QSize:
        return QSize(CARD_W + 24, CARD_H + 64)

    def paint(self, p: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        game = self.window.state.lib.game(index.data(GAME_ID_ROLE))
        dpr = option.widget.devicePixelRatioF() if option.widget else 1.0
        pix = self.window.posters.card(game.name, self.window.state.art(game), dpr=dpr)
        r = option.rect
        x, y = r.x() + (r.width() - CARD_W) // 2, r.y() + 8
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        hovered = option.state & QStyle.StateFlag.State_MouseOver
        selected = option.state & QStyle.StateFlag.State_Selected
        p.drawPixmap(x, y, pix)
        if selected or hovered:
            pen = QPen(ACCENT if selected else QColor(255, 255, 255, 90), 3 if selected else 2)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRect(x - 2, y - 2, CARD_W + 4, CARD_H + 4), 11, 11)
        font = QFont(option.font)
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor("#e8eaf0"))
        name = p.fontMetrics().elidedText(game.name, Qt.TextElideMode.ElideRight, CARD_W)
        p.drawText(QRect(x, y + CARD_H + 6, CARD_W, 20), Qt.AlignmentFlag.AlignLeft, name)
        text, kind = index.data(STATUS_ROLE)
        font.setBold(False)
        if font.pixelSize() > 0:
            font.setPixelSize(round(font.pixelSize() * 0.9))
        else:
            font.setPointSizeF(font.pointSizeF() * 0.9)
        p.setFont(font)
        p.setPen(STATUS_COLORS[kind])
        p.drawText(QRect(x, y + CARD_H + 26, CARD_W, 18), Qt.AlignmentFlag.AlignLeft, text)
        p.restore()


STORE_NAMES = {"steam": "Steam", "epic": "Epic Games", "gog": "GOG", "amazon": "Amazon Games",
               "lutris": "Lutris", "gamebar": "Found by Windows"}


def source_label(game: Game) -> str:
    if game.steam_app_id:
        text = f"Steam app {game.steam_app_id}"
    elif game.store:
        text = STORE_NAMES.get(game.store, game.store.title())
    else:
        text = "Added by hand"
    if game.wine_prefix:
        text += " · Proton" if game.store == "steam" else " · Wine"
    return text


def game_status(state: AppState, game: Game) -> tuple[str, str]:
    if not game.config_locations:
        return "No config files yet", "none"
    states = [state.snapshot_state(game, s)[0] for s in state.lib.setups]
    if not states:
        return "No setups yet", "none"
    active = state.active_setup()
    if active is not None and active.locked and state.changed_files(game, active):
        return "Changed since saved", "warn"
    stale = states.count(SnapState.STALE)
    saved = states.count(SnapState.SAVED)
    if stale:
        return f"{stale} setup(s) out of date", "warn"
    if saved == len(states):
        return f"Ready for all {saved} setups" if saved > 1 else "Ready", "ok"
    return f"Saved for {saved} of {len(states)} setups", "warn" if saved else "none"


# --- dialogs -------------------------------------------------------------------------


class GameDialog(QDialog):
    """Name, install folder, executables and poster for a game."""

    def __init__(self, parent: QWidget, game: Game | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit game" if game else "Add game")
        self.setMinimumWidth(520)
        form = QFormLayout(self)
        self.name = QLineEdit(game.name if game else "")
        self.name.setPlaceholderText("e.g. Elden Ring")
        self.install_dir = QLineEdit(game.install_dir or "" if game else "")
        self.install_dir.setPlaceholderText("Optional: the game's install folder")
        self.exes = QLineEdit(", ".join(game.executables) if game else "")
        self.exes.setPlaceholderText("e.g. eldenring.exe (comma-separated)")
        self.poster = QLineEdit(game.poster or "" if game else "")
        self.poster.setPlaceholderText("Optional: a poster image")
        form.addRow("Name", self.name)
        form.addRow("Install folder", _with_browse(self.install_dir, self._browse_dir))
        form.addRow("Executables", self.exes)
        hint = QLabel("The install folder or executables let RigSwap refuse to swap configs "
                      "while the game is running.")
        hint.setWordWrap(True)
        hint.setObjectName("Hint")
        form.addRow("", hint)
        form.addRow("Poster", _with_browse(self.poster, self._browse_poster))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _browse_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Install folder", self.install_dir.text())
        if d:
            self.install_dir.setText(d)

    def _browse_poster(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Poster image", "",
                                           "Images (*.png *.jpg *.jpeg *.webp)")
        if f:
            self.poster.setText(f)

    def _accept(self) -> None:
        if not self.name.text().strip():
            self.name.setFocus()
            return
        self.accept()

    def apply_to(self, game: Game) -> None:
        game.name = self.name.text().strip()
        game.install_dir = self.install_dir.text().strip() or None
        game.executables = [e.strip() for e in self.exes.text().split(",") if e.strip()]
        game.poster = self.poster.text().strip() or None


class ConditionsDialog(QDialog):
    """When a setup is switched to automatically, with a live 'matches right now?' line."""

    CHOICES = [("Doesn't matter", None), ("Connected", True), ("Not connected", False)]
    POWER = [("Doesn't matter", None), ("Plugged in", Power.AC), ("On battery", Power.BATTERY)]

    def __init__(self, parent: QWidget, setup: Setup, state: hardware.HardwareState) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Use {setup.name} automatically")
        self.setMinimumWidth(500)
        self.hw = state
        form = QFormLayout(self)
        intro = QLabel(f"Switch to <b>{html.escape(setup.name)}</b> automatically when all of "
                       "these are true. Setups are checked from left to right, and the first "
                       "one that matches wins.")
        intro.setWordWrap(True)
        form.addRow(intro)
        w = setup.when
        self.egpu = self._combo(self.CHOICES, w.external_gpu)
        self.gpu = QComboBox()
        self.gpu.setEditable(True)
        self.gpu.addItem("")
        for g in state.gpus:
            self.gpu.addItem(g.name)
        self.gpu.setCurrentText(w.gpu_name or "")
        self.gpu.lineEdit().setPlaceholderText("Doesn't matter (or part of a GPU's name)")
        self.power = self._combo(self.POWER, w.power)
        self.display = self._combo(self.CHOICES, w.external_display)
        form.addRow("External GPU", self.egpu)
        form.addRow("GPU name contains", self.gpu)
        form.addRow("Power", self.power)
        form.addRow("External display", self.display)
        seen = QLabel("Right now RigSwap sees:\n" + "\n".join(f"  {line}"
                                                              for line in state.describe()))
        seen.setObjectName("Hint")
        form.addRow(seen)
        self.preview = QLabel()
        form.addRow(self.preview)
        for combo in (self.egpu, self.power, self.display):
            combo.currentIndexChanged.connect(self._update)
        self.gpu.editTextChanged.connect(self._update)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self._update()

    @staticmethod
    def _combo(choices, current) -> QComboBox:
        combo = QComboBox()
        for label, value in choices:
            combo.addItem(label, value)
        combo.setCurrentIndex(next(i for i, (_, v) in enumerate(choices) if v == current))
        return combo

    def conditions(self) -> Conditions:
        return Conditions(external_gpu=self.egpu.currentData(),
                          gpu_name=self.gpu.currentText().strip() or None,
                          power=self.power.currentData(),
                          external_display=self.display.currentData())

    def _update(self) -> None:
        c = self.conditions()
        if c.is_empty():
            text, kind = "No conditions: this setup is only used when you pick it.", "none"
        elif auto.matches(c, self.hw):
            text, kind = "Right now: this matches.", "ok"
        else:
            text, kind = "Right now: this doesn't match.", "warn"
        self.preview.setText(text)
        self.preview.setStyleSheet(f"color: {STATUS_COLORS[kind].name()};")


class ImportDialog(QDialog):
    """Everything found on this PC, ticked if it's new and looks like a game."""

    def __init__(self, parent: QWidget, found, in_library) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import games")
        self.setMinimumSize(560, 480)
        self.found = found
        v = QVBoxLayout(self)
        new = [g for g in found if not in_library(g)]
        intro = QLabel(f"Found {len(found)} installed game(s); {len(new)} aren't in RigSwap "
                       "yet. Games Windows found on its own can include apps that aren't games, "
                       "so they start unticked.")
        intro.setWordWrap(True)
        v.addWidget(intro)
        self.list = QListWidget()
        for g in found:
            have = in_library(g)
            label = f"{g.name}    ·  {STORE_NAMES.get(g.store, g.store)}"
            if g.wine_prefix:
                label += " · Proton" if g.store == "steam" else " · Wine"
            if have:
                label += "  (already added)"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, g)
            if have:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            else:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if g.suggested
                                   else Qt.CheckState.Unchecked)
            self.list.addItem(item)
        v.addWidget(self.list, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Import")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)

    def chosen(self):
        out = []
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.flags() & Qt.ItemFlag.ItemIsUserCheckable and \
                    item.checkState() == Qt.CheckState.Checked:
                out.append(item.data(Qt.ItemDataRole.UserRole))
        return out


class ScanWorker(QThread):
    finished_scan = Signal(object)

    def __init__(self, fn, parent: QWidget) -> None:
        super().__init__(parent)
        self.fn = fn

    def run(self) -> None:
        try:
            self.finished_scan.emit(self.fn())
        except Exception as e:  # shown in the dialog
            self.finished_scan.emit(e)


class LearnDialog(QDialog):
    """Learn mode: scan, let the user change a setting in the game, scan again, compare."""

    def __init__(self, parent: QWidget, game: Game, scan_fn, suggest_fn,
                 synchronous: bool = False) -> None:
        super().__init__(parent)
        self.game = game
        self.scan_fn, self.suggest_fn = scan_fn, suggest_fn
        self.synchronous = synchronous  # tests run scans on the calling thread
        self.before = None
        self.found = []
        self._worker = None
        self.setWindowTitle(f"Learn where {game.name} keeps its settings")
        self.setMinimumSize(620, 460)
        v = QVBoxLayout(self)
        self.text = QLabel()
        self.text.setWordWrap(True)
        v.addWidget(self.text)
        self.list = QListWidget()
        self.list.setVisible(False)
        v.addWidget(self.list, 1)
        row = QHBoxLayout()
        row.addStretch()
        self.action = QPushButton()
        self.action.setObjectName("Primary")
        self.action.clicked.connect(self._next)
        self.cancel = QPushButton("Cancel")
        self.cancel.clicked.connect(self.reject)
        row.addWidget(self.action)
        row.addWidget(self.cancel)
        v.addLayout(row)
        self.step = "intro"
        self._show_intro()

    def _show_intro(self) -> None:
        self.text.setText(
            f"<b>RigSwap will watch what changes while you change a setting.</b><br><br>"
            f"1. Click <b>Start</b> (and wait a moment while RigSwap takes a first look).<br>"
            f"2. Start {html.escape(self.game.name)}, change one graphics setting "
            f"(resolution is a good one), apply it, then <b>quit the game</b>: many games only "
            f"save their settings when they close.<br>"
            f"3. Come back and click <b>I've changed a setting</b>.")
        self.action.setText("Start")

    def _run(self, fn, then) -> None:
        self.action.setEnabled(False)
        if self.synchronous:
            then(fn())
            return
        self._worker = ScanWorker(fn, self)
        self._worker.finished_scan.connect(then)
        self._worker.start()

    def _next(self) -> None:
        if self.step == "intro":
            self.text.setText("Taking a first look…")
            self._run(self.scan_fn, self._got_before)
        elif self.step == "waiting":
            self.text.setText("Looking for what changed…")
            self._run(self.scan_fn, self._got_after)
        elif self.step == "results":
            self.accept()

    def _got_before(self, result) -> None:
        self.action.setEnabled(True)
        if isinstance(result, Exception):
            self.text.setText(f"Couldn't scan: {html.escape(str(result))}")
            self.step = "intro"
            return
        self.before = result
        self.step = "waiting"
        self.text.setText(
            f"<b>Ready.</b> Now start {html.escape(self.game.name)}, change one graphics "
            "setting, apply it and quit the game. Then click <b>I've changed a setting</b>.")
        self.action.setText("I've changed a setting")

    def _got_after(self, result) -> None:
        self.action.setEnabled(True)
        if isinstance(result, Exception):
            self.text.setText(f"Couldn't scan: {html.escape(str(result))}")
            return
        self.found = self.suggest_fn(self.before, result)
        if not self.found:
            self.text.setText(
                "<b>Nothing changed yet.</b> Make sure the setting was applied and the game "
                "was closed, then click <b>Look again</b>.")
            self.action.setText("Look again")
            return
        self.step = "results"
        n = sum(1 for f in self.found if f.likely)
        self.text.setText(
            f"<b>Found {len(self.found)} change(s).</b> The {n} ticked one(s) look like "
            f"{html.escape(self.game.name)}'s settings. Other programs also write files while "
            "you play, so check the list before adding.")
        self.list.clear()
        for f in self.found:
            icon = "🔑 " if f.location.kind == LocationKind.REGISTRY else "📄 "
            item = QListWidgetItem(f"{icon}{_short(f.location.path)}\n    {f.reason}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if f.likely else Qt.CheckState.Unchecked)
            item.setToolTip(f.location.path)
            self.list.addItem(item)
        self.list.setVisible(True)
        self.action.setText("Add ticked")

    def chosen(self):
        return [self.found[i].location for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.CheckState.Checked]


class PatternsDialog(QDialog):
    def __init__(self, parent: QWidget, loc: ConfigLocation) -> None:
        super().__init__(parent)
        is_reg = loc.kind == LocationKind.REGISTRY
        self.setWindowTitle("Which values in this key?" if is_reg
                            else "Which files in this folder?")
        self.setMinimumWidth(480)
        form = QFormLayout(self)
        shown = html.escape(_short(loc.path))
        if is_reg:
            text = ("Patterns are matched against each value's name, with sub-keys written "
                    "like folders, e.g. <code>*Resolution*</code> or <code>Graphics/*</code>. "
                    "Leave Include empty to take every value.")
        else:
            text = ("Patterns are matched against each file's path inside the folder, e.g. "
                    "<code>*.ini</code> or <code>saves/*</code>. Leave Include empty to take "
                    "every file.")
        label = QLabel(f"<b>{shown}</b><br>{text}")
        label.setWordWrap(True)
        form.addRow(label)
        self.include = QLineEdit(", ".join(loc.include))
        self.include.setPlaceholderText("all values" if is_reg else "all files")
        self.exclude = QLineEdit(", ".join(loc.exclude))
        self.exclude.setPlaceholderText("e.g. *Save*, *Progress*" if is_reg
                                        else "e.g. saves/*, *.sav")
        form.addRow("Include", self.include)
        form.addRow("Exclude", self.exclude)
        warn = QLabel("Exclude saves: anything included is put back to its saved version on "
                      "every switch.")
        warn.setWordWrap(True)
        warn.setObjectName("Hint")
        form.addRow(warn)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def patterns(self) -> tuple[list[str], list[str]]:
        def split(text: str) -> list[str]:
            return [x.strip() for x in text.split(",") if x.strip()]

        return split(self.include.text()), split(self.exclude.text())


def _with_browse(edit: QLineEdit, on_browse) -> QWidget:
    w = QWidget()
    row = QHBoxLayout(w)
    row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(edit)
    b = QPushButton("Browse…")
    b.clicked.connect(on_browse)
    row.addWidget(b)
    return w


# --- details panel -----------------------------------------------------------------


class DetailPanel(QWidget):
    def __init__(self, window: MainWindow) -> None:
        super().__init__()
        self.window = window
        self.game_id: str | None = None
        self.setMinimumWidth(360)
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._content: QWidget | None = None
        self.locations: QListWidget | None = None
        self.show_game(None)

    def _clear(self) -> None:
        """Replace the whole content widget, so nothing from the last game lingers."""
        if self._content is not None:
            self._content.hide()
            self._content.setParent(None)
            self._content.deleteLater()
        self._content = QWidget()
        self.layout_ = QVBoxLayout(self._content)
        self.layout_.setContentsMargins(16, 16, 16, 16)
        self.layout_.setSpacing(12)
        self._outer.addWidget(self._content)
        # Show it now rather than on Qt's next pass, so there's no empty frame
        self._content.show()
        self.locations = None

    def show_game(self, game_id: str | None) -> None:
        self._clear()
        state = self.window.state
        game = None
        if game_id:
            try:
                game = state.lib.game(game_id)
            except LibraryError:
                game = None
        self.game_id = game.id if game else None
        if game is None:
            msg = QLabel("Select a game to see its config files and setups.")
            msg.setObjectName("Hint")
            msg.setWordWrap(True)
            msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.layout_.addStretch()
            self.layout_.addWidget(msg)
            self.layout_.addStretch()
            return

        # Header: poster, name, source, edit/remove
        header = QHBoxLayout()
        poster = QLabel()
        poster.setPixmap(self.window.posters.card(game.name, state.art(game), 0.5,
                                                  self.devicePixelRatioF()))
        header.addWidget(poster, 0, Qt.AlignmentFlag.AlignTop)
        info = QVBoxLayout()
        title = QLabel(game.name)
        title.setObjectName("Title")
        title.setWordWrap(True)
        info.addWidget(title)
        source = source_label(game)
        sub = QLabel(source)
        sub.setObjectName("Hint")
        info.addWidget(sub)
        buttons = QHBoxLayout()
        edit = QPushButton("Edit…")
        edit.clicked.connect(lambda: self.window.edit_game(game))
        remove = QPushButton("Remove")
        remove.clicked.connect(lambda: self.window.remove_game(game))
        buttons.addWidget(edit)
        buttons.addWidget(remove)
        buttons.addStretch()
        info.addLayout(buttons)
        info.addStretch()
        header.addLayout(info, 1)
        self.layout_.addLayout(header)

        # Setups
        setups_box = QGroupBox("Setups")
        grid = QGridLayout(setups_box)
        grid.setColumnStretch(0, 1)
        grid.setVerticalSpacing(12)
        if not state.lib.setups:
            grid.addWidget(_hint("Add a setup at the top first, e.g. Handheld."), 0, 0, 1, 2)
        active = state.lib.settings.active_setup_id
        for row, setup in enumerate(state.lib.setups):
            snap_state, saved_at = state.snapshot_state(game, setup)
            tags = [t for t, on in (("active", setup.id == active), ("locked", setup.locked)) if on]
            name = QLabel(setup.name + (f"  ·  {' · '.join(tags)}" if tags else ""))
            if setup.id == active:
                name.setObjectName("Strong")
            if setup.locked:
                name.setToolTip("Locked: switching away never saves over this setup's configs")
            changed = (snap_state == SnapState.SAVED and setup.id == active
                       and bool(state.changed_files(game, setup)))
            if changed:
                status = _status("Changed since saved", "warn")
                status.setToolTip("The current config files differ from the saved copy. "
                                  + ("Restore saved puts the saved copy back."
                                     if setup.locked else
                                     f"They're saved into {setup.name} when you switch away."))
            elif snap_state == SnapState.SAVED:
                status = _status(f"Saved {saved_at.astimezone():%d %b, %H:%M}", "ok")
                status.setToolTip(f"{saved_at.astimezone():%A %d %B %Y, %H:%M}")
            elif snap_state == SnapState.STALE:
                status = _status("Out of date: config files changed", "warn")
            else:
                status = _status("Not saved", "none")
            buttons = QWidget()
            brow = QHBoxLayout(buttons)
            brow.setContentsMargins(0, 0, 0, 0)
            if changed:
                restore = QPushButton("Restore saved")
                restore.setToolTip(f"Put {game.name}'s saved {setup.name} config back (undoable)")
                restore.clicked.connect(lambda _=False, s=setup: self.window.restore(game, s))
                brow.addWidget(restore)
            save = QPushButton("Save current")
            save.setToolTip(f"Copy {game.name}'s current config into {setup.name}")
            save.setEnabled(bool(game.config_locations))
            save.clicked.connect(lambda _=False, s=setup: self.window.save_snapshot(game, s))
            brow.addWidget(save)
            for b in buttons.findChildren(QPushButton):
                b.setMinimumWidth(b.sizeHint().width())
            info = QWidget()
            col = QVBoxLayout(info)
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(2)
            name.setWordWrap(True)
            status.setWordWrap(True)
            col.addWidget(name)
            col.addWidget(status)
            grid.addWidget(info, row, 0)
            grid.addWidget(buttons, row, 1, Qt.AlignmentFlag.AlignRight)
        self.layout_.addWidget(setups_box)

        # Config locations
        loc_box = QGroupBox("Config files and folders")
        v = QVBoxLayout(loc_box)
        if not game.config_locations:
            v.addWidget(_hint("Use Find automatically, or add the file or folder where this game "
                              "keeps its graphics settings."))
        self.locations = QListWidget()
        self.locations.setObjectName("Locations")
        self.locations.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        metrics = self.locations.fontMetrics()
        for loc in game.config_locations:
            shown = metrics.elidedText(_short(loc.path), Qt.TextElideMode.ElideMiddle, 300)
            icon = {LocationKind.DIRECTORY: "📁 ",
                    LocationKind.REGISTRY: "🔑 "}.get(loc.kind, "📄 ")
            text = icon + shown
            extra = []
            if loc.include:
                extra.append("only " + ", ".join(loc.include))
            if loc.exclude:
                extra.append("not " + ", ".join(loc.exclude))
            if loc.kind != LocationKind.REGISTRY and not Path(loc.path).exists():
                extra.append("doesn't exist yet")
            if extra:
                text += "\n    " + " · ".join(extra)
            item = QListWidgetItem(text)
            item.setToolTip(loc.path)
            self.locations.addItem(item)
        self.locations.setVisible(bool(game.config_locations))
        rows = sum(1 + (self.locations.item(i).text().count("\n"))
                   for i in range(self.locations.count()))
        self.locations.setFixedHeight(min(14 + 22 * rows + 12 * len(game.config_locations), 220))
        v.addWidget(self.locations)
        row = QHBoxLayout()
        add_file = QPushButton("Add file…")
        add_file.clicked.connect(lambda: self.window.add_location(game, LocationKind.FILE))
        add_dir = QPushButton("Add folder…")
        add_dir.clicked.connect(lambda: self.window.add_location(game, LocationKind.DIRECTORY))
        patterns = QPushButton("Choose files…")
        add_reg = QPushButton("Add registry key…")
        add_reg.setToolTip("For games that keep settings in the Windows registry (common for "
                           "Unity games), including inside a Proton or Wine prefix")
        add_reg.clicked.connect(lambda: self.window.add_registry_key(game))
        patterns.setToolTip("Choose which files in a folder belong to the config")
        remove_loc = QPushButton("Remove")
        find = QPushButton("Find automatically")
        find.setObjectName("Primary")
        find.setToolTip("Look this game up in the game config database")
        find.clicked.connect(lambda: self.window.find_config(game))
        for b in (find, add_file, add_dir):
            row.addWidget(b)
        row.addStretch()
        v.addLayout(row)
        row2 = QHBoxLayout()
        learn = QPushButton("Learn…")
        learn.setToolTip("Find the settings by watching what changes while you change one in "
                         "the game")
        learn.clicked.connect(lambda: self.window.learn(game))
        for b in (add_reg, learn, patterns, remove_loc):
            row2.addWidget(b)
        row2.addStretch()
        v.addLayout(row2)

        def selected_index() -> int:
            return self.locations.currentRow() if self.locations else -1

        def update_buttons() -> None:
            i = selected_index()
            remove_loc.setEnabled(i >= 0)
            kind = game.config_locations[i].kind if i >= 0 else None
            patterns.setEnabled(kind in (LocationKind.DIRECTORY, LocationKind.REGISTRY))
            patterns.setText("Choose values…" if kind == LocationKind.REGISTRY
                             else "Choose files…")

        self.locations.currentRowChanged.connect(lambda _: update_buttons())
        patterns.clicked.connect(lambda: self.window.edit_patterns(game, selected_index()))
        remove_loc.clicked.connect(lambda: self.window.remove_location(game, selected_index()))
        update_buttons()
        self.layout_.addWidget(loc_box)

        # Running check
        run_box = QGroupBox("Running check")
        rv = QVBoxLayout(run_box)
        if processes.can_check(game):
            parts = []
            if game.install_dir:
                parts.append(f"Install folder: {_short(game.install_dir)}")
            if game.executables:
                parts.append("Executables: " + ", ".join(game.executables))
            label = QLabel("\n".join(parts))
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            rv.addWidget(label)
        else:
            rv.addWidget(_status("Can't tell when this game is running. Use Edit… to add its "
                                 "executable.", "warn", wrap=True))
        self.layout_.addWidget(run_box)
        self.layout_.addStretch()


def _short(path: str) -> str:
    """Show the home folder as ~ to keep paths readable."""
    home = str(Path.home())
    return "~" + path[len(home):] if path.startswith(home) else path


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Hint")
    label.setWordWrap(True)
    return label


def _status(text: str, kind: str, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet(f"color: {STATUS_COLORS[kind].name()};")
    label.setWordWrap(wrap)
    return label


# --- background download ---------------------------------------------------------------


class ManifestWorker(QThread):
    progress = Signal(int, int)
    done = Signal(str)  # "" on success, else the error

    def __init__(self, root: Path, parent: QWidget) -> None:
        super().__init__(parent)
        self.root = root

    def run(self) -> None:
        from rigswap.core import manifest

        try:
            manifest.update(self.root, progress=lambda d, t: self.progress.emit(d, t or 0))
        except Exception as e:  # reported in the window
            self.done.emit(str(e) or type(e).__name__)
        else:
            self.done.emit("")


# --- main window -------------------------------------------------------------------


class MainWindow(QMainWindow):
    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state
        self.posters = PosterCache()
        self.setWindowTitle("RigSwap")
        self.setWindowIcon(QIcon(icon_path("app.png")))
        self.resize(1180, 760)

        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_header())

        splitter = QSplitter()
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(16, 12, 8, 12)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search games")
        self.search.setClearButtonEnabled(True)
        lv.addWidget(self.search)

        self.model = QStandardItemModel(self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.proxy.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.search.textChanged.connect(self.proxy.setFilterFixedString)

        self.grid = QListView()
        self.grid.setObjectName("Grid")
        self.grid.setModel(self.proxy)
        self.grid.setViewMode(QListView.ViewMode.IconMode)
        self.grid.setResizeMode(QListView.ResizeMode.Adjust)
        self.grid.setMovement(QListView.Movement.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setSpacing(6)
        self.grid.setMouseTracking(True)
        self.grid.setItemDelegate(CardDelegate(self))
        self.grid.selectionModel().currentChanged.connect(self._on_current_changed)

        self.empty = QWidget()
        ev = QVBoxLayout(self.empty)
        ev.addStretch()
        title = QLabel("No games yet")
        title.setObjectName("Title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ev.addWidget(title)
        ev.addWidget(_hint("Import games from Steam, Epic, GOG, Heroic or Lutris, or add any "
                           "game by hand."),
                     0, Qt.AlignmentFlag.AlignCenter)
        eb = QHBoxLayout()
        eb.addStretch()
        b1 = QPushButton("Import games")
        b1.setObjectName("Primary")
        b1.clicked.connect(self.import_games)
        b2 = QPushButton("Add game…")
        b2.clicked.connect(self.add_game)
        eb.addWidget(b1)
        eb.addWidget(b2)
        eb.addStretch()
        ev.addLayout(eb)
        ev.addStretch()

        self.stack = QStackedWidget()
        self.stack.addWidget(self.empty)
        self.stack.addWidget(self.grid)
        lv.addWidget(self.stack, 1)
        splitter.addWidget(left)

        self.detail = DetailPanel(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.detail)
        scroll.setObjectName("DetailScroll")
        splitter.addWidget(scroll)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([700, 460])
        outer.addWidget(splitter, 1)
        self.setCentralWidget(central)
        self.statusBar().showMessage(f"RigSwap {__version__} · data in {_short(str(state.root))}")

        # automatic switching
        self.read_hardware = hardware.read_state  # tests replace this
        self.switcher = auto.AutoSwitcher(lambda: self.state.lib, self.state.switch,
                                          lambda: self.read_hardware())
        self.auto_timer = QTimer(self)
        self.auto_timer.setInterval(3000)
        self.auto_timer.timeout.connect(self.auto_tick)
        self.tray: QSystemTrayIcon | None = None
        self._told_about_tray = False
        self._quitting = False
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(QIcon(icon_path("app.png")), self)
            self.tray.setToolTip("RigSwap")
            self.tray.activated.connect(self._tray_activated)

        state.changed.connect(self.refresh)
        self.refresh()
        QTimer.singleShot(0, self.startup_check)

    # --- building ---------------------------------------------------------------

    def _build_header(self) -> QWidget:
        header = QWidget()
        header.setObjectName("Header")
        row = QHBoxLayout(header)
        row.setContentsMargins(16, 10, 16, 10)
        logo = QLabel()
        logo.setPixmap(QIcon(icon_path("app.png")).pixmap(28, 28))
        row.addWidget(logo)
        label = QLabel("Setup")
        label.setObjectName("Hint")
        row.addWidget(label)
        self.setup_row = QHBoxLayout()
        self.setup_row.setSpacing(6)
        row.addLayout(self.setup_row)
        add = QPushButton("+")
        add.setObjectName("Round")
        add.setToolTip("Add a setup")
        add.clicked.connect(self.add_setup)
        row.addWidget(add)
        row.addStretch()
        self.auto_button = QPushButton("Auto-switch")
        self.auto_button.setObjectName("AutoToggle")
        self.auto_button.setCheckable(True)
        self.auto_button.setToolTip("Switch setups automatically when the hardware changes "
                                    "(right-click a setup to set when it's used)")
        self.auto_button.clicked.connect(lambda checked: self.set_auto_switch(checked))
        row.addWidget(self.auto_button)
        self.undo_button = QPushButton("Undo switch")
        self.undo_button.clicked.connect(self.undo)
        row.addWidget(self.undo_button)
        imp = QPushButton("Import games")
        imp.setToolTip("Find installed games from Steam, Epic, GOG, Heroic and Lutris")
        imp.clicked.connect(self.import_games)
        row.addWidget(imp)
        add_game = QPushButton("Add game…")
        add_game.setObjectName("Primary")
        add_game.clicked.connect(self.add_game)
        row.addWidget(add_game)
        return header

    def refresh(self) -> None:
        # setup buttons
        while self.setup_row.count():
            w = self.setup_row.takeAt(0).widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        active = self.state.lib.settings.active_setup_id
        if not self.state.lib.setups:
            self.setup_row.addWidget(_hint("none yet: add one, e.g. Handheld →"))
        for setup in self.state.lib.setups:
            b = QPushButton(setup.name)
            b.setObjectName("SetupButton")
            b.setCheckable(True)
            b.setChecked(setup.id == active)
            tip = ("Active setup" if setup.id == active
                   else f"Switch every game to its {setup.name} config")
            if not setup.when.is_empty():
                tip += f"\nUsed automatically when {setup.when.describe()}"
            b.setToolTip(tip + "\nRight-click for more")
            b.clicked.connect(lambda _=False, s=setup: self.switch(s))
            b.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            b.customContextMenuRequested.connect(
                lambda pos, s=setup, btn=b: self._setup_menu(s, btn.mapToGlobal(pos)))
            self.setup_row.addWidget(b)
            b.show()
        self.undo_button.setEnabled(self.state.can_undo())
        self._sync_auto()

        # games
        current = self.detail.game_id
        self.model.clear()
        for game in self.state.lib.games:
            item = QStandardItem(game.name)
            item.setData(game.id, GAME_ID_ROLE)
            item.setData(game_status(self.state, game), STATUS_ROLE)
            item.setEditable(False)
            self.model.appendRow(item)
        self.proxy.sort(0)
        self.stack.setCurrentWidget(self.grid if self.state.lib.games else self.empty)
        self.select_game(current)

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Coming back from a game: config files may have changed, so refresh."""
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self.refresh()

    def select_game(self, game_id: str | None) -> None:
        if game_id:
            for row in range(self.proxy.rowCount()):
                idx = self.proxy.index(row, 0)
                if idx.data(GAME_ID_ROLE) == game_id:
                    self.grid.setCurrentIndex(idx)
                    break
        self.detail.show_game(game_id)

    def _on_current_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        gid = current.data(GAME_ID_ROLE) if current.isValid() else None
        if gid != self.detail.game_id:
            self.detail.show_game(gid)

    def _setup_menu(self, setup: Setup, pos) -> None:
        menu = QMenu(self)
        save_all = QAction(f"Save every game's current config for {setup.name}", menu)
        save_all.triggered.connect(lambda: self.save_all(setup))
        rename = QAction("Rename…", menu)
        rename.triggered.connect(lambda: self.rename_setup(setup))
        lock = QAction("Unlock" if setup.locked else "Lock (never save over it when switching)",
                       menu)
        lock.triggered.connect(lambda: self.set_locked(setup, not setup.locked))
        when = QAction("Use automatically when…", menu)
        when.triggered.connect(lambda: self.edit_conditions(setup))
        index = self.state.lib.setups.index(setup)
        left = QAction("Move left (checked earlier)", menu)
        left.setEnabled(index > 0)
        left.triggered.connect(lambda: self._try("Move setup",
                                                 lambda: self.state.move_setup(setup, -1)))
        right = QAction("Move right (checked later)", menu)
        right.setEnabled(index < len(self.state.lib.setups) - 1)
        right.triggered.connect(lambda: self._try("Move setup",
                                                  lambda: self.state.move_setup(setup, 1)))
        remove = QAction("Remove…", menu)
        remove.triggered.connect(lambda: self.remove_setup(setup))
        menu.addActions([when, save_all, lock])
        menu.addSeparator()
        menu.addActions([left, right, rename, remove])
        menu.exec(pos)

    # --- prompts (tests replace these) -----------------------------------------------

    def ask(self, title: str, text: str) -> bool:
        return QMessageBox.question(self, title, text) == QMessageBox.StandardButton.Yes

    def inform(self, title: str, text: str) -> None:
        QMessageBox.information(self, title, text)

    def warn(self, title: str, text: str) -> None:
        QMessageBox.warning(self, title, text)

    def get_text(self, title: str, label: str, default: str = "") -> str | None:
        text, ok = QInputDialog.getText(self, title, label, text=default)
        return text.strip() if ok and text.strip() else None

    def pick_path(self, kind: LocationKind, title: str) -> str | None:
        if kind == LocationKind.DIRECTORY:
            path = QFileDialog.getExistingDirectory(self, title)
        else:
            path, _ = QFileDialog.getOpenFileName(self, title)
        return path or None

    def run_dialog(self, dialog: QDialog) -> bool:
        return dialog.exec() == QDialog.DialogCode.Accepted

    def _try(self, title: str, fn) -> tuple[bool, object]:
        """Run fn; on a known error, explain it and return (False, None)."""
        try:
            return True, fn()
        except swap.GameRunningError as e:
            names = "\n".join(f"• {g.name}" for g in e.games)
            self.warn(title, f"Nothing was changed. Close these games first:\n\n{names}")
        except (LibraryError, swap.SwapError) as e:
            self.warn(title, str(e))
        except OSError as e:
            self.warn(title, f"Nothing was changed (any partial changes were rolled back).\n\n{e}")
        return False, None

    # --- actions ----------------------------------------------------------------------

    def add_setup(self) -> None:
        suggestion = "Handheld" if not self.state.lib.setups else (
            "eGPU Dock" if len(self.state.lib.setups) == 1 else "")
        name = self.get_text("Add setup", "Name of the hardware setup:", suggestion)
        if name:
            self._try("Add setup", lambda: self.state.add_setup(name))

    def rename_setup(self, setup: Setup) -> None:
        name = self.get_text("Rename setup", "New name:", setup.name)
        if name:
            self._try("Rename setup", lambda: self.state.rename_setup(setup, name))

    def remove_setup(self, setup: Setup) -> None:
        saved = sum(1 for s in self.state.lib.snapshots if s.setup_id == setup.id)
        extra = f" Its saved configs for {saved} game(s) will be deleted." if saved else ""
        if self.ask("Remove setup", f"Remove {setup.name}?{extra} Your live config files "
                    "aren't touched."):
            self._try("Remove setup", lambda: self.state.remove_setup(setup))

    def switch(self, setup: Setup) -> None:
        if setup.id == self.state.lib.settings.active_setup_id:
            self.refresh()  # keep the button checked
            return
        ok, result = self._try(f"Switch to {setup.name}", lambda: self.state.switch(setup))
        if not ok:
            self.refresh()
            return
        msg = f"Switched to {setup.name}: {len(result.applied)} game(s) updated."
        self.statusBar().showMessage(msg, 8000)
        notes = []
        if result.stale:
            notes.append("Skipped because their config files changed since they were saved "
                         "(save them again):\n" + "\n".join(f"• {g.name}" for g in result.stale))
        if result.unchecked:
            notes.append("Couldn't check whether these were running (add their executable):\n"
                         + "\n".join(f"• {g.name}" for g in result.unchecked))
        if notes:
            self.inform(f"Switched to {setup.name}", msg + "\n\n" + "\n\n".join(notes))

    def undo(self) -> None:
        ok, result = self._try("Undo", self.state.undo)
        if ok and result is None:
            self.statusBar().showMessage("Nothing to undo.", 6000)
        elif ok:
            active = self.state.active_setup()
            self.statusBar().showMessage(
                f"Undone: {len(result.restored)} game(s) restored. Active setup: "
                f"{active.name if active else 'none'}", 8000)

    def import_steam(self) -> None:
        """Kept for compatibility: imports from every launcher."""
        self.import_games()

    def import_games(self) -> None:
        ok, found = self._try("Import games", self.state.discover)
        if not ok:
            return
        if not found:
            self.inform("Import games", "No installed games were found from Steam, Epic, GOG, "
                        "Heroic or Lutris. Add games by hand with Add game….")
            return
        from rigswap import launchers

        dialog = ImportDialog(self, found, lambda g: launchers.is_in_library(self.state.lib, g))
        if not self.run_dialog(dialog):
            return
        chosen = dialog.chosen()
        ok, added = self._try("Import games", lambda: self.state.import_found(chosen))
        if not ok:
            return
        self.statusBar().showMessage(f"Imported {len(added)} game(s).", 8000)
        if added:
            new_ids = [g.id for g in added]
            self.with_manifest(lambda: self._auto_detect(new_ids), asked_by_user=False)

    def _auto_detect(self, game_ids: list[str]) -> None:
        games = [g for g in self.state.lib.games if g.id in game_ids]
        ok, added = self._try("Find config files", lambda: self.state.auto_detect(games))
        if ok:
            n = sum(1 for v in added.values() if v)
            self.statusBar().showMessage(
                f"Found config files for {n} of {len(games)} new game(s). Games without them "
                "need Find automatically or Add file…", 10000)

    # --- the game config database ------------------------------------------------------

    def with_manifest(self, then, *, asked_by_user: bool) -> None:
        """Run `then` with the game database if it's wanted, downloading it first if needed.

        Without it, `then` still runs: engine rules find many games' settings on their own.
        """
        use = self.state.lib.settings.use_manifest
        if self.state.manifest() is not None and use is not False:
            then()
            return
        if use is False and not asked_by_user:
            then()
            return
        if not self.ask("Find config files automatically",
                        "RigSwap can download a free, community-maintained database of "
                        "where games keep their settings (about 17 MB, refreshed when it "
                        "changes). Without it, RigSwap still recognises Unreal, Unity and "
                        "Source games by itself.\n\nDownload it now?"):
            if use is None:
                self._try("Settings", lambda: self.state.set_use_manifest(False))
            then()
            return
        if use is not True:
            self._try("Settings", lambda: self.state.set_use_manifest(True))
        self.download_manifest(then)

    def download_manifest(self, then) -> None:
        """Download in the background; tests replace this."""
        if getattr(self, "_manifest_worker", None) is not None:
            return
        worker = ManifestWorker(self.state.root, self)
        self._manifest_worker = worker

        def progress(done: int, total: int) -> None:
            pct = f" {done * 100 // total}%" if total else ""
            self.statusBar().showMessage(f"Downloading the game config database…{pct}")

        def finished(error: str) -> None:
            self._manifest_worker = None
            if error:
                self.warn("Game config database", f"Couldn't download it: {error}")
                return
            self.state.manifest_downloaded()
            self.statusBar().showMessage("Game config database ready.", 6000)
            then()

        worker.progress.connect(progress)
        worker.done.connect(finished)
        worker.start()

    def find_config(self, game: Game) -> None:
        self.with_manifest(lambda: self._find_config(game.id), asked_by_user=True)

    def _find_config(self, game_id: str) -> None:
        try:
            game = self.state.lib.game(game_id)
        except LibraryError:
            return
        found = self.state.detect(game)
        title = f"{game.name}: config files"
        if found is None:
            where = ("isn't in the game config database, and " if self.state.usable_manifest()
                     else "")
            self.inform(title, f"{game.name} {where}doesn't use an engine RigSwap recognises. "
                        "Try Learn…: it finds the settings by watching what changes while you "
                        "change one in the game.")
            return
        from rigswap.core import detect as detect_mod

        new = detect_mod.new_locations(game, found)
        if new:
            self._try(title, lambda: self.state.add_locations(game, new))
        lines = []
        if new:
            lines.append("Added:\n" + "\n".join(f"• {_short(loc.path)}" for loc in new))
        elif found.found:
            lines.append("Everything RigSwap found is already added.")
        if found.missing:
            lines.append("Not there yet (launch the game once, then try again):\n"
                         + "\n".join(f"• {_short(p)}" for p in found.missing))
        if found.notes:
            lines.extend(found.notes)
        if found.registry:
            lines.append("Some settings are kept in the Windows registry, which can't be "
                         "swapped on this computer:\n"
                         + "\n".join(f"• {k}" for k in found.registry))
        if not lines and not found.mixed and not found.mixed_registry:
            lines.append("The database has no config files for this game on this system.")
        if lines:
            via = detect_mod.sources(found)
            self.inform(title, (f"Found using {via}.\n\n" if via else "") + "\n\n".join(lines))
        for path in found.mixed:
            if any(os.path.normcase(loc.path) == os.path.normcase(path)
                   for loc in game.config_locations):
                continue
            if self.ask(title, f"This folder holds settings and saves together:\n\n"
                        f"{_short(path)}\n\nAdd it anyway? You'll choose which files count, "
                        "so saves can be left out."):
                loc = ConfigLocation(path, LocationKind.DIRECTORY)
                if self._try(title, lambda loc=loc: self.state.add_locations(game, [loc]))[0]:
                    self.edit_patterns(game, len(game.config_locations) - 1)
        for key in found.mixed_registry:
            if any(loc.path.casefold() == key.casefold() for loc in game.config_locations):
                continue
            if self.ask(title, f"This registry key holds settings and saves together:\n\n"
                        f"{key}\n\nAdd it anyway? You'll choose which values count, so "
                        "saves can be left out."):
                loc = ConfigLocation(key, LocationKind.REGISTRY)
                if self._try(title, lambda loc=loc: self.state.add_locations(game, [loc]))[0]:
                    self.edit_patterns(game, len(game.config_locations) - 1)

    # --- locked setups, restore, startup check --------------------------------------

    def set_locked(self, setup: Setup, locked: bool) -> None:
        if self._try("Lock setup", lambda: self.state.set_locked(setup, locked))[0]:
            self.statusBar().showMessage(
                f"{setup.name} is locked: switching away won't save over it." if locked
                else f"{setup.name} is unlocked.", 6000)

    def restore(self, game: Game, setup: Setup) -> None:
        if not self.ask("Restore saved config",
                        f"Put {game.name}'s saved {setup.name} config back? Its current files "
                        "are backed up, and Undo reverses this."):
            return
        if self._try("Restore saved config", lambda: self.state.restore([game], setup))[0]:
            self.statusBar().showMessage(f"Restored {game.name}'s {setup.name} config.", 6000)

    def startup_check(self) -> None:
        setup, games = self.state.locked_drift()
        if not games:
            return
        names = "\n".join(f"• {g.name}" for g in games)
        if self.ask("Configs changed",
                    f"{setup.name} is locked, but these games' config files changed while "
                    f"RigSwap was closed (a game update or cloud sync can do this):\n\n"
                    f"{names}\n\nPut the saved {setup.name} configs back? "
                    "Undo reverses this."):
            self._try("Restore saved configs", lambda: self.state.restore(games, setup))

    def add_game(self) -> None:
        dialog = GameDialog(self)
        if not self.run_dialog(dialog):
            return
        game = Game(name="")
        dialog.apply_to(game)
        if self._try("Add game", lambda: self.state.add_game(game))[0]:
            self.select_game(game.id)

    def edit_game(self, game: Game) -> None:
        dialog = GameDialog(self, game)
        if self.run_dialog(dialog):
            self._try("Edit game", lambda: self.state.update_game(game, dialog.apply_to))

    def remove_game(self, game: Game) -> None:
        if self.ask("Remove game", f"Remove {game.name} from RigSwap? Its saved configs are "
                    "deleted; the game and its live config files aren't touched."):
            self._try("Remove game", lambda: self.state.remove_game(game))

    def add_location(self, game: Game, kind: LocationKind) -> None:
        word = "folder" if kind == LocationKind.DIRECTORY else "file"
        path = self.pick_path(kind, f"{game.name}: choose a config {word}")
        if not path:
            return
        loc = ConfigLocation(str(Path(path)), kind)
        ok, _ = self._try("Add config location", lambda: self.state.add_location(game, loc))
        if ok and kind == LocationKind.DIRECTORY:
            self.edit_patterns(game, len(game.config_locations) - 1)

    def learn(self, game: Game) -> None:
        from rigswap.core import learn as learn_mod

        dialog = LearnDialog(self, game, lambda: learn_mod.scan(game),
                             lambda before, after: learn_mod.suggestions(game, before, after))
        if not self.run_dialog(dialog):
            return
        chosen = dialog.chosen()
        if chosen and self._try("Learn", lambda: self.state.add_locations(game, chosen))[0]:
            self.statusBar().showMessage(
                f"Added {len(chosen)} config location(s) to {game.name}. Save each setup to "
                "start using them.", 8000)

    def add_registry_key(self, game: Game) -> None:
        from rigswap.core import detect, registry

        if detect.registry_backend(game) is None:
            self.inform("Add registry key", f"{game.name} has no registry on this computer. "
                        "Registry keys work on Windows, or for games that run in a Proton or "
                        "Wine prefix.")
            return
        text = self.get_text("Add registry key", "Registry key, e.g. "
                             "HKEY_CURRENT_USER\\Software\\Company\\Game:")
        if not text:
            return
        try:
            key = registry.normalize_key(text)
        except registry.RegistryError as e:
            self.warn("Add registry key", str(e))
            return
        loc = ConfigLocation(key, LocationKind.REGISTRY)
        if self._try("Add registry key", lambda: self.state.add_location(game, loc))[0]:
            self.edit_patterns(game, len(game.config_locations) - 1)

    def edit_patterns(self, game: Game, index: int) -> None:
        if not 0 <= index < len(game.config_locations):
            return
        dialog = PatternsDialog(self, game.config_locations[index])
        if self.run_dialog(dialog):
            include, exclude = dialog.patterns()
            self._try("Config files",
                      lambda: self.state.set_patterns(game, index, include, exclude))

    def remove_location(self, game: Game, index: int) -> None:
        if not 0 <= index < len(game.config_locations):
            return
        path = game.config_locations[index].path
        if self.ask("Remove config location", f"Stop managing {path}? The file itself isn't "
                    "touched."):
            self._try("Remove config location", lambda: self.state.remove_location(game, index))

    def save_snapshot(self, game: Game, setup: Setup) -> None:
        if self.state.lib.snapshot(game.id, setup.id) and not self.ask(
                "Save config", f"Replace {game.name}'s saved config for {setup.name} with "
                "its current one?"):
            return
        if self._try("Save config", lambda: self.state.save_snapshot(game, setup))[0]:
            self.statusBar().showMessage(f"Saved {game.name} for {setup.name}.", 6000)

    def save_all(self, setup: Setup) -> None:
        games = [g for g in self.state.lib.games if g.config_locations]
        if not games:
            self.inform("Save configs", "No games have config files yet.")
            return
        if not self.ask("Save configs", f"Save the current config of {len(games)} game(s) as "
                        f"their {setup.name} config?"):
            return
        for g in games:
            if not self._try("Save configs", lambda g=g: self.state.save_snapshot(g, setup))[0]:
                return
        self.statusBar().showMessage(f"Saved {len(games)} game(s) for {setup.name}.", 6000)

    # --- automatic switching, tray -------------------------------------------------

    def edit_conditions(self, setup: Setup) -> None:
        try:
            state = self.read_hardware()
        except Exception:
            state = hardware.HardwareState()
        dialog = ConditionsDialog(self, setup, state)
        if self.run_dialog(dialog):
            when = dialog.conditions()
            if self._try("Automatic switching", lambda: self.state.set_conditions(setup, when))[0]:
                self.statusBar().showMessage(
                    f"{setup.name}: used automatically when {when.describe()}.", 8000)
                if not when.is_empty() and not self.state.lib.settings.auto_switch and self.ask(
                        "Automatic switching", "Turn on automatic switching now?"):
                    self.set_auto_switch(True)

    def set_auto_switch(self, on: bool) -> None:
        if on and not any(not s.when.is_empty() for s in self.state.lib.setups):
            self.inform("Automatic switching",
                        "First tell RigSwap when to use a setup: right-click a setup at the "
                        "top and choose \"Use automatically when…\".")
            self.refresh()
            return
        if self._try("Automatic switching", lambda: self.state.set_auto_switch(on))[0]:
            self.statusBar().showMessage(
                "Automatic switching is on: RigSwap checks the hardware every few seconds."
                if on else "Automatic switching is off.", 8000)
            if on:
                self.auto_tick()
                if not self.autostart_enabled() and self.ask(
                        "Start at login", "Start RigSwap in the tray when you log in, so "
                        "automatic switching always works?"):
                    self.set_autostart(True)

    def _sync_auto(self) -> None:
        on = self.state.lib.settings.auto_switch
        self.auto_button.setChecked(on)
        if on and not self.auto_timer.isActive():
            self.auto_timer.start()
        elif not on and self.auto_timer.isActive():
            self.auto_timer.stop()
        if self.tray is not None:
            self.tray.setContextMenu(self._tray_menu())
            self.tray.setVisible(on)

    def auto_tick(self) -> None:
        try:
            event = self.switcher.tick()
        except Exception as e:  # never let a hardware read error kill the timer
            self.statusBar().showMessage(f"Couldn't read the hardware: {e}", 8000)
            return
        if isinstance(event, auto.Switched):
            n = len(event.result.applied)
            self.notify(f"Switched to {event.setup.name}",
                        f"The hardware changed, so {n} game(s) now use their "
                        f"{event.setup.name} settings.")
        elif isinstance(event, auto.Waiting):
            names = ", ".join(g.name for g in event.games)
            self.notify(f"Switching to {event.setup.name} soon",
                        f"RigSwap will switch when you close {names}.")
        elif isinstance(event, auto.Failed):
            self.notify(f"Couldn't switch to {event.setup.name}", str(event.error))

    def notify(self, title: str, message: str) -> None:
        self.statusBar().showMessage(f"{title}: {message}", 10000)
        if self.tray is not None and self.tray.isVisible() and not self.isActiveWindow():
            self.tray.showMessage(title, message, QIcon(icon_path("app.png")), 6000)

    def _tray_menu(self) -> QMenu:
        menu = QMenu(self)
        open_action = QAction("Open RigSwap", menu)
        open_action.triggered.connect(self._show_window)
        menu.addAction(open_action)
        menu.addSeparator()
        active = self.state.lib.settings.active_setup_id
        for setup in self.state.lib.setups:
            a = QAction(setup.name, menu)
            a.setCheckable(True)
            a.setChecked(setup.id == active)
            a.triggered.connect(lambda _=False, s=setup: self.switch(s))
            menu.addAction(a)
        menu.addSeparator()
        auto_action = QAction("Auto-switch", menu)
        auto_action.setCheckable(True)
        auto_action.setChecked(self.state.lib.settings.auto_switch)
        auto_action.triggered.connect(lambda checked: self.set_auto_switch(checked))
        menu.addAction(auto_action)
        login_action = QAction("Start at login", menu)
        login_action.setCheckable(True)
        login_action.setChecked(self.autostart_enabled())
        login_action.triggered.connect(lambda checked: self.set_autostart(checked))
        menu.addAction(login_action)
        quit_action = QAction("Quit RigSwap", menu)
        quit_action.triggered.connect(self.quit_app)
        menu.addAction(quit_action)
        return menu

    def _tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._show_window()

    def _show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_app(self) -> None:
        self._quitting = True
        QApplication.quit()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        """With auto-switch on, closing the window keeps RigSwap running in the tray."""
        keep_running = (self.state.lib.settings.auto_switch and self.tray is not None
                        and self.tray.isVisible() and not self._quitting)
        if keep_running:
            event.ignore()
            self.hide()
            if not self._told_about_tray:
                self._told_about_tray = True
                self.tray.showMessage("RigSwap is still running",
                                      "Automatic switching keeps working in the background. "
                                      "Use the tray icon to open RigSwap or quit.",
                                      QIcon(icon_path("app.png")), 6000)
            return
        event.accept()
        QApplication.quit()

    def autostart_enabled(self) -> bool:
        from rigswap.core import autostart

        try:
            return autostart.is_enabled()
        except Exception:
            return False

    def set_autostart(self, on: bool) -> None:
        from rigswap.core import autostart

        try:
            message = autostart.set_enabled(on)
        except Exception as e:
            self.warn("Start at login", f"Couldn't change it: {e}")
            return
        self.statusBar().showMessage(message, 8000)
        self._sync_auto()
