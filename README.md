# Graphics Settings Manager

This project is a simple GUI application designed for managing graphics settings for video games on handheld PC consoles (ROG ALLY, ALLY X, etc) and EGPU systems. An simple and effective way to backup graphics configurations on a per setting basis, and quickly redeploy them between modes. 

A primary example being, switching between 4k ultra gaming on an EGPU, and 720p/1080p gaming on a handheld. Simply deploy the corrisponding configuration with a button click, and done. 

The system allows for multiple backups of for each game, so that you can have as many configurations as needed for your various setups.


## Features

- Simple UI interface, easily to navigated. 
- Support for multiple graphics configurations per game. 
- Ability to save and load configuration files on the fly. 
- Dynamic Import of games from Steam, GOG, and Epic Launchers.

## Planned Features

- EGPU detection and automatic profile deployment.
- Improved UI navigation features
- Automatic graphics configuration pathing (currently manual pathing is required).


## Project Structure

```
QPs-settings-manager
├── src
│   ├── main.py                # Entry point of the application
│   ├── ui
│   │   └── main_window.py     # Main user interface definition
│   ├── controllers
│   │   └── settings_controller.py # Logic for managing graphics settings
│   └── models
│       └── settings_model.py   # Data structure for graphics settings
├── requirements.txt            # Project dependencies
├── setup.py                    # Packaging information
└── README.md                   # Project documentation
```

## Installation

1. Clone the repository:
   ```
   git clone https://github.com/yourusername/graphics-settings-manager.git
   cd graphics-settings-manager
   ```

2. Install the required dependencies:
   ```
   pip install -r requirements.txt
   ```

## Usage

To run the application, execute the following command:
```
python src/main.py
```

## Contributing

Contributions are welcome! Please feel free to submit a pull request or open an issue for any suggestions or improvements.

## License

This project is licensed under the MIT License. See the LICENSE file for more details.
