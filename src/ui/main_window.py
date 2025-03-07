from PyQt5.QtWidgets import QMainWindow, QLabel, QVBoxLayout, QWidget, QPushButton, QComboBox, QApplication, QFileDialog, QLineEdit, QListWidget, QHBoxLayout, QMessageBox, QInputDialog, QGridLayout, QScrollArea, QDialog, QDialogButtonBox, QFormLayout, QMenuBar, QAction, QToolButton, QMenu, QSpacerItem, QSizePolicy
from PyQt5.QtGui import QPixmap, QIcon, QTransform
from PyQt5.QtCore import Qt
import sys
import os
import shutil
from controllers.settings_controller import SettingsController
from utils.game_importer import import_games, get_default_paths

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("QP's Graphics Manager")
        self.setGeometry(100, 100, 800, 600)
        
        self.settings_controller = SettingsController(config_dir="configs")
        self.selected_game = None
        self.selected_label = None
        self.init_ui()
        self.apply_stylesheet()

    def init_ui(self):
        layout = QVBoxLayout()

        # Create a menu bar
        menu_bar = QMenuBar(self)
        self.setMenuBar(menu_bar)

        # Create a "File" menu
        file_menu = menu_bar.addMenu("File")

        # Add "Add Game" and "Import Games" actions to the "File" menu
        add_game_action = QAction(QIcon("icons/add.png"), "Add Game", self)
        add_game_action.triggered.connect(self.open_add_game_window)
        file_menu.addAction(add_game_action)

        import_games_action = QAction(QIcon("icons/import.png"), "Import Games", self)
        import_games_action.triggered.connect(self.import_games)
        file_menu.addAction(import_games_action)

        # Create a horizontal layout for the top section
        top_layout = QHBoxLayout()

        # Add logo to the top center
        logo_label = QLabel()
        logo_pixmap = QPixmap("icons/QPGM.png")
        logo_label.setPixmap(logo_pixmap.scaled(200, 100, Qt.KeepAspectRatio))
        logo_label.setAlignment(Qt.AlignCenter)
        top_layout.addWidget(logo_label, alignment=Qt.AlignCenter)

        # Create a horizontal layout for the top buttons
        top_buttons_layout = QHBoxLayout()

        self.delete_game_button = QPushButton()
        self.delete_game_button.setIcon(QIcon("icons/delete.png"))
        self.delete_game_button.setFixedSize(30, 30)
        self.delete_game_button.clicked.connect(self.delete_game)
        self.delete_game_button.setEnabled(False)
        top_buttons_layout.addWidget(self.delete_game_button)

        top_buttons_layout.addStretch()

        # Create a dropdown menu for "Add Game" and "Import Games"
        self.add_import_button = QToolButton()
        self.add_import_button.setText("New Game")
        self.add_import_button.setPopupMode(QToolButton.InstantPopup)
        self.add_import_button.setFixedSize(100, 30)
        self.add_import_button.setStyleSheet("QToolButton::menu-indicator { subcontrol-position: center right; }")

        add_import_menu = QMenu(self)
        add_import_menu.addAction(add_game_action)
        add_import_menu.addAction(import_games_action)
        self.add_import_button.setMenu(add_import_menu)
        top_buttons_layout.addWidget(self.add_import_button)

        top_layout.addLayout(top_buttons_layout)
        layout.addLayout(top_layout)

        self.posters_layout = QGridLayout()
        self.load_posters()

        layout.addLayout(self.posters_layout)

        # Create a horizontal layout for the switch buttons
        switch_buttons_layout = QHBoxLayout()
        
        self.profile_dropdown = QComboBox()
        self.profile_dropdown.setEnabled(False)
        switch_buttons_layout.addWidget(self.profile_dropdown)

        self.switch_profile_button = QPushButton("Switch Profile")
        self.switch_profile_button.setIcon(QIcon("icons/switch.png"))
        self.switch_profile_button.clicked.connect(self.switch_profile)
        self.switch_profile_button.setEnabled(False)
        switch_buttons_layout.addWidget(self.switch_profile_button)

        layout.addSpacerItem(QSpacerItem(20, 40, QSizePolicy.Minimum, QSizePolicy.Expanding))
        layout.addLayout(switch_buttons_layout)

        container = QWidget()
        container.setLayout(layout)

        # Add scroll area
        scroll_area = QScrollArea()
        scroll_area.setWidget(container)
        scroll_area.setWidgetResizable(True)
        self.setCentralWidget(scroll_area)

    def apply_stylesheet(self):
        stylesheet = """
        QMainWindow {
            background-color: #2e2e2e;
        }
        QPushButton {
            background-color: #4a4a4a;
            color: white;
            border: 1px solid #5a5a5a;
            padding: 5px;
            border-radius: 5px;
        }
        QPushButton:hover {
            background-color: #5a5a5a;
        }
        QComboBox {
            background-color: #4a4a4a;
            color: white;
            border: 1px solid #5a5a5a;
            padding: 5px;
            border-radius: 5px;
        }
        QLabel {
            color: white;
        }
        QLabel:hover {
            background-color: #5a5a5a;
        }
        QToolButton {
            background-color: #4a4a4a;
            color: white;
            border: 1px solid #5a5a5a;
            padding: 5px;
            border-radius: 5px;
        }
        QToolButton:hover {
            background-color: #5a5a5a;
        }
        QToolButton::menu-indicator {
            subcontrol-position: center right;
        }
        QMenuBar {
            background-color: #3a3a3a;
            color: white;
        }
        QMenuBar::item {
            background-color: #3a3a3a;
            color: white;
        }
        QMenuBar::item:selected {
            background-color: #5a5a5a;
        }
        QMenu {
            background-color: #3a3a3a;
            color: white;
        }
        QMenu::item {
            background-color: #3a3a3a;
            color: white;
        }
        QMenu::item:selected {
            background-color: #5a5a5a;
        }
        """
        self.setStyleSheet(stylesheet)

    def load_posters(self):
        # Clear the existing posters
        for i in reversed(range(self.posters_layout.count())):
            widget_to_remove = self.posters_layout.itemAt(i).widget()
            self.posters_layout.removeWidget(widget_to_remove)
            widget_to_remove.setParent(None)

        self.posters_layout.setSpacing(10)
        games = self.settings_controller.get_games()
        row, col = 0, 0
        for game_name in games:
            poster_path = os.path.join("posters", f"{game_name}.jpg")
            if not os.path.exists(poster_path):
                poster_path = self.get_executable_icon_path(game_name)
            poster_label = QLabel()
            pixmap = QPixmap(poster_path)
            if pixmap.isNull():
                pixmap = QPixmap("icons/noart.png")
            poster_label.setPixmap(pixmap.scaled(100, 150, Qt.KeepAspectRatio))
            poster_label.setFixedSize(100, 150)
            poster_label.setAlignment(Qt.AlignCenter)
            poster_label.mousePressEvent = lambda event, name=game_name, label=poster_label: self.select_game(name, label)
            
            game_name_label = QLabel(game_name)
            game_name_label.setAlignment(Qt.AlignCenter)

            poster_layout = QVBoxLayout()
            poster_layout.addWidget(poster_label)
            poster_layout.addWidget(game_name_label)

            poster_container = QWidget()
            poster_container.setLayout(poster_layout)
            poster_container.setFixedSize(110, 180)  # Including space for the game name and larger click border
            poster_container.setStyleSheet("""
                QWidget:hover {
                    background-color: #5a5a5a;
                }
                QLabel {
                    border: none;
                }
            """)

            self.posters_layout.addWidget(poster_container, row, col)
            col += 1
            if col > 3:
                col = 0
                row += 1

    def get_executable_icon_path(self, game_name):
        # Assuming the executable is in the "executables" directory
        executable_path = os.path.join("executables", f"{game_name}.exe")
        if os.path.exists(executable_path):
            icon = QIcon(executable_path)
            if not icon.isNull():
                icon_pixmap = icon.pixmap(100, 150)
                icon_path = os.path.join("posters", f"{game_name}_icon.png")
                icon_pixmap.save(icon_path)
                return icon_path
        return "icons/noart.png"

    def select_game(self, game_name, poster_label):
        if self.selected_game == game_name:
            self.open_edit_game_window(game_name)
        else:
            if self.selected_label:
                try:
                    self.selected_label.setStyleSheet("")
                except RuntimeError:
                    self.selected_label = None
            self.selected_game = game_name
            self.selected_label = poster_label
            self.delete_game_button.setEnabled(True)
            self.profile_dropdown.setEnabled(True)
            self.switch_profile_button.setEnabled(True)
            poster_label.setStyleSheet("background-color: #5a5a5a;")
            self.load_profiles()

    def load_profiles(self):
        self.profile_dropdown.clear()
        if self.selected_game:
            profiles = self.settings_controller.get_profiles(self.selected_game)
            self.profile_dropdown.addItems(profiles)

    def switch_profile(self):
        selected_profile = self.profile_dropdown.currentText()
        if selected_profile:
            reply = QMessageBox.question(self, "Switch Profile", f"Are you sure you want to switch {self.selected_game} to {selected_profile} profile?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                config_path = self.settings_controller.switch_profile(self.selected_game, selected_profile)
                if config_path:
                    QMessageBox.information(self, "Profile Switched", f"Switched {self.selected_game} to {selected_profile} profile at {config_path}")

    def open_add_game_window(self):
        self.game_window = GameWindow(self.settings_controller, self.load_posters)
        self.game_window.show()

    def open_edit_game_window(self, game_name):
        self.game_window = GameWindow(self.settings_controller, self.load_posters, game_name)
        self.game_window.setWindowTitle(game_name)
        self.game_window.show()

    def delete_game(self):
        if self.selected_game:
            reply = QMessageBox.question(self, "Delete Game", f"Are you sure you want to delete the game '{self.selected_game}' and its config files?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                self.settings_controller.delete_game(self.selected_game)
                self.selected_game = None
                self.selected_label = None
                self.delete_game_button.setEnabled(False)
                self.profile_dropdown.setEnabled(False)
                self.switch_profile_button.setEnabled(False)
                self.load_posters()

    def import_games(self):
        dialog = ImportGamesDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            launcher = dialog.launcher_combo.currentText()
            default_paths = get_default_paths()
            directory_path = default_paths.get(launcher)
            if not os.path.exists(directory_path):
                directory_path = QFileDialog.getExistingDirectory(self, f"Select {launcher} Games Directory")
            if directory_path:
                import_games(self.settings_controller, directory_path)
                self.load_posters()

class ImportGamesDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Game Launcher")
        self.setGeometry(300, 300, 300, 100)

        layout = QVBoxLayout()

        self.launcher_combo = QComboBox()
        self.launcher_combo.addItems(["Steam", "GOG", "Epic"])
        layout.addWidget(self.launcher_combo)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.setLayout(layout)

class GameWindow(QMainWindow):
    def __init__(self, settings_controller, refresh_posters_callback, game_name=None):
        super().__init__()
        self.settings_controller = settings_controller
        self.refresh_posters_callback = refresh_posters_callback
        self.game_name = game_name
        self.setWindowTitle(game_name if game_name else "Add Game")
        self.setGeometry(150, 150, 400, 300)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()

        if self.game_name:
            switch_buttons_layout = QHBoxLayout()

            self.profile_dropdown = QComboBox()
            switch_buttons_layout.addWidget(self.profile_dropdown)

            self.switch_profile_button = QPushButton("Switch Profile")
            self.switch_profile_button.setFixedSize(150, 30)
            self.switch_profile_button.clicked.connect(self.switch_profile)
            switch_buttons_layout.addWidget(self.switch_profile_button)

            self.delete_profile_button = QPushButton("Delete Profile")
            self.delete_profile_button.setFixedSize(150, 30)
            self.delete_profile_button.clicked.connect(self.delete_profile)
            switch_buttons_layout.addWidget(self.delete_profile_button)

            layout.addLayout(switch_buttons_layout)

        self.game_name_label = QLabel("Game Name:")
        self.game_name_input = QLineEdit()
        if self.game_name:
            self.game_name_input.setText(self.game_name)
            self.game_name_input.setDisabled(True)

        self.poster_label = QLabel()
        self.load_poster()

        self.upload_poster_button = QPushButton("Upload Poster")
        self.upload_poster_button.clicked.connect(self.upload_poster)

        self.config_path_label = QLabel("Config Path:")
        self.config_path_input = QLineEdit()
        self.browse_button = QPushButton("Browse")
        self.browse_button.clicked.connect(self.browse_config_path)

        self.save_button = QPushButton("Save Config")
        self.save_button.clicked.connect(self.save_config)

        layout.addWidget(self.game_name_label)
        layout.addWidget(self.game_name_input)
        layout.addWidget(self.poster_label)
        layout.addWidget(self.upload_poster_button)
        layout.addWidget(self.config_path_label)
        layout.addWidget(self.config_path_input)
        layout.addWidget(self.browse_button)
        layout.addWidget(self.save_button)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

        if self.game_name:
            self.load_game_details()
            self.update_status_labels()

    def load_poster(self):
        poster_path = os.path.join("posters", f"{self.game_name}.jpg")
        if not os.path.exists(poster_path):
            poster_path = "icons/noart.png"
        pixmap = QPixmap(poster_path)
        if pixmap.isNull():
            pixmap = QPixmap("icons/noart.png")
        self.poster_label.setPixmap(pixmap.scaled(100, 150, Qt.KeepAspectRatio))

    def upload_poster(self):
        options = QFileDialog.Options()
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Poster Image", "", "Image Files (*.jpg *.jpeg *.png);;All Files (*)", options=options)
        if file_path:
            poster_dir = "posters"
            if not os.path.exists(poster_dir):
                os.makedirs(poster_dir)
            poster_path = os.path.join(poster_dir, f"{self.game_name}.jpg")
            shutil.copyfile(file_path, poster_path)
            self.load_poster()
            self.refresh_posters_callback()

    def browse_config_path(self):
        options = QFileDialog.Options()
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Config File", "", "Config Files (*.cfg *.json *.ini);;All Files (*)", options=options)
        if file_path:
            self.config_path_input.setText(file_path)

    def load_game_details(self):
        games = self.settings_controller.get_games()
        config_path = games.get(self.game_name)
        self.config_path_input.setText(config_path)
        self.load_profiles()

    def load_profiles(self):
        self.profile_dropdown.clear()
        if self.game_name:
            profiles = self.settings_controller.get_profiles(self.game_name)
            self.profile_dropdown.addItems(profiles)

    def save_game(self):
        game_name = self.game_name_input.text()
        config_path = self.config_path_input.text()
        if game_name and config_path:
            self.settings_controller.add_game(game_name, config_path)
            self.refresh_posters_callback()
            self.close()

    def save_config(self):
        game_name = self.game_name_input.text()
        config_path = self.config_path_input.text()
        if game_name and config_path:
            profile_name, ok = QInputDialog.getText(self, "Save Config", "Enter profile name:")
            if ok and profile_name:
                ext = os.path.splitext(config_path)[1]
                target_path = os.path.join(self.settings_controller.config_dir, f"{game_name}_{profile_name}{ext}")
                if os.path.exists(target_path):
                    reply = QMessageBox.question(self, "Overwrite Config", f"Profile '{profile_name}' for {game_name} already exists. Do you want to overwrite it?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                    if reply == QMessageBox.No:
                        return
                shutil.copyfile(config_path, target_path)
                QMessageBox.information(self, "Config Saved", f"Profile '{profile_name}' saved for {game_name} to {target_path}")
                self.update_status_labels()
                self.save_game()  # Save the game after saving the config

    def switch_profile(self):
        selected_profile = self.profile_dropdown.currentText()
        if selected_profile:
            reply = QMessageBox.question(self, "Switch Profile", f"Are you sure you want to switch {self.game_name} to {selected_profile} profile?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                config_path = self.settings_controller.switch_profile(self.game_name, selected_profile)
                if config_path:
                    QMessageBox.information(self, "Profile Switched", f"Switched {self.game_name} to {selected_profile} profile at {config_path}")

    def delete_profile(self):
        selected_profile = self.profile_dropdown.currentText()
        if selected_profile:
            reply = QMessageBox.question(self, "Delete Profile", f"Are you sure you want to delete the profile '{selected_profile}' for {self.game_name}?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                self.settings_controller.delete_profile(self.game_name, selected_profile)
                self.load_profiles()

    def update_status_labels(self):
        if self.game_name:
            profiles = self.settings_controller.get_profiles(self.game_name)
            self.profile_dropdown.clear()
            self.profile_dropdown.addItems(profiles)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())