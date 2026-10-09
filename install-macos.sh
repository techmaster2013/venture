#!/bin/bash
set -euo pipefail

LABEL="com.venture.crawler"
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$REPO_DIR/logs"
PYTHON="$(command -v python3 || true)"

if [ "${1:-}" = "uninstall" ]; then
  launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "VentureBot LaunchAgent removed. The index database was left untouched."
  exit 0
fi

if [ -z "$PYTHON" ]; then
  echo "python3 was not found. Install Python 3 first, then run this script again."
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"
cd "$REPO_DIR"

"$PYTHON" crawler.py init --seeds-file "$REPO_DIR/seeds.txt"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>

  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/caffeinate</string>
    <string>-i</string>
    <string>$PYTHON</string>
    <string>$REPO_DIR/crawler.py</string>
    <string>run</string>
    <string>--db</string>
    <string>$REPO_DIR/venture.db</string>
    <string>--seeds-file</string>
    <string>$REPO_DIR/seeds.txt</string>
    <string>--delay</string>
    <string>2.0</string>
    <string>--recrawl-days</string>
    <string>7</string>
    <string>--max-depth</string>
    <string>6</string>
  </array>

  <key>WorkingDirectory</key>
  <string>$REPO_DIR</string>

  <key>RunAtLoad</key>
  <true/>

  <key>KeepAlive</key>
  <true/>

  <key>ProcessType</key>
  <string>Background</string>

  <key>LowPriorityIO</key>
  <true/>

  <key>Nice</key>
  <integer>10</integer>

  <key>StandardOutPath</key>
  <string>$LOG_DIR/crawler.log</string>

  <key>StandardErrorPath</key>
  <string>$LOG_DIR/crawler-error.log</string>
</dict>
</plist>
EOF

plutil -lint "$PLIST"
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null || true

if launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null; then
  launchctl enable "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl kickstart -k "gui/$(id -u)/$LABEL" 2>/dev/null || true
else
  launchctl load "$PLIST"
fi

echo ""
echo "VentureBot installed and started."
echo "Repo: $REPO_DIR"
echo "Index: $REPO_DIR/venture.db"
echo "Log: $LOG_DIR/crawler.log"
echo ""
echo "Check status with:"
echo "  cd \"$REPO_DIR\" && $PYTHON crawler.py stats"
echo ""
echo "Watch it live with:"
echo "  tail -f \"$LOG_DIR/crawler.log\""
