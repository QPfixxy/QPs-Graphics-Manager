import os
import shutil
import json

class SettingsController:
    def __init__(self, config_dir):
        self.config_dir = config_dir
        self.games_file = os.path.join(config_dir, "games.json")
        if not os.path.exists(config_dir):
            os.makedirs(config_dir)
        if not os.path.exists(self.games_file):
            with open(self.games_file, 'w') as file:
                json.dump({}, file)

    def add_game(self, game_name, config_path):
        with open(self.games_file, 'r') as file:
            games = json.load(file)
        games[game_name] = config_path
        with open(self.games_file, 'w') as file:
            json.dump(games, file)

    def get_games(self):
        with open(self.games_file, 'r') as file:
            games = json.load(file)
        return games

    def get_profiles(self, game_name):
        profiles = []
        for file in os.listdir(self.config_dir):
            if file.startswith(game_name + "_"):
                profiles.append(file[len(game_name) + 1:].rsplit('.', 1)[0])
        return profiles

    def save_config(self, game_name, profile_name, config_path):
        ext = os.path.splitext(config_path)[1]
        target_path = os.path.join(self.config_dir, f"{game_name}_{profile_name}{ext}")
        shutil.copyfile(config_path, target_path)
        return target_path

    def switch_profile(self, game_name, profile_name):
        profile_files = [file for file in os.listdir(self.config_dir) if file.startswith(f"{game_name}_{profile_name}")]
        if profile_files:
            target_path = os.path.join(self.config_dir, profile_files[0])
            with open(self.games_file, 'r') as file:
                games = json.load(file)
            config_path = games.get(game_name)
            if config_path:
                shutil.copyfile(target_path, config_path)
                return config_path
        return None

    def delete_profile(self, game_name, profile_name):
        profile_files = [file for file in os.listdir(self.config_dir) if file.startswith(f"{game_name}_{profile_name}")]
        for profile_file in profile_files:
            os.remove(os.path.join(self.config_dir, profile_file))

    def delete_game(self, game_name):
        with open(self.games_file, 'r') as file:
            games = json.load(file)
        if game_name in games:
            del games[game_name]
            with open(self.games_file, 'w') as file:
                json.dump(games, file)
            # Remove config files
            for file in os.listdir(self.config_dir):
                if file.startswith(game_name + "_"):
                    os.remove(os.path.join(self.config_dir, file))

    def import_games(self, steam_common_path):
        game_folders = [name for name in os.listdir(steam_common_path) if os.path.isdir(os.path.join(steam_common_path, name))]
        for game_name in game_folders:
            self.add_game(game_name, "")