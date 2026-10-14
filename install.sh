#!/usr/bin/env bash
# Installs Qobuz-DL GUI for the current user (no root needed).
#   ./install.sh            install or update (qobuz-dl from PyPI)
#   ./install.sh --git      use the latest qobuz-dl from GitHub instead of PyPI
#   ./install.sh --uninstall
set -euo pipefail

APP_ID="qobuz-dl-gui"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/$APP_ID"
VENV="$DATA_DIR/venv"
BIN_DIR="$HOME/.local/bin"
DESKTOP_FILE="${XDG_DATA_HOME:-$HOME/.local/share}/applications/$APP_ID.desktop"
ICON_FILE="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps/$APP_ID.svg"

say() { printf '\033[1;36m::\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }

if [[ "${1:-}" == "--uninstall" ]]; then
    rm -rf "$DATA_DIR" "$BIN_DIR/$APP_ID" "$DESKTOP_FILE" "$ICON_FILE"
    command -v update-desktop-database >/dev/null && update-desktop-database -q "$(dirname "$DESKTOP_FILE")" || true
    say "Removed. Your qobuz-dl settings in ~/.config/qobuz-dl were left in place."
    exit 0
fi

QDL_SOURCE="qobuz-dl-ultimate"
[[ "${1:-}" == "--git" ]] && QDL_SOURCE="git+https://github.com/Sei969/qobuz-dl.git"

command -v python3 >/dev/null || { warn "python3 is missing: sudo pacman -S python"; exit 1; }

# A venv that can also see system packages, so a pacman-installed PySide6 (native Breeze look)
# is used when it's there.
say "Creating the Python environment in $DATA_DIR"
mkdir -p "$DATA_DIR"
[[ -x "$VENV/bin/python" ]] || python3 -m venv --system-site-packages "$VENV"
"$VENV/bin/python" -m pip install -q --upgrade pip

if "$VENV/bin/python" -c "import PySide6" 2>/dev/null; then
    say "Using the installed PySide6"
else
    say "Installing PySide6 (tip: 'sudo pacman -S pyside6' first gives the native KDE look)"
    "$VENV/bin/python" -m pip install -q PySide6
fi

say "Installing qobuz-dl ($QDL_SOURCE)"
"$VENV/bin/python" -m pip install -q --upgrade "$QDL_SOURCE" keyring

say "Copying the app"
rm -rf "$DATA_DIR/app"
mkdir -p "$DATA_DIR/app"
cp -r "$SRC_DIR/qobuz_gui" "$DATA_DIR/app/"
find "$DATA_DIR/app" -name '__pycache__' -prune -exec rm -rf {} +

mkdir -p "$BIN_DIR" "$(dirname "$DESKTOP_FILE")" "$(dirname "$ICON_FILE")"
cat > "$BIN_DIR/$APP_ID" <<EOF
#!/usr/bin/env bash
export PYTHONPATH="$DATA_DIR/app\${PYTHONPATH:+:\$PYTHONPATH}"
exec "$VENV/bin/python" -m qobuz_gui "\$@"
EOF
chmod +x "$BIN_DIR/$APP_ID"

cp "$SRC_DIR/qobuz_gui/icon.svg" "$ICON_FILE"
cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Qobuz-DL
GenericName=Music Downloader
Comment=Search and download Hi-Res music from Qobuz
Exec=$BIN_DIR/$APP_ID
Icon=$APP_ID
Terminal=false
Categories=AudioVideo;Audio;Network;Qt;
Keywords=qobuz;flac;hi-res;music;download;
StartupWMClass=qobuz-dl-gui
EOF
command -v update-desktop-database >/dev/null && update-desktop-database -q "$(dirname "$DESKTOP_FILE")" || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor" 2>/dev/null || true

command -v ffmpeg >/dev/null || warn "ffmpeg not found — segmented downloads need it: sudo pacman -S ffmpeg"

say "Done. Start 'Qobuz-DL' from the app launcher, or run: $APP_ID"
