#!/bin/bash
# Installs (or restarts) the notifier as a launchd user agent: starts at login,
# restarts automatically if it crashes.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.kaya.notifier2"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
# launchd's stdout/stderr redirect has to live outside ~/Desktop (and Documents/
# Downloads): those folders are TCC-protected, and xpcproxy — the stub launchd
# spawns jobs through — has no grant for them, so it silently gets
# "deny(1) file-read-data" from the macOS Sandbox on the redirect target and the
# job never starts (posix_spawn error 0x1). Exec'ing the venv python / script
# itself from inside Desktop is fine; only this log redirect is affected.
LOGDIR="$HOME/Library/Logs/kaya-notifier"

if [ ! -f "$DIR/kaya_session.session" ]; then
  echo "Not logged in to Telegram yet. Run this first and complete the login:"
  echo "  $DIR/venv/bin/python $DIR/notifier.py --login"
  exit 1
fi

# Guard rail: if a future edit ever points LOGDIR back inside a TCC-protected
# folder, fail loudly here instead of installing a job that silently never spawns.
for protected in "$HOME/Desktop" "$HOME/Documents" "$HOME/Downloads"; do
  case "$LOGDIR" in
    "$protected"|"$protected"/*)
      echo "[!!] LOGDIR ($LOGDIR) is inside $protected, which is TCC-protected."
      echo "     launchd's xpcproxy has no access grant there, so the agent would fail"
      echo "     to spawn with 'posix_spawn ... Operation not permitted'. Point LOGDIR"
      echo "     somewhere unprotected (e.g. ~/Library/Logs/...) and try again."
      exit 1
      ;;
  esac
done

mkdir -p "$LOGDIR"
# Convenience symlink so "src/launchd.log" still works for tailing/grepping;
# the real file (launchd's actual redirect target) lives in $LOGDIR.
if [ -e "$DIR/launchd.log" ] && [ ! -L "$DIR/launchd.log" ]; then
  rm -f "$DIR/launchd.log"
fi
ln -sf "$LOGDIR/launchd.log" "$DIR/launchd.log"

NEW_PLIST_CONTENT="$(sed "s|__DIR__|$DIR|g; s|__LOGDIR__|$LOGDIR|g" "$DIR/com.kaya.notifier.plist.template")"

# If it's already installed for this folder and actually running, leave it
# alone — no need to bootout+bootstrap a job that's already healthy. Pass
# --force to restart anyway (e.g. right after editing config.json).
if [ "${1:-}" != "--force" ] && [ -f "$PLIST" ] && [ "$(cat "$PLIST" 2>/dev/null)" = "$NEW_PLIST_CONTENT" ]; then
  STATE=$(launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | grep -m1 -E '^[[:space:]]*state = ' | sed 's/.*= //' || true)
  if [ "$STATE" = "running" ]; then
    PID=$(launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | grep -m1 -E '^[[:space:]]*pid = ' | sed 's/.*= //')
    echo "Already installed and running (pid $PID) — leaving it alone."
    echo "(changed config.json? run './install.sh --force' to pick it up)"
    exit 0
  fi
fi

mkdir -p "$HOME/Library/LaunchAgents"
printf '%s\n' "$NEW_PLIST_CONTENT" > "$PLIST"

# Kill whatever is currently running: the agent-managed instance first, then
# any stray manual runs (they'd fight over the session file).
OLD_PID=$(launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | grep -m1 -E '^[[:space:]]*pid = ' | sed 's/.*= //' || true)
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
# Wait for launchd to finish tearing down the old instance; bootstrapping too
# soon after a bootout intermittently fails with "5: Input/output error".
for _ in $(seq 1 20); do
  launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || break
  sleep 0.5
done
pkill -f "$DIR/notifier.py" 2>/dev/null || true
for _ in $(seq 1 10); do
  pgrep -f "$DIR/notifier.py" >/dev/null 2>&1 || break
  sleep 0.5
done
if [ -n "$OLD_PID" ]; then
  echo "Stopped the running notifier (pid $OLD_PID)."
else
  echo "No notifier was running under the agent."
fi
started=""
for _ in $(seq 1 5); do
  if launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null; then
    started=1
    break
  fi
  sleep 1
done
if [ -z "$started" ]; then
  launchctl load -w "$PLIST" || { echo "Could not start the agent."; exit 1; }
fi

# launchctl accepting the plist only means launchd scheduled the job — the
# process itself can still fail to spawn (e.g. macOS Sandbox denying xpcproxy
# access to a path the job references). Confirm it actually reached "running"
# before declaring success; if it didn't, diagnose instead of lying about it.
STATE=""
for _ in $(seq 1 10); do
  STATE=$(launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | grep -m1 -E '^[[:space:]]*state = ' | sed 's/.*= //' || true)
  [ "$STATE" = "running" ] && break
  sleep 1
done
NEW_PID=$(launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | grep -m1 -E '^[[:space:]]*pid = ' | sed 's/.*= //' || true)

if [ "$STATE" != "running" ]; then
  echo "[!!] launchd accepted the job but it never reached 'running' (state: ${STATE:-none})."
  SPAWN_ERR=$(/usr/bin/log show --last 2m --predicate "eventMessage CONTAINS \"$LABEL\" AND eventMessage CONTAINS \"posix_spawn\"" 2>/dev/null | grep -m1 "posix_spawn" || true)
  if [ -n "$SPAWN_ERR" ]; then
    echo "    ${SPAWN_ERR#*launchd: }"
    DENY=$(/usr/bin/log show --last 2m --predicate 'eventMessage CONTAINS "Sandbox" AND eventMessage CONTAINS "deny"' 2>/dev/null | grep -m1 "$DIR" || true)
    if [ -n "$DENY" ]; then
      echo "    Cause: macOS Sandbox is blocking a path this job references:"
      echo "    ${DENY#*deny}"
      echo "    That path is likely inside a TCC-protected folder (Desktop/Documents/"
      echo "    Downloads) that launchd's xpcproxy has no access grant for. Move it"
      echo "    outside (e.g. into ~/Library/Logs/...) and run './install.sh --force'."
    else
      echo "    Run: log show --last 5m --predicate 'eventMessage CONTAINS \"$LABEL\"' for details."
    fi
  fi
  exit 1
fi
echo "Started a fresh notifier (pid ${NEW_PID:-?}). Logs: tail -f $DIR/notifier.log"
