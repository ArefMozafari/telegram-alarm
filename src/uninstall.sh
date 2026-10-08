#!/bin/bash
# Stops the notifier agent and removes it from login items. Project files are kept.
set -uo pipefail
LABEL="io.github.arefmozafari.telegram-alarm"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
echo "Stopped and removed the $LABEL agent (project files kept)."
