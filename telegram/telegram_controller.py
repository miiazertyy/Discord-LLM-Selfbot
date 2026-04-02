"""
telegram_controller.py — External Telegram controller for the Discord AI Selfbot.

HOW TO SET UP:
  1. Message @BotFather on Telegram → /newbot → copy the token
  2. Get your Telegram user ID from @userinfobot
  3. Add to your config/.env:
       TELEGRAM_BOT_TOKEN=your_token_here
       TELEGRAM_OWNER_ID=your_telegram_user_id_here
  4. Install dependency:  pip install python-telegram-bot
  5. Run alongside the selfbot:  python telegram/telegram_controller.py

MULTI-ACCOUNT SUPPORT:
  If you run multiple Discord tokens (DISCORD_TOKEN_1, DISCORD_TOKEN_2 ...),
  each selfbot instance reads from its own IPC file:
    config/tg_commands_1.json / tg_results_1.json  (account #1)
    config/tg_commands_2.json / tg_results_2.json  (account #2)
    ...
  Use /account <number> to switch which account your commands target.
  The currently selected account is shown in every command reply header.

WHY TELEGRAM INSTEAD OF DISCORD COMMANDS:
  Sending management commands from your real Discord account is risky —
  Discord can flag unusual self-bot patterns and ban your account.
  Using this Telegram controller means zero activity on Discord from your side:
  all commands stay off Discord entirely, making the selfbot much harder to detect.

COMMANDS AVAILABLE:
  🔀 Accounts
    /account              — show current account
    /account <n>          — switch to account number n (1-based)

  🤖 AI
    /pause              — toggle pause/unpause AI responses
    /pauseuser <id>     — stop responding to a user
    /unpauseuser <id>   — resume responding to a user
    /wipe               — clear all conversation history
    /persona <id> <txt> — set persona for a user (/persona <id> off to clear)
    /analyse <id>       — psychological profile of a user

  💬 Replies
    /reply check        — show unreplied conversations
    /reply all          — respond to all unreplied users
    /reply / response <id>  — respond to a specific user by ID

  ⚙️ Config & Instructions
    /config             — view current config
    /config <key> <val> — edit a config value  e.g. /config tts.enabled true
    /prompt             — view current instructions
    /prompt <text>      — set instructions inline
    /prompt clear       — clear instructions
    /getconfig          — download config.yaml
    /setconfig          — upload a new config.yaml (attach .yaml file)
    /instructions       — upload a new instructions.txt (attach .txt file)
    /getinstructions    — download current instructions.txt
    /getdb              — download bot_data.db
    /reload             — reload all cogs + instructions

  🎭 Behaviour
    /mood               — view current mood (reads live bot state)
    /mood <n>           — set mood (chill/playful/busy/tired/annoyed/flirty)
    /ignore <id>        — ignore / unignore a user

  🎙️ Profile & Status
    /status             — show bot status (paused, mood, active channels)
    /setstatus [emoji] [text] — set Discord custom status (or clear it)
    /bio [text]         — set profile bio (omit text to clear)
    /pfp <url>          — change profile picture by URL

  📡 Channels
    /toggledm           — toggle DM responses
    /togglegc           — toggle group chat responses
    /toggleserver       — toggle server responses
    /toggleactive <id>  — toggle a channel as active by channel ID

  🎙️ Voice
    /join <id/link>     — join a voice channel (muted & deafened)
    /leave              — leave the current voice channel
    /autojoin <id/link> — auto-join a voice channel on startup
    /autojoin off       — disable auto-join

  🖼️ Images
    /imagels / imagelist    — list all pictures with descriptions
    /imagedownload / imagedl <n>  — download image by number
    /imagedelete <n>    — delete image by number
    /imagedeleteall     — delete all images

  🛠️ System
    /leaderboard        — top users by message count
    /leaderboard <filter> — e.g. /leaderboard 7d
    /addfriend <id>     — send a friend request by user ID
    /restart            — restart the selfbot
    /shutdown           — shut down the selfbot
    /ping               — check if the controller is running
    /update             — update to latest release
    /update main        — update to latest commit
"""

import asyncio
import functools
import json
import os
import sys
import time
import logging
import uuid
from pathlib import Path

# ── Dependency check ─────────────────────────────────────────────────────────
try:
    from telegram import Update, Document
    from telegram.ext import (
        Application,
        CommandHandler,
        ContextTypes,
        MessageHandler,
        filters,
    )
    from telegram.constants import ParseMode
except ImportError:
    print(
        "\n[TG Controller] Missing dependency.\n"
        "Run:  pip install python-telegram-bot\n"
    )
    sys.exit(1)

from dotenv import load_dotenv

# ── Paths ─────────────────────────────────────────────────────────────────────
# telegram_controller.py lives in telegram/ — one level below the project root.
_BASE = Path(__file__).resolve().parent.parent
# Make the project root importable so `from utils.db import ...` works regardless
# of which directory Python is invoked from.
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))
_CONFIG_DIR = _BASE / "config"
_ENV_PATH = _CONFIG_DIR / ".env"
_CONFIG_YAML = _CONFIG_DIR / "config.yaml"
_INSTRUCTIONS_PATH = _CONFIG_DIR / "instructions.txt"
_DB_PATH = _CONFIG_DIR / "bot_data.db"
_PICTURES_DIR = _CONFIG_DIR / "pictures"

# Buffer for collecting media-group (album) messages before processing them together.
# Maps media_group_id -> list of (tg_file, filename_hint, arrival_time)
_media_group_buffer: dict = {}
_MEDIA_GROUP_WAIT = 1.2  # seconds to wait for all album messages to arrive

load_dotenv(dotenv_path=_ENV_PATH, override=True)

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_OWNER_ID = int(os.getenv("TELEGRAM_OWNER_ID", "0"))

if not TG_TOKEN or not TG_OWNER_ID:
    print(
        "\n[TG Controller] TELEGRAM_BOT_TOKEN or TELEGRAM_OWNER_ID not set in config/.env\n"
        "Add them and restart.\n"
    )
    sys.exit(1)


# ── Discover how many Discord accounts are configured ─────────────────────────
def _count_accounts() -> int:
    count = 0
    i = 1
    while os.getenv(f"DISCORD_TOKEN_{i}"):
        count = i
        i += 1
    if count == 0 and os.getenv("DISCORD_TOKEN"):
        count = 1
    return max(count, 1)


NUM_ACCOUNTS = _count_accounts()

logging.basicConfig(
    format="%(asctime)s [TG] %(levelname)s %(message)s",
    level=logging.WARNING,
)
# Suppress noisy httpx / httpcore / python-telegram-bot polling logs
for _noisy in ("httpx", "httpcore", "telegram.ext.Application", "apscheduler"):
    logging.getLogger(_noisy).setLevel(logging.ERROR)
logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


# ── Per-account IPC file helpers ──────────────────────────────────────────────
def _cmd_file(account: int) -> Path:
    return _CONFIG_DIR / f"tg_commands_{account}.json"


def _result_file(account: int) -> Path:
    return _CONFIG_DIR / f"tg_results_{account}.json"


def _get_account(context: ContextTypes.DEFAULT_TYPE) -> int:
    return context.bot_data.get("account", 1)


def _account_label(account: int) -> str:
    if NUM_ACCOUNTS == 1:
        return ""
    return f"[Account {account}/{NUM_ACCOUNTS}] "


# ── Auth guard ────────────────────────────────────────────────────────────────
def owner_only(func):
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        uid = user.id if user else None
        if uid != TG_OWNER_ID:
            logger.warning(f"[AUTH] Rejected /{func.__name__} — uid={uid}")
            await update.message.reply_text(
                f"⛔ Not authorised.\n\n"
                f"Your Telegram user ID is: `{uid}`\n"
                f"Configured TELEGRAM\\_OWNER\\_ID is: `{TG_OWNER_ID}`\n\n"
                f"If these don't match, update `TELEGRAM_OWNER_ID={uid}` in your `config/.env` and restart the controller.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        await func(update, context)
    return wrapper


# ── Config helpers (direct file access — same filesystem) ─────────────────────
def _load_config() -> dict:
    import yaml
    with open(_CONFIG_YAML, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _save_config(cfg: dict):
    import yaml
    with open(_CONFIG_YAML, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)


def _load_instructions() -> str:
    if _INSTRUCTIONS_PATH.exists():
        return _INSTRUCTIONS_PATH.read_text(encoding="utf-8")
    return ""


def _save_instructions(text: str):
    _INSTRUCTIONS_PATH.write_text(text, encoding="utf-8")


# ── IPC helpers ───────────────────────────────────────────────────────────────
def _send_command(account: int, cmd: str, payload: dict = None) -> str:
    """Write a command to the per-account IPC file, with retries on write failure."""
    cmd_id = str(uuid.uuid4())
    entry = {
        "id": cmd_id,
        "cmd": cmd,
        "payload": payload or {},
        "ts": time.time(),
    }
    f = _cmd_file(account)
    for attempt in range(5):
        try:
            existing = []
            if f.exists():
                try:
                    existing = json.loads(f.read_text())
                    if not isinstance(existing, list):
                        existing = []
                except Exception:
                    existing = []
            existing.append(entry)
            # Write to a temp file then atomically replace to avoid corruption
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps(existing))
            tmp.replace(f)
            return cmd_id
        except Exception as e:
            logger.warning(f"[IPC] _send_command attempt {attempt + 1} failed: {e}")
            time.sleep(0.05 * (attempt + 1))
    logger.error(f"[IPC] _send_command failed after 5 attempts for cmd={cmd}")
    return cmd_id


async def _wait_for_result(account: int, cmd_id: str, timeout: float = 10.0) -> dict | None:
    """Poll the per-account result file until the selfbot posts a result."""
    f = _result_file(account)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if f.exists():
            try:
                results = json.loads(f.read_text())
                if cmd_id in results:
                    result = results.pop(cmd_id)
                    tmp = f.with_suffix(".tmp")
                    tmp.write_text(json.dumps(results))
                    tmp.replace(f)
                    return result
            except Exception:
                pass
        await asyncio.sleep(0.3)
    return None


def _fmt_bool(val) -> str:
    return "✅" if val else "❌"


def _escape(text: str) -> str:
    for ch in r"\_*[]()~`>#+-=|{}.!":
        text = text.replace(ch, f"\\{ch}")
    return text


# ── /account ──────────────────────────────────────────────────────────────────
@owner_only
async def cmd_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current = _get_account(context)

    if not context.args:
        if NUM_ACCOUNTS == 1:
            await update.message.reply_text("Only one Discord account is configured.")
        else:
            await update.message.reply_text(
                f"Currently targeting *account {current}* of {NUM_ACCOUNTS}.\n"
                f"Use `/account <n>` to switch.",
                parse_mode=ParseMode.MARKDOWN
            )
        return

    try:
        n = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Usage: /account <number>")
        return

    if n < 1 or n > NUM_ACCOUNTS:
        await update.message.reply_text(
            f"❌ Invalid account. You have {NUM_ACCOUNTS} account(s) configured (1–{NUM_ACCOUNTS})."
        )
        return

    context.bot_data["account"] = n
    await update.message.reply_text(
        f"✅ Switched to *account {n}* of {NUM_ACCOUNTS}.",
        parse_mode=ParseMode.MARKDOWN
    )


# ── /ping ─────────────────────────────────────────────────────────────────────
@owner_only
async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    start = time.time()
    msg = await update.message.reply_text("🟢 Controller is running — measuring latency...")
    latency = (time.time() - start) * 1000
    await msg.edit_text(
        f"🟢 Controller is running.\nLatency: `{latency:.0f} ms`",
        parse_mode=ParseMode.MARKDOWN
    )


# ── /status — live bot state via IPC ─────────────────────────────────────────
@owner_only
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account = _get_account(context)
    label = _account_label(account)
    cmd_id = _send_command(account, "get_status")
    result = await _wait_for_result(account, cmd_id, timeout=10.0)

    if not result:
        await update.message.reply_text(
            f"{label}⚠️ Selfbot didn't respond. Make sure the IPC bridge is running in main.py."
        )
        return

    try:
        cfg = _load_config()
        bot_cfg = cfg.get("bot", {})
        mood = result.get("mood", "?")
        paused = result.get("paused", False)
        channels = result.get("active_channels", 0)
        ignored = result.get("ignored_users", 0)
        lines = [
            f"📊 *{label}Bot Status*",
            f"  paused: {_fmt_bool(paused)}",
            f"  allow\\_dm: {_fmt_bool(bot_cfg.get('allow_dm'))}",
            f"  allow\\_gc: {_fmt_bool(bot_cfg.get('allow_gc'))}",
            f"  allow\\_server: {_fmt_bool(bot_cfg.get('allow_server', True))}",
            f"  mood: `{mood}`",
            f"  active channels: {channels}",
            f"  ignored users: {ignored}",
        ]
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


# ── /config ───────────────────────────────────────────────────────────────────
@owner_only
async def cmd_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account = _get_account(context)
    label = _account_label(account)
    args = context.args

    if not args:
        try:
            cfg = _load_config()
            bot_cfg = cfg.get("bot", {})
            tts = bot_cfg.get("tts") or {}
            mood = bot_cfg.get("mood") or {}
            late = bot_cfg.get("late_reply") or {}
            nudge = bot_cfg.get("nudge") or {}
            fr = bot_cfg.get("friend_requests") or {}
            stale = bot_cfg.get("stale_reply") or {}
            status = bot_cfg.get("status") or {}
            notif = cfg.get("notifications") or {}
            wait_times = bot_cfg.get("batch_wait_times") or []
            wt_str = "  ".join(f"{w['time']}s({w['weight']})" for w in wait_times)
            mood_list = ", ".join(mood.get("moods", {}).keys())
            nudge_hours = nudge.get("send_during_hours", [10, 22])
            models = bot_cfg.get("groq_models", [])
            tts_tones = tts.get('tones', [])
            status_statuses = status.get('statuses', [])
            SEP = "`─────────────────────────────`"

            def e(text: str) -> str:
                """Escape a plain string for MarkdownV2."""
                for ch in r"\_*[]()~`>#+-=|{}.!":
                    text = text.replace(ch, f"\\{ch}")
                return text

            def row(key: str, val) -> str:
                """key in monospace — plain escaped value."""
                v = str(val) if val is not None else "not set"
                return f"  `{key}` \u2014 {e(v)}"

            def brow(key: str, val) -> str:
                """key in monospace — true/false for booleans."""
                word = "true" if val else "false"
                return f"  `{key}` \u2014 {word}"

            title_label = f" \u2014 {e(label.strip())}" if label else ""
            lines = [
                f"⚙️ *Bot Config{title_label}*",
                SEP,
                "  🔧  *General*",
                row("prefix",          bot_cfg.get("prefix")),
                row("trigger",         bot_cfg.get("trigger")),
                row("owner_id",        bot_cfg.get("owner_id")),
                row("priority_prefix", bot_cfg.get("priority_prefix")),
                SEP,
                "  💬  *Responses*",
                brow("allow_dm",                  bot_cfg.get("allow_dm")),
                brow("allow_gc",                  bot_cfg.get("allow_gc")),
                brow("allow_server",               bot_cfg.get("allow_server", True)),
                brow("discord_commands_enabled",   bot_cfg.get("discord_commands_enabled", True)),
                brow("hold_conversation",          bot_cfg.get("hold_conversation")),
                brow("realistic_typing",           bot_cfg.get("realistic_typing")),
                brow("reply_ping",                 bot_cfg.get("reply_ping")),
                brow("disable_mentions",           bot_cfg.get("disable_mentions")),
                brow("batch_messages",             bot_cfg.get("batch_messages")),
                row("batch_wait_times",            wt_str or "not set"),
                SEP,
                "  🎭  *Behaviour*",
                row("ignore_chance",  bot_cfg.get("ignore_chance")),
                row("typo_chance",    bot_cfg.get("typo_chance")),
                brow("anti_age_ban", bot_cfg.get("anti_age_ban")),
                SEP,
                "  🤖  *Models*",
                row("groq_models",         ", ".join(models) if isinstance(models, list) else str(models)),
                row("groq_image_model",    bot_cfg.get("groq_image_model")),
                row("groq_whisper_model",  bot_cfg.get("groq_whisper_model")),
                SEP,
                "  🔊  *TTS*",
                brow("tts.enabled", tts.get("enabled")),
                row("tts.voice",    tts.get("voice")),
                row("tts.tones",    ", ".join(tts_tones) if isinstance(tts_tones, list) else str(tts_tones)),
                SEP,
                "  😶  *Mood*",
                brow("mood.enabled",            mood.get("enabled")),
                row("mood.shift_interval_min",  mood.get("shift_interval_min")),
                row("mood.shift_interval_max",  mood.get("shift_interval_max")),
                row("mood.moods",               mood_list or "none"),
                SEP,
                "  🕐  *Status*",
                brow("status.enabled",             status.get("enabled")),
                row("status.change_interval_min",  status.get("change_interval_min")),
                row("status.change_interval_max",  status.get("change_interval_max")),
                row("status.statuses",             ", ".join(status_statuses) if isinstance(status_statuses, list) else str(status_statuses)),
                SEP,
                "  💬  *Late Reply*",
                brow("late_reply.enabled",    late.get("enabled")),
                row("late_reply.threshold",   late.get("threshold")),
                SEP,
                "  🗑️  *Stale Reply* _\\(servers & GCs only\\)_",
                brow("stale_reply.enabled",      stale.get("enabled", False)),
                row("stale_reply.max_messages",  stale.get("max_messages", 10)),
                row("stale_reply.min_age",       f"{stale.get('min_age', 120)}s"),
                SEP,
                "  💤  *Nudge*",
                brow("nudge.enabled",              nudge.get("enabled", False)),
                row("nudge.threshold_days",        nudge.get("threshold_days", 2)),
                row("nudge.check_interval_hours",  nudge.get("check_interval_hours", 6)),
                row("nudge.send_during_hours",     f"{nudge_hours[0]}:00\u2013{nudge_hours[1]}:00"),
                SEP,
                "  👥  *Friend Requests*",
                brow("friend_requests.enabled",          fr.get("enabled", True)),
                row("friend_requests.accept_delay_min",  f"{fr.get('accept_delay_min', 120)}s"),
                row("friend_requests.accept_delay_max",  f"{fr.get('accept_delay_max', 600)}s"),
                SEP,
                "  🔔  *Notifications*",
                row("error_webhook",                  "set" if notif.get("error_webhook") else "not set"),
                brow("ratelimit_notifications",        notif.get("ratelimit_notifications")),
                brow("telegram_error_notifications",   notif.get("telegram_error_notifications", False)),
                SEP,
                "*✏️ To edit:* `/config key value`",
                "_e\\.g\\. /config tts\\.enabled true_",
            ]
            await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            await update.message.reply_text(f"❌ Error reading config: {e}")
        return

    if len(args) < 2:
        await update.message.reply_text("Usage: /config <key> <value>\nExample: /config tts.enabled true")
        return

    key = args[0]
    value = " ".join(args[1:])

    def coerce(v, existing=None):
        if v.lower() == "true": return True
        if v.lower() == "false": return False
        try: return int(v)
        except ValueError: pass
        try: return float(v)
        except ValueError: pass
        LIST_KEYS = {"groq_models", "tones", "statuses"}
        keys_parts = key.split(".")
        if isinstance(existing, list) or (keys_parts[-1] in LIST_KEYS):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    try:
        cfg = _load_config()
        keys_parts = key.split(".")
        node = None
        for _section in ["bot", "notifications"]:
            _candidate = cfg.get(_section, {})
            _found = True
            for k in keys_parts[:-1]:
                if k not in _candidate:
                    _found = False
                    break
                _candidate = _candidate[k]
            if _found and keys_parts[-1] in _candidate:
                node = _candidate
                break
        if node is None:
            await update.message.reply_text(f"❌ Key `{key}` not found in config.")
            return
        final_key = keys_parts[-1]
        old_val = node[final_key]
        node[final_key] = coerce(value, old_val)
        _save_config(cfg)
        _send_command(account, "config_update", {"key": key, "value": node[final_key]})
        await update.message.reply_text(
            f"{label}✅ `{key}` updated: `{old_val}` → `{node[final_key]}`",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


@owner_only
async def cmd_getconfig(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _CONFIG_YAML.exists():
        await update.message.reply_text("❌ config.yaml not found.")
        return
    await update.message.reply_document(
        document=open(_CONFIG_YAML, "rb"),
        filename="config.yaml",
        caption="📄 config.yaml"
    )


@owner_only
async def cmd_setconfig(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account = _get_account(context)
    if not update.message.document:
        await update.message.reply_text(
            "📎 Attach a `.yaml` file to update the config.\n"
            "Example: send /setconfig with a config.yaml attached."
        )
        return
    doc = update.message.document
    if not doc.file_name.endswith(".yaml"):
        await update.message.reply_text("❌ Only `.yaml` files are supported.")
        return
    try:
        import yaml
        tg_file = await doc.get_file()
        content = await tg_file.download_as_bytearray()
        text = content.decode("utf-8")
        yaml.safe_load(text)
    except UnicodeDecodeError:
        await update.message.reply_text("❌ Could not read file — make sure it's valid UTF-8.")
        return
    except Exception as e:
        await update.message.reply_text(f"❌ Invalid YAML: {e}")
        return
    _CONFIG_YAML.write_text(text, encoding="utf-8")
    _send_command(account, "restart")
    await update.message.reply_text("✅ Config updated. Restart command sent to selfbot.")


@owner_only
async def cmd_getdb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _DB_PATH.exists():
        await update.message.reply_text("❌ bot_data.db not found.")
        return
    await update.message.reply_document(
        document=open(_DB_PATH, "rb"),
        filename="bot_data.db",
        caption="🗄️ bot_data.db"
    )


@owner_only
async def cmd_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account = _get_account(context)
    args = context.args

    if not args:
        text = _load_instructions()
        if text:
            if len(text) > 3800:
                for i in range(0, len(text), 3800):
                    await update.message.reply_text(f"```\n{text[i:i+3800]}\n```", parse_mode=ParseMode.MARKDOWN)
            else:
                await update.message.reply_text(f"📝 Current instructions:\n```\n{text}\n```", parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text("No instructions currently set.")
        return

    if args[0].lower() == "clear":
        _save_instructions("")
        _send_command(account, "instructions_update", {"text": ""})
        await update.message.reply_text("🗑️ Instructions cleared.")
        return

    new_text = " ".join(args)
    _save_instructions(new_text)
    _send_command(account, "instructions_update", {"text": new_text})
    await update.message.reply_text("✅ Instructions updated.")


@owner_only
async def cmd_getinstructions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _INSTRUCTIONS_PATH.exists():
        await update.message.reply_text("❌ instructions.txt not found.")
        return
    await update.message.reply_document(
        document=open(_INSTRUCTIONS_PATH, "rb"),
        filename="instructions.txt",
        caption="📝 instructions.txt"
    )


@owner_only
async def cmd_instructions_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account = _get_account(context)
    doc = update.message.document
    if not doc or not doc.file_name.endswith(".txt"):
        return
    try:
        tg_file = await doc.get_file()
        content = await tg_file.download_as_bytearray()
        text = content.decode("utf-8")
    except Exception as e:
        await update.message.reply_text(f"❌ Could not read file: {e}")
        return
    _save_instructions(text)
    _send_command(account, "instructions_update", {"text": text})
    await update.message.reply_text("✅ Instructions updated from file!")


# ── /leaderboard — via IPC with pagination ───────────────────────────────────
@owner_only
async def cmd_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account = _get_account(context)
    label = _account_label(account)
    import re as _re
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    filter_str = context.args[0] if context.args else None
    if filter_str:
        m = _re.fullmatch(r"(\d+(?:\.\d+)?)\s*([hdwm])", filter_str.strip().lower())
        if not m:
            await update.message.reply_text(
                "Invalid filter\\. Examples: /leaderboard 24h · /leaderboard 7d · /leaderboard 1w",
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return

    wait_msg = await update.message.reply_text("📊 Fetching leaderboard\\.\\.\\.", parse_mode=ParseMode.MARKDOWN_V2)
    cmd_id = _send_command(account, "get_leaderboard", {"filter": filter_str})
    result = await _wait_for_result(account, cmd_id, timeout=15.0)

    if not result:
        await wait_msg.edit_text(f"{label}⚠️ Selfbot didn't respond in time\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return

    rows = result.get("rows", [])
    filter_label = result.get("filter_label", "all time")

    if not rows:
        safe_label = _escape(f"{label}No conversations recorded ({filter_label}).")
        await wait_msg.edit_text(safe_label, parse_mode=ParseMode.MARKDOWN_V2)
        return

    PER_PAGE = 10
    total_pages = max(1, (len(rows) + PER_PAGE - 1) // PER_PAGE)
    medal = ["🥇", "🥈", "🥉"]

    def _build_lb_page(page: int) -> str:
        start = page * PER_PAGE
        chunk = rows[start:start + PER_PAGE]
        header_raw = f"📊 {label}Leaderboard — {filter_label}  (page {page+1}/{total_pages})"
        lines = [_escape(header_raw), ""]
        for i, row in enumerate(chunk):
            rank_n = start + i
            rank = medal[rank_n] if rank_n < 3 else f"\\#{rank_n+1}"
            msg_count = row["message_count"]
            msg_str = f"{msg_count} msg{'s' if msg_count != 1 else ''}"
            name_esc = _escape(row["username"])
            date_esc = _escape(row["first_seen_fmt"])
            lines.append(f"{rank} `{name_esc}` — {_escape(msg_str)} · since {date_esc}")
        return "\n".join(lines)

    def _build_lb_keyboard(page: int, filter_arg: str):
        buttons = []
        if page > 0:
            buttons.append(InlineKeyboardButton("◀ Prev", callback_data=f"lb:{page-1}:{filter_arg or ''}:{account}"))
        if page < total_pages - 1:
            buttons.append(InlineKeyboardButton("Next ▶", callback_data=f"lb:{page+1}:{filter_arg or ''}:{account}"))
        return InlineKeyboardMarkup([buttons]) if buttons else None

    kb = _build_lb_keyboard(0, filter_str)
    await wait_msg.edit_text(
        _build_lb_page(0),
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=kb,
    )


async def _leaderboard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle ◀/▶ pagination for /leaderboard."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    query = update.callback_query
    if not query:
        return
    user = query.from_user
    if not user or user.id != TG_OWNER_ID:
        await query.answer("Not authorised.")
        return
    await query.answer()

    parts = query.data.split(":", 3)
    if len(parts) < 4 or parts[0] != "lb":
        return
    page = int(parts[1])
    filter_str = parts[2] or None
    account = int(parts[3])

    cmd_id = _send_command(account, "get_leaderboard", {"filter": filter_str})
    result = await _wait_for_result(account, cmd_id, timeout=15.0)
    if not result:
        await query.edit_message_text("⚠️ Selfbot didn't respond in time.")
        return

    rows = result.get("rows", [])
    filter_label = result.get("filter_label", "all time")
    label = _account_label(account)

    if not rows:
        await query.edit_message_text(f"{label}No conversations recorded ({filter_label}).")
        return

    PER_PAGE = 10
    total_pages = max(1, (len(rows) + PER_PAGE - 1) // PER_PAGE)
    medal = ["🥇", "🥈", "🥉"]

    def _build_lb_page(pg: int) -> str:
        start = pg * PER_PAGE
        chunk = rows[start:start + PER_PAGE]
        header_raw = f"📊 {label}Leaderboard — {filter_label}  (page {pg+1}/{total_pages})"
        lines = [_escape(header_raw), ""]
        for i, row in enumerate(chunk):
            rank_n = start + i
            rank = medal[rank_n] if rank_n < 3 else f"\\#{rank_n+1}"
            msg_count = row["message_count"]
            msg_str = f"{msg_count} msg{'s' if msg_count != 1 else ''}"
            name_esc = _escape(row["username"])
            date_esc = _escape(row["first_seen_fmt"])
            lines.append(f"{rank} `{name_esc}` — {_escape(msg_str)} · since {date_esc}")
        return "\n".join(lines)

    def _build_lb_keyboard(pg: int):
        buttons = []
        if pg > 0:
            buttons.append(InlineKeyboardButton("◀ Prev", callback_data=f"lb:{pg-1}:{filter_str or ''}:{account}"))
        if pg < total_pages - 1:
            buttons.append(InlineKeyboardButton("Next ▶", callback_data=f"lb:{pg+1}:{filter_str or ''}:{account}"))
        return InlineKeyboardMarkup([buttons]) if buttons else None

    await query.edit_message_text(
        _build_lb_page(page),
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=_build_lb_keyboard(page),
    )


# ── /clear — delete all messages in the Telegram chat ────────────────────────
_bot_message_ids: list[int] = []   # track message IDs sent by the controller

@owner_only
async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete all messages in this chat (up to 200 recent messages).

    Strategy: try to delete every message from the recent history.
    Telegram only lets bots delete their own messages in private chats,
    so we delete bot messages AND the user's /clear command itself.
    Then we use the tracked _bot_message_ids list to catch anything missed.
    """
    chat_id = update.effective_chat.id
    clear_msg_id = update.message.message_id

    # Delete the /clear command first
    try:
        await update.message.delete()
    except Exception:
        pass

    # Build the full set of IDs to delete: tracked + a sweep of recent history
    ids_to_delete = set(_bot_message_ids)
    _bot_message_ids.clear()

    # Sweep: try deleting message IDs from (current-200) to current
    # This catches everything in the conversation regardless of tracking
    for msg_id in range(max(1, clear_msg_id - 200), clear_msg_id):
        ids_to_delete.add(msg_id)

    deleted = 0
    failed = 0
    for msg_id in sorted(ids_to_delete, reverse=True):
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            deleted += 1
        except Exception:
            failed += 1

    note = await context.bot.send_message(
        chat_id=chat_id,
        text=f"🧹 Cleared {deleted} message(s)." + (f" ({failed} couldn't be deleted — normal for old/user messages)" if failed else ""),
    )
    _bot_message_ids.append(note.message_id)


# ── /imagels — browse images as photos with full descriptions ─────────────────
@owner_only
async def cmd_imagels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send images one by one as actual photos with their full AI description as caption."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    try:
        from utils.db import get_picture_description
        if not _PICTURES_DIR.exists():
            await update.message.reply_text("No pictures folder found.")
            return
        exts = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
        files = sorted([f for f in _PICTURES_DIR.iterdir() if f.suffix.lower() in exts])
        if not files:
            await update.message.reply_text("No images saved yet.")
            return

        total = len(files)

        # Store browse state in context so navigation callbacks can use it
        # We'll send page 0 and attach ◀ ▶ buttons
        def _build_nav(index: int):
            buttons = []
            if index > 0:
                buttons.append(InlineKeyboardButton("◀ Prev", callback_data=f"imgls:{index-1}"))
            if index < total - 1:
                buttons.append(InlineKeyboardButton("Next ▶", callback_data=f"imgls:{index+1}"))
            buttons.append(InlineKeyboardButton(f"🗑 Delete", callback_data=f"imgdel:{index}"))
            return InlineKeyboardMarkup([buttons]) if buttons else None

        async def _send_image(idx: int, reply_to=None):
            f = files[idx]
            desc = get_picture_description(f.name) or "(no description)"
            caption = f"🖼 `{f.name}` — {idx+1}/{total}\n\n{desc}"
            # Truncate to Telegram's 1024-char caption limit
            if len(caption) > 1024:
                caption = caption[:1021] + "…"
            kb = _build_nav(idx)
            try:
                if reply_to:
                    return await reply_to.reply_photo(
                        photo=open(f, "rb"),
                        caption=caption,
                        reply_markup=kb,
                    )
                else:
                    return await update.message.reply_photo(
                        photo=open(f, "rb"),
                        caption=caption,
                        reply_markup=kb,
                    )
            except Exception:
                # Fallback: send as document if photo fails (e.g. webp)
                cap2 = caption[:1024]
                if reply_to:
                    return await reply_to.reply_document(
                        document=open(f, "rb"),
                        filename=f.name,
                        caption=cap2,
                        reply_markup=kb,
                    )
                return await update.message.reply_document(
                    document=open(f, "rb"),
                    filename=f.name,
                    caption=cap2,
                    reply_markup=kb,
                )

        await _send_image(0)

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def _imagels_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle ◀/▶ navigation and 🗑 delete for /imagels."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    query = update.callback_query
    if not query:
        return
    if not query.from_user or query.from_user.id != TG_OWNER_ID:
        await query.answer("Not authorised.")
        return
    await query.answer()

    data = query.data
    if not (data.startswith("imgls:") or data.startswith("imgdel:")):
        return

    try:
        from utils.db import get_picture_description, delete_picture_db, rename_picture_db, clear_all_pictures_db
        exts = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

        if data.startswith("imgdel:"):
            idx = int(data.split(":")[1])
            files = sorted([f for f in _PICTURES_DIR.iterdir() if f.suffix.lower() in exts])
            if idx >= len(files):
                await query.edit_message_caption(caption="❌ Image not found (already deleted?)")
                return
            target = files[idx]
            target.unlink(missing_ok=True)
            delete_picture_db(target.name)
            # Renumber remaining
            remaining = sorted(
                [f for f in _PICTURES_DIR.iterdir() if f.suffix.lower() in exts],
                key=lambda f: int(f.stem[4:]) if f.stem.startswith("IMG_") and f.stem[4:].isdigit() else 99999
            )
            for i, rf in enumerate(remaining, start=1):
                if rf.stem.startswith("IMG_") and rf.stem[4:].isdigit() and int(rf.stem[4:]) != i:
                    new_name = f"IMG_{i}{rf.suffix}"
                    rf.rename(_PICTURES_DIR / new_name)
                    rename_picture_db(rf.name, new_name)
            await query.edit_message_caption(caption=f"🗑 Deleted `{target.name}`.")
            return

        # Navigation
        idx = int(data.split(":")[1])
        files = sorted([f for f in _PICTURES_DIR.iterdir() if f.suffix.lower() in exts])
        total = len(files)
        if not files or idx >= total:
            await query.edit_message_caption(caption="No more images.")
            return

        f = files[idx]
        desc = get_picture_description(f.name) or "(no description)"
        caption = f"🖼 `{f.name}` — {idx+1}/{total}\n\n{desc}"
        if len(caption) > 1024:
            caption = caption[:1021] + "…"

        buttons = []
        if idx > 0:
            buttons.append(InlineKeyboardButton("◀ Prev", callback_data=f"imgls:{idx-1}"))
        if idx < total - 1:
            buttons.append(InlineKeyboardButton("Next ▶", callback_data=f"imgls:{idx+1}"))
        buttons.append(InlineKeyboardButton("🗑 Delete", callback_data=f"imgdel:{idx}"))
        kb = InlineKeyboardMarkup([buttons]) if buttons else None

        # Edit the existing message — replace the photo
        try:
            from telegram import InputMediaPhoto
            await query.edit_message_media(
                media=InputMediaPhoto(media=open(f, "rb"), caption=caption),
                reply_markup=kb,
            )
        except Exception:
            # Fallback: send a new message
            await query.message.reply_photo(photo=open(f, "rb"), caption=caption, reply_markup=kb)

    except Exception as e:
        try:
            await query.edit_message_caption(caption=f"❌ Error: {e}")
        except Exception:
            pass


@owner_only
async def cmd_imagedownload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /imagedownload <number>\nUse /imagels to see images.")
        return
    name = context.args[0]
    if not _PICTURES_DIR.exists():
        await update.message.reply_text("No pictures folder found.")
        return
    exts = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    files = sorted([f for f in _PICTURES_DIR.iterdir() if f.suffix.lower() in exts])
    target = None
    if name.isdigit():
        matches = [f for f in files if f.stem == f"IMG_{name}"]
        if matches:
            target = matches[0]
    if not target:
        matches = [f for f in files if name.lower() in f.name.lower()]
        if len(matches) == 1:
            target = matches[0]
        elif len(matches) > 1:
            await update.message.reply_text(f"Multiple matches: {', '.join(f.name for f in matches)}. Be more specific.")
            return
    if not target or not target.exists():
        await update.message.reply_text(f"❌ Image `{name}` not found.", parse_mode=ParseMode.MARKDOWN)
        return
    await update.message.reply_document(
        document=open(target, "rb"),
        filename=target.name,
        caption=f"🖼️ {target.name}"
    )


@owner_only
async def cmd_imagedelete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account = _get_account(context)
    label = _account_label(account)
    if not context.args:
        await update.message.reply_text(
            "Usage: /imagedelete <n> [n2 n3 ...]\n"
            "Examples: /imagedelete 3  |  /imagedelete 1 4 7\n"
            "Use /imagels to see image numbers."
        )
        return

    # Parse all args — support "1,2,3", "1 2 3", or a mix
    raw = " ".join(context.args).replace(",", " ").split()
    nums = []
    for token in raw:
        token = token.strip()
        if token.isdigit():
            nums.append(token)
        else:
            await update.message.reply_text(f"❌ Invalid number: `{token}`")
            return

    if len(nums) == 1:
        # Single delete — use existing IPC command
        cmd_id = _send_command(account, "image_delete", {"name": nums[0]})
        result = await _wait_for_result(account, cmd_id, timeout=10.0)
        if result and result.get("ok"):
            await update.message.reply_text(f"{label}✅ Deleted image #{nums[0]}.")
        elif result:
            await update.message.reply_text(f"❌ {result.get('reason', 'Unknown error')}")
        else:
            await update.message.reply_text(f"{label}⚠️ Selfbot did not respond in time.")
    else:
        # Multi-delete — send as a batch
        status = await update.message.reply_text(
            f"{label}⏳ Deleting {len(nums)} images: {', '.join(f'#{n}' for n in nums)}..."
        )
        cmd_id = _send_command(account, "image_delete_multi", {"names": nums})
        result = await _wait_for_result(account, cmd_id, timeout=15.0)
        if result and result.get("ok"):
            deleted = result.get("deleted", nums)
            failed = result.get("failed", [])
            lines = [f"{label}✅ Deleted {len(deleted)} image(s)."]
            if failed:
                lines.append(f"⚠️ Not found: {', '.join(f'#{n}' for n in failed)}")
            await status.edit_text("\n".join(lines))
        elif result:
            await status.edit_text(f"❌ {result.get('reason', 'Unknown error')}")
        else:
            await status.edit_text(f"{label}⚠️ Selfbot did not respond in time.")


@owner_only
async def cmd_imagedeleteall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account = _get_account(context)
    label = _account_label(account)
    cmd_id = _send_command(account, "image_delete_all")
    result = await _wait_for_result(account, cmd_id, timeout=10.0)
    if result:
        await update.message.reply_text(f"{label}✅ Deleted all {result.get('count', '?')} image(s).")
    else:
        await update.message.reply_text(f"{label}⚠️ Command sent, selfbot did not respond in time.")


def _next_img_index() -> int:
    """Return the next free IMG_N index in the pictures folder."""
    valid_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    if not _PICTURES_DIR.exists():
        _PICTURES_DIR.mkdir(parents=True, exist_ok=True)
    used = set()
    for f in _PICTURES_DIR.iterdir():
        if f.suffix.lower() in valid_exts and f.stem.startswith("IMG_") and f.stem[4:].isdigit():
            used.add(int(f.stem[4:]))
    idx = 1
    while idx in used:
        idx += 1
    return idx


def _extract_tg_file_info(msg):
    """Return (tg_file_coro_getter, filename_hint) for a message, or (None, None)."""
    valid_exts = (".jpg", ".jpeg", ".png", ".gif", ".webp")
    if msg.photo:
        return msg.photo[-1], "upload.jpg"
    elif msg.document:
        doc = msg.document
        fname = doc.file_name or ""
        is_img = (doc.mime_type and doc.mime_type.startswith("image/")) or                  any(fname.lower().endswith(e) for e in valid_exts)
        if is_img:
            return doc, fname or "upload.png"
    return None, None


async def _process_images(account, label, msg, file_infos):
    """Download, save, and analyse a list of (tg_obj, filename_hint) tuples.
    Sends a single status message that is updated per image."""
    total = len(file_infos)
    status = await msg.reply_text(f"{label}⏳ Uploading {total} image{'s' if total > 1 else ''}...")

    saved = []   # (new_name, ext)
    for i, (tg_obj, filename_hint) in enumerate(file_infos, 1):
        if total > 1:
            try:
                await status.edit_text(f"{label}⏳ Downloading image {i}/{total}...")
            except Exception:
                pass
        try:
            tg_file = await tg_obj.get_file()
            image_bytes = await tg_file.download_as_bytearray()
        except Exception as e:
            await status.edit_text(f"❌ Failed to download image {i}: {e}")
            return

        ext = ".jpg"
        if filename_hint:
            _, e2 = os.path.splitext(filename_hint)
            if e2.lower() in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
                ext = e2.lower()

        idx = _next_img_index()
        new_name = f"IMG_{idx}{ext}"
        dest = _PICTURES_DIR / new_name
        try:
            dest.write_bytes(bytes(image_bytes))
        except Exception as e:
            await status.edit_text(f"❌ Failed to save image {i}: {e}")
            return
        saved.append((new_name, ext))

    # Now run vision analysis on each saved image sequentially
    results_text = []
    for i, (new_name, ext) in enumerate(saved, 1):
        if total > 1:
            try:
                await status.edit_text(
                    f"{label}⏳ Analysing image {i}/{total}: `{new_name}`..."
                )
            except Exception:
                pass
        else:
            try:
                await status.edit_text(f"{label}⏳ Saved as `{new_name}` — running AI vision analysis...")
            except Exception:
                pass

        cmd_id = _send_command(account, "image_analyse", {"name": new_name, "b64": "", "ext": ext})
        _VISION_TIMEOUT = 90.0
        _TICK = 15.0
        result = None
        f = _result_file(account)
        deadline = time.time() + _VISION_TIMEOUT
        while time.time() < deadline:
            await asyncio.sleep(min(_TICK, max(0.3, deadline - time.time())))
            if f.exists():
                try:
                    res_data = json.loads(f.read_text())
                    if cmd_id in res_data:
                        result = res_data.pop(cmd_id)
                        f.write_text(json.dumps(res_data))
                        break
                except Exception:
                    pass
            if time.time() < deadline:
                remaining_s = int(deadline - time.time())
                try:
                    await status.edit_text(
                        f"{label}⏳ Analysing {i}/{total}: `{new_name}`... (~{remaining_s}s left)"
                    )
                except Exception:
                    pass

        if result and result.get("ok"):
            desc = result.get("description", "(no description)")
            short = desc[:200] + ("…" if len(desc) > 200 else "")
            results_text.append(f"✅ `{new_name}` — {short}")
        elif result:
            results_text.append(f"✅ `{new_name}` — vision failed: {result.get('reason', '?')}")
        else:
            results_text.append(f"✅ `{new_name}` — vision timed out")

    summary = f"{label}Done! {total} image{'s' if total > 1 else ''} uploaded:\n\n" + "\n\n".join(results_text)
    # Telegram message limit is 4096 chars
    if len(summary) > 4000:
        summary = summary[:3997] + "…"
    await status.edit_text(summary)


async def cmd_imageupload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Upload one or multiple images. Send photos/files directly or reply to a photo."""
    user = update.effective_user
    if not user or user.id != TG_OWNER_ID:
        return

    account = _get_account(context)
    label = _account_label(account)
    msg = update.message

    # ── Media group (album) handling ──────────────────────────────────────────
    # Telegram sends each photo in an album as a separate message with the same
    # media_group_id. We buffer them for _MEDIA_GROUP_WAIT seconds then process all.
    if msg.media_group_id:
        mgid = msg.media_group_id
        tg_obj, filename_hint = _extract_tg_file_info(msg)
        if tg_obj is None:
            return  # not an image in this album slot
        if mgid not in _media_group_buffer:
            _media_group_buffer[mgid] = {"files": [], "account": account, "label": label, "msg": msg}
        _media_group_buffer[mgid]["files"].append((tg_obj, filename_hint))

        async def _flush_after_wait(group_id):
            await asyncio.sleep(_MEDIA_GROUP_WAIT)
            group = _media_group_buffer.pop(group_id, None)
            if not group or not group["files"]:
                return
            await _process_images(group["account"], group["label"], group["msg"], group["files"])

        # Only the first message in the group starts the flush timer
        if len(_media_group_buffer[mgid]["files"]) == 1:
            asyncio.create_task(_flush_after_wait(mgid))
        return

    # ── Single image or reply ─────────────────────────────────────────────────
    tg_obj, filename_hint = _extract_tg_file_info(msg)

    if tg_obj is None and msg.reply_to_message:
        tg_obj, filename_hint = _extract_tg_file_info(msg.reply_to_message)

    if tg_obj is None:
        await msg.reply_text(
            "📎 To upload image(s):\n"
            "• Send one or more photos directly (album supported)\n"
            "• Send image files as documents\n"
            "• Reply to an existing photo with /imageupload"
        )
        return

    await _process_images(account, label, msg, [(tg_obj, filename_hint)])


# ── /mood — live state via IPC ────────────────────────────────────────────────
@owner_only
async def cmd_mood(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account = _get_account(context)
    label = _account_label(account)

    try:
        cfg = _load_config()
        available = list(cfg["bot"]["mood"]["moods"].keys())
    except Exception:
        available = []

    if not context.args:
        cmd_id = _send_command(account, "mood_get")
        result = await _wait_for_result(account, cmd_id, timeout=8.0)
        current = result.get("mood", "?") if result else "?"
        moods_str = "  ".join(f"`{m}`" for m in available)
        await update.message.reply_text(
            f"{label}Current mood: `{current}`\nAvailable: {moods_str}",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    mood_name = context.args[0].lower().strip()
    if available and mood_name not in available:
        await update.message.reply_text(f"❌ Unknown mood `{mood_name}`. Available: {', '.join(available)}")
        return

    _send_command(account, "mood_set", {"mood": mood_name})
    await update.message.reply_text(f"{label}✅ Mood set to `{mood_name}`.", parse_mode=ParseMode.MARKDOWN)


# ── IPC-relayed commands ───────────────────────────────────────────────────────

@owner_only
async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account = _get_account(context)
    label = _account_label(account)
    cmd_id = _send_command(account, "pause")
    result = await _wait_for_result(account, cmd_id)
    if result:
        state = "⏸️ Paused" if result.get("paused") else "▶️ Unpaused"
        await update.message.reply_text(
            f"{label}{state} — AI responses are now {'paused' if result.get('paused') else 'active'}."
        )
    else:
        await update.message.reply_text(
            f"{label}⚠️ Command sent, but selfbot didn't respond.\n"
            "Make sure the IPC bridge is running in main.py."
        )


@owner_only
async def cmd_wipe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account = _get_account(context)
    label = _account_label(account)
    cmd_id = _send_command(account, "wipe")
    result = await _wait_for_result(account, cmd_id)
    if result:
        await update.message.reply_text(f"{label}🗑️ Conversation history wiped.")
    else:
        await update.message.reply_text(f"{label}⚠️ Command sent. Selfbot will wipe on next poll.")


@owner_only
async def cmd_ignore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account = _get_account(context)
    label = _account_label(account)
    if not context.args:
        await update.message.reply_text("Usage: /ignore <user_id>")
        return
    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")
        return
    # Use a single toggle command that returns the new state
    cmd_id = _send_command(account, "ignore_toggle", {"user_id": user_id})
    result = await _wait_for_result(account, cmd_id, timeout=8.0)
    if result:
        if result.get("ignored"):
            await update.message.reply_text(f"{label}✅ Now ignoring `{user_id}`.", parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(f"{label}✅ Unignored `{user_id}`.", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(f"{label}⚠️ Command sent, selfbot didn't respond in time.")


@owner_only
async def cmd_pauseuser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account = _get_account(context)
    label = _account_label(account)
    if not context.args:
        await update.message.reply_text("Usage: /pauseuser <user_id>")
        return
    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")
        return
    _send_command(account, "pauseuser", {"user_id": user_id})
    await update.message.reply_text(
        f"{label}✅ Pause command sent for user `{user_id}`.",
        parse_mode=ParseMode.MARKDOWN
    )


@owner_only
async def cmd_unpauseuser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account = _get_account(context)
    label = _account_label(account)
    if not context.args:
        await update.message.reply_text("Usage: /unpauseuser <user_id>")
        return
    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")
        return
    _send_command(account, "unpauseuser", {"user_id": user_id})
    await update.message.reply_text(
        f"{label}✅ Unpause command sent for user `{user_id}`.",
        parse_mode=ParseMode.MARKDOWN
    )


@owner_only
async def cmd_persona(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account = _get_account(context)
    label = _account_label(account)
    if not context.args:
        await update.message.reply_text(
            "Usage:\n"
            "  /persona <user_id> <instructions>\n"
            "  /persona <user_id> off   — clear persona\n"
            "  /persona <user_id> show  — view current persona"
        )
        return
    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /persona <user_id> <text|off|show>")
        return

    rest = " ".join(context.args[1:])

    if rest.strip().lower() == "show":
        cmd_id = _send_command(account, "persona_get", {"user_id": user_id})
        result = await _wait_for_result(account, cmd_id, timeout=8.0)
        if result:
            p = result.get("persona")
            if p:
                await update.message.reply_text(
                    f"{label}🎭 Persona for `{user_id}`:\n{p}",
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await update.message.reply_text(
                    f"{label}No custom persona set for `{user_id}`.",
                    parse_mode=ParseMode.MARKDOWN
                )
        else:
            await update.message.reply_text(f"{label}⚠️ Selfbot didn't respond in time.")
        return

    if rest.strip().lower() in ("off", "clear", "remove", "none"):
        _send_command(account, "persona_clear", {"user_id": user_id})
        await update.message.reply_text(
            f"{label}✅ Persona cleared for `{user_id}`.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    _send_command(account, "persona_set", {"user_id": user_id, "persona": rest.strip()})
    await update.message.reply_text(
        f"{label}✅ Persona set for `{user_id}`:\n_{rest.strip()}_",
        parse_mode=ParseMode.MARKDOWN
    )


@owner_only
async def cmd_analyse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account = _get_account(context)
    label = _account_label(account)
    if not context.args:
        await update.message.reply_text(
            "Usage: /analyse <discord_user_id>\n"
            "Example: /analyse 123456789012345678"
        )
        return
    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")
        return

    await update.message.reply_text(
        f"{label}🔍 Analysing user `{user_id}`... (waiting up to 60s)",
        parse_mode=ParseMode.MARKDOWN
    )
    cmd_id = _send_command(account, "analyse_user", {"user_id": user_id})
    result = await _wait_for_result(account, cmd_id, timeout=60.0)

    if not result:
        await update.message.reply_text(f"{label}⚠️ Selfbot didn't respond in time.")
        return
    if not result.get("ok"):
        await update.message.reply_text(f"❌ {result.get('reason', 'Unknown error')}")
        return

    profile = result.get("profile", "")
    if not profile:
        await update.message.reply_text("❌ No profile was generated.")
        return

    if len(profile) > 4000:
        for i in range(0, len(profile), 4000):
            await update.message.reply_text(profile[i:i+4000])
    else:
        await update.message.reply_text(profile)


@owner_only
async def cmd_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account = _get_account(context)
    label = _account_label(account)
    if not context.args:
        await update.message.reply_text(
            "Usage:\n"
            "  /reply check       — list unreplied conversations\n"
            "  /reply all         — respond to all unreplied users\n"
            "  /reply <user_id>   — respond to a specific user"
        )
        return

    keyword = context.args[0].lower()

    if keyword == "check":
        cmd_id = _send_command(account, "reply_check")
        await update.message.reply_text(f"{label}🔍 Checking... (waiting up to 15s)")
        result = await _wait_for_result(account, cmd_id, timeout=15.0)
        if result and result.get("users"):
            lines = [f"*{label}Unreplied conversations:*"]
            for entry in result["users"]:
                count_label = f" ({entry['count']} msgs)" if entry["count"] > 1 else ""
                lines.append(f"• *{entry['name']}* (`{entry['id']}`){count_label} — `{entry['snippet']}`")
            await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        elif result:
            await update.message.reply_text(f"{label}✅ No unreplied conversations.")
        else:
            await update.message.reply_text(f"{label}⚠️ Selfbot didn't respond in time.")

    elif keyword == "all":
        cmd_id = _send_command(account, "reply_all")
        await update.message.reply_text(
            f"{label}⏳ Replying to all unreplied users... (waiting up to 120s)"
        )
        result = await _wait_for_result(account, cmd_id, timeout=120.0)
        if result:
            total = result.get('total', 0)
            if total == 0:
                await update.message.reply_text(f"{label}✅ No unreplied users found.")
            else:
                lines = [f"{label}✅ Done — replied to {total} user(s):"]
                for r in result.get("results", []):
                    icon = "✅" if r["success"] else "❌"
                    name = _escape(r['name'])
                    lines.append(f"{icon} {name} (`{r['id']}`)" + ("" if r["success"] else f" — {r.get('reason', '')}"))
                await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(f"{label}⚠️ Selfbot didn't respond in time. It may still be replying — check Discord.")

    else:
        try:
            user_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID.")
            return
        cmd_id = _send_command(account, "reply_user", {"user_id": user_id})
        await update.message.reply_text(
            f"{label}⏳ Replying to `{user_id}`...",
            parse_mode=ParseMode.MARKDOWN
        )
        result = await _wait_for_result(account, cmd_id, timeout=20.0)
        if result:
            if result.get("success"):
                await update.message.reply_text(
                    f"{label}✅ Replied to `{user_id}`.",
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await update.message.reply_text(f"❌ Couldn't reply: {result.get('reason', 'unknown error')}")
        else:
            await update.message.reply_text(f"{label}⚠️ Selfbot didn't respond in time.")


@owner_only
async def cmd_toggledm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account = _get_account(context)
    label = _account_label(account)
    cmd_id = _send_command(account, "toggle_dm")
    result = await _wait_for_result(account, cmd_id)
    if result:
        await update.message.reply_text(
            f"{label}DMs are now {'✅ allowed' if result.get('allow_dm') else '❌ disallowed'}."
        )
    else:
        await update.message.reply_text(f"{label}⚠️ Command sent to selfbot.")


@owner_only
async def cmd_togglegc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account = _get_account(context)
    label = _account_label(account)
    cmd_id = _send_command(account, "toggle_gc")
    result = await _wait_for_result(account, cmd_id)
    if result:
        await update.message.reply_text(
            f"{label}Group chats are now {'✅ allowed' if result.get('allow_gc') else '❌ disallowed'}."
        )
    else:
        await update.message.reply_text(f"{label}⚠️ Command sent to selfbot.")


@owner_only
async def cmd_toggleserver(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account = _get_account(context)
    label = _account_label(account)
    cmd_id = _send_command(account, "toggle_server")
    result = await _wait_for_result(account, cmd_id)
    if result:
        await update.message.reply_text(
            f"{label}Server responses are now {'✅ enabled' if result.get('allow_server') else '❌ disabled'}."
        )
    else:
        await update.message.reply_text(f"{label}⚠️ Command sent to selfbot.")


@owner_only
async def cmd_toggleactive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account = _get_account(context)
    label = _account_label(account)
    if not context.args:
        await update.message.reply_text("Usage: /toggleactive <channel_id>")
        return
    try:
        channel_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid channel ID.")
        return
    cmd_id = _send_command(account, "toggle_active", {"channel_id": channel_id})
    result = await _wait_for_result(account, cmd_id)
    if result:
        await update.message.reply_text(
            f"{label}Channel `{channel_id}` is now {'✅ active' if result.get('active') else '❌ inactive'}.",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text(f"{label}⚠️ Command sent to selfbot.")


@owner_only
async def cmd_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account = _get_account(context)
    label = _account_label(account)
    if not context.args:
        await update.message.reply_text("Usage:\n  /join <channel_id>\n  /join <discord channel link>")
        return
    cmd_id = _send_command(account, "voice_join", {"args": " ".join(context.args)})
    await update.message.reply_text(f"{label}⏳ Sending join command... (waiting up to 15s)")
    result = await _wait_for_result(account, cmd_id, timeout=15.0)
    if result:
        if result.get("ok"):
            await update.message.reply_text(
                f"{label}✅ Joined **{result.get('channel', '?')}** in **{result.get('guild', '?')}**."
            )
        else:
            await update.message.reply_text(f"❌ {result.get('reason', 'Unknown error')}")
    else:
        await update.message.reply_text(f"{label}⚠️ Selfbot didn't respond in time.")


@owner_only
async def cmd_leave(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account = _get_account(context)
    label = _account_label(account)
    guild_id = None
    if context.args:
        try:
            guild_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ Invalid guild ID.")
            return
    cmd_id = _send_command(account, "voice_leave", {"guild_id": guild_id})
    result = await _wait_for_result(account, cmd_id, timeout=10.0)
    if result:
        if result.get("ok"):
            await update.message.reply_text(
                f"{label}✅ Left **{result.get('channel', '?')}** in **{result.get('guild', '?')}**."
            )
        else:
            await update.message.reply_text(f"❌ {result.get('reason', 'Not in a voice channel.')}")
    else:
        await update.message.reply_text(f"{label}⚠️ Selfbot didn't respond in time.")


@owner_only
async def cmd_autojoin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account = _get_account(context)
    label = _account_label(account)
    if not context.args:
        await update.message.reply_text(
            "Usage:\n"
            "  /autojoin <channel_id>   — set auto-join channel\n"
            "  /autojoin <discord link> — set via channel link\n"
            "  /autojoin off            — disable auto-join"
        )
        return
    cmd_id = _send_command(account, "voice_autojoin", {"args": " ".join(context.args)})
    result = await _wait_for_result(account, cmd_id, timeout=10.0)
    if result:
        if result.get("ok"):
            if result.get("disabled"):
                await update.message.reply_text(f"{label}✅ Auto-join disabled.")
            else:
                await update.message.reply_text(
                    f"{label}✅ Auto-join set to **{result.get('channel', '?')}** in **{result.get('guild', '?')}**."
                )
        else:
            await update.message.reply_text(f"❌ {result.get('reason', 'Unknown error')}")
    else:
        await update.message.reply_text(f"{label}⚠️ Selfbot didn't respond in time.")


@owner_only
async def cmd_setstatus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account = _get_account(context)
    label = _account_label(account)
    emoji = None
    text = None
    if context.args:
        first = context.args[0]
        if len(first) <= 2 or (len(first) <= 4 and not first.isalpha()):
            emoji = first
            text = " ".join(context.args[1:]) or None
        else:
            text = " ".join(context.args)
    cmd_id = _send_command(account, "set_status", {"emoji": emoji, "text": text})
    result = await _wait_for_result(account, cmd_id, timeout=10.0)
    if result:
        msg = "✅ Discord status updated." if (text or emoji) else "✅ Discord status cleared."
        await update.message.reply_text(f"{label}{msg}")
    else:
        await update.message.reply_text(f"{label}⚠️ Selfbot didn't respond in time.")


@owner_only
async def cmd_bio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account = _get_account(context)
    label = _account_label(account)
    text = " ".join(context.args) if context.args else ""
    cmd_id = _send_command(account, "set_bio", {"text": text})
    result = await _wait_for_result(account, cmd_id, timeout=10.0)
    if result:
        if result.get("ok"):
            await update.message.reply_text(f"{label}✅ Bio updated." if text else f"{label}✅ Bio cleared.")
        else:
            await update.message.reply_text(f"❌ {result.get('reason', 'Unknown error')}")
    else:
        await update.message.reply_text(f"{label}⚠️ Selfbot didn't respond in time.")


@owner_only
async def cmd_pfp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account = _get_account(context)
    label = _account_label(account)
    url = None
    image_b64 = None
    _valid_img_exts = (".jpg", ".jpeg", ".png", ".gif", ".webp")

    async def _tg_obj_from_msg(msg):
        """Return (tg_file_obj, ext) from a message's photo or document, or (None, None)."""
        if msg.photo:
            return msg.photo[-1], ".jpg"
        if msg.document:
            doc = msg.document
            _mime = doc.mime_type or ""
            _fname = doc.file_name or ""
            _ext = os.path.splitext(_fname)[1].lower() or ".png"
            if _mime.startswith("image/") or any(_fname.lower().endswith(e) for e in _valid_img_exts):
                return doc, _ext
        return None, None

    if context.args:
        url = context.args[0]
    else:
        # Check the command message itself first, then fall back to the replied-to message
        tg_obj, ext = await _tg_obj_from_msg(update.message)
        if tg_obj is None and update.message.reply_to_message:
            tg_obj, ext = await _tg_obj_from_msg(update.message.reply_to_message)
        if tg_obj is not None:
            try:
                import base64 as _b64
                tg_file = await tg_obj.get_file()
                img_bytes = await tg_file.download_as_bytearray()
                image_b64 = _b64.b64encode(bytes(img_bytes)).decode()
            except Exception as e:
                await update.message.reply_text(f"❌ Failed to download image: {e}")
                return

    if not url and not image_b64:
        await update.message.reply_text(
            "Usage: /pfp <image_url>\n"
            "Or attach an image (as photo or document) and send /pfp,\n"
            "or reply to an image with /pfp."
        )
        return

    cmd_id = _send_command(account, "set_pfp", {"url": url, "b64": image_b64})
    await update.message.reply_text(f"{label}⏳ Updating profile picture...")
    result = await _wait_for_result(account, cmd_id, timeout=20.0)
    if result:
        if result.get("ok"):
            await update.message.reply_text(f"{label}✅ Profile picture updated!")
        else:
            await update.message.reply_text(f"❌ {result.get('reason', 'Unknown error')}")
    else:
        await update.message.reply_text(f"{label}⚠️ Selfbot didn't respond in time.")


@owner_only
async def cmd_addfriend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account = _get_account(context)
    label = _account_label(account)
    if not context.args:
        await update.message.reply_text("Usage: /addfriend <discord_user_id>")
        return
    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")
        return
    cmd_id = _send_command(account, "add_friend", {"user_id": user_id})
    await update.message.reply_text(
        f"{label}⏳ Sending friend request to `{user_id}`...",
        parse_mode=ParseMode.MARKDOWN
    )
    result = await _wait_for_result(account, cmd_id, timeout=15.0)
    if result:
        if result.get("ok"):
            await update.message.reply_text(
                f"{label}✅ Friend request sent to `{user_id}`.",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await update.message.reply_text(f"❌ {result.get('reason', 'Unknown error')}")
    else:
        await update.message.reply_text(f"{label}⚠️ Selfbot didn't respond in time.")


@owner_only
async def cmd_reload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account = _get_account(context)
    label = _account_label(account)
    cmd_id = _send_command(account, "reload")
    await update.message.reply_text(f"{label}⏳ Reloading... (waiting up to 15s)")
    result = await _wait_for_result(account, cmd_id, timeout=15.0)
    if result:
        await update.message.reply_text(f"{label}✅ All cogs reloaded.")
    else:
        await update.message.reply_text(f"{label}⚠️ Selfbot didn't respond in time.")


@owner_only
async def cmd_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account = _get_account(context)
    label = _account_label(account)
    source = "main" if (context.args and context.args[0].lower() == "main") else "release"
    label_str = "latest commit (main)" if source == "main" else "latest release"

    # Write a sentinel flag file directly — avoids polluting the JSON IPC channel
    # which the error notification loop also reads, causing conflicts.
    flag_path = _CONFIG_DIR / "update.flag"
    try:
        flag_path.write_text(source)
    except Exception as e:
        await update.message.reply_text(f"\u274c Failed to write update flag: {e}")
        return

    await update.message.reply_text(
        f"{label}\U0001f504 Update flag written \u2014 pulling {label_str}. Bot will restart shortly."
    )


@owner_only
async def cmd_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account = _get_account(context)
    label = _account_label(account)
    await update.message.reply_text(f"{label}🔄 Sending restart command to selfbot...")
    _send_command(account, "restart")
    await update.message.reply_text(f"{label}✅ Restart command sent. Bot will be back shortly.")


@owner_only
async def cmd_shutdown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account = _get_account(context)
    label = _account_label(account)
    await update.message.reply_text(f"{label}🛑 Sending shutdown command to selfbot...")
    _send_command(account, "shutdown")
    await update.message.reply_text(f"{label}✅ Shutdown command sent.")


# ── /start & /help ─────────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id if user else None
    logger.info(f"[START] /start from user_id={uid}")

    if uid != TG_OWNER_ID:
        await update.message.reply_text(
            f"⛔ Not authorised.\n\n"
            f"Your Telegram user ID is: `{uid}`\n"
            f"Configured TELEGRAM\\_OWNER\\_ID is: `{TG_OWNER_ID}`\n\n"
            f"If these don't match, update `TELEGRAM_OWNER_ID={uid}` in your `config/.env` and restart the controller.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    await _send_help(update, context)


async def _send_help(update: Update, context: ContextTypes.DEFAULT_TYPE = None):
    account = _get_account(context) if context else 1
    label = _account_label(account)

    account_section = ""
    if NUM_ACCOUNTS > 1:
        account_section = (
            f"\n*\U0001f500 Accounts* \u2014 targeting account {account}\\/{NUM_ACCOUNTS}\n"
            "/account \u2014 show current account\n"
            "/account \\<n\\> \u2014 switch to account n\n"
        )

    help_text = f"""📋 *Commands*{account_section}
`─────────────────────────────`
  🌙  *AI*
  /pause \u2014 pause / unpause AI responses
  /pauseuser \\<user\\> \u2014 stop responding to a user
  /unpauseuser \\<user\\> \u2014 resume responding to a user
  /persona \\<user\\> \\[text\\] \u2014 set / clear / show a per\\-user persona
  /wipe \u2014 clear conversation history
  /analyse \\<user\\> \u2014 psychological read of a user
`─────────────────────────────`
  💬  *Replies*
  /reply \\<user\\> \u2014 manually reply to a user \\(also: /response\\)
  /reply check \u2014 show users with unread messages
  /reply all \u2014 respond to all users with unread messages
`─────────────────────────────`
  ⚙️  *Instructions & Config*
  /prompt \\[text\\] \u2014 view / set / clear instructions
  /instructions \u2014 upload a new instructions\\.txt
  /getinstructions \u2014 download current instructions\\.txt
  /config \u2014 view full config \\(all sections\\)
  /config \\<key\\> \\<value\\> \u2014 edit a config value
  /getconfig \u2014 download config\\.yaml
  /setconfig \u2014 upload a new config\\.yaml
`─────────────────────────────`
  📡  *Channels*
  /toggleactive \\<id\\> \u2014 toggle a channel as active
  /toggledm \u2014 toggle DM responses
  /togglegc \u2014 toggle group chat responses
  /toggleserver \u2014 toggle server mention\\/reply responses
  /ignore \\<user\\> \u2014 ignore / unignore a user
`─────────────────────────────`
  🎙️  *Voice*
  /join \\<id\\/link\\> \u2014 join a voice channel
  /leave \u2014 leave the current voice channel
  /autojoin \\<id\\/link\\> \u2014 auto\\-join a voice channel on startup
  /autojoin off \u2014 disable auto\\-join
`─────────────────────────────`
  🖼️  *Images*
  /imagels /imagelist \u2014 list all pictures with descriptions
  /imageupload \u2014 upload picture\\(s\\) \\(attach file \u2014 auto\\-analysed\\)
  /imagedownload /imagedl \\<n\\> \u2014 download a picture by number
  /imagedelete \\<n\\> \u2014 delete a picture by number
  /imagedeleteall \u2014 delete all pictures
`─────────────────────────────`
  🎭  *Profile & Status*
  /setstatus \\[emoji\\] \\[text\\] \u2014 set a custom status
  /bio \\[text\\] \u2014 set profile bio
  /pfp \\<url\\> \u2014 change profile picture
  /mood \\[name\\] \u2014 view or set current mood
`─────────────────────────────`
  🛠️  *System*
  /addfriend \\<user\\_id\\> \u2014 send a friend request by user ID
  /reload \u2014 reload all cogs \\+ instructions
  /restart \u2014 restart the bot
  /shutdown \u2014 shut down the bot
  /update \u2014 update to latest release
  /update main \u2014 update to latest commit
  /getdb \u2014 download memory database
  /leaderboard \\[f\\] \u2014 show top users \\(e\\.g\\. /leaderboard 3d / 1w\\)
  /ping \u2014 check controller is running
"""
    # Retry up to 3 times on transient network errors, fall back to plain text
    from telegram.error import NetworkError as TGNetworkError
    for attempt in range(3):
        try:
            await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN_V2)
            return
        except TGNetworkError as e:
            if attempt < 2:
                logger.warning(f"[HELP] Network error on attempt {attempt + 1}, retrying in 2s: {e}")
                await asyncio.sleep(2)
            else:
                logger.error(f"[HELP] All retries failed, sending plain text fallback: {e}")
                try:
                    await update.message.reply_text(
                        "Bot controller is running.\nSend /help to see commands."
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"[HELP] Unexpected error sending help: {e}")
            try:
                await update.message.reply_text(
                    "Bot controller is running.\nSend /help to see commands."
                )
            except Exception:
                pass
            return


@owner_only
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _send_help(update, context)


# ── Global error handler ──────────────────────────────────────────────────────
async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log all errors; suppress transient network errors silently."""
    from telegram.error import NetworkError as TGNetworkError, TimedOut
    err = context.error
    if isinstance(err, (TGNetworkError, TimedOut)):
        logger.warning(f"[ERROR] Transient network error (suppressed): {err}")
        return
    logger.error(f"[ERROR] Unhandled exception: {err}", exc_info=err)


# ── Error notification polling loop ──────────────────────────────────────────
async def _error_notification_loop(app):
    """Poll the IPC commands file for error notifications sent by the selfbot
    and forward them as Telegram DMs to the owner."""
    import yaml
    _POLL_INTERVAL = 3.0

    def _tg_notifications_enabled() -> bool:
        try:
            with open(_CONFIG_YAML, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            return cfg.get("notifications", {}).get("telegram_error_notifications", False)
        except Exception:
            return False

    logger.info("[TG Error Loop] Started — polling for error notifications")
    while True:
        await asyncio.sleep(_POLL_INTERVAL)
        if not _tg_notifications_enabled():
            continue
        for account in range(1, NUM_ACCOUNTS + 1):
            cmd_file = _cmd_file(account)
            if not cmd_file.exists():
                continue
            try:
                raw = cmd_file.read_text()
                commands_list = json.loads(raw)
            except Exception:
                continue
            if not commands_list:
                continue

            remaining = []
            changed = False
            for entry in commands_list:
                if entry.get("cmd") != "send_error_notification":
                    remaining.append(entry)
                    continue
                changed = True
                payload = entry.get("payload", {})
                title = payload.get("title", "Error")
                detail = payload.get("detail", "")
                try:
                    msg_text = f"🚨 *{_escape(title)}*\n\n{_escape(detail)}"
                    sent = await app.bot.send_message(
                        chat_id=TG_OWNER_ID,
                        text=msg_text,
                        parse_mode=ParseMode.MARKDOWN_V2,
                    )
                    _bot_message_ids.append(sent.message_id)
                except Exception as e:
                    logger.error(f"[TG Error Loop] Failed to send error notification: {e}")
            if changed:
                cmd_file.write_text(json.dumps(remaining))



# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"[TG Controller] Starting")
    print(f"[TG Controller] Owner ID  : {TG_OWNER_ID}")
    print(f"[TG Controller] Accounts  : {NUM_ACCOUNTS} Discord account(s) detected")
    print(f"[TG Controller] IPC files : ../config/tg_commands_N.json (per account)")
    print(f"[TG Controller] Token     : {TG_TOKEN[:8]}...{TG_TOKEN[-4:]}")

    from telegram.request import HTTPXRequest
    from telegram.ext import CallbackQueryHandler

    request = HTTPXRequest(connect_timeout=20.0, read_timeout=20.0)
    app = Application.builder().token(TG_TOKEN).request(request).build()
    app.bot_data["account"] = 1  # default to account 1

    app.add_handler(CommandHandler("start",           cmd_start))
    app.add_handler(CommandHandler("account",         cmd_account))
    app.add_handler(CommandHandler("ping",            cmd_ping))
    app.add_handler(CommandHandler("pause",           cmd_pause))
    app.add_handler(CommandHandler("pauseuser",       cmd_pauseuser))
    app.add_handler(CommandHandler("unpauseuser",     cmd_unpauseuser))
    app.add_handler(CommandHandler("wipe",            cmd_wipe))
    app.add_handler(CommandHandler("persona",         cmd_persona))
    app.add_handler(CommandHandler("analyse",         cmd_analyse))
    app.add_handler(CommandHandler("analyze",         cmd_analyse))
    app.add_handler(CommandHandler("reply",           cmd_reply))
    app.add_handler(CommandHandler("response",        cmd_reply))
    app.add_handler(CommandHandler("config",          cmd_config))
    app.add_handler(CommandHandler("getconfig",       cmd_getconfig))
    app.add_handler(CommandHandler("setconfig",       cmd_setconfig))
    app.add_handler(CommandHandler("prompt",          cmd_prompt))
    app.add_handler(CommandHandler("getinstructions", cmd_getinstructions))
    app.add_handler(CommandHandler("getdb",           cmd_getdb))
    app.add_handler(CommandHandler("reload",          cmd_reload))
    app.add_handler(CommandHandler("update",          cmd_update))
    app.add_handler(CommandHandler("mood",            cmd_mood))
    app.add_handler(CommandHandler("ignore",          cmd_ignore))
    app.add_handler(CommandHandler("status",          cmd_status))
    app.add_handler(CommandHandler("setstatus",       cmd_setstatus))
    app.add_handler(CommandHandler("bio",             cmd_bio))
    app.add_handler(CommandHandler("pfp",             cmd_pfp))
    app.add_handler(CommandHandler("toggledm",        cmd_toggledm))
    app.add_handler(CommandHandler("togglegc",        cmd_togglegc))
    app.add_handler(CommandHandler("toggleserver",    cmd_toggleserver))
    app.add_handler(CommandHandler("toggleactive",    cmd_toggleactive))
    app.add_handler(CommandHandler("join",            cmd_join))
    app.add_handler(CommandHandler("leave",           cmd_leave))
    app.add_handler(CommandHandler("autojoin",        cmd_autojoin))
    app.add_handler(CommandHandler("imagels",         cmd_imagels))
    app.add_handler(CommandHandler("imagelist",       cmd_imagels))
    app.add_handler(CommandHandler("imagedownload",   cmd_imagedownload))
    app.add_handler(CommandHandler("imagedl",         cmd_imagedownload))
    app.add_handler(CommandHandler("imagedelete",     cmd_imagedelete))
    app.add_handler(CommandHandler("imagedeleteall",  cmd_imagedeleteall))
    app.add_handler(CommandHandler("imageupload",     cmd_imageupload))
    app.add_handler(CallbackQueryHandler(_imagels_callback,     pattern=r"^imgls:"))
    app.add_handler(CallbackQueryHandler(_imagels_callback,     pattern=r"^imgdel:"))
    app.add_handler(CommandHandler("leaderboard",     cmd_leaderboard))
    app.add_handler(CommandHandler("addfriend",       cmd_addfriend))
    app.add_handler(CommandHandler("restart",         cmd_restart))
    app.add_handler(CommandHandler("shutdown",        cmd_shutdown))
    app.add_handler(CommandHandler("clear",           cmd_clear))
    app.add_handler(CommandHandler("help",            cmd_help))
    app.add_handler(CallbackQueryHandler(_leaderboard_callback, pattern=r"^lb:"))
    app.add_handler(MessageHandler(filters.Document.FileExtension("txt"),  cmd_instructions_file))
    app.add_handler(MessageHandler(filters.Document.FileExtension("yaml"), cmd_setconfig))
    app.add_handler(MessageHandler(filters.PHOTO, cmd_imageupload))
    app.add_error_handler(_error_handler)

    async def _post_init(application):
        """Start background tasks after the app initialises."""
        asyncio.create_task(_error_notification_loop(application))

    app.post_init = _post_init

    print("[TG Controller] Running — send /start to your bot on Telegram.")
    app.run_polling(allowed_updates=Update.ALL_TYPES, bootstrap_retries=5)


if __name__ == "__main__":
    main()
