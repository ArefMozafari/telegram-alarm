#!/usr/bin/env python3
"""Fullscreen-flash + alarm-sound alerts for new Telegram messages from chosen chats.

Usage:
  notifier.py --test          Preview a new-message alert (no Telegram needed)
  notifier.py --test-warning  Preview a connection-problem alert
  notifier.py --proxy-check   Show which proxy would be used right now
  notifier.py --check-config  Exit 0 if config.json is filled in, 1 if not
  notifier.py --login         One-time interactive Telegram login, then exit
  notifier.py --restore-volume  Fix a volume left raised by an interrupted alert, then exit
  notifier.py                 Run the listener (this is what the launchd agent runs)

The listener supervises its own connection. A watchdog tracks the last proof
of life (successful ping, incoming message, completed handshake) and fires a
distinct amber "connection problem" alert once that proof is
`connection_alert_after_seconds` stale — re-alerted every
`connection_realert_minutes` while down. Every reconnect attempt re-reads the
macOS system proxy so proxy changes are picked up live.
"""
import asyncio
import json
import logging
import logging.handlers
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

try:
    import Quartz
    HAVE_QUARTZ = True
except ImportError:
    HAVE_QUARTZ = False

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
SESSION_PATH = BASE / "telegram_alarm"
VOLUME_RESTORE_PATH = BASE / "volume_restore.json"

AFPLAY = "/usr/bin/afplay"
OSASCRIPT = "/usr/bin/osascript"
SAY = "/usr/bin/say"
SCUTIL = "/usr/sbin/scutil"
FALLBACK_SOUND = "/System/Library/Sounds/Sosumi.aiff"

# The example config's stand-in for the chats to watch; a config that still
# holds it has not been filled in yet.
EXAMPLE_CHAT = "PASTE_CHAT_USERNAME"
# Marks where the sending chat's name goes in the configured headline.
CHAT_FIELD = "{chat}"
# Shown when the configured headline would otherwise come out empty.
GENERIC_HEADLINE = "New message"

log = logging.getLogger("telegram-alarm")


def configure_logging():
    # Just the rotating file — launchd already redirects this process's stdout
    # and stderr to launchd.log, so a StreamHandler here would only duplicate
    # every line into both files. Called from main() rather than at import, so
    # importing this module (as the tests do) never creates notifier.log.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.handlers.RotatingFileHandler(
                BASE / "notifier.log", maxBytes=5_000_000, backupCount=3),
        ],
    )
    # The supervisor logs every state change itself; telethon's own chatter
    # (catch-up warnings for every channel, connect/disconnect info) is noise.
    logging.getLogger("telethon").setLevel(logging.ERROR)


def load_config():
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        log.exception("config.json is missing or not valid JSON — fix it and restart")
        raise


def watched_chats(cfg):
    """Returns the chats to watch: usernames without the leading @, or numeric IDs.

    Reads the `chats` list, falling back to the single `bot_username` setting
    that configs written before multi-chat support still carry.
    """
    chats = cfg.get("chats")
    if chats is None:
        bot_username = cfg.get("bot_username")
        chats = [bot_username] if bot_username else []
    return [chat.strip().lstrip("@") if isinstance(chat, str) else chat for chat in chats]


def config_ready(cfg):
    api_id, api_hash = cfg.get("api_id"), cfg.get("api_hash", "")
    chats = watched_chats(cfg)
    return (
        isinstance(api_id, int)
        and api_id > 0
        and isinstance(api_hash, str)
        and len(api_hash) >= 10
        and "PASTE" not in api_hash.upper()
        and bool(chats)
        and EXAMPLE_CHAT not in chats
    )


def alert_headline(cfg, chat_name):
    """Returns the new-message headline with the sending chat's name filled in.

    Falls back to a generic headline when that leaves nothing to show, e.g. a
    chat without a name.
    """
    headline = str(cfg.get("headline", CHAT_FIELD)).replace(CHAT_FIELD, chat_name).strip()
    return headline or GENERIC_HEADLINE


def osascript(expr):
    r = subprocess.run([OSASCRIPT, "-e", expr], capture_output=True, text=True)
    return r.stdout.strip()


def get_output_volume():
    # osascript returns e.g. "output volume:44, input volume:75, alert volume:100, output muted:false"
    try:
        parts = dict(p.split(":", 1) for p in osascript("get volume settings").split(", "))
        return int(parts["output volume"]), parts["output muted"] == "true"
    except Exception:
        return None, None


def restore_volume(volume, muted):
    state = "with output muted" if muted else "without output muted"
    osascript(f"set volume output volume {volume} {state}")
    VOLUME_RESTORE_PATH.unlink(missing_ok=True)


def restore_stray_volume():
    """Finish a volume restore an earlier run didn't get to — e.g. it was
    killed (Stop Telegram Alarm.command, a crash, a forced restart) while an
    alert had temporarily raised the volume for min_volume. Safe to call on
    every startup: a no-op when nothing was left boosted."""
    try:
        data = json.loads(VOLUME_RESTORE_PATH.read_text())
    except FileNotFoundError:
        return None
    except Exception:
        log.exception("stray volume_restore.json is unreadable — removing it")
        VOLUME_RESTORE_PATH.unlink(missing_ok=True)
        return None
    restore_volume(data["volume"], data["muted"])
    log.info("Restored volume to %d%% left over from an interrupted alert", data["volume"])
    return data["volume"]


def sound_worker(cfg, sound_name, speak_phrase):
    restore = None
    try:
        min_volume = cfg.get("min_volume") or 0
        if min_volume:
            volume, muted = get_output_volume()
            if volume is not None and (muted or volume < min_volume):
                restore = (volume, muted)
                # Written before boosting (not after) so a kill mid-alert
                # leaves a record another run can finish the restore from.
                VOLUME_RESTORE_PATH.write_text(json.dumps({"volume": volume, "muted": muted}))
                osascript(f"set volume output volume {max(volume, min_volume)} without output muted")
        if cfg.get("sound_enabled", True):
            sound = BASE / sound_name
            if not sound.is_file():
                sound = Path(FALLBACK_SOUND)
            for _ in range(max(1, int(cfg.get("sound_repeats", 2)))):
                r = subprocess.run([AFPLAY, str(sound)], capture_output=True)
                if r.returncode:
                    log.warning("afplay failed (%s): %s", r.returncode,
                                r.stderr.decode(errors="replace").strip())
                    break
        if cfg.get("speak_enabled") and speak_phrase:
            subprocess.run([SAY, speak_phrase], capture_output=True)
    except Exception:
        log.exception("sound alert failed")
    finally:
        if restore:
            restore_volume(*restore)


def show_banner(cfg, text, title):
    if not cfg.get("banner_enabled", True):
        return
    safe = text.replace("\\", " ").replace('"', "'").replace("\n", "  ")[:120]
    osascript(f'display notification "{safe}" with title "{title}"')


def display_is_asleep():
    """True while the display is off — including the brief background wakes
    (Power Nap) that let this process keep running with the lid closed, which
    is exactly when a flash + alarm would land unseen and unwelcome."""
    if not HAVE_QUARTZ:
        return False
    try:
        return bool(Quartz.CGDisplayIsAsleep(Quartz.CGMainDisplayID()))
    except Exception:
        return False


_last_project_alert = 0.0


def alert(cfg, text, kind="project", chat_name=""):
    global _last_project_alert
    project = kind == "project"
    headline = alert_headline(cfg, chat_name) if project else "Connection problem!"
    banner_title = headline if project else "Telegram Alarm: connection problem"
    first_line = next((l for l in (text or "").splitlines() if l.strip()), "(no text)")
    (log.info if project else log.warning)("ALERT[%s]%s: %s", kind,
                                           f" {chat_name}" if chat_name else "",
                                           first_line[:200])
    show_banner(cfg, text or "", banner_title)
    if project:
        if cfg.get("mute_while_asleep", True) and display_is_asleep():
            log.info("Display asleep — skipping flash/sound for this alert (banner only)")
            return
        # After a reconnect Telegram may deliver several missed messages at
        # once; only the first in a burst gets the full flash + alarm. The
        # cooldown is anchored to the last alert that actually fired, not to
        # every suppressed message — otherwise a steady stream of distinct
        # projects arriving faster than the cooldown would mute every alert
        # after the first one, forever.
        now = time.monotonic()
        if now - _last_project_alert < cfg.get("burst_cooldown_seconds", 15):
            return
        _last_project_alert = now
    if cfg.get("flash_enabled", True):
        try:
            with open(BASE / "flash.err.log", "ab") as errlog:
                subprocess.Popen(
                    [sys.executable, str(BASE / "flash.py"), headline,
                     (text or "")[:1200], str(cfg.get("flash_seconds", 12)),
                     str(cfg.get("flash_style", "island")),
                     str(cfg.get("bar_height", 150)), kind],
                    stdout=subprocess.DEVNULL, stderr=errlog,
                )
        except Exception:
            log.exception("flash failed")
    if project:
        sound_name = cfg.get("sound_file", "alarm.wav")
        phrase = cfg.get("speak_text", "New Telegram message")
    else:
        sound_name = cfg.get("warning_sound_file", "warning.wav")
        phrase = "Telegram alarm connection problem"
    threading.Thread(target=sound_worker, args=(cfg, sound_name, phrase)).start()


def warn_alert(cfg, text):
    alert(cfg, text, kind="warning")


# --------------------------------------------------------------------------
# proxy handling
# --------------------------------------------------------------------------

def _proxy_dict(kind, addr, port):
    try:
        from python_socks import ProxyType
    except ImportError:
        log.error("python-socks not installed — cannot use a proxy, going direct")
        return None
    types = {"socks5": ProxyType.SOCKS5, "socks4": ProxyType.SOCKS4, "http": ProxyType.HTTP}
    return {"proxy_type": types[kind], "addr": addr, "port": int(port), "rdns": True}


def detect_system_proxy():
    """Read the macOS system proxy via scutil. SOCKS wins over HTTPS/HTTP."""
    try:
        out = subprocess.run([SCUTIL, "--proxy"], capture_output=True, text=True,
                             timeout=10).stdout
    except Exception as exc:
        log.warning("scutil --proxy failed: %s", exc)
        return None

    def val(key):
        m = re.search(rf"^\s*{key}\s*:\s*(\S+)", out, re.MULTILINE)
        return m.group(1) if m else None

    if val("ProxyAutoConfigEnable") == "1":
        log.info("System uses a PAC auto-proxy, which isn't supported — trying direct")
    if val("SOCKSEnable") == "1" and val("SOCKSProxy"):
        return _proxy_dict("socks5", val("SOCKSProxy"), val("SOCKSPort") or 1080)
    if val("HTTPSEnable") == "1" and val("HTTPSProxy"):
        return _proxy_dict("http", val("HTTPSProxy"), val("HTTPSPort") or 8080)
    if val("HTTPEnable") == "1" and val("HTTPProxy"):
        return _proxy_dict("http", val("HTTPProxy"), val("HTTPPort") or 8080)
    return None


def resolve_proxy(cfg):
    """Config 'proxy': "system" (default), "none", or "socks5|socks4|http://host:port"."""
    setting = str(cfg.get("proxy", "system") or "none").strip().lower()
    if setting in ("none", ""):
        return None
    if setting == "system":
        return detect_system_proxy()
    m = re.match(r"^(socks5|socks4|http)://([^:/]+):(\d+)$", setting)
    if m:
        return _proxy_dict(m.group(1), m.group(2), m.group(3))
    log.error("Unrecognized proxy setting %r — going direct", setting)
    return None


def proxy_desc(proxy):
    if not proxy:
        return "direct connection"
    name = getattr(proxy.get("proxy_type"), "name", "proxy").lower()
    return f"{name} proxy {proxy['addr']}:{proxy['port']}"


# --------------------------------------------------------------------------
# supervised listener
# --------------------------------------------------------------------------

async def run(cfg, login_only=False):
    from telethon import TelegramClient, events, utils
    from telethon.tl import functions

    interval = max(5, int(cfg.get("health_check_seconds", 15)))
    warn_after = max(10, int(cfg.get("connection_alert_after_seconds", 20)))
    realert = int(cfg.get("connection_realert_minutes", 10)) * 60

    state = {
        "last_ok": time.monotonic(),  # last proof the connection worked
        "was_down": False,
        "last_warn": 0.0,
    }

    async def watchdog():
        # Fires the warning purely from staleness of last_ok — independent of
        # where the reconnect loop happens to be (even hung mid-connect) — so
        # the alert lands ~connection_alert_after_seconds after the last
        # proof of life.
        prev = time.monotonic()
        while True:
            await asyncio.sleep(2)
            now = time.monotonic()
            if now - prev > 30:
                # The Mac slept; give the connection a fresh grace period
                # instead of alerting the instant it wakes.
                state["last_ok"] = now
            prev = now
            stale = now - state["last_ok"]
            if stale < warn_after:
                continue
            if state["was_down"] and not (realert > 0 and now - state["last_warn"] >= realert):
                continue
            state["was_down"] = True
            state["last_warn"] = now
            warn_alert(cfg, "Can't reach Telegram — no connection for ~%ds.\n"
                            "New messages will NOT alert until it's back." % int(stale))

    watchdog_task = None
    if not login_only:
        watchdog_task = asyncio.create_task(watchdog())

    attempt = 0
    while True:
        # Re-resolved on every (re)connect so proxy changes made while the
        # notifier is running are picked up as soon as the old path dies.
        proxy = resolve_proxy(cfg)
        client = TelegramClient(
            str(SESSION_PATH), cfg["api_id"], cfg["api_hash"],
            proxy=proxy, connection_retries=1, retry_delay=2,
            request_retries=2, timeout=10, catch_up=True,
        )
        try:
            async def setup():
                await client.connect()
                # A completed MTProto handshake proves the network path works,
                # well before the (slow) catch-up finishes.
                state["last_ok"] = time.monotonic()
                if not await client.is_user_authorized():
                    if sys.stdin.isatty():
                        print("First-time Telegram login: enter your phone number (with country code),")
                        print("then the code Telegram sends you, and your 2FA password if you have one.")
                        await client.start()
                    else:
                        log.error("Telegram session missing or expired. Run: %s %s --login",
                                  sys.executable, BASE / "notifier.py")
                        warn_alert(cfg, "Telegram session expired — message alerts are DOWN.\n"
                                        "Run the login again (see README).")
                        sys.exit(1)
                me = await client.get_me()
                entities = []
                # Keyed by the same marked peer ID that event.chat_id carries,
                # so an incoming message can name the chat it came from.
                names = {}
                for chat in watched_chats(cfg):
                    try:
                        entity = await client.get_entity(chat)
                    except Exception as exc:
                        log.error("Couldn't find %s (%s). Check chats in config.json.",
                                  chat, exc)
                        if login_only:
                            sys.exit(1)
                        raise
                    entities.append(entity)
                    names[utils.get_peer_id(entity)] = utils.get_display_name(entity) or str(chat)
                return me, entities, names

            # Watchdog timeout on the whole setup phase: connect + catch-up +
            # entity lookup can hang indefinitely on a dead proxy.
            # Interactive logins are exempt (they wait on keyboard input).
            if sys.stdin.isatty():
                me, entities, names = await setup()
            else:
                me, entities, names = await asyncio.wait_for(setup(), timeout=120)
            watching = ", ".join(names.values())

            if login_only:
                log.info("Logged in as %s via %s; found %s. Session saved — now run install.sh",
                         me.first_name, proxy_desc(proxy), watching)
                await client.disconnect()
                return

            @client.on(events.NewMessage(chats=entities, incoming=True))
            async def on_message(event):
                state["last_ok"] = time.monotonic()
                alert(cfg, event.raw_text or "", kind="project",
                      chat_name=names.get(event.chat_id, ""))

            state["last_ok"] = time.monotonic()
            if state["was_down"]:
                log.info("Connection restored — message alerts active again")
                show_banner(cfg, "Connection restored — message alerts are active again.",
                            "Telegram Alarm reconnected")
                state["was_down"] = False
            attempt = 0
            log.info("Logged in as %s — listening for %s (%s)",
                     me.first_name, watching, proxy_desc(proxy))

            fails = 0
            while True:
                await asyncio.sleep(interval)
                try:
                    await asyncio.wait_for(client(functions.updates.GetStateRequest()),
                                           timeout=8)
                    state["last_ok"] = time.monotonic()
                    fails = 0
                except (asyncio.CancelledError, KeyboardInterrupt):
                    raise
                except Exception as exc:
                    fails += 1
                    log.warning("Health check failed (%d/2): %r", fails, exc)
                    if fails >= 2:
                        raise ConnectionError(f"health checks failing: {exc!r}")

        except (KeyboardInterrupt, asyncio.CancelledError, SystemExit):
            raise
        except Exception as exc:
            log.error("Connection problem: %r — reconnecting with fresh proxy settings", exc)
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass
        attempt += 1
        await asyncio.sleep(min(15.0, 3.0 + attempt * 3.0))


def main():
    configure_logging()
    restored = restore_stray_volume()
    if "--restore-volume" in sys.argv:
        if restored is not None:
            print(f"Restored volume to {restored}% (left over from an interrupted alert).")
        else:
            print("Nothing to restore — volume wasn't left boosted.")
        return
    if "--check-config" in sys.argv:
        # Quiet on purpose: Telegram Alarm.command only reads the exit status.
        try:
            sys.exit(0 if config_ready(json.loads(CONFIG_PATH.read_text())) else 1)
        except (OSError, ValueError):
            sys.exit(1)
    cfg = load_config()
    if "--test" in sys.argv:
        print("Firing a test message alert (flash + sound)...")
        alert(cfg, "TEST — this is what a new message alert looks like.\n"
                   "The first line of the message becomes the title.",
              kind="project", chat_name="Test chat")
        return
    if "--test-warning" in sys.argv:
        print("Firing a test connection-problem alert...")
        alert(cfg, "TEST — this is how a connection problem looks.\n"
                   "Can't reach Telegram — new messages will NOT alert until it's back.",
              kind="warning")
        return
    if "--proxy-check" in sys.argv:
        print("Proxy that would be used right now:", proxy_desc(resolve_proxy(cfg)))
        return
    if not config_ready(cfg):
        print("config.json still has placeholder values.")
        print("Get api_id + api_hash at https://my.telegram.org (API development tools)")
        print("and list the chats to watch under \"chats\".")
        print(f"Put them in {CONFIG_PATH}, then run:  {sys.executable} {BASE / 'notifier.py'} --login")
        sys.exit(1)
    asyncio.run(run(cfg, login_only="--login" in sys.argv))


if __name__ == "__main__":
    main()
