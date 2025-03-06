# Graphics Settings Manager

This project is a simple GUI application designed for managing graphics settings for video games on a handheld console, with support for an external GPU (eGPU) when connected.

## Features

- User-friendly interface for adjusting graphics settings.
- Support for various settings such as resolution, frame rate, and more.
- Ability to save and load settings from a configuration file.
- Dynamic updates to settings based on user interactions.

## Project Structure

```
graphics-settings-manager
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