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

HOW IT WORKS:
  - Runs as a completely separate process (no shared memory with the selfbot)
  - Communicates with the selfbot via a lightweight shared state file:
      config/tg_commands.json  → Telegram → selfbot  (pending commands)
      config/tg_results.json   → selfbot  → Telegram  (results to send back)
  - The selfbot polls the command file every few seconds and executes commands
  - Commands that modify in-memory bot state (pause, wipe, mood...) are relayed
    through the shared file so the selfbot process applies them directly

COMMANDS AVAILABLE:
  🤖 AI
    /pause              — toggle pause/unpause AI responses
    /pauseuser <id>     — stop responding to a user
    /unpauseuser <id>   — resume responding to a user
    /wipe               — clear all conversation history
    /persona <id> <txt> — set persona for a user (/persona <id> off to clear)
    /analyse <id>       — psychological profile of a user (last 200 msgs)

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
    /mood               — view current mood
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
    /ping               — check if the controller is running (shows latency)
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

# Shared IPC files between this process and the selfbot process
_CMD_FILE = _CONFIG_DIR / "tg_commands.json"    # we write, selfbot reads
_RESULT_FILE = _CONFIG_DIR / "tg_results.json"  # selfbot writes, we read

load_dotenv(dotenv_path=_ENV_PATH, override=True)

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_OWNER_ID = int(os.getenv("TELEGRAM_OWNER_ID", "0"))

if not TG_TOKEN or not TG_OWNER_ID:
    print(
        "\n[TG Controller] TELEGRAM_BOT_TOKEN or TELEGRAM_OWNER_ID not set in config/.env\n"
        "Add them and restart.\n"
    )
    sys.exit(1)

logging.basicConfig(
    format="%(asctime)s [TG] %(levelname)s %(message)s",
    level=logging.WARNING,
)


# ── Auth guard ────────────────────────────────────────────────────────────────
def owner_only(func):
    """Decorator — silently ignores messages from anyone but the owner."""
    @functools.wraps(func)  # FIX: preserve function name so CommandHandler can register it
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != TG_OWNER_ID:
            return
        await func(update, context)
    return wrapper


# ── Config helpers (direct file access — no selfbot process needed) ───────────
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
def _send_command(cmd: str, payload: dict = None):
    """Write a command for the selfbot to pick up and execute."""
    entry = {
        "id": str(time.time()),
        "cmd": cmd,
        "payload": payload or {},
        "ts": time.time(),
    }
    existing = []
    if _CMD_FILE.exists():
        try:
            existing = json.loads(_CMD_FILE.read_text())
        except Exception:
            pass
    existing.append(entry)
    _CMD_FILE.write_text(json.dumps(existing))
    return entry["id"]


async def _wait_for_result(cmd_id: str, timeout: float = 10.0) -> dict | None:
    """Poll the result file until the selfbot posts a result for cmd_id."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _RESULT_FILE.exists():
            try:
                results = json.loads(_RESULT_FILE.read_text())
                if cmd_id in results:
                    result = results.pop(cmd_id)
                    _RESULT_FILE.write_text(json.dumps(results))
                    return result
            except Exception:
                pass
        await asyncio.sleep(0.3)
    return None


def _fmt_bool(val) -> str:
    return "✅" if val else "❌"


def _escape(text: str) -> str:
    """Minimal escaping for Telegram MarkdownV2."""
    for ch in r"\_*[]()~`>#+-=|{}.!":
        text = text.replace(ch, f"\\{ch}")
    return text


# ── Direct commands (no selfbot IPC needed — operate on files directly) ───────

@owner_only
async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check if the controller is running. Also shows Telegram bot latency."""
    start = time.time()
    msg = await update.message.reply_text("🟢 Controller is running — measuring latency...")
    latency = (time.time() - start) * 1000
    await msg.edit_text(f"🟢 Controller is running.\nLatency: `{latency:.0f} ms`", parse_mode=ParseMode.MARKDOWN)


@owner_only
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        cfg = _load_config()
        bot_cfg = cfg.get("bot", {})
        from utils.db import get_channels, get_ignored_users
        from utils.mood import get_mood
        channels = get_channels()
        ignored = get_ignored_users()
        mood = get_mood()
        lines = [
            "📊 *Bot Status*",
            f"  allow\\_dm: {_fmt_bool(bot_cfg.get('allow_dm'))}",
            f"  allow\\_gc: {_fmt_bool(bot_cfg.get('allow_gc'))}",
            f"  allow\\_server: {_fmt_bool(bot_cfg.get('allow_server', True))}",
            f"  mood: `{mood}`",
            f"  active channels: {len(channels)}",
            f"  ignored users: {len(ignored)}",
        ]
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


@owner_only
async def cmd_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args

    if not args:
        # Show full config
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
                "⚙️  Bot Config",
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
        _sections = ["bot", "notifications"]
        node = None
        for _section in _sections:
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
        # Also relay to selfbot for in-memory update
        _send_command("config_update", {"key": key, "value": node[final_key]})
        await update.message.reply_text(f"✅ `{key}` updated: `{old_val}` → `{node[final_key]}`", parse_mode=ParseMode.MARKDOWN)
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
    """Upload a new config.yaml. Attach a .yaml file to this command."""
    if not update.message.document:
        await update.message.reply_text(
            "📎 Attach a `.yaml` file to this command to update the config.\n"
            "The selfbot will need to be restarted for all changes to take effect.\n"
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
        yaml.safe_load(text)  # validate
    except UnicodeDecodeError:
        await update.message.reply_text("❌ Could not read file — make sure it's valid UTF-8.")
        return
    except Exception as e:
        await update.message.reply_text(f"❌ Invalid YAML: {e}")
        return
    _CONFIG_YAML.write_text(text, encoding="utf-8")
    _send_command("restart")
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
    args = context.args

    if not args:
        text = _load_instructions()
        if text:
            # Split into chunks if too long for Telegram
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
        _send_command("instructions_update", {"text": ""})
        await update.message.reply_text("🗑️ Instructions cleared.")
        return

    new_text = " ".join(args)
    _save_instructions(new_text)
    _send_command("instructions_update", {"text": new_text})
    await update.message.reply_text(f"✅ Instructions updated.")


@owner_only
async def cmd_getinstructions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Download the current instructions.txt file."""
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
    """Handle a .txt file upload to set new instructions."""
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
    _send_command("instructions_update", {"text": text})
    await update.message.reply_text("✅ Instructions updated from file!")


@owner_only
async def cmd_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        from utils.db import get_leaderboard
        from datetime import datetime
        import re as _re

        since_ts = None
        filter_label = "all time"
        if context.args:
            m = _re.fullmatch(r"(\d+(?:\.\d+)?)\s*([hdwm])", context.args[0].strip().lower())
            if m:
                amount, unit = float(m.group(1)), m.group(2)
                seconds_map = {"h": 3600, "d": 86400, "w": 604800, "m": 2592000}
                since_ts = time.time() - amount * seconds_map[unit]
                unit_names = {"h": "hour", "d": "day", "w": "week", "m": "month"}
                n = int(amount) if amount == int(amount) else amount
                filter_label = f"last {n} {unit_names[unit]}{'s' if n != 1 else ''}"
            else:
                await update.message.reply_text("Invalid filter. Examples: /leaderboard 24h · /leaderboard 7d · /leaderboard 1w")
                return

        rows = get_leaderboard(limit=20, since=since_ts)
        if not rows:
            await update.message.reply_text(f"No conversations recorded ({filter_label}).")
            return

        medal = ["🥇", "🥈", "🥉"]
        lines = [f"📊 *Leaderboard* — {filter_label}\n"]
        for i, row in enumerate(rows):
            rank = medal[i] if i < 3 else f"`#{i+1}`"
            first_seen = datetime.fromtimestamp(row["first_seen"]).strftime("%d %b %Y")
            msg = row["message_count"]
            lines.append(f"{rank} *{row['username']}* — {msg} msg{'s' if msg != 1 else ''} · since {first_seen}")

        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


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
    """Download an image by number. Usage: /imagedownload <n>"""
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
        idx = int(name)
        matches = [f for f in files if f.stem == f"IMG_{idx}"]
        if matches:
            target = matches[0]
    if not target:
        # Try name match
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
    """Delete an image by number. Usage: /imagedelete <n>"""
    if not context.args:
        await update.message.reply_text("Usage: /imagedelete <number>\nUse /imagels to see images.")
        return
    name = context.args[0]
    cmd_id = _send_command("image_delete", {"name": name})
    result = await _wait_for_result(cmd_id, timeout=10.0)
    if result:
        if result.get("ok"):
            await update.message.reply_text(f"✅ Deleted `{name}`.", parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(f"❌ {result.get('reason', 'Unknown error')}")
    else:
        await update.message.reply_text("⚠️ Command sent, selfbot did not respond in time.")


@owner_only
async def cmd_imagedeleteall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete all images."""
    cmd_id = _send_command("image_delete_all")
    result = await _wait_for_result(cmd_id, timeout=10.0)
    if result:
        count = result.get("count", "?")
        await update.message.reply_text(f"✅ Deleted all {count} image(s).")
    else:
        await update.message.reply_text("⚠️ Command sent, selfbot did not respond in time.")


@owner_only
async def cmd_mood(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        from utils.mood import get_mood
        import utils.mood as mood_module
        cfg = _load_config()
        available = list(cfg["bot"]["mood"]["moods"].keys())

        if not context.args:
            current = get_mood()
            moods_str = "  ".join(f"`{m}`" for m in available)
            await update.message.reply_text(
                f"Current mood: `{current}`\nAvailable: {moods_str}",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        mood_name = context.args[0].lower().strip()
        if mood_name not in available:
            await update.message.reply_text(f"❌ Unknown mood `{mood_name}`. Available: {', '.join(available)}")
            return

        mood_module.current_mood = mood_name
        _send_command("mood_set", {"mood": mood_name})
        await update.message.reply_text(f"✅ Mood set to `{mood_name}`.", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


# ── IPC-relayed commands (need the selfbot process to execute) ─────────────────

@owner_only
async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd_id = _send_command("pause")
    result = await _wait_for_result(cmd_id)
    if result:
        state = "⏸️ Paused" if result.get("paused") else "▶️ Unpaused"
        await update.message.reply_text(f"{state} — AI responses are now {'paused' if result.get('paused') else 'active'}.")
    else:
        await update.message.reply_text("⚠️ Command sent, but selfbot didn't respond in time.\nMake sure the IPC bridge is running in main.py.")


@owner_only
async def cmd_wipe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd_id = _send_command("wipe")
    result = await _wait_for_result(cmd_id)
    if result:
        await update.message.reply_text("🗑️ Conversation history wiped.")
    else:
        await update.message.reply_text("⚠️ Command sent. Selfbot will wipe on next poll.")


@owner_only
async def cmd_ignore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /ignore <user_id>")
        return
    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")
        return
    from utils.db import get_ignored_users, add_ignored_user, remove_ignored_user
    ignored = get_ignored_users()
    if user_id in ignored:
        remove_ignored_user(user_id)
        _send_command("ignore_remove", {"user_id": user_id})
        await update.message.reply_text(f"✅ Unignored `{user_id}`.", parse_mode=ParseMode.MARKDOWN)
    else:
        add_ignored_user(user_id)
        _send_command("ignore_add", {"user_id": user_id})
        await update.message.reply_text(f"✅ Ignoring `{user_id}`.", parse_mode=ParseMode.MARKDOWN)


@owner_only
async def cmd_pauseuser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /pauseuser <user_id>")
        return
    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")
        return
    _send_command("pauseuser", {"user_id": user_id})
    await update.message.reply_text(f"✅ Pause command sent for user `{user_id}`.", parse_mode=ParseMode.MARKDOWN)


@owner_only
async def cmd_unpauseuser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /unpauseuser <user_id>")
        return
    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")
        return
    _send_command("unpauseuser", {"user_id": user_id})
    await update.message.reply_text(f"✅ Unpause command sent for user `{user_id}`.", parse_mode=ParseMode.MARKDOWN)


@owner_only
async def cmd_persona(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        from utils.memory import get_persona
        p = get_persona(user_id)
        if p:
            await update.message.reply_text(f"🎭 Persona for `{user_id}`:\n{p}", parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(f"No custom persona set for `{user_id}`.", parse_mode=ParseMode.MARKDOWN)
        return

    if rest.strip().lower() in ("off", "clear", "remove", "none"):
        from utils.memory import clear_persona
        clear_persona(user_id)
        _send_command("persona_clear", {"user_id": user_id})
        await update.message.reply_text(f"✅ Persona cleared for `{user_id}`.", parse_mode=ParseMode.MARKDOWN)
        return

    from utils.memory import set_persona
    set_persona(user_id, rest.strip())
    _send_command("persona_set", {"user_id": user_id, "persona": rest.strip()})
    await update.message.reply_text(f"✅ Persona set for `{user_id}`:\n_{rest.strip()}_", parse_mode=ParseMode.MARKDOWN)


@owner_only
async def cmd_analyse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Psychological profile of a Discord user based on their recent messages.
    Usage: /analyse <discord_user_id>
    The selfbot will fetch messages from that user's DM channel and return a profile.
    """
    if not context.args:
        await update.message.reply_text(
            "Usage: /analyse <discord_user_id>\n"
            "Example: /analyse 123456789012345678"
        )
        return
    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID. Provide a numeric Discord user ID.")
        return

    await update.message.reply_text(f"🔍 Analysing user `{user_id}`... (waiting up to 60s)", parse_mode=ParseMode.MARKDOWN)
    cmd_id = _send_command("analyse_user", {"user_id": user_id})
    result = await _wait_for_result(cmd_id, timeout=60.0)

    if not result:
        await update.message.reply_text("⚠️ Selfbot didn't respond in time. Make sure the IPC bridge is running.")
        return

    if not result.get("ok"):
        await update.message.reply_text(f"❌ {result.get('reason', 'Unknown error')}")
        return

    profile = result.get("profile", "")
    if not profile:
        await update.message.reply_text("❌ No profile was generated.")
        return

    # Split long responses
    if len(profile) > 4000:
        for i in range(0, len(profile), 4000):
            await update.message.reply_text(profile[i:i+4000])
    else:
        await update.message.reply_text(profile)


@owner_only
async def cmd_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        cmd_id = _send_command("reply_check")
        await update.message.reply_text("🔍 Checking... (waiting up to 15s)")
        result = await _wait_for_result(cmd_id, timeout=15.0)
        if result and result.get("users"):
            lines = ["*Unreplied conversations:*"]
            for entry in result["users"]:
                count_label = f" ({entry['count']} msgs)" if entry["count"] > 1 else ""
                lines.append(f"• *{entry['name']}* (`{entry['id']}`){count_label} — `{entry['snippet']}`")
            await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        elif result:
            await update.message.reply_text("✅ No unreplied conversations.")
        else:
            await update.message.reply_text("⚠️ Selfbot didn't respond in time.")
    elif keyword == "all":
        cmd_id = _send_command("reply_all")
        await update.message.reply_text("⏳ Sending reply all command... (waiting up to 30s)")
        result = await _wait_for_result(cmd_id, timeout=30.0)
        if result:
            lines = [f"✅ Done — {result.get('total', 0)} user(s):"]
            for r in result.get("results", []):
                icon = "✅" if r["success"] else "❌"
                lines.append(f"{icon} {r['name']} (`{r['id']}`)" + ("" if r["success"] else f" — {r.get('reason', '')}"))
            await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text("⚠️ Selfbot didn't respond in time.")
    else:
        # Single user ID
        try:
            user_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ Invalid user ID.")
            return
        cmd_id = _send_command("reply_user", {"user_id": user_id})
        await update.message.reply_text(f"⏳ Replying to `{user_id}`...", parse_mode=ParseMode.MARKDOWN)
        result = await _wait_for_result(cmd_id, timeout=20.0)
        if result:
            if result.get("success"):
                await update.message.reply_text(f"✅ Replied to `{user_id}`.", parse_mode=ParseMode.MARKDOWN)
            else:
                await update.message.reply_text(f"❌ Couldn't reply: {result.get('reason', 'unknown error')}")
        else:
            await update.message.reply_text("⚠️ Selfbot didn't respond in time.")


@owner_only
async def cmd_toggledm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd_id = _send_command("toggle_dm")
    result = await _wait_for_result(cmd_id)
    if result:
        state = result.get("allow_dm")
        await update.message.reply_text(f"DMs are now {'✅ allowed' if state else '❌ disallowed'}.")
    else:
        await update.message.reply_text("⚠️ Command sent to selfbot.")


@owner_only
async def cmd_togglegc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd_id = _send_command("toggle_gc")
    result = await _wait_for_result(cmd_id)
    if result:
        state = result.get("allow_gc")
        await update.message.reply_text(f"Group chats are now {'✅ allowed' if state else '❌ disallowed'}.")
    else:
        await update.message.reply_text("⚠️ Command sent to selfbot.")


@owner_only
async def cmd_toggleserver(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd_id = _send_command("toggle_server")
    result = await _wait_for_result(cmd_id)
    if result:
        state = result.get("allow_server")
        await update.message.reply_text(f"Server responses are now {'✅ enabled' if state else '❌ disabled'}.")
    else:
        await update.message.reply_text("⚠️ Command sent to selfbot.")


@owner_only
async def cmd_toggleactive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle a Discord channel as active by its ID. Usage: /toggleactive <channel_id>"""
    if not context.args:
        await update.message.reply_text("Usage: /toggleactive <channel_id>")
        return
    try:
        channel_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid channel ID.")
        return
    cmd_id = _send_command("toggle_active", {"channel_id": channel_id})
    result = await _wait_for_result(cmd_id)
    if result:
        state = result.get("active")
        await update.message.reply_text(
            f"Channel `{channel_id}` is now {'✅ active' if state else '❌ inactive'}.",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text("⚠️ Command sent to selfbot.")


@owner_only
async def cmd_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Join a Discord voice channel. Usage: /join <channel_id or discord link>"""
    if not context.args:
        await update.message.reply_text(
            "Usage:\n"
            "  /join <channel_id>\n"
            "  /join <discord channel link>"
        )
        return
    args_str = " ".join(context.args)
    cmd_id = _send_command("voice_join", {"args": args_str})
    await update.message.reply_text("⏳ Sending join command... (waiting up to 15s)")
    result = await _wait_for_result(cmd_id, timeout=15.0)
    if result:
        if result.get("ok"):
            await update.message.reply_text(f"✅ Joined **{result.get('channel', '?')}** in **{result.get('guild', '?')}**.")
        else:
            await update.message.reply_text(f"❌ {result.get('reason', 'Unknown error')}")
    else:
        await update.message.reply_text("⚠️ Selfbot didn't respond in time.")


@owner_only
async def cmd_leave(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Leave the current Discord voice channel. Usage: /leave or /leave <guild_id>"""
    guild_id = None
    if context.args:
        try:
            guild_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ Invalid guild ID.")
            return
    cmd_id = _send_command("voice_leave", {"guild_id": guild_id})
    result = await _wait_for_result(cmd_id, timeout=10.0)
    if result:
        if result.get("ok"):
            await update.message.reply_text(f"✅ Left **{result.get('channel', '?')}** in **{result.get('guild', '?')}**.")
        else:
            await update.message.reply_text(f"❌ {result.get('reason', 'Not in a voice channel.')}")
    else:
        await update.message.reply_text("⚠️ Selfbot didn't respond in time.")


@owner_only
async def cmd_autojoin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set or disable voice channel auto-join on startup.
    Usage: /autojoin <channel_id or link>  |  /autojoin off
    """
    if not context.args:
        await update.message.reply_text(
            "Usage:\n"
            "  /autojoin <channel_id>        — set auto-join channel\n"
            "  /autojoin <discord link>      — set via channel link\n"
            "  /autojoin off                 — disable auto-join"
        )
        return
    args_str = " ".join(context.args)
    cmd_id = _send_command("voice_autojoin", {"args": args_str})
    result = await _wait_for_result(cmd_id, timeout=10.0)
    if result:
        if result.get("ok"):
            if result.get("disabled"):
                await update.message.reply_text("✅ Auto-join disabled.")
            else:
                await update.message.reply_text(
                    f"✅ Auto-join set to **{result.get('channel', '?')}** in **{result.get('guild', '?')}**."
                )
        else:
            await update.message.reply_text(f"❌ {result.get('reason', 'Unknown error')}")
    else:
        await update.message.reply_text("⚠️ Selfbot didn't respond in time.")


@owner_only
async def cmd_setstatus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set or clear the Discord custom status.
    Usage: /setstatus [emoji] [text]   — both optional; omit both to clear
    Example: /setstatus 🎮 gaming rn
    """
    emoji = None
    text = None
    if context.args:
        # Heuristic: if first arg is a single emoji or short symbol, treat as emoji
        first = context.args[0]
        if len(first) <= 2 or (len(first) <= 4 and not first.isalpha()):
            emoji = first
            text = " ".join(context.args[1:]) or None
        else:
            text = " ".join(context.args)
    cmd_id = _send_command("set_status", {"emoji": emoji, "text": text})
    result = await _wait_for_result(cmd_id, timeout=10.0)
    if result:
        if text or emoji:
            await update.message.reply_text("✅ Discord status updated.")
        else:
            await update.message.reply_text("✅ Discord status cleared.")
    else:
        await update.message.reply_text("⚠️ Selfbot didn't respond in time.")


@owner_only
async def cmd_bio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set or clear the Discord profile bio.
    Usage: /bio <text>   — set bio
           /bio          — clear bio
    """
    text = " ".join(context.args) if context.args else ""
    cmd_id = _send_command("set_bio", {"text": text})
    result = await _wait_for_result(cmd_id, timeout=10.0)
    if result:
        if result.get("ok"):
            await update.message.reply_text("✅ Bio updated." if text else "✅ Bio cleared.")
        else:
            await update.message.reply_text(f"❌ {result.get('reason', 'Unknown error')}")
    else:
        await update.message.reply_text("⚠️ Selfbot didn't respond in time.")


@owner_only
async def cmd_pfp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Change the Discord profile picture.
    Usage: /pfp <image_url>
    Or send /pfp with an image attached.
    """
    url = None
    if context.args:
        url = context.args[0]
    elif update.message.photo:
        # Telegram photo attached
        photo = update.message.photo[-1]
        tg_file = await photo.get_file()
        url = tg_file.file_path
    elif update.message.document and update.message.document.mime_type.startswith("image/"):
        tg_file = await update.message.document.get_file()
        url = tg_file.file_path

    if not url:
        await update.message.reply_text(
            "Usage: /pfp <image_url>\n"
            "Or send /pfp with an image attached."
        )
        return

    cmd_id = _send_command("set_pfp", {"url": url})
    await update.message.reply_text("⏳ Updating profile picture...")
    result = await _wait_for_result(cmd_id, timeout=20.0)
    if result:
        if result.get("ok"):
            await update.message.reply_text("✅ Profile picture updated!")
        else:
            await update.message.reply_text(f"❌ {result.get('reason', 'Unknown error')}")
    else:
        await update.message.reply_text("⚠️ Selfbot didn't respond in time.")


@owner_only
async def cmd_addfriend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a Discord friend request by user ID. Usage: /addfriend <user_id>"""
    if not context.args:
        await update.message.reply_text("Usage: /addfriend <discord_user_id>")
        return
    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")
        return
    cmd_id = _send_command("add_friend", {"user_id": user_id})
    await update.message.reply_text(f"⏳ Sending friend request to `{user_id}`...", parse_mode=ParseMode.MARKDOWN)
    result = await _wait_for_result(cmd_id, timeout=15.0)
    if result:
        if result.get("ok"):
            await update.message.reply_text(f"✅ Friend request sent to `{user_id}`.", parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(f"❌ {result.get('reason', 'Unknown error')}")
    else:
        await update.message.reply_text("⚠️ Selfbot didn't respond in time.")


@owner_only
async def cmd_reload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reload all cogs and instructions in the selfbot."""
    cmd_id = _send_command("reload")
    await update.message.reply_text("⏳ Reloading... (waiting up to 15s)")
    result = await _wait_for_result(cmd_id, timeout=15.0)
    if result:
        await update.message.reply_text("✅ All cogs reloaded.")
    else:
        await update.message.reply_text("⚠️ Selfbot didn't respond in time.")


@owner_only
async def cmd_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Update selfbot to latest release or commit.
    Usage: /update         — latest release
           /update main    — latest commit
    """
    source = "main" if (context.args and context.args[0].lower() == "main") else "release"
    label = "latest commit (main)" if source == "main" else "latest release"
    _send_command("update", {"source": source})
    await update.message.reply_text(f"🔄 Update command sent — pulling {label}. Bot will restart shortly.")


@owner_only
async def cmd_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔄 Sending restart command to selfbot...")
    _send_command("restart")
    await update.message.reply_text("✅ Restart command sent. Bot will be back shortly.")


@owner_only
async def cmd_shutdown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🛑 Sending shutdown command to selfbot...")
    _send_command("shutdown")
    await update.message.reply_text("✅ Shutdown command sent.")


@owner_only
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
🤖 *AI Selfbot Telegram Controller*

*🤖 AI*
/pause — toggle pause AI responses
/pauseuser <id> — stop responding to user
/unpauseuser <id> — resume responding to user
/wipe — clear conversation history
/persona <id> <text|off|show> — manage per-user persona
/analyse <id> — psychological profile of a user

*💬 Replies*
/reply check — show unreplied conversations
/reply all — respond to all unreplied
/reply <id> — respond to specific user

*⚙️ Config & Instructions*
/config — view full config
/config <key> <value> — edit a value
/prompt — view instructions
/prompt <text> — update instructions
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
/mood — view current mood
/mood <name> — set mood
/ignore <id> — ignore/unignore user

*🎙️ Profile & Status*
/status — show bot status
/setstatus \\[emoji\\] \\[text\\] — set Discord custom status
/bio \\[text\\] — set profile bio \\(omit to clear\\)
/pfp <url> — change profile picture

*📡 Channels*
/toggledm — toggle DM responses
/togglegc — toggle group chat responses
/toggleserver — toggle server responses
/toggleactive <id> — toggle channel as active

*🎙️ Voice*
/join <id/link> — join voice channel
/leave — leave voice channel
/autojoin <id/link> — set auto\\-join channel
/autojoin off — disable auto\\-join

*🖼️ Images*
/imagels — list all pictures
/imagedownload <n> — download image by number
/imagedelete <n> — delete image by number
/imagedeleteall — delete all images

*📊 Stats*
/leaderboard — top users all time
/leaderboard <filter> — e\\.g\\. /leaderboard 7d
/addfriend <id> — send friend request

*🛠️ System*
/restart — restart selfbot
/shutdown — shut down selfbot
/ping — check controller is running
"""
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN_V2)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"[TG Controller] Starting — owner ID: {TG_OWNER_ID}")
    print(f"[TG Controller] IPC command file: {_CMD_FILE}")

    app = Application.builder().token(TG_TOKEN).build()

    # AI
    app.add_handler(CommandHandler("ping",              cmd_ping))
    app.add_handler(CommandHandler("pause",             cmd_pause))
    app.add_handler(CommandHandler("pauseuser",         cmd_pauseuser))
    app.add_handler(CommandHandler("unpauseuser",       cmd_unpauseuser))
    app.add_handler(CommandHandler("wipe",              cmd_wipe))
    app.add_handler(CommandHandler("persona",           cmd_persona))
    app.add_handler(CommandHandler("analyse",           cmd_analyse))
    app.add_handler(CommandHandler("analyze",           cmd_analyse))   # alias

    # Replies
    app.add_handler(CommandHandler("reply",             cmd_reply))

    # Config & Instructions
    app.add_handler(CommandHandler("config",            cmd_config))
    app.add_handler(CommandHandler("getconfig",         cmd_getconfig))
    app.add_handler(CommandHandler("setconfig",         cmd_setconfig))
    app.add_handler(CommandHandler("prompt",            cmd_prompt))
    app.add_handler(CommandHandler("getinstructions",   cmd_getinstructions))
    app.add_handler(CommandHandler("getdb",             cmd_getdb))
    app.add_handler(CommandHandler("reload",            cmd_reload))
    app.add_handler(CommandHandler("update",            cmd_update))

    # Behaviour
    app.add_handler(CommandHandler("mood",              cmd_mood))
    app.add_handler(CommandHandler("ignore",            cmd_ignore))

    # Profile & Status
    app.add_handler(CommandHandler("status",            cmd_status))
    app.add_handler(CommandHandler("setstatus",         cmd_setstatus))
    app.add_handler(CommandHandler("bio",               cmd_bio))
    app.add_handler(CommandHandler("pfp",               cmd_pfp))

    # Channels
    app.add_handler(CommandHandler("toggledm",          cmd_toggledm))
    app.add_handler(CommandHandler("togglegc",          cmd_togglegc))
    app.add_handler(CommandHandler("toggleserver",      cmd_toggleserver))
    app.add_handler(CommandHandler("toggleactive",      cmd_toggleactive))

    # Voice
    app.add_handler(CommandHandler("join",              cmd_join))
    app.add_handler(CommandHandler("leave",             cmd_leave))
    app.add_handler(CommandHandler("autojoin",          cmd_autojoin))

    # Images
    app.add_handler(CommandHandler("imagels",           cmd_imagels))
    app.add_handler(CommandHandler("imagedownload",     cmd_imagedownload))
    app.add_handler(CommandHandler("imagedelete",       cmd_imagedelete))
    app.add_handler(CommandHandler("imagedeleteall",    cmd_imagedeleteall))

    # Stats & System
    app.add_handler(CommandHandler("leaderboard",       cmd_leaderboard))
    app.add_handler(CommandHandler("addfriend",         cmd_addfriend))
    app.add_handler(CommandHandler("restart",           cmd_restart))
    app.add_handler(CommandHandler("shutdown",          cmd_shutdown))
    app.add_handler(CommandHandler("help",              cmd_help))

    # File upload handlers
    app.add_handler(MessageHandler(filters.Document.FileExtension("txt"), cmd_instructions_file))
    app.add_handler(MessageHandler(filters.Document.FileExtension("yaml"), cmd_setconfig))

    print("[TG Controller] Running. Send /help to your bot on Telegram.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
