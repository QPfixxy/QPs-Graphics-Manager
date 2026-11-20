# RigSwap — Roadmap

RigSwap keeps several versions of each game's graphics settings and swaps between them with one click (or automatically). It's built for handheld gaming PCs and laptops that are sometimes connected to an external GPU (eGPU): 4K ultra when docked, 720p/1080p low on the go.

Version 0.1 (PyQt5, manual paths, per-game profiles) proved the idea. Version 2 is a ground-up rewrite that targets **Windows and Linux**, including Bazzite, SteamOS-style distros, and games running under Proton.

## Design decisions

### Stack: Python + PySide6 (Qt 6)
Cross-platform, official Qt binding, LGPL licensed.

### Data model: global setups
A **setup** is a hardware situation, such as `Handheld`, `eGPU Dock` or `Laptop on Battery`. Setups are defined once.

Each game stores **one config snapshot per setup**. Switching setup applies every game's matching snapshot at once, which is what makes automatic switching possible.

When you switch away from a setup, the game's current config can be saved back into that setup's snapshot, so tweaks you made in a setup are kept for next time.

### Config file locations: Ludusavi manifest
The [Ludusavi manifest](https://github.com/mtkennerly/ludusavi-manifest) is built from PCGamingWiki data. It lists file paths for thousands of games and tags each one as a save or config file. We use its config paths so that users don't have to find config files themselves. Users can still override a path that's wrong or missing.

> Open question: check the manifest's data license before bundling it. Downloading it at runtime may be the cleaner option.

### Proton is a first-class case
On Linux, most games are Windows games running under Proton. Their config files live inside each game's Proton prefix (`steamapps/compatdata/<appid>/pfx/drive_c/...`). For that reason games are identified by Steam app ID, not by folder name. Both native Steam and Flatpak Steam (`~/.var/app/com.valvesoftware.Steam/...`) must be supported.

### Architecture

```
core/        game discovery, config locations, setups, safe swapping (no GUI code)
platform/    windows.py / linux.py: paths, GPU detection, process checks
gui/         PySide6 desktop app
daemon/      background service for auto-switching (tray on desktop, headless for Game Mode)
cli/         command-line control, e.g. `rigswap switch "eGPU Dock"`
```

The headless daemon matters for Bazzite and SteamOS-style Game Mode (gamescope), where there is no system tray.

## Phases

### Phase 0: Foundation
- [x] New project layout with `pyproject.toml` (PySide6, `platformdirs`, `psutil`)
- [x] User data stored in the OS's per-user folder via `platformdirs`, not the folder the app was launched from
- [x] Data model: games, setups, snapshots, settings
- [x] pytest test suite
- [x] GitHub Actions CI running on Windows and Ubuntu

### Phase 1: Manual switching on both OSes (MVP)
- [x] Steam game discovery from `libraryfolders.vdf` and `appmanifest_*.acf` (Windows, Linux, Flatpak Steam), including Proton prefixes
- [x] Add games by hand and pick their config files, including games with several files or a whole folder (with include/exclude patterns)
- [x] Safe swapping:
  - [x] refuse while the game is running
  - [x] back up the live config before every swap
  - [x] atomic writes, so a crash can't leave a half-written config
  - [x] undo
- [x] Posters from Steam's cached library artwork
- [x] GUI built around setups

### Phase 2: Automatic config detection
- [ ] Load the Ludusavi manifest's config paths
- [ ] Expand its path placeholders for Windows and Proton prefixes
- [ ] Detect each game's config files on import; users can override them

### Phase 3: Automatic switching
- [ ] GPU detection:
  - [ ] Linux: read `/sys/class/drm` and watch for hotplug events with `pyudev`
  - [ ] Windows: query WMI and listen for device-change events
- [ ] Optional conditions: AC vs. battery, external display
- [ ] Rules that map conditions to a setup (e.g. "RTX 4070 present → eGPU Dock")
- [ ] Start the daemon at login: systemd user service on Linux, startup task on Windows

### Phase 4: More launchers and harder cases
- [ ] Heroic (GOG and Epic) and Lutris on Linux
- [ ] GOG (registry) and Epic (`.item` manifests) on Windows
- [ ] Registry-stored settings (common in Unity games): the Windows registry, and `user.reg` inside Proton prefixes

### Phase 5: Distribution
- [ ] Windows: PyInstaller build plus an installer
- [ ] Linux: AppImage first, then Flatpak/Flathub (the natural install route on Bazzite; needs filesystem permissions to reach game folders)
- [ ] Community fixes for config paths the manifest gets wrong

## Related projects
- [GPUPrefSwitcher](https://github.com/sharpjd/GPUPrefSwitcher): Windows; per-app GPU preference, swaps settings files on plug/unplug
- [Thunderbolt Switch](https://github.com/Valkirie/Thunderbolt-Switch): Windows; switches game settings and power profiles by device state
- [Ludusavi](https://github.com/mtkennerly/ludusavi): cross-platform game save backup; source of the config path manifest
