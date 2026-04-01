"""
telegram_controller.py — External Telegram controller for the Discord AI Selfbot.

HOW TO SET UP:
  1. Message @BotFather on Telegram → /newbot → copy the token
  2. Get your Telegram user ID from @userinfobot
  3. Add to your config/.env:
       TELEGRAM_BOT_TOKEN=your_token_here
       TELEGRAM_OWNER_ID=your_telegram_user_id_here
  4. Install dependency:  pip install python-telegram-bot
  5. Run alongside the selfbot:  python telegram_controller.py

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
    /reply <id>         — respond to a specific user by ID

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
    /imagels            — list all pictures with descriptions
    /imagedownload <n>  — download image by number
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
_BASE = Path(__file__).resolve().parent
_CONFIG_DIR = _BASE / "config"
_ENV_PATH = _CONFIG_DIR / ".env"
_CONFIG_YAML = _CONFIG_DIR / "config.yaml"
_INSTRUCTIONS_PATH = _CONFIG_DIR / "instructions.txt"
_DB_PATH = _CONFIG_DIR / "bot_data.db"
_PICTURES_DIR = _CONFIG_DIR / "pictures"

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
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


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
    """Write a command to the per-account IPC file."""
    entry = {
        "id": str(time.time()),
        "cmd": cmd,
        "payload": payload or {},
        "ts": time.time(),
    }
    f = _cmd_file(account)
    existing = []
    if f.exists():
        try:
            existing = json.loads(f.read_text())
        except Exception:
            pass
    existing.append(entry)
    f.write_text(json.dumps(existing))
    return entry["id"]


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
                    f.write_text(json.dumps(results))
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
            status = bot_cfg.get("status") or {}
            notif = cfg.get("notifications") or {}
            wait_times = bot_cfg.get("batch_wait_times") or []
            wt_str = "  ".join(f"{w['time']}s({w['weight']})" for w in wait_times)
            mood_list = ", ".join(mood.get("moods", {}).keys())
            nudge_hours = nudge.get("send_during_hours", [10, 22])
            models = bot_cfg.get("groq_models", [])
            lines = [
                "```",
                f"⚙️  Bot Config{' — ' + label.strip() if label else ''}",
                "─────────────────────────────",
                "  🔧  General",
                f"  prefix               {bot_cfg.get('prefix')}",
                f"  trigger              {bot_cfg.get('trigger')}",
                f"  priority_prefix      {bot_cfg.get('priority_prefix')}",
                "─────────────────────────────",
                "  💬  Responses",
                f"  allow_dm             {bot_cfg.get('allow_dm')}",
                f"  allow_gc             {bot_cfg.get('allow_gc')}",
                f"  allow_server         {bot_cfg.get('allow_server', True)}",
                f"  hold_conversation    {bot_cfg.get('hold_conversation')}",
                f"  realistic_typing     {bot_cfg.get('realistic_typing')}",
                f"  reply_ping           {bot_cfg.get('reply_ping')}",
                f"  disable_mentions     {bot_cfg.get('disable_mentions')}",
                f"  batch_messages       {bot_cfg.get('batch_messages')}",
                f"  batch_wait_times     {wt_str}",
                "─────────────────────────────",
                "  🎭  Behaviour",
                f"  ignore_chance        {bot_cfg.get('ignore_chance')}",
                f"  typo_chance          {bot_cfg.get('typo_chance')}",
                f"  anti_age_ban         {bot_cfg.get('anti_age_ban')}",
                "─────────────────────────────",
                "  🤖  Models",
                f"  groq_models          {', '.join(models) if isinstance(models, list) else models}",
                f"  groq_image_model     {bot_cfg.get('groq_image_model')}",
                f"  groq_whisper_model   {bot_cfg.get('groq_whisper_model')}",
                "─────────────────────────────",
                "  🔊  TTS",
                f"  tts.enabled          {tts.get('enabled')}",
                f"  tts.voice            {tts.get('voice')}",
                "─────────────────────────────",
                "  😶  Mood",
                f"  mood.enabled         {mood.get('enabled')}",
                f"  mood.moods           {mood_list}",
                "─────────────────────────────",
                "  🕐  Status",
                f"  status.enabled       {status.get('enabled')}",
                "─────────────────────────────",
                "  💬  Late Reply",
                f"  late_reply.enabled   {late.get('enabled')}",
                f"  late_reply.threshold {late.get('threshold')}",
                "─────────────────────────────",
                "  💤  Nudge",
                f"  nudge.enabled        {nudge.get('enabled', False)}",
                f"  nudge.threshold_days {nudge.get('threshold_days', 2)}",
                f"  nudge.send_during    {nudge_hours[0]}:00–{nudge_hours[1]}:00",
                "─────────────────────────────",
                "  👥  Friend Requests",
                f"  fr.enabled           {fr.get('enabled', True)}",
                f"  fr.accept_delay_min  {fr.get('accept_delay_min', 120)}s",
                f"  fr.accept_delay_max  {fr.get('accept_delay_max', 600)}s",
                "─────────────────────────────",
                "  🔔  Notifications",
                f"  error_webhook        {'set' if notif.get('error_webhook') else 'not set'}",
                f"  ratelimit_notifs     {notif.get('ratelimit_notifications')}",
                "```",
                "Edit with: /config key value",
                "Example: /config tts.enabled true",
            ]
            await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
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


# ── /leaderboard — via IPC ────────────────────────────────────────────────────
@owner_only
async def cmd_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account = _get_account(context)
    label = _account_label(account)
    import re as _re

    filter_str = context.args[0] if context.args else None
    if filter_str:
        m = _re.fullmatch(r"(\d+(?:\.\d+)?)\s*([hdwm])", filter_str.strip().lower())
        if not m:
            await update.message.reply_text(
                "Invalid filter. Examples: /leaderboard 24h · /leaderboard 7d · /leaderboard 1w"
            )
            return

    cmd_id = _send_command(account, "get_leaderboard", {"filter": filter_str})
    result = await _wait_for_result(account, cmd_id, timeout=15.0)

    if not result:
        await update.message.reply_text(f"{label}⚠️ Selfbot didn't respond in time.")
        return

    rows = result.get("rows", [])
    filter_label = result.get("filter_label", "all time")

    if not rows:
        await update.message.reply_text(f"{label}No conversations recorded ({filter_label}).")
        return

    medal = ["🥇", "🥈", "🥉"]
    lines = [f"📊 *{label}Leaderboard* — {filter_label}\n"]
    for i, row in enumerate(rows):
        rank = medal[i] if i < 3 else f"`#{i+1}`"
        msg = row["message_count"]
        lines.append(f"{rank} *{row['username']}* — {msg} msg{'s' if msg != 1 else ''} · since {row['first_seen_fmt']}")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ── /imagels — direct file access ────────────────────────────────────────────
@owner_only
async def cmd_imagels(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        lines = [f"🖼️ *Pictures* ({len(files)} total)\n"]
        for f in files:
            desc = get_picture_description(f.name) or "*(no description)*"
            short_desc = desc[:80] + ("…" if len(desc) > 80 else "")
            lines.append(f"`{f.name}` — {short_desc}")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


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
        await update.message.reply_text("Usage: /imagedelete <number>\nUse /imagels to see images.")
        return
    cmd_id = _send_command(account, "image_delete", {"name": context.args[0]})
    result = await _wait_for_result(account, cmd_id, timeout=10.0)
    if result:
        if result.get("ok"):
            await update.message.reply_text(f"{label}✅ Deleted `{context.args[0]}`.", parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(f"❌ {result.get('reason', 'Unknown error')}")
    else:
        await update.message.reply_text(f"{label}⚠️ Command sent, selfbot did not respond in time.")


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
        await update.message.reply_text(f"{label}⏳ Sending reply all command... (waiting up to 30s)")
        result = await _wait_for_result(account, cmd_id, timeout=30.0)
        if result:
            lines = [f"{label}✅ Done — {result.get('total', 0)} user(s):"]
            for r in result.get("results", []):
                icon = "✅" if r["success"] else "❌"
                lines.append(f"{icon} {r['name']} (`{r['id']}`)" + ("" if r["success"] else f" — {r.get('reason', '')}"))
            await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(f"{label}⚠️ Selfbot didn't respond in time.")

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
    if context.args:
        url = context.args[0]
    elif update.message.photo:
        tg_file = await update.message.photo[-1].get_file()
        url = tg_file.file_path
    elif update.message.document and update.message.document.mime_type.startswith("image/"):
        tg_file = await update.message.document.get_file()
        url = tg_file.file_path

    if not url:
        await update.message.reply_text("Usage: /pfp <image_url>\nOr send /pfp with an image attached.")
        return

    cmd_id = _send_command(account, "set_pfp", {"url": url})
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
    _send_command(account, "update", {"source": source})
    await update.message.reply_text(
        f"{label}🔄 Update command sent — pulling {label_str}. Bot will restart shortly."
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
            f"\n*🔀 Accounts* — targeting account {account}/{NUM_ACCOUNTS}\n"
            "/account — show current account\n"
            "/account \\<n\\> — switch to account n\n"
        )

    help_text = f"""
🤖 *AI Selfbot Telegram Controller*
💡 _Using Telegram is safer than Discord commands — no selfbot activity on your account\\._{account_section}
*🤖 AI*
/pause — toggle pause AI responses
/pauseuser \\<id\\> — stop responding to user
/unpauseuser \\<id\\> — resume responding to user
/wipe — clear conversation history
/persona \\<id\\> \\<text|off|show\\> — manage per\\-user persona
/analyse \\<id\\> — psychological profile of a user

*💬 Replies*
/reply check — show unreplied conversations
/reply all — respond to all unreplied
/reply \\<id\\> — respond to specific user

*⚙️ Config & Instructions*
/config — view full config
/config \\<key\\> \\<value\\> — edit a value
/prompt — view instructions
/prompt \\<text\\> — update instructions
/prompt clear — clear instructions
/getconfig — download config\\.yaml
/setconfig — upload new config\\.yaml \\(attach file\\)
/instructions — upload new instructions\\.txt \\(attach file\\)
/getinstructions — download instructions\\.txt
/getdb — download bot\\_data\\.db
/reload — reload all cogs \\+ instructions
/update — update to latest release
/update main — update to latest commit

*🎭 Behaviour*
/mood — view current mood \\(live\\)
/mood \\<n\\> — set mood
/ignore \\<id\\> — ignore/unignore user

*🎙️ Profile & Status*
/status — show bot status
/setstatus \\[emoji\\] \\[text\\] — set Discord custom status
/bio \\[text\\] — set profile bio \\(omit to clear\\)
/pfp \\<url\\> — change profile picture

*📡 Channels*
/toggledm — toggle DM responses
/togglegc — toggle group chat responses
/toggleserver — toggle server responses
/toggleactive \\<id\\> — toggle channel as active

*🎙️ Voice*
/join \\<id/link\\> — join voice channel
/leave — leave voice channel
/autojoin \\<id/link\\> — set auto\\-join channel
/autojoin off — disable auto\\-join

*🖼️ Images*
/imagels — list all pictures
/imagedownload \\<n\\> — download image by number
/imagedelete \\<n\\> — delete image by number
/imagedeleteall — delete all images

*📊 Stats*
/leaderboard — top users all time
/leaderboard \\<filter\\> — e\\.g\\. /leaderboard 7d
/addfriend \\<id\\> — send friend request

*🛠️ System*
/restart — restart selfbot
/shutdown — shut down selfbot
/ping — check controller is running
"""
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN_V2)


@owner_only
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _send_help(update, context)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"[TG Controller] Starting")
    print(f"[TG Controller] Owner ID  : {TG_OWNER_ID}")
    print(f"[TG Controller] Accounts  : {NUM_ACCOUNTS} Discord account(s) detected")
    print(f"[TG Controller] IPC files : config/tg_commands_N.json (per account)")
    print(f"[TG Controller] Token     : {TG_TOKEN[:8]}...{TG_TOKEN[-4:]}")

    app = Application.builder().token(TG_TOKEN).build()
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
    app.add_handler(CommandHandler("imagedownload",   cmd_imagedownload))
    app.add_handler(CommandHandler("imagedelete",     cmd_imagedelete))
    app.add_handler(CommandHandler("imagedeleteall",  cmd_imagedeleteall))
    app.add_handler(CommandHandler("leaderboard",     cmd_leaderboard))
    app.add_handler(CommandHandler("addfriend",       cmd_addfriend))
    app.add_handler(CommandHandler("restart",         cmd_restart))
    app.add_handler(CommandHandler("shutdown",        cmd_shutdown))
    app.add_handler(CommandHandler("help",            cmd_help))
    app.add_handler(MessageHandler(filters.Document.FileExtension("txt"),  cmd_instructions_file))
    app.add_handler(MessageHandler(filters.Document.FileExtension("yaml"), cmd_setconfig))

    print("[TG Controller] Running — send /start to your bot on Telegram.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
