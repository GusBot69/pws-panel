#!/usr/bin/env bash
# PWS Panel installer — run on the Fedora laptop (GNOME 50).
# Installs: pws-bridge (systemd user service, 127.0.0.1:8766) + GNOME extension.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UUID="pws-panel@fedora"
BRIDGE_DIR="$HOME/.local/share/pws-bridge"
EXT_DIR="$HOME/.local/share/gnome-shell/extensions/$UUID"

echo "==> Installing PWS bridge to $BRIDGE_DIR"
mkdir -p "$BRIDGE_DIR"
cp "$SRC/bridge/pws-bridge.py" "$BRIDGE_DIR/"
# Fetchers must live next to the bridge (same code /pws uses)
cp "$SRC/bridge/pws_fetcher.py" "$BRIDGE_DIR/"
cp "$SRC/bridge/aqi_fetcher.py" "$BRIDGE_DIR/"

echo "==> Installing systemd user unit"
mkdir -p "$HOME/.config/systemd/user"
cp "$SRC/systemd/pws-bridge.service" "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
# restart, not just enable --now: if the service is already running (from a
# previous install), enable --now leaves the OLD process alive on the port and
# the extension talks to a zombie bridge with stale code.
systemctl --user enable pws-bridge.service
systemctl --user restart pws-bridge.service

echo "==> Waiting for bridge to come up"
for i in $(seq 1 10); do
    if curl -fsS --max-time 2 http://127.0.0.1:8766/health >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

echo "==> Installing GNOME extension to $EXT_DIR"
mkdir -p "$EXT_DIR"
cp "$SRC/extension/metadata.json" "$EXT_DIR/"
cp "$SRC/extension/extension.js" "$EXT_DIR/"
cp "$SRC/extension/stylesheet.css" "$EXT_DIR/"
cp "$SRC/extension/prefs.js" "$EXT_DIR/"

echo "==> Installing GSettings schema"
mkdir -p "$HOME/.local/share/glib-2.0/schemas"
cp "$SRC/extension/schemas/org.gnome.shell.extensions.pws-panel.gschema.xml" \
    "$HOME/.local/share/glib-2.0/schemas/"
SCHEMA_OUT="$(glib-compile-schemas --strict "$HOME/.local/share/glib-2.0/schemas" 2>&1)" || true
if [ -n "$SCHEMA_OUT" ]; then
    echo "!! Schema compile reported:"
    echo "$SCHEMA_OUT"
    echo "!! This will break the extension — fix the gschema.xml before proceeding."
    exit 1
fi
echo "    schema compiled OK"

echo "==> Enabling extension"
if command -v gnome-extensions >/dev/null 2>&1; then
    gnome-extensions enable "$UUID" 2>/dev/null || \
        echo "    (enable after next login:  gnome-extensions enable $UUID)"
else
    echo "    (gnome-extensions not on PATH — enable via Extensions app or after login)"
fi

echo
echo "==> Bridge health:"
curl -fsS http://127.0.0.1:8766/health || echo "!! bridge not responding"
echo
echo "==> Sample /pws payload:"
curl -fsS http://127.0.0.1:8766/pws | python3 -m json.tool | head -20 || echo "!! no data yet"
echo
echo "==> Service status:"
systemctl --user status pws-bridge.service --no-pager | head -6
echo
echo "DONE. The top bar should show the temp next to the clock."
echo "If not, restart the shell:  Alt+F2 -> 'r'  (or log out/in)"
