"""Main window: setup buttons on top, poster grid, details of the selected game."""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import QModelIndex, QRect, QSize, QSortFilterProxyModel, Qt
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
    QVBoxLayout,
    QWidget,
)

from rigswap import __version__
from rigswap.core import processes, swap
from rigswap.core.models import ConfigLocation, Game, LibraryError, LocationKind, Setup
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


def game_status(state: AppState, game: Game) -> tuple[str, str]:
    if not game.config_locations:
        return "No config files yet", "none"
    states = [state.snapshot_state(game, s)[0] for s in state.lib.setups]
    if not states:
        return "No setups yet", "none"
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


class PatternsDialog(QDialog):
    def __init__(self, parent: QWidget, loc: ConfigLocation) -> None:
        super().__init__(parent)
        self.setWindowTitle("Which files in this folder?")
        self.setMinimumWidth(480)
        form = QFormLayout(self)
        shown = html.escape(_short(loc.path))
        label = QLabel(f"<b>{shown}</b><br>Patterns are matched against each file's path "
                       "inside the folder, e.g. <code>*.ini</code> or <code>saves/*</code>. "
                       "Leave Include empty to take every file.")
        label.setWordWrap(True)
        form.addRow(label)
        self.include = QLineEdit(", ".join(loc.include))
        self.include.setPlaceholderText("all files")
        self.exclude = QLineEdit(", ".join(loc.exclude))
        self.exclude.setPlaceholderText("e.g. saves/*, *.sav")
        form.addRow("Include", self.include)
        form.addRow("Exclude", self.exclude)
        warn = QLabel("Exclude save files: anything included is put back to its saved "
                      "version on every switch.")
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
        source = f"Steam app {game.steam_app_id}" if game.steam_app_id else "Added by hand"
        if game.proton_prefix:
            source += " · Proton"
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
        grid.setColumnStretch(1, 1)
        if not state.lib.setups:
            grid.addWidget(_hint("Add a setup at the top first, e.g. Handheld."), 0, 0, 1, 3)
        active = state.lib.settings.active_setup_id
        for row, setup in enumerate(state.lib.setups):
            snap_state, saved_at = state.snapshot_state(game, setup)
            name = QLabel(setup.name + ("  (active)" if setup.id == active else ""))
            if setup.id == active:
                name.setObjectName("Strong")
            if snap_state == SnapState.SAVED:
                status = _status(f"Saved {saved_at.astimezone():%d %b, %H:%M}", "ok")
                status.setToolTip(f"{saved_at.astimezone():%A %d %B %Y, %H:%M}")
            elif snap_state == SnapState.STALE:
                status = _status("Out of date: config files changed", "warn")
            else:
                status = _status("Not saved", "none")
            save = QPushButton("Save current")
            save.setToolTip(f"Copy {game.name}'s current config into {setup.name}")
            save.setEnabled(bool(game.config_locations))
            save.clicked.connect(lambda _=False, s=setup: self.window.save_snapshot(game, s))
            grid.addWidget(name, row, 0)
            grid.addWidget(status, row, 1)
            grid.addWidget(save, row, 2)
        self.layout_.addWidget(setups_box)

        # Config locations
        loc_box = QGroupBox("Config files and folders")
        v = QVBoxLayout(loc_box)
        if not game.config_locations:
            v.addWidget(_hint("Add the file or folder where this game keeps its graphics "
                              "settings. PCGamingWiki lists it for most games."))
        self.locations = QListWidget()
        self.locations.setObjectName("Locations")
        self.locations.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        metrics = self.locations.fontMetrics()
        for loc in game.config_locations:
            shown = metrics.elidedText(_short(loc.path), Qt.TextElideMode.ElideMiddle, 300)
            text = ("📁 " if loc.kind == LocationKind.DIRECTORY else "📄 ") + shown
            extra = []
            if loc.include:
                extra.append("only " + ", ".join(loc.include))
            if loc.exclude:
                extra.append("not " + ", ".join(loc.exclude))
            if not Path(loc.path).exists():
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
        patterns = QPushButton("Files…")
        patterns.setToolTip("Choose which files in a folder belong to the config")
        remove_loc = QPushButton("Remove")
        for b in (add_file, add_dir, patterns, remove_loc):
            row.addWidget(b)
        row.addStretch()
        v.addLayout(row)

        def selected_index() -> int:
            return self.locations.currentRow() if self.locations else -1

        def update_buttons() -> None:
            i = selected_index()
            remove_loc.setEnabled(i >= 0)
            patterns.setEnabled(i >= 0 and game.config_locations[i].kind == LocationKind.DIRECTORY)

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
        ev.addWidget(_hint("Import your Steam games, or add any game by hand."),
                     0, Qt.AlignmentFlag.AlignCenter)
        eb = QHBoxLayout()
        eb.addStretch()
        b1 = QPushButton("Import Steam games")
        b1.setObjectName("Primary")
        b1.clicked.connect(self.import_steam)
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

        state.changed.connect(self.refresh)
        self.refresh()

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
        self.undo_button = QPushButton("Undo switch")
        self.undo_button.clicked.connect(self.undo)
        row.addWidget(self.undo_button)
        imp = QPushButton("Import Steam games")
        imp.clicked.connect(self.import_steam)
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
            b.setToolTip("Active setup" if setup.id == active
                         else f"Switch every game to its {setup.name} config")
            b.clicked.connect(lambda _=False, s=setup: self.switch(s))
            b.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            b.customContextMenuRequested.connect(
                lambda pos, s=setup, btn=b: self._setup_menu(s, btn.mapToGlobal(pos)))
            self.setup_row.addWidget(b)
        self.undo_button.setEnabled(self.state.can_undo())

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
        remove = QAction("Remove…", menu)
        remove.triggered.connect(lambda: self.remove_setup(setup))
        menu.addActions([save_all, rename, remove])
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
        ok, result = self._try("Import Steam games", self.state.import_steam)
        if not ok:
            return
        if not self.state.steam_roots:
            self.inform("Import Steam games", "No Steam install was found on this computer.")
            return
        self.statusBar().showMessage(f"Steam: {len(result.added)} new game(s), "
                                     f"{len(result.updated)} updated.", 8000)

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
