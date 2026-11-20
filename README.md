# QP's Graphics Manager

Keep a separate copy of each game's graphics settings for every hardware setup you play on, and swap between them in one click.

It's built for handheld gaming PCs (ROG Ally, Legion Go, Steam Deck, ...) and laptops that are sometimes plugged into an external GPU. Run 4K ultra when docked to the eGPU and 720p low on the go, without changing settings in every game each time.

> **Status: v2 rewrite in progress.** This branch is a ground-up rewrite for Windows and Linux (including Bazzite and Proton games). It doesn't have a GUI yet. The original v0.1 app is on the [`legacy`](https://github.com/QPfixxy/QPs-Graphics-Manager/tree/legacy) branch and the [`v0.1`](https://github.com/QPfixxy/QPs-Graphics-Manager/releases/tag/v0.1) tag. See [ROADMAP.md](ROADMAP.md) for the plan.

## How it will work

1. **Define your setups once**, e.g. `Handheld` and `eGPU Dock`.
2. **Add your games.** Steam games (including Proton games on Linux) are found automatically, and so are their config files.
3. **Switch setup.** Every game's config is swapped to the version saved for that setup. The configs you were using are saved back first, so tweaks aren't lost.
4. **Optional:** let it switch automatically when the eGPU is plugged in or unplugged.

Swaps are safe. They're refused while the game is running, the live config is backed up first, and the files are written atomically.

## Development

Requires Python 3.11 or newer.

```bash
git clone git@github.com:QPfixxy/QPs-Graphics-Manager.git
cd QPs-Graphics-Manager
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

pytest                           # run the tests
ruff check .                     # lint
qpgm --help                      # every command
```

### Trying it from the command line

There's no GUI yet, but the whole workflow runs from the `qpgm` command:

```bash
qpgm setup add "Handheld"
qpgm setup add "eGPU Dock"

# Steam games are found automatically...
qpgm steam --import
# ...or add any game by hand. --exe lets QPGM see when it's running.
qpgm game add "Elden Ring" --exe eldenring.exe

# Tell it where the game keeps its graphics settings (a file or a folder).
# For folders, keep save files out with --exclude.
qpgm config add "Elden Ring" "%APPDATA%\EldenRing" --include "*.xml"

# Set the game up for each setup, saving each time.
qpgm save "Handheld" "Elden Ring"
qpgm save "eGPU Dock" --all

qpgm switch "eGPU Dock"          # apply every game's config for that setup
qpgm undo                        # put back the configs from before the last switch
qpgm game show "Elden Ring"      # locations and when each setup was saved
```

Switching refuses while an affected game is running, backs up what it overwrites, and never deletes files from your config folders.

The app keeps its data in the per-user folder for each OS: `~/.local/share/qpgm` on Linux and `%LOCALAPPDATA%\qpgm` on Windows. Set `QPGM_DATA_DIR` to use a different folder.

### Layout

```
src/qpgm/
  core/      data model, storage, paths (no GUI code)
  gui/       PySide6 app (Phase 1) and bundled icons
  cli.py     `qpgm` command
tests/
```

Each phase adds the `platform/` and `daemon/` packages described in the roadmap when it needs them.
