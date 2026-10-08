#!/bin/bash
# Telegram Alarm — one-click setup, health check, and live log viewer.
# Double-click this file in Finder: it opens a Terminal window, sets up anything
# that's missing (Python env, credentials, Telegram login, background agent),
# verifies the notifier is listening, and then shows the live log.
set -u
cd "$(dirname "$0")/src" || exit 1
PYBIN="venv/bin/python"
LABEL="io.github.arefmozafari.telegram-alarm"
printf '\e]0;Telegram Alarm\a'

hr()   { echo "----------------------------------------------------------------"; }
step() { echo; hr; echo "== $1"; hr; }
die()  { echo "[!!] $1"; echo; read -r -p "Press Enter to close... "; exit 1; }

step "Telegram Alarm — setup & status"

# 1. Python environment (created on first run, or if the folder was moved to a new Mac)
if [ ! -x "$PYBIN" ]; then
  echo "[..] Python environment missing — creating it, this takes a minute..."
  SYSPY="/opt/homebrew/bin/python3"
  [ -x "$SYSPY" ] || SYSPY="/usr/local/bin/python3"
  [ -x "$SYSPY" ] || SYSPY="/usr/bin/python3"
  "$SYSPY" -m venv venv && "$PYBIN" -m pip install --quiet -r requirements.txt \
    || die "Could not set up the Python environment."
  echo "[OK] Python environment ready."
else
  echo "[OK] Python environment present."
fi

# 2. Credentials. A fresh clone has no config.json yet, so start one from the
#    example; it holds secrets once filled in, hence owner-only permissions.
if [ ! -f config.json ]; then
  cp config.example.json config.json && chmod 600 config.json
fi
until "$PYBIN" notifier.py --check-config; do
  echo "[!!] config.json still needs your Telegram api_id, api_hash and the chats to watch."
  echo "     1. Log in at https://my.telegram.org -> 'API development tools'"
  echo "     2. Copy api_id and api_hash into config.json (opening it for you now)."
  echo "     3. Replace PASTE_CHAT_USERNAME under \"chats\" with the bots, people,"
  echo "        groups or channels to watch."
  open -t config.json 2>/dev/null || true
  echo
  read -r -p "Press Enter once you've saved config.json (Ctrl+C to quit)... "
done
echo "[OK] Credentials and chats configured."

# 3. One-time Telegram login
if [ ! -f telegram_alarm.session ]; then
  step "One-time Telegram login"
  "$PYBIN" notifier.py --login || die "Login didn't complete — double-click this script to try again."
  chmod 600 telegram_alarm.session 2>/dev/null
fi
echo "[OK] Logged in to Telegram (session saved)."

# 4. Install the background agent if it's not already up (skips the
#    kill+restart dance when it's already installed and healthy — see
#    install.sh for why that matters); pass --force to always restart.
step "Background agent"
LOG_MARK=$(wc -c < notifier.log 2>/dev/null || echo 0)
INSTALL_OUT=$(./install.sh "$@" 2>&1) || die "Could not install the launchd agent.
$INSTALL_OUT"
echo "$INSTALL_OUT"
ALREADY_RUNNING=""
case "$INSTALL_OUT" in
  "Already installed and running"*) ALREADY_RUNNING=1 ;;
esac

# 5. Health check (state is briefly "xpcproxy" while launchd spawns the process, so retry)
step "Health check"
STATE=""
for _ in $(seq 1 10); do
  STATE=$(launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | grep -m1 -E '^[[:space:]]*state = ' | sed 's/.*= //')
  [ "$STATE" = "running" ] && break
  sleep 1
done
PID=$(launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | grep -m1 -E '^[[:space:]]*pid = ' | sed 's/.*= //')
if [ "${STATE:-}" = "running" ]; then
  echo "[OK] Agent running (pid ${PID:-?}) — starts at every login, restarts if it crashes."
else
  echo "[!!] Agent state: ${STATE:-not loaded} — check launchd.log below."
fi
if [ -n "$ALREADY_RUNNING" ]; then
  LISTEN=$(grep "listening" notifier.log 2>/dev/null | tail -1 || true)
else
  LISTEN=""
  for _ in $(seq 1 90); do
    LISTEN=$(tail -c +$((LOG_MARK + 1)) notifier.log 2>/dev/null | grep -m1 "listening" || true)
    [ -n "$LISTEN" ] && break
    sleep 1
  done
fi
if [ -n "$LISTEN" ]; then
  echo "[OK] ${LISTEN#*INFO }"
else
  echo "[!!] Didn't see a fresh 'listening' line within 90s (a slow proxy can"
  echo "     take longer — watch the log below) — recent log:"
  tail -5 notifier.log 2>/dev/null
  tail -5 launchd.log 2>/dev/null
fi

# 6. Optional test alert
echo
read -r -p "Fire a test alert now (fullscreen flash + alarm)? [y/N] " yn
case "${yn:-n}" in
  [Yy]*) "$PYBIN" notifier.py --test ;;
esac

# 7. Live log
step "Live log — every new message alert appears here"
echo "Closing this window does NOT stop the notifier; it keeps running in the background."
echo "Press Ctrl+C (or just close the window) to stop watching."
echo
if [ "${TELEGRAM_ALARM_NO_TAIL:-}" = "1" ]; then
  tail -n 15 notifier.log
else
  tail -n 15 -f notifier.log
fi
