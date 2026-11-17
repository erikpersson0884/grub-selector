#!/usr/bin/env bash
# Installs GRUB Selector so it shows up in your app launcher.
#   ./install.sh              install for your user (~/.local)
#   sudo ./install.sh         install for everyone (/usr/local)
#   ./install.sh --uninstall  remove it again (use sudo if you installed with sudo)
set -euo pipefail

APP=grub-selector
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $EUID -eq 0 ]]; then PREFIX=/usr/local; else PREFIX="$HOME/.local"; fi
APP_DIR="$PREFIX/share/$APP"
BIN="$PREFIX/bin/$APP"
DESKTOP="$PREFIX/share/applications/$APP.desktop"
ICON="$PREFIX/share/icons/hicolor/scalable/apps/$APP.svg"

refresh_caches() {
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$PREFIX/share/applications" 2>/dev/null || true
    command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t "$PREFIX/share/icons/hicolor" 2>/dev/null || true
}

if [[ "${1:-}" == "--uninstall" ]]; then
    rm -rf "$APP_DIR"
    rm -f "$BIN" "$DESKTOP" "$ICON"
    refresh_caches
    echo "Removed GRUB Selector from $PREFIX"
    exit 0
fi

if ! python3 -c "import tkinter" 2>/dev/null; then
    echo "Warning: Python Tkinter is missing. Install it first:" >&2
    echo "  Fedora: sudo dnf install python3-tkinter" >&2
    echo "  Debian/Ubuntu: sudo apt install python3-tk" >&2
    echo "  Arch: sudo pacman -S tk" >&2
fi

install -Dm755 "$HERE/app.py" "$APP_DIR/app.py"
install -Dm644 "$HERE/$APP.svg" "$ICON"

mkdir -p "$(dirname "$BIN")"
ln -sf "$APP_DIR/app.py" "$BIN"

# Use the full path so the launcher works even if ~/.local/bin is not in PATH
mkdir -p "$(dirname "$DESKTOP")"
sed "s|^Exec=.*|Exec=python3 $APP_DIR/app.py|" "$HERE/$APP.desktop" > "$DESKTOP"
chmod 644 "$DESKTOP"

refresh_caches
echo "Installed to $PREFIX. Search for \"GRUB Selector\" in your app launcher."
echo "(If it doesn't show up right away, log out and back in.)"
