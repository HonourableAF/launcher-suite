# launcher-suite

A Nord-themed launcher setup for KDE Plasma on Linux.

- **radial/**: keybind-triggered radial blob launcher (Python + QML)
- **wallpaper-picker/**: fullscreen wallpaper carousel (PyQt6 + QtQuick)
- **otter-launcher/**: otter-launcher config, foot config, desktop entry and helper script

## Requirements

- KDE Plasma 6
- Python 3 with PySide6 (radial) and PyQt6 (wallpaper-picker)
- [otter-launcher](https://github.com/kuokuo123/otter-launcher), `fzf`, `foot`
- Optional, used by radial menu entries: `playerctl`, `wpctl`, `nmcli`, `bluetoothctl`, `yazi`, `dolphin`
- Fonts: Monocraft and Symbols Nerd Font

## Install

Install the wallpaper picker and otter first, since the radial menu launches both.

    # wallpaper-picker
    install -Dm755 wallpaper-picker/wallpaper-picker ~/.local/bin/wallpaper-picker

    # otter-launcher
    install -Dm644 otter-launcher/config.toml ~/.config/otter-launcher/config.toml
    install -Dm644 otter-launcher/otter.ini ~/.config/foot/otter.ini
    install -Dm755 otter-launcher/otter-help ~/.local/bin/otter-help
    sed "s|/home/puchai|$HOME|g" otter-launcher/otter-launcher.desktop > ~/.local/share/applications/otter-launcher.desktop

## Radial menu

    python radial/radial.py --daemon    # autostart this, it starts hidden
    python radial/radial.py             # bind a key to this to open the menu

To customize, copy the example config next to the script and edit it. It is re-read every time the menu opens.

    cp radial/menu.example.json radial/menu.json

Make sure ~/.local/bin is on your PATH so the menu can find wallpaper-picker.

## Wallpapers

Put images in ~/Pictures/Wallpapers, or set WP_DIR to another folder.
