#!/bin/bash
# Telegram Alarm — full stop. Kills the background agent (and removes it from
# login items) plus any alert that's on screen or mid-sound right now.
# Handy right before putting the Mac to sleep / closing the lid, so a
# message that arrives at that instant can't still flash or play a sound.
# Project files, config, and your Telegram login are kept — double-click
# "Telegram Alarm.command" any time to start it back up.
set -u
cd "$(dirname "$0")/src" || exit 1
DIR="$(pwd)"
printf '\e]0;Stop Telegram Alarm\a'

hr()   { echo "----------------------------------------------------------------"; }
step() { echo; hr; echo "== $1"; hr; }

step "Stopping Telegram Alarm"

# 1. Stop the launchd agent and remove it from login items.
./uninstall.sh

# 2. Kill any stray manual run of the listener (shouldn't normally exist
#    alongside the agent, but don't leave one behind).
pkill -f "$DIR/notifier.py" 2>/dev/null && echo "[OK] Stopped a manually-running notifier.py."

# 3. Kill anything an alert already kicked off: the on-screen flash/island,
#    the alarm sound, and any spoken phrase.
pkill -f "$DIR/flash.py" 2>/dev/null && echo "[OK] Closed an on-screen alert."
pkill afplay 2>/dev/null && echo "[OK] Silenced the alarm sound."
pkill -x say 2>/dev/null && echo "[OK] Stopped a spoken alert."

# 4. An alert may have temporarily raised the system volume (min_volume) and
#    not gotten to put it back before the kill above — fix that now instead
#    of leaving the Mac loud.
[ -x "./venv/bin/python" ] && ./venv/bin/python notifier.py --restore-volume

echo
echo "[OK] Telegram Alarm is fully stopped — safe to sleep or close the lid now."
echo "     Double-click \"Telegram Alarm.command\" to start it again."
read -r -p "Press Enter to close... "
