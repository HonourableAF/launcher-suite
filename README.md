# launcher-suite

A Nord-themed launcher setup for KDE Plasma on Linux.

- **radial/**: keybind-triggered radial blob launcher (Python + QML)
- **wallpaper-picker/**: fullscreen wallpaper carousel (PyQt6 + QtQuick)
- **otter-launcher/**: [otter-launcher](https://github.com/kuokuo123/otter-launcher) config, foot config, desktop entry and helper script

Tested on EndeavourOS with KDE Plasma 6.

## 1. Install dependencies

### Arch / EndeavourOS

Required:

```sh
sudo pacman -S --needed python-pyqt6 pyside6 qt6-declarative foot fzf libnotify ttf-nerd-fonts-symbols
```

From the AUR (use `yay` or `paru`):

```sh
yay -S otter-launcher fsel-bin
```

Font: download [Monocraft](https://github.com/IdreesInc/Monocraft/releases) and install it to `~/.local/share/fonts/`, then run `fc-cache -f`.

Optional, only needed for the matching radial menu entries:

```sh
sudo pacman -S --needed playerctl wireplumber networkmanager bluez-utils yazi dolphin neovim
```

### Other distros

Install the equivalents of: Python 3, PyQt6, PySide6, Qt 6 Declarative (QtQuick), `foot`, `fzf`, `libnotify`, `otter-launcher`, `fsel`, a Symbols Nerd Font and Monocraft. Optional: `playerctl`, `wpctl` (WirePlumber), `nmcli`, `bluetoothctl`, `yazi`, `dolphin`, `nvim`.

### Check everything is installed

In fish:

```fish
for c in foot fzf otter-launcher fsel notify-send playerctl wpctl nmcli bluetoothctl yazi
    command -q $c; or echo "missing: $c"
end
```

Anything listed as missing is only a problem if you use that module or menu entry.

## 2. Install

```sh
git clone https://github.com/HonourableAF/launcher-suite
cd launcher-suite
```

Install the wallpaper picker and otter first, since the radial menu launches both.

```sh
# wallpaper-picker
install -Dm755 wallpaper-picker/wallpaper-picker ~/.local/bin/wallpaper-picker

# otter-launcher
install -Dm644 otter-launcher/config.toml ~/.config/otter-launcher/config.toml
install -Dm644 otter-launcher/otter.ini ~/.config/foot/otter.ini
install -Dm755 otter-launcher/otter-help ~/.local/bin/otter-help
sed "s|/home/puchai|$HOME|g" otter-launcher/otter-launcher.desktop > ~/.local/share/applications/otter-launcher.desktop
```

Make sure `~/.local/bin` is on your `PATH`.

## 3. Radial menu

```sh
python radial/radial.py --daemon    # autostart this, it starts hidden
python radial/radial.py             # bind a key to this to open the menu
```

To customize, copy the example config next to the script and edit it. It is re-read every time the menu opens.

```sh
cp radial/menu.example.json radial/menu.json
```

## Wallpapers

Put images in `~/Pictures/Wallpapers`, or set `WP_DIR` to another folder:

```sh
WP_DIR=~/some/folder wallpaper-picker
```
