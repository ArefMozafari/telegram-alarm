<p align="center">
  <img src="assets/icons/telegram.svg" width="36" alt="Telegram">
  &nbsp;&nbsp;&nbsp;➜&nbsp;&nbsp;&nbsp;
  <picture><source media="(prefers-color-scheme: dark)" srcset="assets/icons/apple-white.svg"><img src="assets/icons/apple.svg" width="36" alt="macOS"></picture>
</p>

<h1 align="center">Telegram Alarm</h1>

<p align="center"><b>Unmissable macOS alerts when chosen Telegram chats message you</b></p>

<p align="center">
  <a href="https://github.com/ArefMozafari/telegram-alarm/releases/latest"><img src="https://img.shields.io/github/v/release/ArefMozafari/telegram-alarm?color=orange" alt="Release"></a>
  <img src="https://img.shields.io/badge/platform-macOS-000000?logo=apple&logoColor=white" alt="platform macOS">
  <img src="https://img.shields.io/badge/python-3.x-3776AB?logo=python&logoColor=white" alt="python 3">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
</p>
An alert you can't miss on your Mac whenever chosen Telegram chats message you: job
boards, on-call bots, a family group, anything you can't afford to see an hour late.

<img src="docs/island.png" alt="The island alert showing a new job post from a bot" width="820">

Shows an unmissable alert — by default a Dynamic-Island-style Liquid Glass pill that
springs out of the MacBook notch: real backdrop blur of whatever is behind it, dark
glass tint with a specular sheen and rim, and a breathing glow that cycles
red → amber → magenta → azure. The island stays on screen until you click it; a newer
alert replaces a still-visible one. Alternatives: a flashing top bar or a fullscreen
takeover. All styles sit above every window, Space, and fullscreen app, and an alarm
plays whenever one of the chats you list — a bot, a person, a group or a channel —
sends a message.
It logs in as your own Telegram account via Telethon, so it works even when Telegram
Desktop is closed, and it ignores Focus / Do Not Disturb.

## Requirements

- macOS
- Python 3 (tested with 3.14 from Homebrew)
- A Telegram account, plus an API ID and hash from https://my.telegram.org

## Install

    git clone https://github.com/ArefMozafari/telegram-alarm.git

Then double-click **Telegram Alarm.command** in the cloned folder (see below).

## Layout

The project folder has two double-clickable `.command` files and everything
else lives in `src/`:

- **Telegram Alarm.command** — sets up and (re)starts everything.
- **Stop Telegram Alarm.command** — stops the background agent and kills any
  alert that's on screen or mid-sound right now. Handy right before sleep /
  closing the lid, so a message that lands at that instant can't still flash
  or play a sound.

Keep both `.command` files next to `src/`; if you want them elsewhere, make a
Finder alias to each instead of moving them.

## Easiest way to run everything

Double-click **Telegram Alarm.command** in Finder. It does the whole lifecycle:
creates the Python environment if missing, walks you through credentials and the
one-time Telegram login if needed, (re)starts the background agent, health-checks
it, optionally fires a test alert, and then streams the live log. Safe to run any
time — it only sets up what's missing.

To stop everything — e.g. before closing the lid to sleep — double-click
**Stop Telegram Alarm.command**.

## Setup (one time, manual alternative)

Run these from inside `src/` (`cd src`).

1. **Python environment**:

       python3 -m venv venv
       venv/bin/python -m pip install -r requirements.txt

2. **API credentials** — copy `config.example.json` to `config.json` (the
   `.command` script does this for you). Log in at https://my.telegram.org →
   *API development tools* → create an app (any name/short name). Copy **api_id**
   and **api_hash** into `config.json`, and list the chats to watch under
   **chats**.

3. **Log in to Telegram** (asks for your phone number + the code Telegram sends you,
   then saves a session file so it never asks again):

       venv/bin/python notifier.py --login

4. **Install the background agent** (starts at login, auto-restarts on crashes):

       ./install.sh

Preview the alerts anytime, no Telegram needed:

    venv/bin/python notifier.py --test           # new-message alert (red)
    venv/bin/python notifier.py --test-warning   # connection-problem alert (amber)
    venv/bin/python notifier.py --proxy-check    # show which proxy would be used

## Example: freelance job alerts

The screenshot above is a job-board bot. For the @KayaProjectsBot freelance feed,
set these two keys in `config.json`:

    "chats": ["@KayaProjectsBot"],
    "headline": "New Kaya project",

Job-board messages that carry a `fixed:` or `hourly:` budget line and a flag-emoji
location line get those two shown on the right of the island; any other message
shows its first line as the title.

## Connection health & proxy

The listener pings Telegram every `health_check_seconds`. If the connection stays
broken past `connection_alert_after_seconds`, you get a **different** alert — amber
island / bar, descending warning sound, "new messages will NOT alert" — repeated every
`connection_realert_minutes` until it's back, plus a banner when it reconnects. An
expired Telegram session triggers the same warning.

Every reconnect attempt re-reads the macOS system proxy (`scutil --proxy`), so turning
a proxy on/off or switching servers while the notifier runs is handled automatically:
the health check notices the dead path and the next attempt uses the new settings.

## config.json

| key | meaning |
|---|---|
| `api_id`, `api_hash` | your credentials from my.telegram.org |
| `chats` | the chats to watch, as usernames (`"@SomeBot"`) or `t.me` links. Bots, people, groups and channels all work. An older single `bot_username` setting is still read when `chats` is missing |
| `headline` | the alert's headline; `{chat}` becomes the sending chat's name (default `"{chat}"`) |
| `flash_enabled`, `flash_seconds` | flash overlay on/off; the seconds apply to bar/fullscreen only — the island stays until clicked |
| `flash_style` | `"island"` = Liquid Glass Dynamic-Island pill from the notch (default; on the notched screen, or top-center of the main one); `"bar"` = flashing strip across the top of every display; `"fullscreen"` = whole-screen takeover. Island and bar never steal keyboard focus |
| `bar_height` | height of the bar style, in pixels |
| `sound_enabled`, `sound_file`, `sound_repeats` | alarm sound (any file afplay can play; path relative to `src/`) and repeat count |
| `min_volume` | if output is muted or quieter than this (0–100), temporarily raise it for the alarm, then restore; `0` disables |
| `speak_enabled`, `speak_text` | also speak a phrase out loud with `say` |
| `banner_enabled` | also post a normal macOS notification (lands in Notification Center history) |
| `burst_cooldown_seconds` | if several messages arrive within this window (e.g. after wake/reconnect), only the first gets flash + sound |
| `mute_while_asleep` | default `true`: while the display is off (including a Power Nap background wake with the lid closed), message alerts get banner + log only — no flash, no alarm. Not replayed later; check the log or Notification Center if you want to know what came in |
| `proxy` | `"system"` (default) = use the macOS system proxy (SOCKS wins over HTTPS/HTTP; PAC files unsupported); `"none"` = always direct; or explicit `"socks5://host:port"` / `"http://host:port"`. Re-read on every reconnect, so proxy changes are picked up live |
| `health_check_seconds` | how often to ping Telegram to prove the connection is alive |
| `connection_alert_after_seconds` | how long the connection must stay broken before the amber warning alert fires (short blips stay silent) |
| `connection_realert_minutes` | re-fire the warning at this interval while still down (`0` = warn once) |
| `warning_sound_file` | sound for connection-problem alerts (descending tones by default) |

After changing the config, run `./install.sh --force` to restart the agent and pick up
the change (a plain `./install.sh` — including the one `Telegram Alarm.command` runs —
skips reinstalling when the agent is already up and healthy, to avoid needless restarts).

## Notes / troubleshooting

- Logs, all in `src/`: `notifier.log` (main), `launchd.log` (agent stdout/stderr — a
  symlink into `~/Library/Logs/telegram-alarm/`; the real file lives outside the
  project because the macOS Sandbox silently blocks launchd from redirecting into
  TCC-protected folders like Desktop/Documents/Downloads, where a clone often sits),
  `flash.err.log` (overlay errors).
- A fully sleeping Mac can't alert — but macOS Power Nap lets it briefly wake in the
  background (lid still closed, display still off) to receive messages, which used to
  mean a full flash + alarm through a closed lid. `mute_while_asleep` (default on) now
  skips the flash/alarm for those — see `config.json` above. If you'd rather the Mac
  not wake in the background at all, turn off Power Nap in System Settings → Battery
  → Options.
- Don't run `notifier.py` (listener mode) manually while the agent is installed — two
  copies fight over the session file. Stop the agent first (see below).
  `--test` is always safe.
- Stop everything: double-click **Stop Telegram Alarm.command** (or run
  `./uninstall.sh` from inside `src/`).
- `config.json` and `telegram_alarm.session` are secrets (full account access) — keep
  them private. `.gitignore` keeps both out of the repository.

## Development

The tests cover the config helpers in `src/notifier.py`. Run them from the repository
root:

    src/venv/bin/python -m pip install pytest
    src/venv/bin/python -m pytest

## License

MIT — see [LICENSE](LICENSE).
