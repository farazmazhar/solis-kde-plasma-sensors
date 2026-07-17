#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# solis2kde — Install script for Fedora 40+ / KDE Plasma 6
# ---------------------------------------------------------------------------
# Clones and builds the ksystemstats_custom_sensors plugin from source,
# copies config files, and sets up the systemd user service.
# Building from source avoids library version mismatches (libsensors, etc.).
# ---------------------------------------------------------------------------

echo "==> Installing build dependencies ..."
sudo dnf install -y cmake extra-cmake-modules qt6-qtbase-devel \
  kf6-kcoreaddons-devel libksysguard-devel lm_sensors-devel

PLUGIN_SRC="/tmp/ksystemstats_custom_sensors"

if [ -d "$PLUGIN_SRC" ]; then
    echo "==> Updating existing clone ..."
    git -C "$PLUGIN_SRC" pull --ff-only
else
    echo "==> Cloning ksystemstats_custom_sensors ..."
    git clone https://github.com/vazh2100/ksystemstats_custom_sensors.git "$PLUGIN_SRC"
fi

echo "==> Building plugin ..."
BUILD_DIR="$PLUGIN_SRC/build"
mkdir -p "$BUILD_DIR"
cmake -S "$PLUGIN_SRC" -B "$BUILD_DIR" -DCMAKE_BUILD_TYPE=Release
cmake --build "$BUILD_DIR"

# Detect plugin install path
if [ -d "/usr/lib64/qt6/plugins/ksystemstats" ]; then
    PLUGIN_DEST="/usr/lib64/qt6/plugins/ksystemstats"
elif [ -d "/usr/lib/qt6/plugins/ksystemstats" ]; then
    PLUGIN_DEST="/usr/lib/qt6/plugins/ksystemstats"
else
    PLUGIN_DEST=$(find /usr -type d -path "*/qt6/plugins/ksystemstats" 2>/dev/null | head -1)
    if [ -z "$PLUGIN_DEST" ]; then
        echo "Error: cannot find ksystemstats plugin directory."
        echo "Create one with: sudo mkdir -p /usr/lib64/qt6/plugins/ksystemstats"
        exit 1
    fi
fi

echo "==> Installing plugin to $PLUGIN_DEST ..."
sudo cp "$BUILD_DIR/bin/ksystemstats_plugin.so" "$PLUGIN_DEST/"

echo "==> Installing solis2kde.py to ~/.local/bin/ ..."
mkdir -p "$HOME/.local/bin"
cp solis2kde.py "$HOME/.local/bin/solis2kde.py"
chmod 755 "$HOME/.local/bin/solis2kde.py"

echo "==> Installing customsensorrc to ~/.config/ ..."
mkdir -p "$HOME/.config"
cp customsensorrc "$HOME/.config/customsensorrc"
chmod 600 "$HOME/.config/customsensorrc"

echo "==> Creating config directory and template ..."
mkdir -p "$HOME/.config/solis2kde"
if [ ! -f "$HOME/.config/solis2kde/config.yaml" ]; then
    cat > "$HOME/.config/solis2kde/config.yaml" <<- 'YAML'
# SolisCloud API credentials
# Get these from https://www.soliscloud.com/ -> Personal -> API Management
api_key: "YOUR_API_KEY"
api_secret: "YOUR_API_SECRET"

# Your station ID — find it on the SolisCloud dashboard URL or via
#   python3 ~/.local/bin/solis2kde.py --discover
station_id: "YOUR_STATION_ID"

# Polling interval in seconds (minimum 300 for SolisCloud free tier)
poll_interval: 300
YAML
    chmod 600 "$HOME/.config/solis2kde/config.yaml"
    echo "    Created $HOME/.config/solis2kde/config.yaml — EDIT WITH YOUR CREDENTIALS"
else
    echo "    $HOME/.config/solis2kde/config.yaml already exists — skipping"
fi

echo "==> Installing systemd user service ..."
mkdir -p "$HOME/.config/systemd/user"
cp solis2kde.service "$HOME/.config/systemd/user/solis2kde.service"
chmod 644 "$HOME/.config/systemd/user/solis2kde.service"
systemctl --user daemon-reload

echo "==> Restarting ksystemstats ..."
if systemctl --user is-active plasma-ksystemstats.service &>/dev/null; then
    systemctl --user restart plasma-ksystemstats.service
else
    echo "    (plasma-ksystemstats.service not running — will activate on next login)"
fi

echo ""
echo "========================================================================="
echo "  Installation complete!"
echo "========================================================================="
echo ""
echo "  NEXT STEPS:"
echo ""
echo "  1. Edit your API credentials:"
echo "       nano ~/.config/solis2kde/config.yaml"
echo ""
echo "  2. (Optional) Test API connection and discover field names:"
echo "       python3 ~/.local/bin/solis2kde.py --discover"
echo ""
echo "  3. Start the daemon:"
echo "       systemctl --user enable --now solis2kde.service"
echo ""
echo "  4. Check logs:"
echo "       journalctl --user -u solis2kde -f"
echo ""
echo "  5. Open KDE System Monitor, add the 6 new sensors:"
echo "       Page -> Edit -> Add Sensor -> look for Solar Generation,"
echo "       Grid Power, Load Consumption, Battery SOC, Energy Today,"
echo "       and Total Energy."
echo ""
echo "  Config files containing secrets are created with mode 0600."
echo "========================================================================="
