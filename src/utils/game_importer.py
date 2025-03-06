import requests
import os
import shutil
import platform

def get_steam_games(api_key, steam_id):
    url = f"http://api.steampowered.com/IPlayerService/GetOwnedGames/v0001/?key={api_key}&steamid={steam_id}&format=json"
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        games = data.get('response', {}).get('games', [])
        game_names = [game['name'] for game in games]
        return game_names
    else:
        return []

def get_installed_games_from_directory(directory_path):
    if not os.path.exists(directory_path):
        return []

    game_folders = [name for name in os.listdir(directory_path) if os.path.isdir(os.path.join(directory_path, name))]
    return game_folders

def import_games(settings_controller, directory_path):
    game_names = get_installed_games_from_directory(directory_path)
    for game_name in game_names:
        settings_controller.add_game(game_name, "")
        game_path = os.path.join(directory_path, game_name)
        poster_path = find_poster(game_path)
        if poster_path:
            target_path = os.path.join("posters", f"{game_name}.jpg")
            shutil.copyfile(poster_path, target_path)

def find_poster(game_path):
    for root, dirs, files in os.walk(game_path):
        for file in files:
            if file.lower().endswith(('.ico', '.jpg', '.jpeg', '.png')):
                return os.path.join(root, file)
    return None

def get_default_paths():
    system = platform.system()
    if system == "Darwin":  # macOS
        paths = {
            "Steam": os.path.expanduser("~/Library/Application Support/Steam/steamapps/common"),
            "GOG": os.path.expanduser("~/Library/Application Support/GOG Galaxy/Games"),
            "Epic": os.path.expanduser("~/Library/Application Support/Epic/Games")
        }
    elif system == "Windows":
        paths = {
            "Steam": os.path.expanduser("C:/Program Files (x86)/Steam/steamapps/common"),
            "GOG": os.path.expanduser("C:/Program Files (x86)/GOG Galaxy/Games"),
            "Epic": os.path.expanduser("C:/Program Files/Epic Games")
        }
    elif system == "Linux":
        paths = {
            "Steam": os.path.expanduser("~/.steam/steam/steamapps/common"),
            "GOG": os.path.expanduser("~/GOG Games"),
            "Epic": os.path.expanduser("~/Epic Games")
        }
    else:
        paths = {}
    return paths