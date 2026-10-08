#!/bin/bash
# Stops the notifier agent and removes it from login items. Project files are kept.
set -uo pipefail
LABEL="com.kaya.notifier2"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
# Also clean up the old pre-2026-08 label, in case it's still registered
# somewhere (macOS got that one permanently stuck refusing to spawn it).
launchctl bootout "gui/$(id -u)/com.kaya.notifier" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/com.kaya.notifier.plist"
echo "Stopped and removed the $LABEL agent (project files kept)."
