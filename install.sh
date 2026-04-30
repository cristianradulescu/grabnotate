#!/bin/bash
# Grabnotate installer
# Usage: ./install.sh
# Installs system deps, uv, Python env, launchers, and optional GNOME keybindings.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCHER="$HOME/.local/bin/grabnotate"
LAUNCHER_SELECT="$HOME/.local/bin/grabnotate-select"
KEYBINDING_PATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/grabnotate/"
KEYBINDING_SELECT_PATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/grabnotate-select/"

# ── Helpers ──────────────────────────────────────────────────────────────────

green()  { printf '\033[0;32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[0;33m%s\033[0m\n' "$*"; }
red()    { printf '\033[0;31m%s\033[0m\n' "$*"; }
step()   { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

require_apt() {
    # Install any listed apt packages that are not already installed
    local missing=()
    for pkg in "$@"; do
        if ! dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed"; then
            missing+=("$pkg")
        fi
    done
    if [ ${#missing[@]} -eq 0 ]; then
        green "  All required system packages already installed."
        return
    fi
    yellow "  Missing packages: ${missing[*]}"
    echo "  Installing via apt (sudo required)..."
    sudo apt-get update -qq
    sudo apt-get install -y "${missing[@]}"
    green "  System packages installed."
}

# ── 1. Detect distro and install system libraries ────────────────────────────

step "Checking system dependencies"

if ! command -v apt-get &>/dev/null; then
    red "This installer supports Debian/Ubuntu-based systems (apt)."
    red "Please install the following packages manually and re-run:"
    red "  libcairo2-dev libgirepository-2.0-dev libgtk-4-dev"
    red "  gir1.2-gtk-4.0 python3-gi python3-gi-cairo python3-dev python3.14-dev"
    exit 1
fi

require_apt \
    libcairo2-dev \
    libgirepository-2.0-dev \
    libgtk-4-dev \
    gir1.2-gtk-4.0 \
    python3-gi \
    python3-gi-cairo \
    python3-dev \
    python3.14-dev

# ── 2. Ensure uv is installed ────────────────────────────────────────────────

step "Checking uv"

UV_BIN="$HOME/.local/bin/uv"

if command -v uv &>/dev/null; then
    UV_BIN="$(command -v uv)"
    green "  uv found: $UV_BIN ($(uv --version))"
elif [ -x "$UV_BIN" ]; then
    green "  uv found: $UV_BIN ($("$UV_BIN" --version))"
else
    yellow "  uv not found — installing via official installer..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # The installer puts uv in ~/.local/bin
    if [ ! -x "$UV_BIN" ]; then
        red "uv installation failed or was placed somewhere unexpected."
        red "Please install uv manually: https://docs.astral.sh/uv/getting-started/installation/"
        exit 1
    fi
    green "  uv installed: $UV_BIN ($("$UV_BIN" --version))"
    yellow "  NOTE: Add ~/.local/bin to your PATH if it isn't already:"
    yellow "    echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc"
fi

# ── 3. Install Python environment ────────────────────────────────────────────

step "Installing Python environment"

cd "$SCRIPT_DIR"
"$UV_BIN" pip install -e .
green "  Python environment ready."

# ── 4. Install launchers ─────────────────────────────────────────────────────

step "Installing launchers"

mkdir -p "$HOME/.local/bin"

cat > "$LAUNCHER" <<EOF
#!/bin/bash
cd "$SCRIPT_DIR" && "$UV_BIN" run grabnotate "\$@"
EOF
chmod +x "$LAUNCHER"
green "  Installed: $LAUNCHER"

cat > "$LAUNCHER_SELECT" <<EOF
#!/bin/bash
cd "$SCRIPT_DIR" && "$UV_BIN" run grabnotate --select
EOF
chmod +x "$LAUNCHER_SELECT"
green "  Installed: $LAUNCHER_SELECT"

# ── 5. GNOME keyboard shortcuts (optional) ───────────────────────────────────

_register_keybinding() {
    local kpath="$1" name="$2" cmd="$3" binding="$4"
    local EXISTING
    EXISTING=$(gsettings get org.gnome.settings-daemon.plugins.media-keys custom-keybindings 2>/dev/null || echo "@as []")
    if ! echo "$EXISTING" | grep -q "$kpath"; then
        if [ "$EXISTING" = "@as []" ] || [ "$EXISTING" = "[]" ]; then
            NEW_LIST="['$kpath']"
        else
            NEW_LIST="${EXISTING%']'},'$kpath']"
        fi
        gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings "$NEW_LIST"
    fi
    gsettings set "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$kpath" name    "$name"
    gsettings set "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$kpath" command "$cmd"
    gsettings set "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$kpath" binding "$binding"
    green "  Registered shortcut '$binding' → $name"
}

if ! command -v gsettings &>/dev/null; then
    yellow "\ngsettings not available — skipping GNOME keyboard shortcuts."
else
    step "GNOME keyboard shortcuts (optional)"

    echo "  Open the main Grabnotate window:"
    echo "    1) Print"
    echo "    2) Ctrl+Print"
    echo "    3) Skip"
    read -rp "  Choice [1/2/3]: " choice
    case "$choice" in
        1) _register_keybinding "$KEYBINDING_PATH" "Grabnotate" "$LAUNCHER" "Print" ;;
        2) _register_keybinding "$KEYBINDING_PATH" "Grabnotate" "$LAUNCHER" "<Control>Print" ;;
        *) yellow "  Skipped." ;;
    esac

    echo ""
    echo "  Go straight to region capture (--select):"
    echo "    1) Print"
    echo "    2) Ctrl+Print"
    echo "    3) Shift+Print"
    echo "    4) Skip"
    read -rp "  Choice [1/2/3/4]: " choice2
    case "$choice2" in
        1) _register_keybinding "$KEYBINDING_SELECT_PATH" "Grabnotate (Select)" "$LAUNCHER_SELECT" "Print" ;;
        2) _register_keybinding "$KEYBINDING_SELECT_PATH" "Grabnotate (Select)" "$LAUNCHER_SELECT" "<Control>Print" ;;
        3) _register_keybinding "$KEYBINDING_SELECT_PATH" "Grabnotate (Select)" "$LAUNCHER_SELECT" "<Shift>Print" ;;
        *) yellow "  Skipped." ;;
    esac
fi

# ── Done ─────────────────────────────────────────────────────────────────────

printf '\n'
green "╔══════════════════════════════════════════╗"
green "║  Grabnotate installed successfully!      ║"
green "╚══════════════════════════════════════════╝"
echo ""
echo "  Run it now:"
echo "    grabnotate              # open main window"
echo "    grabnotate --select     # go straight to region capture"
echo ""
echo "  If 'grabnotate' is not found, add ~/.local/bin to your PATH:"
echo "    echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc && source ~/.bashrc"
