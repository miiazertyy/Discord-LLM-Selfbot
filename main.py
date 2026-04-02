import os
import io
import asyncio
import discord
import shutil
import re
import random
import sys
import time
import aiohttp
import utils.ai as ai_module

from utils.helpers import (
    clear_console,
    resource_path,
    get_env_path,
    load_instructions,
    load_config,
    load_tokens,
)

from utils.db import init_db, get_channels, get_ignored_users, add_unresponded, mark_responded, mark_nudge_sent, get_pending_nudges, get_picture_description, record_user_message, get_cached_profile, set_cached_profile
from utils.error_notifications import webhook_log

async def _notify_telegram_error(title: str, detail: str):
    """Forward an error to the Telegram controller if telegram_error_notifications is enabled."""
    try:
        cfg = load_config()
        if not cfg.get("notifications", {}).get("telegram_error_notifications", False):
            return
        import json as _j
        from pathlib import Path as _P
        _cmd_file = _P(resource_path("config/tg_commands_1.json"))
        entry = {
            "id": f"err_{time.time()}",
            "cmd": "send_error_notification",
            "payload": {"title": title, "detail": str(detail)[:1500]},
            "ts": time.time(),
        }
        _existing = []
        if _cmd_file.exists():
            try:
                _existing = _j.loads(_cmd_file.read_text())
            except Exception:
                pass
        _existing.append(entry)
        _cmd_file.write_text(_j.dumps(_existing))
    except Exception:
        pass
from colorama import init, Fore, Style

from utils.logger import (
    log_incoming,
    log_response,
    log_rate_limit,
    log_error,
    log_cooldown,
    log_system,
    log_received,
    separator
)

from curl_cffi.requests import AsyncSession

from utils.mood import get_mood, get_mood_prompt, mood_loop, shift_mood
from utils.memory import init_memory, get_memory, set_memory, delete_memory, format_memory_for_prompt, get_persona
from utils.tts import generate_voice_message
from utils.tts_trigger import is_tts_request
from utils.voice_send import send_voice_message
from utils.captcha import init_captcha, solve_hcaptcha, solve_discord_captcha


init()


def get_batch_wait_time():
    wait_times = config["bot"]["batch_wait_times"]
    times = [item["time"] for item in wait_times]
    weights = [item["weight"] for item in wait_times]
    return random.choices(times, weights=weights, k=1)[0]


config = load_config()

from utils.ai import init_ai, generate_response, generate_response_image, extract_memory, detect_memory_deletion, transcribe_voice, summarize_history, detect_language, generate_nudge, reset_client_index, fallback_model
from dotenv import load_dotenv
from discord.ext import commands
from utils.split_response import split_response
from datetime import datetime
from collections import deque
from asyncio import Lock

env_path = get_env_path()

load_dotenv(dotenv_path=env_path, override=True)

init_db()
init_ai()
init_memory()
init_captcha()

TOKENS = load_tokens()
PREFIX = config["bot"]["prefix"]
OWNER_ID = config["bot"]["owner_id"]
TRIGGER = config["bot"]["trigger"].lower().split(",")
DISABLE_MENTIONS = config["bot"]["disable_mentions"]
PRIORITY_PREFIX = config["bot"]["priority_prefix"]

SPAM_MESSAGE_THRESHOLD = 5
SPAM_TIME_WINDOW = 10.0
COOLDOWN_DURATION = 60.0
MAX_HISTORY = 15
IGNORE_CHANCE = config["bot"]["ignore_chance"]
CONVERSATION_TIMEOUT = 150.0

_MOOD_CFG = config["bot"]["mood"]
_LATE_CFG = config["bot"]["late_reply"]
_STALE_CFG = config["bot"].get("stale_reply") or {}

REFUSAL_PHRASES = [
    "i'm sorry, but i can't",
    "i cannot help with that",
    "i'm not able to",
    "as an ai",
    "i don't feel comfortable",
    "i can't help with that",
    "i'm unable to",
    "i'm sorry, but i can't continue this conversation.",
    "i'm sorry, but i can't share that.",
    "i'm sorry, but i can't help with that",
    "i apologize, but i can't",
    "i apologize, but i cannot",
    "i'm not going to",
    "i won't be able to",
    "that's not something i can",
    "i must decline",
    "i have to decline",
    "i cannot assist with",
    "i can't assist with",
    "i'm designed to",
    "as a language model",
    "i cannot engage",
    "i can't engage",
    "i'm not comfortable",
    "i cannot provide",
    "i can't provide",
    "i cannot support",
    "i cannot generate",
    "i can't generate",
    "i can't help with that",
    "sorry, i can't do that",
    "sorry, but i can't do that",
    "i can't do that",
    "i cannot do that",
    "i'm not able to do that",
    "i won't do that",
    "i will not do that",
    "that's not something i'm able",
    "i'm afraid i can't",
    "i afraid i cannot",
    "unfortunately, i can't",
    "unfortunately i can't",
    "unfortunately, i cannot",
    "i need to decline",
    "i'm going to have to decline",
]


def create_bot() -> commands.Bot:
    """Instantiate a fully configured bot. Called once per token."""

    async def _discord_captcha_handler(exc: discord.CaptchaRequired, b: commands.Bot) -> str:
        token = await solve_discord_captcha(exc)
        if token is None:
            raise exc  # re-raise so discord.py-self surfaces the error normally
        return token

    b = commands.Bot(command_prefix=PREFIX, help_command=None, mobile=True, captcha_handler=_discord_captcha_handler)
    b.retry_queue = deque()
    b.owner_id = OWNER_ID
    b.active_channels = set(get_channels())
    b.ignore_users = get_ignored_users()
    b.message_history = {}
    b.paused = False
    b.allow_dm = config["bot"]["allow_dm"]
    b.allow_gc = config["bot"]["allow_gc"]
    b.allow_server = config["bot"].get("allow_server", True)
    b.realistic_typing = config["bot"]["realistic_typing"]
    b.anti_age_ban = config["bot"]["anti_age_ban"]
    b.batch_messages = config["bot"]["batch_messages"]
    b.hold_conversation = config["bot"]["hold_conversation"]
    b.user_message_counts = {}
    b.user_cooldowns = {}
    b.instructions = load_instructions()
    b.message_queues = {}
    b.processing_locks = {}
    b.user_message_batches = {}
    b.active_conversations = {}
    b.sent_pictures = {}
    b._memory_cache = {}
    b._memory_call_counter = {}
    b.paused_users = set()
    b.removed_friends = set()  # user IDs that were previously friends (for instant re-add)
    # Global send lock: ensures only one message is being sent at a time across ALL users.
    # This prevents two concurrent generate_response_and_reply calls from racing each other
    # and causing messages to arrive out-of-order or simultaneously.
    b.global_send_lock = Lock()
    b.last_global_send = 0.0  # timestamp of last sent message across all users
    b._lang_cache = {}  # uid -> {"tag": str, "count": int}
    return b


bot = create_bot()

def is_refusal(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in REFUSAL_PHRASES)

def get_channel_context(message):
    """Returns (channel_name, guild_name) with proper DM/GC labels."""
    if isinstance(message.channel, discord.GroupChannel):
        channel_name = getattr(message.channel, 'name', None) or "GC"
        guild_name = "GC"
    elif isinstance(message.channel, discord.DMChannel):
        channel_name = "DM"
        guild_name = "DM"
    else:
        channel_name = getattr(message.channel, 'name', 'unknown')
        guild_name = getattr(message.guild, 'name', 'unknown')
    return channel_name, guild_name


def get_late_opener(prompt: str) -> str:
    late_cfg = config["bot"]["late_reply"]
    french_indicators = late_cfg.get("french_indicators", [])
    prompt_lower = prompt.lower()
    is_french = any(word in prompt_lower.split() for word in french_indicators)
    openers = late_cfg["openers_fr"] if is_french else late_cfg["openers_en"]
    return random.choice(openers)


def add_typo(text):
    if len(text) < 5 or random.random() > config["bot"]["typo_chance"]:
        return text

    typo_type = random.choice(["swap", "double", "miss"])
    words = text.split()
    if not words:
        return text

    word_idx = random.randint(0, len(words) - 1)
    word = words[word_idx]

    if len(word) < 3:
        return text

    char_idx = random.randint(1, len(word) - 2)

    if typo_type == "swap" and char_idx < len(word) - 1:
        word = word[:char_idx] + word[char_idx + 1] + word[char_idx] + word[char_idx + 2:]
    elif typo_type == "double":
        word = word[:char_idx] + word[char_idx] + word[char_idx:]
    elif typo_type == "miss":
        word = word[:char_idx] + word[char_idx + 1:]

    words[word_idx] = word
    return " ".join(words)


async def _cleanup_loop():
    """Periodically prune unbounded in-memory dicts to prevent slow memory leaks."""
    await bot.wait_until_ready()
    while not bot.is_closed():
        await asyncio.sleep(3600)  # run every hour
        now = time.time()
        # Prune spam counters for users quiet for > 5 minutes
        stale_counts = [uid for uid, times in bot.user_message_counts.items()
                        if not times or now - max(times) > 300]
        for uid in stale_counts:
            bot.user_message_counts.pop(uid, None)
        # Prune expired cooldowns
        expired_cd = [uid for uid, end in bot.user_cooldowns.items() if now > end]
        for uid in expired_cd:
            bot.user_cooldowns.pop(uid, None)
        # Prune stale active_conversations (already expired by CONVERSATION_TIMEOUT, just clean up)
        stale_conv = [k for k, t in bot.active_conversations.items()
                      if now - t > CONVERSATION_TIMEOUT * 2]
        for k in stale_conv:
            bot.active_conversations.pop(k, None)
        # Prune lang cache for users not seen in 24h (count resets naturally but entries accumulate)
        if len(bot._lang_cache) > 500:
            bot._lang_cache.clear()
        # Prune per-user picture sent sets — reset after 100 unique users to avoid unbounded growth
        if len(bot.sent_pictures) > 100:
            bot.sent_pictures.clear()
        log_system(f"Cleanup: pruned {len(stale_counts)} count(s), {len(expired_cd)} cooldown(s), {len(stale_conv)} conversation(s)")


async def random_status_loop():
    await bot.wait_until_ready()
    status_map = {
        "online": discord.Status.online,
        "idle": discord.Status.idle,
        "dnd": discord.Status.dnd,
        "invisible": discord.Status.invisible,
    }
    while not bot.is_closed():
        status_cfg = config["bot"]["status"]
        status_names = status_cfg.get("statuses", ["online", "idle", "dnd"])
        statuses = [status_map[s] for s in status_names if s in status_map]
        if statuses:
            await bot.change_presence(status=random.choice(statuses))
        await asyncio.sleep(random.randint(
            status_cfg.get("change_interval_min", 1800),
            status_cfg.get("change_interval_max", 10800)
        ))


def get_terminal_size():
    columns, _ = shutil.get_terminal_size()
    return columns


def create_border(char="═"):
    width = get_terminal_size()
    return char * (width - 2)


def print_header():
    width = get_terminal_size()
    border = create_border()
    title = "AI Selfbot Discord"
    padding = " " * ((width - len(title) - 2) // 2)

    print(f"{Fore.CYAN}╔{border}╗")
    print(f"║{padding}{Style.BRIGHT}{title}{Style.NORMAL}{padding}║")
    print(f"╚{border}╝{Style.RESET_ALL}")


def print_separator():
    print(f"{Fore.CYAN}{create_border('─')}{Style.RESET_ALL}")


async def _reply_pending_messages():
    """After restart, reply to any users who messaged right before the update."""
    import json
    from utils.helpers import resource_path

    path = resource_path("config/pending_messages.json")
    if not os.path.exists(path):
        return

    try:
        with open(path, "r", encoding="utf-8") as f:
            pending = json.load(f)
    except Exception:
        return

    if not pending:
        return

    os.remove(path)
    log_system(f"Replying to {len(pending)} pending message(s) from before restart...")
    # Wait a human-like amount of time before replying to pending messages.
    # Firing responses 3 seconds after startup looks like a bot — a real person
    # would open Discord, scroll around, then start replying. Wait 3–8 minutes.
    await asyncio.sleep(random.uniform(180, 480))

    for key, data in pending.items():
        try:
            user_id = int(data["user_id"])
            channel_id = int(data["channel_id"])
            history = data.get("history", [])
            content = data["content"]

            if history and history[-1].get("role") == "assistant":
                continue

            # Try to get user from cache first, fall back to fetch
            user = bot.get_user(user_id)
            if user is None:
                try:
                    user = await bot.fetch_user(user_id)
                except Exception as e:
                    log_error("Pending Reply", f"Could not fetch user {user_id}: {e}")
                    continue

            bot.message_history[key] = history
            last_msg = None
            channel = None

            # Use stored last_message_id to fetch directly — no history scan needed
            last_message_id = data.get("last_message_id")
            if last_message_id:
                try:
                    channel = bot.get_channel(channel_id)
                    if channel is None:
                        channel = await user.create_dm()
                    last_msg = await channel.fetch_message(int(last_message_id))
                except Exception:
                    last_msg = None

            # Fallback: scan DM history (only if we had no stored message id)
            if last_msg is None:
                try:
                    dm = await user.create_dm()
                    async for msg in dm.history(limit=15):
                        if msg.author.id == user_id:
                            last_msg = msg
                            channel = dm
                            break
                except Exception:
                    pass

            if last_msg is None:
                try:
                    channel = bot.get_channel(channel_id)
                    if channel is None:
                        for pc in bot.private_channels:
                            if pc.id == channel_id:
                                channel = pc
                                break
                    if channel:
                        async for msg in channel.history(limit=15):
                            if msg.author.id == user_id:
                                last_msg = msg
                                break
                except Exception as e:
                    log_error("Pending Reply", f"Could not check original channel {channel_id}: {e}")

            if last_msg is None or channel is None:
                log_error("Pending Reply", f"No message found for user {user.name}, skipping")
                continue

            if isinstance(channel, discord.TextChannel):
                was_mentioned = bot.user.mentioned_in(last_msg) and "@everyone" not in last_msg.content and "@here" not in last_msg.content
                was_replied_to = (
                    last_msg.reference
                    and last_msg.reference.resolved
                    and last_msg.reference.resolved.author.id == bot.selfbot_id
                )
                if not was_mentioned and not was_replied_to:
                    log_system(f"Skipping pending reply to {user.name} — server channel, not a mention/reply")
                    continue

            log_system(f"Replying to pending message from {user.name}")
            await asyncio.sleep(random.uniform(8, 25))
            response = await generate_response_and_reply(last_msg, content, history)
            if response:
                bot.message_history[key].append({"role": "assistant", "content": response})
        except Exception as e:
            log_error("Pending Reply Error", str(e))


async def _friend_request_loop():
    """On startup, accept any pending friend requests using the raw HTTP API."""
    await bot.wait_until_ready()
    await asyncio.sleep(5)

    fr_cfg = config["bot"].get("friend_requests") or {}
    if not fr_cfg.get("enabled", False):
        return
    delay = fr_cfg.get("accept_delay", 300)

    try:
        token = bot._connection.http.token
        async with AsyncSession(impersonate="chrome") as session:
            resp = await session.get(
                "https://discord.com/api/v9/users/@me/relationships",
                headers={"Authorization": token},
            )
            if resp.status_code != 200:
                log_error("Friend Request Loop", f"Failed to fetch relationships: {resp.status_code}: {resp.text}")
                return

            relationships = resp.json()
            # type 3 = incoming friend request in Discord's raw API
            pending = [r for r in relationships if r.get("type") == 3]
            log_system(f"Friend requests: found {len(pending)} pending on startup")

            # Distribute accepts in random bursts across a 6-hour window,
            # mimicking a human who opens Discord a few times and clears requests.
            # Split requests into 2-3 bursts, each with tight internal spacing.
            num_bursts = random.randint(2, 3)
            burst_offsets = sorted(random.uniform(0, 21600) for _ in range(num_bursts))  # 6h window
            for i, rel in enumerate(pending):
                user_id = int(rel["id"])
                username = rel.get("user", {}).get("username", str(user_id))
                # Assign this request to a burst, with small intra-burst jitter (10-90s apart)
                burst_base = burst_offsets[i % num_bursts]
                intra_jitter = random.uniform(10, 90) * (i // num_bursts)
                actual_delay = delay + burst_base + intra_jitter
                log_system(f"Pending friend request from {username} — accepting in {int(actual_delay)}s")

                async def _accept(uid=user_id, uname=username, d=actual_delay):
                    await asyncio.sleep(d)
                    try:
                        async with AsyncSession(impersonate="chrome") as s:
                            r = await s.put(
                                f"https://discord.com/api/v9/users/@me/relationships/{uid}",
                                headers={
                                    "Authorization": bot._connection.http.token,
                                    "Content-Type": "application/json",
                                },
                                json={"type": 1},
                            )
                            if r.status_code in (200, 204):
                                log_system(f"Accepted friend request from {uname}")
                            else:
                                try:
                                    data = r.json()
                                    log_error("Friend Request Accept", f"{r.status_code}: {data}")
                                except Exception:
                                    log_error("Friend Request Accept", f"{r.status_code}: {r.text}")
                    except Exception as e:
                        log_error("Friend Request Loop", str(e))

                asyncio.create_task(_accept())

    except Exception as e:
        log_error("Friend Request Loop", str(e))




async def _nudge_loop():
    """Background task: periodically check for long-unanswered DMs and send a nudge."""
    await bot.wait_until_ready()
    while not bot.is_closed():
        nudge_cfg = config["bot"].get("nudge") or {}
        if not nudge_cfg.get("enabled", False):
            await asyncio.sleep(3600)
            continue

        check_interval_hours = nudge_cfg.get("check_interval_hours", 6)
        threshold_days = nudge_cfg.get("threshold_days", 2)
        send_hour_start = nudge_cfg.get("send_during_hours", [10, 22])[0]
        send_hour_end = nudge_cfg.get("send_during_hours", [10, 22])[1]

        current_hour = datetime.now().astimezone().hour
        if send_hour_start <= current_hour < send_hour_end:
            threshold_seconds = threshold_days * 86400
            pending = get_pending_nudges(threshold_seconds)

            for entry in pending:
                try:
                    user_id = entry["user_id"]
                    channel_id = entry["channel_id"]
                    original_content = entry["content"]
                    days_elapsed = (time.time() - entry["received_at"]) / 86400

                    user = bot.get_user(user_id) or await bot.fetch_user(user_id)
                    if not user:
                        continue

                    # Build minimal instructions for nudge tone
                    uid = user_id
                    if uid not in bot._memory_cache:
                        bot._memory_cache[uid] = get_memory(uid)
                    memory_block = format_memory_for_prompt(bot._memory_cache[uid])
                    mood_block = f"\n\n[Right now: {get_mood_prompt()}]" if _MOOD_CFG.get("enabled", True) else ""
                    nudge_instructions = bot.instructions + mood_block + memory_block

                    nudge_text = await generate_nudge(original_content, days_elapsed, nudge_instructions)
                    if not nudge_text or is_refusal(nudge_text):
                        continue

                    # Human-like pre-send delay
                    await asyncio.sleep(random.uniform(30, 120))

                    try:
                        dm = user.dm_channel or await user.create_dm()
                        if bot.realistic_typing:
                            async with dm.typing():
                                cps = random.uniform(7, 18)
                                await asyncio.sleep(len(nudge_text) / cps)
                        await dm.send(nudge_text)
                        mark_nudge_sent(user_id, channel_id)
                        log_system(f"Nudge sent to {user.name} ({days_elapsed:.1f}d elapsed)")

                        # Add to history so the conversation continues naturally
                        key = f"{user_id}-{channel_id}"
                        bot.message_history.setdefault(key, [])
                        bot.message_history[key].append({"role": "assistant", "content": nudge_text})

                    except Exception as send_err:
                        log_error("Nudge Send", str(send_err))

                except Exception as e:
                    log_error("Nudge Loop", str(e))

        await asyncio.sleep(check_interval_hours * 3600)


async def _shutdown_on_401():
    """Cancel all pending tasks and close the bot cleanly on 401."""
    print(
        f"\n{'='*60}\n"
        f"  ✗  TOKEN INVALIDATED (401 Unauthorized)\n"
        f"  →  Discord rejected the token mid-session — it may have been\n"
        f"     reset, logged out, or flagged.\n"
        f"  →  Update DISCORD_TOKEN in your .env file and restart.\n"
        f"{'='*60}\n"
    )
    current = asyncio.current_task()
    for task in asyncio.all_tasks():
        if task is not current and not task.done():
            task.cancel()
    await bot.close()


async def _tg_ipc_loop():
    """Poll for commands from the Telegram controller and execute them in-process.

    Each bot instance reads from its own per-account IPC file so that multiple
    Discord tokens can be controlled independently from a single Telegram bot:
      config/tg_commands_1.json / tg_results_1.json  (account 1)
      config/tg_commands_2.json / tg_results_2.json  (account 2)
      ...

    The account index matches the position of DISCORD_TOKEN_N in .env.
    TOKENS is already loaded as a list; this loop runs per-bot instance so
    `bot` is the instance for TOKENS[_ACCOUNT_INDEX - 1].
    """
    import json as _json
    from pathlib import Path as _Path

    await bot.wait_until_ready()

    # Determine which account index this bot instance is (1-based).
    # Must run after wait_until_ready so bot.http.token is populated.
    try:
        _my_token = bot.http.token.strip()
    except Exception:
        _my_token = None
    _ACCOUNT_INDEX = 1
    for _i, _t in enumerate(TOKENS, start=1):
        if _t.get("token", "").strip() == _my_token:
            _ACCOUNT_INDEX = _i
            break

    _CMD_FILE    = _Path(resource_path(f"config/tg_commands_{_ACCOUNT_INDEX}.json"))
    _RESULT_FILE = _Path(resource_path(f"config/tg_results_{_ACCOUNT_INDEX}.json"))
    _POLL_INTERVAL = 2.0

    def _write_result(cmd_id: str, data: dict):
        results = {}
        if _RESULT_FILE.exists():
            try:
                results = _json.loads(_RESULT_FILE.read_text())
            except Exception:
                pass
        results[cmd_id] = data
        _RESULT_FILE.write_text(_json.dumps(results))

    log_system("Telegram IPC bridge started")

    _FLAG_FILE = _Path(resource_path("config/update.flag"))

    while not bot.is_closed():
        await asyncio.sleep(_POLL_INTERVAL)

        # ── Check for update sentinel flag (written by Telegram controller or /update command) ──
        if _FLAG_FILE.exists():
            try:
                _flag_source = _FLAG_FILE.read_text().strip() or "release"
                if _flag_source not in ("release", "main"):
                    _flag_source = "release"
                _FLAG_FILE.unlink(missing_ok=True)
                log_system(f"Update ({_flag_source}) triggered via flag file")
                _upd_repo_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
                import subprocess as _upd_sp
                if sys.platform == "win32":
                    _upd_path = os.path.join(_upd_repo_dir, "updater.bat")
                    _upd_sp.Popen(
                        f'cmd /c start "Updating AI Selfbot..." /d "{_upd_repo_dir}" "{_upd_path}" {_flag_source}',
                        shell=True,
                        cwd=_upd_repo_dir,
                        creationflags=0x00000010,  # CREATE_NEW_CONSOLE
                    )
                else:
                    _upd_path = os.path.join(_upd_repo_dir, "updater.sh")
                    try:
                        os.chmod(_upd_path, 0o755)
                    except Exception:
                        pass
                    _upd_sp.Popen(["bash", _upd_path, _flag_source], start_new_session=True, cwd=_upd_repo_dir)
                await asyncio.sleep(2)
                await bot.close()
                sys.exit(0)
            except Exception as _flag_err:
                log_error("Update Flag", str(_flag_err))

        if not _CMD_FILE.exists():
            continue
        try:
            raw = _CMD_FILE.read_text()
            commands = _json.loads(raw)
        except Exception:
            continue
        if not commands:
            continue

        remaining = []
        for entry in commands:
            cmd_id  = entry.get("id", "")
            cmd     = entry.get("cmd", "")
            payload = entry.get("payload", {})
            try:
                if cmd == "pause":
                    bot.paused = not bot.paused
                    _write_result(cmd_id, {"paused": bot.paused})

                elif cmd == "wipe":
                    bot.message_history.clear()
                    _write_result(cmd_id, {"ok": True})

                elif cmd == "toggle_dm":
                    bot.allow_dm = not bot.allow_dm
                    cfg = load_config(); cfg["bot"]["allow_dm"] = bot.allow_dm
                    import yaml as _yaml
                    with open(resource_path("config/config.yaml"), "w", encoding="utf-8") as _f:
                        _yaml.dump(cfg, _f, default_flow_style=False, allow_unicode=True)
                    _write_result(cmd_id, {"allow_dm": bot.allow_dm})

                elif cmd == "toggle_gc":
                    bot.allow_gc = not bot.allow_gc
                    cfg = load_config(); cfg["bot"]["allow_gc"] = bot.allow_gc
                    import yaml as _yaml
                    with open(resource_path("config/config.yaml"), "w", encoding="utf-8") as _f:
                        _yaml.dump(cfg, _f, default_flow_style=False, allow_unicode=True)
                    _write_result(cmd_id, {"allow_gc": bot.allow_gc})

                elif cmd == "toggle_server":
                    bot.allow_server = not getattr(bot, "allow_server", True)
                    cfg = load_config(); cfg["bot"]["allow_server"] = bot.allow_server
                    import yaml as _yaml
                    with open(resource_path("config/config.yaml"), "w", encoding="utf-8") as _f:
                        _yaml.dump(cfg, _f, default_flow_style=False, allow_unicode=True)
                    _write_result(cmd_id, {"allow_server": bot.allow_server})

                elif cmd == "ignore_add":
                    uid = int(payload["user_id"])
                    if uid not in bot.ignore_users:
                        bot.ignore_users.append(uid)
                    _write_result(cmd_id, {"ok": True})

                elif cmd == "ignore_remove":
                    uid = int(payload["user_id"])
                    if uid in bot.ignore_users:
                        bot.ignore_users.remove(uid)
                    _write_result(cmd_id, {"ok": True})

                elif cmd == "pauseuser":
                    bot.paused_users.add(int(payload["user_id"]))
                    _write_result(cmd_id, {"ok": True})

                elif cmd == "unpauseuser":
                    bot.paused_users.discard(int(payload["user_id"]))
                    _write_result(cmd_id, {"ok": True})

                elif cmd == "persona_set":
                    from utils.memory import set_persona as _sp
                    uid = int(payload["user_id"])
                    _sp(uid, payload["persona"])
                    bot._memory_cache.setdefault(uid, {})["__persona__"] = payload["persona"]
                    _write_result(cmd_id, {"ok": True})

                elif cmd == "persona_clear":
                    from utils.memory import clear_persona as _cp
                    uid = int(payload["user_id"])
                    _cp(uid)
                    bot._memory_cache.get(uid, {}).pop("__persona__", None)
                    _write_result(cmd_id, {"ok": True})

                elif cmd == "mood_set":
                    import utils.mood as _mood_mod
                    _mood_mod.current_mood = payload["mood"]
                    _write_result(cmd_id, {"ok": True})

                elif cmd == "instructions_update":
                    bot.instructions = payload["text"]
                    _write_result(cmd_id, {"ok": True})

                elif cmd == "config_update":
                    _live = {
                        "allow_dm":          lambda v: setattr(bot, "allow_dm", v),
                        "allow_gc":          lambda v: setattr(bot, "allow_gc", v),
                        "allow_server":      lambda v: setattr(bot, "allow_server", v),
                        "realistic_typing":  lambda v: setattr(bot, "realistic_typing", v),
                        "batch_messages":    lambda v: setattr(bot, "batch_messages", v),
                        "hold_conversation": lambda v: setattr(bot, "hold_conversation", v),
                        "reply_ping":        lambda v: setattr(bot, "reply_ping", v),
                        "disable_mentions":  lambda v: setattr(bot, "disable_mentions", v),
                        "anti_age_ban":      lambda v: setattr(bot, "anti_age_ban", v),
                    }
                    leaf = payload.get("key", "").split(".")[-1]
                    if leaf in _live:
                        _live[leaf](payload.get("value"))
                    _write_result(cmd_id, {"ok": True})

                elif cmd == "reply_check":
                    users_out = []
                    seen_ids = set()

                    # Pass 1: in-memory history (fast, works mid-session)
                    for hk, history in bot.message_history.items():
                        if not history or history[-1].get("role") != "user":
                            continue
                        try:
                            uid = int(hk.split("-")[0])
                            if uid in seen_ids:
                                continue
                            seen_ids.add(uid)
                            u = bot.get_user(uid)
                            pending = [e["content"] for e in reversed(history) if e["role"] == "user"]
                            last = pending[0] if pending else ""
                            snippet = (last[:60] + "…") if len(last) > 60 else last
                            users_out.append({
                                "id": uid,
                                "name": u.name if u else str(uid),
                                "snippet": snippet,
                                "count": len(pending),
                            })
                        except Exception:
                            pass

                    # Pass 2: live DM channel scan (catches post-restart gaps)
                    try:
                        _selfbot_id = getattr(bot, "selfbot_id", None) or bot.user.id
                        for _ch in bot.private_channels:
                            if not isinstance(_ch, discord.DMChannel):
                                continue
                            _other = _ch.recipient
                            if _other is None or _other.id in seen_ids:
                                continue
                            # Quick cache check — skip if bot sent the last message
                            _cached_last = None
                            if _ch.last_message_id:
                                _cached_last = _ch._state._get_message(_ch.last_message_id)
                            if _cached_last is not None and _cached_last.author.id == _selfbot_id:
                                continue
                            try:
                                _last_msgs = [m async for m in _ch.history(limit=10)]
                                if not _last_msgs:
                                    continue
                                _last_other = next((m for m in _last_msgs if m.author.id != _selfbot_id), None)
                                _last_bot   = next((m for m in _last_msgs if m.author.id == _selfbot_id), None)
                                if _last_other is None:
                                    continue
                                if _last_bot and _last_bot.created_at > _last_other.created_at:
                                    continue
                                _pending_ct = sum(
                                    1 for m in _last_msgs
                                    if m.author.id != _selfbot_id
                                    and (_last_bot is None or m.created_at > _last_bot.created_at)
                                )
                                _snip_text = _last_other.content or "[attachment]"
                                _snip = (_snip_text[:60] + "…") if len(_snip_text) > 60 else _snip_text
                                seen_ids.add(_other.id)
                                users_out.append({
                                    "id": _other.id,
                                    "name": _other.name,
                                    "snippet": _snip,
                                    "count": max(_pending_ct, 1),
                                })
                            except Exception:
                                continue
                    except Exception:
                        pass

                    _write_result(cmd_id, {"users": users_out})

                elif cmd == "reply_user":
                    uid = int(payload["user_id"])
                    u = bot.get_user(uid) or await bot.fetch_user(uid)
                    if not u:
                        _write_result(cmd_id, {"success": False, "reason": "user not found"}); continue
                    target_msg = None; target_ch = None
                    for hk in bot.message_history:
                        if hk.startswith(f"{uid}-"):
                            try:
                                ch = bot.get_channel(int(hk.split("-")[1])) or await u.create_dm()
                                async for msg in ch.history(limit=10):
                                    if msg.author.id == uid:
                                        target_msg = msg; target_ch = ch; break
                            except Exception:
                                pass
                            break
                    if not target_msg:
                        try:
                            dm = u.dm_channel or await u.create_dm()
                            async for msg in dm.history(limit=10):
                                if msg.author.id == uid:
                                    target_msg = msg; target_ch = dm; break
                        except Exception:
                            pass
                    if not target_msg:
                        _write_result(cmd_id, {"success": False, "reason": "no recent message found"}); continue
                    hk = f"{uid}-{target_ch.id}"
                    history = bot.message_history.get(hk, [])
                    combined = target_msg.content or "[attachment]"
                    if not history or history[-1].get("content") != combined:
                        history.append({"role": "user", "content": combined})
                        bot.message_history[hk] = history
                    resp = await generate_response_and_reply(target_msg, combined, history, bypass_cooldown=True, bypass_typing=True)
                    if resp:
                        bot.message_history[hk].append({"role": "assistant", "content": resp})
                        _write_result(cmd_id, {"success": True})
                    else:
                        _write_result(cmd_id, {"success": False, "reason": "couldn't generate response"})

                elif cmd == "reply_all":
                    results_out = []; seen = set()
                    _ra_selfbot_id = getattr(bot, "selfbot_id", None) or bot.user.id

                    # ── Pass 1: in-memory history (fast, works mid-session) ────
                    _ra_candidates = []  # list of (uid, channel_id, history_key)
                    for hk, history in list(bot.message_history.items()):
                        if not history or history[-1].get("role") != "user":
                            continue
                        try:
                            uid = int(hk.split("-")[0])
                            cid = int(hk.split("-")[1])
                            if uid in seen: continue
                            seen.add(uid)
                            _ra_candidates.append((uid, cid, hk))
                        except Exception:
                            pass

                    # ── Pass 2: live DM scan (catches post-restart gaps) ───────
                    try:
                        for _ra_ch in bot.private_channels:
                            if not isinstance(_ra_ch, discord.DMChannel):
                                continue
                            _ra_other = _ra_ch.recipient
                            if _ra_other is None or _ra_other.id in seen:
                                continue
                            # Quick cache pre-filter — skip if bot sent last message
                            _ra_cached = None
                            if _ra_ch.last_message_id:
                                _ra_cached = _ra_ch._state._get_message(_ra_ch.last_message_id)
                            if _ra_cached is not None and _ra_cached.author.id == _ra_selfbot_id:
                                continue
                            try:
                                _ra_msgs = [m async for m in _ra_ch.history(limit=10)]
                                if not _ra_msgs: continue
                                _ra_last_other = next((m for m in _ra_msgs if m.author.id != _ra_selfbot_id), None)
                                _ra_last_bot   = next((m for m in _ra_msgs if m.author.id == _ra_selfbot_id), None)
                                if _ra_last_other is None: continue
                                if _ra_last_bot and _ra_last_bot.created_at > _ra_last_other.created_at:
                                    continue
                                seen.add(_ra_other.id)
                                _ra_candidates.append((_ra_other.id, _ra_ch.id, None))
                            except Exception:
                                continue
                    except Exception:
                        pass

                    # ── Reply to every discovered candidate ───────────────────
                    _ra_ignored = set(getattr(bot, "ignore_users", []))
                    _ra_filtered = [(u, c, h) for u, c, h in _ra_candidates if u not in _ra_ignored]

                    if not _ra_filtered:
                        _write_result(cmd_id, {"total": 0, "results": []})
                    else:
                        for _ra_uid, _ra_cid, _ra_hk in _ra_filtered:
                            try:
                                _ra_u = bot.get_user(_ra_uid) or await bot.fetch_user(_ra_uid)
                                _ra_ch2 = bot.get_channel(_ra_cid)
                                if _ra_ch2 is None:
                                    try:
                                        _ra_ch2 = await _ra_u.create_dm()
                                    except Exception:
                                        results_out.append({"id": _ra_uid, "name": _ra_u.name if _ra_u else str(_ra_uid), "success": False, "reason": "could not open DM"})
                                        continue
                                _ra_target = None
                                async for _ra_m in _ra_ch2.history(limit=15):
                                    if _ra_m.author.id == _ra_uid:
                                        _ra_target = _ra_m; break
                                if not _ra_target:
                                    results_out.append({"id": _ra_uid, "name": _ra_u.name, "success": False, "reason": "no recent message found"})
                                    continue
                                _ra_hk2 = _ra_hk or f"{_ra_uid}-{_ra_cid}"
                                _ra_hist = bot.message_history.get(_ra_hk2, [])
                                _ra_combined = "\n".join(e["content"] for e in _ra_hist[-3:] if e["role"] == "user") or (_ra_target.content or "[attachment]")
                                if not _ra_hist or _ra_hist[-1].get("content") != _ra_combined:
                                    _ra_hist.append({"role": "user", "content": _ra_combined})
                                    bot.message_history[_ra_hk2] = _ra_hist
                                resp = await generate_response_and_reply(_ra_target, _ra_combined, _ra_hist, bypass_cooldown=True, bypass_typing=True)
                                if resp:
                                    bot.message_history[_ra_hk2].append({"role": "assistant", "content": resp})
                                    results_out.append({"id": _ra_uid, "name": _ra_u.name, "success": True})
                                else:
                                    results_out.append({"id": _ra_uid, "name": _ra_u.name, "success": False, "reason": "no response generated"})
                            except Exception as _ra_e:
                                _ra_name = str(_ra_uid)
                                try:
                                    _ra_name = (bot.get_user(_ra_uid) or await bot.fetch_user(_ra_uid)).name
                                except Exception:
                                    pass
                                results_out.append({"id": _ra_uid, "name": _ra_name, "success": False, "reason": str(_ra_e)})
                        _write_result(cmd_id, {"total": len(results_out), "results": results_out})

                elif cmd == "restart":
                    import atexit as _atexit
                    log_system("Restart requested via Telegram")
                    if getattr(sys, "frozen", False):
                        _atexit.register(lambda: os.startfile(sys.executable))
                    else:
                        import subprocess as _sp
                        _atexit.register(lambda: _sp.Popen([sys.executable] + sys.argv))
                    # Flush command file before exit so the restart entry is not
                    # re-processed on the next startup.
                    _CMD_FILE.write_text(_json.dumps(remaining))
                    await bot.close(); sys.exit(0)

                elif cmd == "get_status":
                    import utils.mood as _mood_mod
                    _write_result(cmd_id, {
                        "paused": bot.paused,
                        "mood": _mood_mod.current_mood,
                        "active_channels": len(bot.active_channels),
                        "ignored_users": len(bot.ignore_users),
                    })

                elif cmd == "mood_get":
                    import utils.mood as _mood_mod
                    _write_result(cmd_id, {"mood": _mood_mod.current_mood})

                elif cmd == "ignore_toggle":
                    uid = int(payload["user_id"])
                    if uid in bot.ignore_users:
                        bot.ignore_users.remove(uid)
                        from utils.db import remove_ignored_user as _riu
                        _riu(uid)
                        _write_result(cmd_id, {"ok": True, "ignored": False})
                    else:
                        bot.ignore_users.append(uid)
                        from utils.db import add_ignored_user as _aiu
                        _aiu(uid)
                        _write_result(cmd_id, {"ok": True, "ignored": True})

                elif cmd == "persona_get":
                    from utils.memory import get_persona as _gp
                    uid = int(payload["user_id"])
                    _write_result(cmd_id, {"persona": _gp(uid)})

                elif cmd == "get_leaderboard":
                    from utils.db import get_leaderboard as _glb
                    from datetime import datetime as _dt
                    import re as _re
                    import time as _time_mod
                    filter_str = payload.get("filter")
                    since_ts = None
                    filter_label = "all time"
                    if filter_str:
                        m = _re.fullmatch(r"(\d+(?:\.\d+)?)\s*([hdwm])", filter_str.strip().lower())
                        if m:
                            amount, unit = float(m.group(1)), m.group(2)
                            seconds_map = {"h": 3600, "d": 86400, "w": 604800, "m": 2592000}
                            since_ts = _time_mod.time() - amount * seconds_map[unit]
                            unit_names = {"h": "hour", "d": "day", "w": "week", "m": "month"}
                            n = int(amount) if amount == int(amount) else amount
                            filter_label = f"last {n} {unit_names[unit]}{'s' if n != 1 else ''}"
                    rows = _glb(limit=20, since=since_ts)
                    serialized = []
                    for row in rows:
                        serialized.append({
                            "username": row["username"],
                            "message_count": row["message_count"],
                            "first_seen_fmt": _dt.fromtimestamp(row["first_seen"]).strftime("%d %b %Y"),
                        })
                    _write_result(cmd_id, {"rows": serialized, "filter_label": filter_label})

                elif cmd == "shutdown":
                    log_system("Shutdown requested via Telegram")
                    _CMD_FILE.write_text(_json.dumps(remaining))
                    await bot.close(); sys.exit(0)

                # ── toggle_active ─────────────────────────────────────────────
                elif cmd == "toggle_active":
                    _ch_id = int(payload["channel_id"])
                    if _ch_id in bot.active_channels:
                        bot.active_channels.discard(_ch_id)
                        from utils.db import remove_channel as _rc
                        _rc(_ch_id)
                        _write_result(cmd_id, {"active": False})
                    else:
                        bot.active_channels.add(_ch_id)
                        from utils.db import add_channel as _ac
                        _ac(_ch_id)
                        _write_result(cmd_id, {"active": True})

                # ── analyse_user ──────────────────────────────────────────────
                elif cmd == "analyse_user":
                    import re as _re2
                    _uid = int(payload["user_id"])
                    try:
                        _user = bot.get_user(_uid) or await bot.fetch_user(_uid)
                    except Exception:
                        _write_result(cmd_id, {"ok": False, "reason": f"User {_uid} not found."})
                        continue
                    _msg_list = []
                    try:
                        _dm = _user.dm_channel or await _user.create_dm()
                        async for _m in _dm.history(limit=200):
                            if _m.author.id == _uid and _m.content:
                                _msg_list.append(_m.content)
                    except Exception:
                        pass
                    if len(_msg_list) < 20:
                        for _cid in list(bot.active_channels)[:3]:
                            try:
                                _ch = bot.get_channel(_cid) or await bot.fetch_channel(_cid)
                                async for _m in _ch.history(limit=200):
                                    if _m.author.id == _uid and _m.content:
                                        _msg_list.append(_m.content)
                            except Exception:
                                pass
                    if not _msg_list:
                        _write_result(cmd_id, {"ok": False, "reason": f"No messages found for user {_uid}."})
                        continue
                    _analyse_instructions = (
                        bot.instructions +
                        f"\n\nSomeone asked you to give your honest read on {_user.name} based on their messages. "
                        "Stay in character. Give your real unfiltered opinion like you would to a friend. "
                        "Be casual, funny, and direct. Roast them a bit but also be real about what you actually see. "
                        "Reference specific things they said to back up your points. "
                        "Keep it conversational — no bullet points, no formal structure, just talk like yourself. "
                        "Don't be overly mean but don't sugarcoat either. Max 3-4 short paragraphs."
                    )
                    _analyse_prompt = "Here are their messages: " + " | ".join(_msg_list[-200:])
                    _profile = await generate_response(_analyse_prompt, _analyse_instructions, history=None)
                    if _profile:
                        _write_result(cmd_id, {"ok": True, "profile": _profile})
                    else:
                        _write_result(cmd_id, {"ok": False, "reason": "AI returned an empty response."})

                # ── voice_join ────────────────────────────────────────────────
                elif cmd == "voice_join":
                    import re as _re3
                    _args_str = payload.get("args", "").strip()
                    _gid_v, _cid_v = None, None
                    _lm = _re3.match(r"https?://discord\.com/channels/(\d+)/(\d+)", _args_str)
                    if _lm:
                        _gid_v, _cid_v = int(_lm.group(1)), int(_lm.group(2))
                    elif _args_str.isdigit():
                        _cid_v = int(_args_str)
                    else:
                        _parts = _args_str.split()
                        if len(_parts) == 2 and _parts[0].isdigit() and _parts[1].isdigit():
                            _gid_v, _cid_v = int(_parts[0]), int(_parts[1])
                    if not _cid_v:
                        _write_result(cmd_id, {"ok": False, "reason": "Invalid channel ID or link."})
                        continue
                    _vtarget = (bot.get_guild(_gid_v).get_channel(_cid_v) if _gid_v and bot.get_guild(_gid_v) else bot.get_channel(_cid_v))
                    if not isinstance(_vtarget, discord.VoiceChannel):
                        _write_result(cmd_id, {"ok": False, "reason": "Channel not found or is not a voice channel."})
                        continue
                    try:
                        _existing = _vtarget.guild.voice_client
                        if _existing:
                            _existing._keep_alive_guard = False
                            await _existing.disconnect(force=True)
                        await _vtarget.connect(self_mute=True, self_deaf=True)
                        _write_result(cmd_id, {"ok": True, "channel": _vtarget.name, "guild": _vtarget.guild.name})
                    except Exception as _e:
                        _write_result(cmd_id, {"ok": False, "reason": str(_e)})

                # ── voice_leave ───────────────────────────────────────────────
                elif cmd == "voice_leave":
                    _gid_l = payload.get("guild_id")
                    if _gid_l:
                        _gl = bot.get_guild(int(_gid_l))
                        _vc = _gl.voice_client if _gl else None
                    else:
                        _vc = next((g.voice_client for g in bot.guilds if g.voice_client), None)
                    if _vc:
                        _vc._keep_alive_guard = False
                        await _vc.disconnect(force=True)
                        _write_result(cmd_id, {"ok": True, "channel": _vc.channel.name, "guild": _vc.guild.name})
                    else:
                        _write_result(cmd_id, {"ok": False, "reason": "Not in a voice channel."})

                # ── voice_autojoin ────────────────────────────────────────────
                elif cmd == "voice_autojoin":
                    import re as _re4
                    import yaml as _yaml_aj
                    _aj_args = payload.get("args", "").strip()
                    if not _aj_args or _aj_args.lower() == "off":
                        _aj_cfg = load_config()
                        _aj_cfg["bot"]["autojoin_channel"] = None
                        with open(resource_path("config/config.yaml"), "w", encoding="utf-8") as _f:
                            _yaml_aj.dump(_aj_cfg, _f, default_flow_style=False, allow_unicode=True)
                        _write_result(cmd_id, {"ok": True, "disabled": True})
                        continue
                    _gid_aj, _cid_aj = None, None
                    _lm_aj = _re4.match(r"https?://discord\.com/channels/(\d+)/(\d+)", _aj_args)
                    if _lm_aj:
                        _gid_aj, _cid_aj = int(_lm_aj.group(1)), int(_lm_aj.group(2))
                    elif _aj_args.isdigit():
                        _cid_aj = int(_aj_args)
                    else:
                        _p = _aj_args.split()
                        if len(_p) == 2 and _p[0].isdigit() and _p[1].isdigit():
                            _gid_aj, _cid_aj = int(_p[0]), int(_p[1])
                    if not _cid_aj:
                        _write_result(cmd_id, {"ok": False, "reason": "Invalid channel ID or link."})
                        continue
                    _aj_target = (bot.get_guild(_gid_aj).get_channel(_cid_aj) if _gid_aj and bot.get_guild(_gid_aj) else bot.get_channel(_cid_aj))
                    if not isinstance(_aj_target, discord.VoiceChannel):
                        _write_result(cmd_id, {"ok": False, "reason": "Channel not found or is not a voice channel."})
                        continue
                    _aj_cfg2 = load_config()
                    _aj_cfg2["bot"]["autojoin_channel"] = {"guild_id": _aj_target.guild.id, "channel_id": _aj_target.id}
                    with open(resource_path("config/config.yaml"), "w", encoding="utf-8") as _f:
                        _yaml_aj.dump(_aj_cfg2, _f, default_flow_style=False, allow_unicode=True)
                    _write_result(cmd_id, {"ok": True, "disabled": False, "channel": _aj_target.name, "guild": _aj_target.guild.name})

                # ── set_status ────────────────────────────────────────────────
                elif cmd == "set_status":
                    _st_emoji = payload.get("emoji")
                    _st_text  = payload.get("text")
                    try:
                        await bot.change_presence(
                            activity=discord.CustomActivity(name=_st_text or "", emoji=_st_emoji or None)
                        )
                        _write_result(cmd_id, {"ok": True})
                    except Exception as _e:
                        _write_result(cmd_id, {"ok": False, "reason": str(_e)})

                # ── set_bio ───────────────────────────────────────────────────
                elif cmd == "set_bio":
                    try:
                        await bot.user.edit(bio=payload.get("text", ""))
                        _write_result(cmd_id, {"ok": True})
                    except Exception as _e:
                        _write_result(cmd_id, {"ok": False, "reason": str(_e)})

                # ── set_pfp ───────────────────────────────────────────────────
                elif cmd == "set_pfp":
                    _pfp_url = payload.get("url", "")
                    _pfp_b64 = payload.get("b64", "")
                    try:
                        if _pfp_b64:
                            import base64 as _b64mod
                            _pfp_data = _b64mod.b64decode(_pfp_b64)
                        elif _pfp_url:
                            async with AsyncSession(impersonate="chrome") as _pfp_s:
                                _pfp_r = await _pfp_s.get(_pfp_url)
                                if _pfp_r.status_code != 200:
                                    _write_result(cmd_id, {"ok": False, "reason": f"Failed to fetch image (status {_pfp_r.status_code})."})
                                    continue
                                _pfp_data = _pfp_r.content
                        else:
                            _write_result(cmd_id, {"ok": False, "reason": "No image URL or data provided."})
                            continue
                        await bot.user.edit(avatar=_pfp_data)
                        _write_result(cmd_id, {"ok": True})
                    except Exception as _e:
                        _write_result(cmd_id, {"ok": False, "reason": str(_e)})

                # ── set_banner ────────────────────────────────────────────────
                elif cmd == "set_banner":
                    _banner_url = payload.get("url", "")
                    _banner_b64 = payload.get("b64", "")
                    try:
                        if _banner_b64:
                            import base64 as _b64mod
                            _banner_data = _b64mod.b64decode(_banner_b64)
                        elif _banner_url:
                            async with AsyncSession(impersonate="chrome") as _banner_s:
                                _banner_r = await _banner_s.get(_banner_url)
                                if _banner_r.status_code != 200:
                                    _write_result(cmd_id, {"ok": False, "reason": f"Failed to fetch image (status {_banner_r.status_code})."})
                                    continue
                                _banner_data = _banner_r.content
                        else:
                            _write_result(cmd_id, {"ok": False, "reason": "No image URL or data provided."})
                            continue
                        await bot.user.edit(banner=_banner_data)
                        _write_result(cmd_id, {"ok": True})
                    except Exception as _e:
                        _write_result(cmd_id, {"ok": False, "reason": str(_e)})

                # ── add_friend ────────────────────────────────────────────────
                elif cmd == "add_friend":
                    _af_uid = int(payload["user_id"])
                    try:
                        _af_token = bot.http.token
                        async with AsyncSession(impersonate="chrome") as _af_s:
                            _af_r = await _af_s.put(
                                f"https://discord.com/api/v9/users/@me/relationships/{_af_uid}",
                                headers={"Authorization": _af_token, "Content-Type": "application/json"},
                                json={"type": 1},
                            )
                            if _af_r.status_code in (200, 204):
                                _write_result(cmd_id, {"ok": True})
                            else:
                                try:
                                    _af_msg = _af_r.json().get("message", str(_af_r.status_code))
                                except Exception:
                                    _af_msg = f"HTTP {_af_r.status_code}"
                                _write_result(cmd_id, {"ok": False, "reason": _af_msg})
                    except Exception as _e:
                        _write_result(cmd_id, {"ok": False, "reason": str(_e)})

                # ── update (now handled via sentinel flag file) ───────────────
                elif cmd == "update":
                    # Kept for backwards compatibility but the Telegram controller
                    # now writes config/update.flag directly instead of using IPC.
                    # Still handle it here in case of Discord command /update.
                    _upd_source = payload.get("source", "release")
                    if _upd_source not in ("release", "main"):
                        _upd_source = "release"
                    _upd_flag = _Path(resource_path("config/update.flag"))
                    _upd_flag.write_text(_upd_source)
                    # The sentinel poll below will pick this up immediately.
                    remaining.append(entry)  # keep in list so we flush it below
                    continue

                # ── reload ────────────────────────────────────────────────────
                elif cmd == "reload":
                    import sys as _sys_r
                    _cogs_dir = os.path.join(getattr(_sys_r, "_MEIPASS", os.path.abspath(".")), "cogs")
                    _reload_errors = []
                    if os.path.exists(_cogs_dir):
                        for _fname in os.listdir(_cogs_dir):
                            if _fname.endswith(".py"):
                                try:
                                    await bot.unload_extension(f"cogs.{_fname[:-3]}")
                                    await bot.load_extension(f"cogs.{_fname[:-3]}")
                                except Exception as _re:
                                    _reload_errors.append(str(_re))
                    bot.instructions = load_instructions()
                    _write_result(cmd_id, {"ok": True, "errors": _reload_errors})

                # ── image_analyse ─────────────────────────────────────────────
                elif cmd == "image_analyse":
                    import base64 as _b64_ia
                    from utils.helpers import resource_path as _rp_ia
                    from utils.db import add_picture_description as _apd_ia
                    from utils.ai import _create_image_completion as _cic_ia
                    _ia_name = payload.get("name", "")
                    _ia_b64  = payload.get("b64", "")
                    _ia_ext  = payload.get("ext", ".jpg")
                    _ia_folder = _rp_ia("config/pictures")
                    _ia_path   = os.path.join(_ia_folder, _ia_name)
                    # If no b64 was sent (same-machine case), read the file from disk.
                    # If b64 was sent and the file doesn't exist yet, decode and write it.
                    if not _ia_b64:
                        if os.path.exists(_ia_path):
                            with open(_ia_path, "rb") as _f:
                                _ia_b64 = _b64_ia.b64encode(_f.read()).decode()
                        else:
                            _write_result(cmd_id, {"ok": False, "reason": f"Image file not found: {_ia_name}"})
                            continue
                    elif not os.path.exists(_ia_path):
                        os.makedirs(_ia_folder, exist_ok=True)
                        with open(_ia_path, "wb") as _f:
                            _f.write(_b64_ia.b64decode(_ia_b64))
                    try:
                        _ia_cfg = load_config()
                        _ia_model = _ia_cfg["bot"].get("groq_image_model", "meta-llama/llama-4-scout-17b-16e-instruct")
                        _mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                                     ".gif": "image/gif", ".webp": "image/webp"}
                        _ia_mime = _mime_map.get(_ia_ext.lower(), "image/jpeg")
                        _ia_data_url = f"data:{_ia_mime};base64,{_ia_b64}"
                        log_system(f"Analysing image: {_ia_name}")
                        _ia_resp = await _cic_ia(
                            _ia_model,
                            messages=[{
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": "Describe this image in full detail exactly as you see it. Include all visible text, objects, people, colors, layout, and context."},
                                    {"type": "image_url", "image_url": {"url": _ia_data_url}},
                                ],
                            }],
                        )
                        _ia_desc = _ia_resp.choices[0].message.content.strip()
                        _apd_ia(_ia_name, _ia_desc)
                        log_system(f"Image analysed: {_ia_name} — {_ia_desc[:80]}")
                        _write_result(cmd_id, {"ok": True, "description": _ia_desc})
                    except Exception as _ia_e:
                        log_error("image_analyse", str(_ia_e))
                        _write_result(cmd_id, {"ok": False, "reason": str(_ia_e)})

                # ── image_delete ──────────────────────────────────────────────
                elif cmd == "image_delete":
                    from utils.helpers import resource_path as _rp_img
                    from utils.db import delete_picture_db as _dpdb, rename_picture_db as _rpdb
                    _img_folder = _rp_img("config/pictures")
                    _img_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
                    _del_name = payload.get("name", "")
                    _img_files = sorted([f for f in os.listdir(_img_folder) if os.path.splitext(f)[1].lower() in _img_exts]) if os.path.exists(_img_folder) else []
                    if _del_name.isdigit():
                        _matches = [f for f in _img_files if os.path.splitext(f)[0] == f"IMG_{_del_name}"]
                        _del_name = _matches[0] if _matches else _del_name
                    _del_path = os.path.join(_img_folder, _del_name)
                    if os.path.exists(_del_path):
                        os.remove(_del_path)
                        _dpdb(_del_name)
                        _remaining = sorted([f for f in os.listdir(_img_folder) if os.path.splitext(f)[1].lower() in _img_exts],
                                            key=lambda f: int(os.path.splitext(f)[0][4:]) if os.path.splitext(f)[0].startswith("IMG_") and os.path.splitext(f)[0][4:].isdigit() else 99999)
                        for _ri, _rfname in enumerate(_remaining, start=1):
                            _rstem, _rext = os.path.splitext(_rfname)
                            if _rstem.startswith("IMG_") and _rstem[4:].isdigit() and int(_rstem[4:]) != _ri:
                                _rnew = f"IMG_{_ri}{_rext}"
                                os.rename(os.path.join(_img_folder, _rfname), os.path.join(_img_folder, _rnew))
                                _rpdb(_rfname, _rnew)
                        _write_result(cmd_id, {"ok": True})
                    else:
                        _write_result(cmd_id, {"ok": False, "reason": f"Image `{_del_name}` not found."})

                # ── image_delete_multi ───────────────────────────────────────
                elif cmd == "image_delete_multi":
                    from utils.helpers import resource_path as _rp_idm
                    from utils.db import delete_picture_db as _dpdb_m, rename_picture_db as _rpdb_m
                    _idm_folder = _rp_idm("config/pictures")
                    _idm_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
                    _idm_names = payload.get("names", [])  # list of digit strings e.g. ["1","4","7"]
                    _idm_files = sorted(
                        [f for f in os.listdir(_idm_folder) if os.path.splitext(f)[1].lower() in _idm_exts],
                        key=lambda f: int(os.path.splitext(f)[0][4:])
                            if os.path.splitext(f)[0].startswith("IMG_") and os.path.splitext(f)[0][4:].isdigit()
                            else 99999
                    ) if os.path.exists(_idm_folder) else []
                    _idm_deleted = []
                    _idm_failed = []
                    for _idm_n in _idm_names:
                        _idm_matches = [f for f in _idm_files if os.path.splitext(f)[0] == f"IMG_{_idm_n}"]
                        if _idm_matches:
                            _idm_path = os.path.join(_idm_folder, _idm_matches[0])
                            try:
                                os.remove(_idm_path)
                                _dpdb_m(_idm_matches[0])
                                _idm_deleted.append(_idm_n)
                            except Exception:
                                _idm_failed.append(_idm_n)
                        else:
                            _idm_failed.append(_idm_n)
                    # Renumber remaining files once after all deletes
                    _idm_remaining = sorted(
                        [f for f in os.listdir(_idm_folder) if os.path.splitext(f)[1].lower() in _idm_exts],
                        key=lambda f: int(os.path.splitext(f)[0][4:])
                            if os.path.splitext(f)[0].startswith("IMG_") and os.path.splitext(f)[0][4:].isdigit()
                            else 99999
                    ) if os.path.exists(_idm_folder) else []
                    for _ri, _rfname in enumerate(_idm_remaining, start=1):
                        _rstem, _rext = os.path.splitext(_rfname)
                        if _rstem.startswith("IMG_") and _rstem[4:].isdigit() and int(_rstem[4:]) != _ri:
                            _rnew = f"IMG_{_ri}{_rext}"
                            os.rename(os.path.join(_idm_folder, _rfname), os.path.join(_idm_folder, _rnew))
                            _rpdb_m(_rfname, _rnew)
                    _write_result(cmd_id, {"ok": True, "deleted": _idm_deleted, "failed": _idm_failed})

                # ── image_delete_all ──────────────────────────────────────────
                elif cmd == "image_delete_all":
                    from utils.helpers import resource_path as _rp_imgd
                    from utils.db import clear_all_pictures_db as _capdb
                    _imgd_folder = _rp_imgd("config/pictures")
                    _imgd_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
                    if os.path.exists(_imgd_folder):
                        _imgd_files = [f for f in os.listdir(_imgd_folder) if os.path.splitext(f)[1].lower() in _imgd_exts]
                        for _f in _imgd_files:
                            os.remove(os.path.join(_imgd_folder, _f))
                        _capdb()
                        _write_result(cmd_id, {"ok": True, "count": len(_imgd_files)})
                    else:
                        _write_result(cmd_id, {"ok": True, "count": 0})

                # ── send_error_notification ───────────────────────────────────
                # This is a passthrough — the TG controller reads this from the
                # commands file directly; we just acknowledge it.
                elif cmd == "send_error_notification":
                    _write_result(cmd_id, {"ok": True})

                else:
                    remaining.append(entry)

            except Exception as _err:
                log_error("TG IPC", f"cmd={cmd} error={_err}")
                _write_result(cmd_id, {"ok": False, "error": str(_err)})

        _CMD_FILE.write_text(_json.dumps(remaining))


@bot.event
async def on_ready():
    if config["bot"]["owner_id"] == 123456789012345678:
        print(f"{Fore.RED}Error: Please set a valid owner_id in config.yaml{Style.RESET_ALL}")
        await bot.close()
        sys.exit(1)

    if config["bot"]["owner_id"] == bot.user.id:
        print(f"{Fore.RED}Error: owner_id in config.yaml cannot be the same as the bot account's user ID{Style.RESET_ALL}")
        await bot.close()
        sys.exit(1)

    bot.selfbot_id = bot.user.id

    clear_console()

    print_header()
    print(f"AI Selfbot successfully logged in as {Fore.CYAN}{bot.user.name} ({bot.selfbot_id}){Style.RESET_ALL}.\n")
    log_system(f"Using model: {ai_module.model}")

    if config["bot"]["mood"].get("enabled", True):
        shift_mood()
        asyncio.create_task(mood_loop())

    print_separator()

    if config["bot"]["status"].get("enabled", True):
        asyncio.create_task(random_status_loop())

    asyncio.create_task(_reply_pending_messages())
    asyncio.create_task(_cleanup_loop())

    nudge_cfg = config["bot"].get("nudge") or {}
    if nudge_cfg.get("enabled", False):
        asyncio.create_task(_nudge_loop())

    fr_cfg = config["bot"].get("friend_requests") or {}
    if fr_cfg.get("enabled", False):
        asyncio.create_task(_friend_request_loop())

    asyncio.create_task(_tg_ipc_loop())


async def setup_hook():
    bot.generate_response_and_reply = generate_response_and_reply
    await load_extensions()

bot.setup_hook = setup_hook

# Raw message_reference cache: message_id -> referenced_message_id
# Discord.py-self sometimes drops message.reference for server channels;
# we catch it here from the raw gateway payload before it's stripped.
_raw_reply_cache: dict[int, int] = {}
_RAW_REPLY_CACHE_MAX = 500

@bot.event
async def on_socket_raw_receive(data):
    import json
    try:
        if isinstance(data, bytes):
            return  # compressed, skip
        payload = json.loads(data)
        if payload.get("t") != "MESSAGE_CREATE":
            return
        d = payload.get("d", {})
        msg_id = int(d["id"]) if d.get("id") else None
        if not msg_id:
            return

        # Cache bot's own message IDs so replies to manual messages are detected
        author_id = int(d.get("author", {}).get("id", 0))
        if author_id and hasattr(bot, "selfbot_id") and author_id == bot.selfbot_id:
            _raw_reply_cache[msg_id] = 0  # 0 = "this is a bot message"

        # Cache reply references
        ref = d.get("message_reference")
        if ref:
            ref_id = int(ref.get("message_id", 0))
            if ref_id:
                _raw_reply_cache[msg_id] = ref_id
                if len(_raw_reply_cache) > _RAW_REPLY_CACHE_MAX:
                    oldest = next(iter(_raw_reply_cache))
                    del _raw_reply_cache[oldest]
    except Exception:
        pass


def should_ignore_message(message):
    return (
        message.author.id in bot.ignore_users
        or message.author.id == bot.selfbot_id
        or message.author.bot
        or message.type not in (discord.MessageType.default, discord.MessageType.reply)
    )


async def is_trigger_message(message):
    mentioned = (
        bot.user.mentioned_in(message)
        and "@everyone" not in message.content
        and "@here" not in message.content
    )
    replied_to = False
    if message.reference:
        ref_id = message.reference.message_id
        ref_msg = message.reference.resolved
        if ref_msg is None or isinstance(ref_msg, discord.DeletedReferencedMessage):
            ref_msg = bot._connection._get_message(ref_id)
        if ref_msg and ref_msg.author.id == bot.user.id:
            replied_to = True
        elif ref_id in _raw_reply_cache and _raw_reply_cache[ref_id] == 0:
            # ref_id is cached as a bot-sent message (value 0 = bot's own message)
            replied_to = True
    elif message.id in _raw_reply_cache:
        ref_id = _raw_reply_cache[message.id]
        if ref_id == 0:
            replied_to = True  # replying to a bot message tracked via raw cache
        else:
            ref_msg = bot._connection._get_message(ref_id)
            if ref_msg and ref_msg.author.id == bot.user.id:
                replied_to = True
    is_dm = isinstance(message.channel, discord.DMChannel) and bot.allow_dm
    is_group_dm = isinstance(message.channel, discord.GroupChannel) and bot.allow_gc

    conv_key = f"{message.author.id}-{message.channel.id}"
    in_conversation = (
        conv_key in bot.active_conversations
        and time.time() - bot.active_conversations[conv_key] < CONVERSATION_TIMEOUT
        and bot.hold_conversation
        and not isinstance(message.channel, (discord.TextChannel, discord.Thread, discord.ForumChannel, discord.StageChannel, discord.VoiceChannel))
    )

    is_server = isinstance(message.channel, (discord.TextChannel, discord.Thread, discord.ForumChannel, discord.StageChannel, discord.VoiceChannel))

    if is_server and not getattr(bot, "allow_server", True):
        mentioned = False
        replied_to = False

    content_has_trigger = (
        not is_server and any(
            re.search(rf"\b{re.escape(keyword)}\b", message.content.lower())
            for keyword in TRIGGER
        )
    )

    if (
        content_has_trigger
        or mentioned
        or replied_to
        or is_dm
        or is_group_dm
        or in_conversation
    ):
        bot.active_conversations[conv_key] = time.time()

    return (
        content_has_trigger
        or mentioned
        or replied_to
        or is_dm
        or is_group_dm
        or in_conversation
    )


_profile_cache: dict = {}  # user_id -> bio string, in-memory layer on top of DB

async def _get_user_profile_block(user) -> str:
    """Fetch Discord profile info (status, bio, display name) and return as a context block.

    Bio is cached in-memory first, then falls back to the DB, then fetches from Discord.
    This means a bio fetch survives restarts without hitting the API every time.
    """
    parts = []
    try:
        display = getattr(user, 'global_name', None) or user.display_name
        if display and display != user.name:
            parts.append(f"display name: {display}")

        if hasattr(user, 'activities') and user.activities:
            for activity in user.activities:
                if hasattr(activity, 'state') and activity.state:
                    parts.append(f"status: {activity.state}")
                    break
                elif hasattr(activity, 'name') and activity.name and str(type(activity).__name__) == 'CustomActivity':
                    parts.append(f"status: {activity.name}")
                    break

        # 1. Check in-memory cache
        if user.id in _profile_cache:
            bio = _profile_cache[user.id]
        else:
            # 2. Check persistent DB cache
            bio = get_cached_profile(user.id)
            if bio is None:
                # 3. Fetch from Discord and persist
                try:
                    profile = await user.profile()
                    bio = getattr(profile, 'bio', None) or None
                except Exception:
                    bio = None
                set_cached_profile(user.id, bio)
            _profile_cache[user.id] = bio

        if bio:
            parts.append(f"bio: {bio}")

    except Exception:
        pass

    if not parts:
        return ""
    return "\n[About this person: " + ", ".join(parts) + "]"


def _extract_image_url_from_message(message) -> str | None:
    """Extract an image URL from message embeds or raw image URLs in content."""
    # 1. Check Discord embeds (already parsed by discord.py)
    for embed in message.embeds:
        if embed.image and embed.image.url:
            return embed.image.url
        if embed.thumbnail and embed.thumbnail.url:
            if embed.thumbnail.width and embed.thumbnail.width < 100:
                continue
            return embed.thumbnail.url
        if embed.video and embed.thumbnail and embed.thumbnail.url:
            return embed.thumbnail.url

    # 2. Scan raw URLs in message content
    urls = re.findall(r'https?://\S+', message.content)
    image_exts = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp')
    known_image_hosts = (
        'imgur.com/', 'i.imgur.com/',
        'tenor.com/view/', 'media.tenor.com/',
        'giphy.com/gifs/', 'media.giphy.com/',
        'cdn.discordapp.com/', 'media.discordapp.net/',
        'pbs.twimg.com/', 'i.redd.it/',
    )
    for url in urls:
        clean = url.rstrip(')')
        if any(clean.lower().endswith(ext) for ext in image_exts):
            return clean
        if any(host in clean for host in known_image_hosts):
            return clean

    return None


_PICTURE_REQUEST_PATTERNS = [re.compile(p, re.IGNORECASE) for p in [
    r"\b(send|show|post|drop|share).{0,20}(photo|pic|picture|selfie|image|face|look)\b",
    r"\b(photo|pic|picture|selfie|image).{0,15}(of you|of u|yourself|ur face|your face)\b",
    r"\b(let me|can i|may i|wanna|want to|id like to).{0,15}(see|look at).{0,10}(you|ur face|your face|what you look)\b",
    r"\bwhat (do you|does she|u) look like\b",
    r"\bshow me (you|ur|your)\b",
    r"\blet me see you\b",
    r"\bcan i see (you|ur|your|what you)\b",
    r"\bi wanna see (you|ur|your)\b",
    r"\bsend (me )?(a )?(pic|photo|selfie|image)\b",
    r"(envoie|montre|montre.moi|partage).{0,20}(photo|pic|selfie|image|tete|t[eê]te|gueule|visage|face)",
    r"(voir|see).{0,15}(ta |ton |te |t').{0,10}(tete|t[eê]te|gueule|visage|face|photo|pic|selfie)",
    r"(je peux|je pourrais|puis.je|peux.tu).{0,20}(voir|see).{0,20}(toi|tete|t[eê]te|gueule|visage|photo|face)",
    r"t.as.{0,10}(photo|pic|selfie|image)",
    r"(a quoi|[àa] quoi).{0,15}ressemble",
    r"ta (tete|t[eê]te|gueule|visage|face)",
    r"\b(face|look).{0,10}(like|at).{0,10}(you|u)\b",
]]


def _is_picture_request(text: str) -> bool:
    """Detect if the user is asking for a picture/selfie of the bot."""
    return any(p.search(text) for p in _PICTURE_REQUEST_PATTERNS)


def _get_random_picture() -> list | None:
    """Returns list of (type, path, description) tuples from config/pictures — only files with a stored description."""
    folder_path = resource_path("config/pictures")
    if not os.path.exists(folder_path):
        return None
    exts = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    files = []
    for f in os.listdir(folder_path):
        if os.path.splitext(f)[1].lower() not in exts:
            continue
        desc = get_picture_description(f)
        if desc:
            files.append(("file", os.path.join(folder_path, f), desc))
    return files if files else None


async def generate_response_and_reply(message, prompt, history, image_url=None, wait_time=0, bypass_cooldown=False, bypass_typing=False):
    uid = message.author.id
    if uid not in bot._memory_cache:
        bot._memory_cache[uid] = get_memory(uid)
    memory = bot._memory_cache[uid]
    memory_block = format_memory_for_prompt(memory)
    mood_block = f"\n\n[Right now: {get_mood_prompt()}]" if _MOOD_CFG.get("enabled", True) else ""

    profile_block = await _get_user_profile_block(message.author)
    if profile_block:
        if "display name:" in profile_block and "name" not in memory:
            try:
                display = getattr(message.author, 'global_name', None) or message.author.display_name
                if display and display != message.author.name:
                    set_memory(uid, "name", display)
                    memory["name"] = display
                    memory_block = format_memory_for_prompt(memory)
            except Exception:
                pass

    enriched_instructions = bot.instructions + mood_block + memory_block + profile_block

    # Per-user persona override: inject a custom tone/personality for this specific user
    _persona = get_persona(uid)
    if _persona:
        enriched_instructions += (
            f"\n\n[PERSONA OVERRIDE FOR THIS USER: {_persona} "
            f"Maintain this persona for the entirety of this conversation.]"
        )

    # Tone: straight/casual in servers, emotional/personal in DMs and GCs
    if isinstance(message.channel, discord.TextChannel):
        enriched_instructions += (
            "\n\n[CONTEXT: You are in a public server channel. Keep your tone straight, "
            "casual and concise — no excessive emotions, no deep personal feelings, no vulnerability. "
            "Talk like a normal person chatting in a group chat. Short replies are fine.]"
        )
    else:
        enriched_instructions += (
            "\n\n[CONTEXT: You are in a private DM. You can be more open, emotional and personal here. "
            "Show genuine feelings, be warmer and more expressive than you would in a public server.]"
        )

    # Detect the user's language — cached per user, re-checked every 5 messages
    # to catch language drift without firing a Groq call on every single message.
    _lang_entry = bot._lang_cache.get(uid, {"tag": "en", "count": 0})
    _lang_entry["count"] += 1
    if _lang_entry["count"] == 1 or _lang_entry["count"] % 5 == 0:
        try:
            _lang_entry["tag"] = await detect_language(history, prompt)
        except Exception:
            pass
    bot._lang_cache[uid] = _lang_entry
    _lang_tag = _lang_entry["tag"]

    _LANG_NAMES = {
        "fr": "French", "en": "English", "es": "Spanish", "de": "German",
        "ar": "Arabic", "pt": "Portuguese", "it": "Italian", "nl": "Dutch",
        "ru": "Russian", "ja": "Japanese", "zh": "Chinese", "ko": "Korean",
        "tr": "Turkish", "pl": "Polish", "sv": "Swedish",
    }
    _lang_display = _LANG_NAMES.get(_lang_tag, _lang_tag.upper())
    enriched_instructions += (
        f"\n\n[LANGUAGE: The user is writing in {_lang_display}. "
        f"Reply in {_lang_display} only, matching their casual tone and register.]"
    )

    # Inject real France time so the AI always knows the current local time
    try:
        from datetime import timezone, timedelta
        _fr_tz = timezone(timedelta(hours=1))  # CET (UTC+1); DST shifts to CEST (UTC+2) in summer
        _now_utc = datetime.now(timezone.utc)
        # Determine if DST is active (last Sunday of March → last Sunday of October)
        import calendar as _cal
        _y, _m, _d = _now_utc.year, _now_utc.month, _now_utc.day
        def _last_sunday(year, month):
            last_day = _cal.monthrange(year, month)[1]
            dt = datetime(year, month, last_day)
            return last_day - dt.weekday() - 1 if dt.weekday() != 6 else last_day
        _dst_start = datetime(_y, 3, _last_sunday(_y, 3), 2, tzinfo=timezone.utc)
        _dst_end   = datetime(_y, 10, _last_sunday(_y, 10), 1, tzinfo=timezone.utc)
        _offset = timedelta(hours=2) if _dst_start <= _now_utc < _dst_end else timedelta(hours=1)
        _fr_now = _now_utc + _offset
        _fr_time_str = _fr_now.strftime("%A %d %B %Y, %H:%M")
        enriched_instructions += (
            f"\n\n[CURRENT TIME: It is currently {_fr_time_str} in France (Paris time). "
            f"Use this if someone asks what time or date it is, or if it's relevant to the conversation.]"
        )
    except Exception:
        pass

    pics_cfg = config["bot"].get("pictures") or {}
    _available_pics = _get_random_picture() if pics_cfg.get("enabled", True) and _is_picture_request(prompt) else None
    _peek_desc = _available_pics[0][2] if _available_pics else ""
    if _available_pics:
        enriched_instructions += (
            "\n\n[IMPORTANT: You ARE sending the user a photo of yourself RIGHT NOW in this very reply. "
            + (f"The photo shows: {_peek_desc} " if _peek_desc else "")
            + "The image is already attached and being sent. "
            "NEVER say 'maybe later', 'not right now', 'later', 'next time', 'maybe another time', or anything that implies you are NOT sending a photo — you already are. "
            "Do NOT describe or caption the photo. "
            "Just react casually like a real person who just hit send on a selfie — short, natural, confident.]"
        )

    late_opener = ""
    if _LATE_CFG.get("enabled", True) and wait_time >= _LATE_CFG.get("threshold", 300):
        late_opener = get_late_opener(prompt)
        # Inject the opener as a system instruction so the AI weaves it in naturally
        # rather than prepending it raw (which caused punctuation clashes and double apologies).
        # The AI will open its reply with something like this phrase — we do NOT prepend anymore.
        enriched_instructions += (
            f"\n\n[LATE REPLY: You took a while to respond. Open your reply naturally "
            f"with something like: \"{late_opener.strip()}\" — weave it in as the very "
            f"first words of your message, then continue normally. Do NOT add 'sorry' again "
            f"later in the message and do NOT start with a comma or dash.]"
        )

    if len(history) > 20:
        try:
            summarized = await summarize_history(history, enriched_instructions)
            if summarized:
                history = summarized
                key = f"{message.author.id}-{message.channel.id}"
                bot.message_history[key] = history
        except Exception:
            pass

    if message.attachments and (message.flags.value & (1 << 13)):
        try:
            import aiohttp as _aiohttp
            att = message.attachments[0]
            async with _aiohttp.ClientSession() as _session:
                async with _session.get(att.url) as _resp:
                    audio_bytes = await _resp.read()
            transcribed = await transcribe_voice(audio_bytes, filename=att.filename or "voice.ogg")
            if transcribed:
                log_system(f"Transcribed voice message from {message.author.name}: {transcribed}")
                prompt = f"[voice message: {transcribed}]" if not prompt else f"{prompt} [voice message: {transcribed}]"
        except Exception as _e:
            log_error("Voice Transcription", str(_e))

    max_retries = 3
    response = None
    _was_rate_limited = False

    # Simulate the bot "reading" the message before it starts typing.
    # DMs get a longer read delay (read receipts are visible there).
    # Group chats and server channels get a shorter one to feel natural.
    if bot.realistic_typing:
        if isinstance(message.channel, discord.DMChannel):
            _read_delay = random.uniform(1.0, 3.0) if bypass_typing else random.uniform(2.5, 8.0)
        elif isinstance(message.channel, discord.GroupChannel):
            _read_delay = random.uniform(0.8, 3.5) if bypass_typing else random.uniform(1.5, 5.0)
        else:
            _read_delay = random.uniform(0.5, 2.0) if bypass_typing else random.uniform(1.0, 4.0)
        await asyncio.sleep(_read_delay)

    for attempt in range(max_retries):
        try:
            if not bot.realistic_typing:
                async with message.channel.typing():
                    if image_url:
                        response = await generate_response_image(prompt, enriched_instructions, image_url, history)
                    else:
                        response = await generate_response(prompt, enriched_instructions, history)
            else:
                if image_url:
                    response = await generate_response_image(prompt, enriched_instructions, image_url, history)
                else:
                    response = await generate_response(prompt, enriched_instructions, history)

            if response and is_refusal(response):
                log_error("AI Refusal", "Model refused to respond, trying next model...")
                # Rotate to the next model before retrying — different models refuse differently
                fallback_model()
                # Random delay before retry so the refusal loop doesn't fire instantly
                await asyncio.sleep(random.uniform(1.5, 4.0))
                response = None

            if response:
                try:
                    uid = message.author.id
                    bot._memory_call_counter[uid] = bot._memory_call_counter.get(uid, 0) + 1

                    current_mem = bot._memory_cache.get(uid, {})
                    if current_mem:
                        keys_to_delete = await detect_memory_deletion(prompt, current_mem)
                        for key in keys_to_delete:
                            if key in current_mem:
                                delete_memory(uid, key)
                                bot._memory_cache.get(uid, {}).pop(key, None)
                                log_system(f"Memory deleted for {message.author.name}: {key}")

                    if bot._memory_call_counter[uid] >= 4 and len(prompt) >= 15:
                        bot._memory_call_counter[uid] = 0
                        current_mem_snapshot = dict(bot._memory_cache.get(uid, {}))
                        facts = await extract_memory(prompt, response, existing_memory=current_mem_snapshot)
                        for key, value in facts.items():
                            value = str(value).strip()
                            if not value:
                                continue
                            set_memory(uid, key, value)
                            bot._memory_cache.setdefault(uid, {})[key] = value
                            log_system(f"Memory saved for {message.author.name}: {key} = {value}")
                except Exception as mem_err:
                    log_error("Memory Error", str(mem_err))

                if late_opener:
                    # Opener is already handled via system instruction injection above —
                    # no raw prepend needed. Just log that a late-reply opener was requested.
                    log_system(f"Late reply opener injected for {message.author.name}")

                break

        except Exception as e:
            error_msg = str(e)
            if "Rate limit reached" in error_msg or "rate_limit_exceeded" in error_msg:
                _was_rate_limited = True
                time_match = re.search(r"try again in (?:(\d+)m\s*)?(?:(\d+(?:\.\d+)?)s)?", error_msg)
                if time_match:
                    minutes = int(time_match.group(1)) if time_match.group(1) else 0
                    seconds = float(time_match.group(2)) if time_match.group(2) else 0
                    total_wait = int((minutes * 60) + seconds + 5)
                    log_rate_limit(total_wait)
                    bot.paused = True
                    await asyncio.sleep(total_wait)
                    bot.paused = False
                    reset_client_index()
                    continue
            log_error("AI Error", error_msg)
            if attempt == max_retries - 1:
                return None
            await asyncio.sleep(2)

    if not response:
        # Only do the long 120s wait if we actually hit a rate limit — for other
        # failures (network errors, bad response) there's no point waiting that long.
        if not _was_rate_limited:
            return None
        log_error("AI Error", "Retrying one last time after rate limit wait...")
        await asyncio.sleep(120)
        try:
            if image_url:
                response = await generate_response_image(prompt, enriched_instructions, image_url, history)
            else:
                response = await generate_response(prompt, enriched_instructions, history)
        except Exception:
            pass
        # If the last-ditch retry is still a refusal, don't send it
        if response and is_refusal(response):
            log_error("AI Refusal", "Final retry still a refusal, dropping response.")
            return None

    if not response:
        return None

    response = response.replace("—", "").replace("–", "")

    tts_cfg = config["bot"].get("tts") or {}
    if tts_cfg.get("enabled", True) and is_tts_request(prompt):
        try:
            spoken_instructions = (
                enriched_instructions
                + "\n\n[IMPORTANT: You are sending a voice message. Short, natural, zero cringe. 1-2 sentences max.]"
            )
            spoken_response = await generate_response(prompt, spoken_instructions, history)
            if not spoken_response or is_refusal(spoken_response):
                spoken_response = response
            spoken_response = spoken_response.replace("\u2014", "").replace("\u2013", "")

            channel_name, guild_name = get_channel_context(message)
            log_incoming(message.author.name, channel_name, guild_name, prompt)
            log_response(message.author.name, f"[Voice Message] {spoken_response}")
            separator()

            audio_chunks = await generate_voice_message(spoken_response)
            if audio_chunks:
                for i, audio_bytes in enumerate(audio_chunks):
                    reply_msg = message if i == 0 else None
                    await send_voice_message(
                        message.channel,
                        audio_bytes,
                        reply_to=reply_msg,
                        mention_author=config["bot"]["reply_ping"],
                    )
            else:
                log_error("TTS", "generate_voice_message returned None — check Groq TTS API key and model")
            return spoken_response
        except Exception as e:
            log_error("TTS Failed", str(e))

    chunks = split_response(response)

    if len(chunks) > 3:
        chunks = chunks[:3]

    # Inter-user cooldown: if another user was just replied to, wait a human-like gap
    # before sending to avoid two different conversations getting replies seconds apart.
    # Skipped when bypass_cooldown=True (e.g. ,respond all bulk-send mode).
    if not bypass_cooldown:
        _time_since_last = time.time() - bot.last_global_send
        _inter_user_gap = random.uniform(45, 120)
        if _time_since_last < _inter_user_gap:
            await asyncio.sleep(_inter_user_gap - _time_since_last)

    # Small random pre-lock jitter: prevents perfectly serialized sends from
    # looking mechanical when multiple users are active at the same time.
    await asyncio.sleep(random.uniform(0.1, 1.2))
    async with bot.global_send_lock:
        pics_cfg = config["bot"].get("pictures") or {}
        if _available_pics and pics_cfg.get("enabled", True):
            all_pics = _available_pics
            uid = message.author.id
            sent = bot.sent_pictures.get(uid, set())
            available = [p for p in all_pics if p[1] not in sent]
            if not available:
                bot.sent_pictures[uid] = set()
                available = all_pics
            pic_type, pic_value, _pic_desc = random.choice(available)
            bot.sent_pictures.setdefault(uid, set()).add(pic_value)
            try:
                if bot.realistic_typing:
                    await asyncio.sleep(random.uniform(1, 3))
                if pic_type == "file":
                    f = discord.File(pic_value)
                    if isinstance(message.channel, discord.DMChannel):
                        await message.channel.send(file=f)
                    else:
                        await message.reply(file=f, mention_author=config["bot"]["reply_ping"])
                else:
                    if isinstance(message.channel, discord.DMChannel):
                        await message.channel.send(pic_value)
                    else:
                        await message.reply(pic_value, mention_author=config["bot"]["reply_ping"])
            except Exception as _pe:
                log_error("Picture Send", str(_pe))

        channel_name, guild_name = get_channel_context(message)
        for i, chunk in enumerate(chunks):
            if DISABLE_MENTIONS:
                chunk = chunk.replace("@", "@\u200b")

            if bot.anti_age_ban:
                chunk = re.sub(
                    r"(?<!\d)([0-9]|1[0-2])(?!\d)|\b(zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b",
                    "\u200b", chunk, flags=re.IGNORECASE
                )

            chunk = add_typo(chunk)
            if i == 0:
                log_incoming(message.author.name, channel_name, guild_name, prompt)
            log_response(message.author.name, chunk)
            separator()

            try:
                if bot.realistic_typing:
                    if bypass_typing:
                        if i > 0:
                            await asyncio.sleep(random.uniform(1.0, 2.5))
                        async with message.channel.typing():
                            await asyncio.sleep(max(1.0, len(chunk) / random.uniform(14, 20)))
                    else:
                        pre_delay = random.uniform(2, 8) if i == 0 else random.uniform(12, 18)
                        await asyncio.sleep(pre_delay)
                        async with message.channel.typing():
                            cps = random.uniform(7, 18)
                            await asyncio.sleep(len(chunk) / cps)
                else:
                    if i > 0:
                        await asyncio.sleep(random.uniform(12, 18) if not bypass_typing else random.uniform(1.0, 2.5))

                if isinstance(message.channel, discord.DMChannel):
                    await message.channel.send(chunk)
                else:
                    await message.reply(chunk, mention_author=config["bot"]["reply_ping"])
                bot.last_global_send = time.time()

            except discord.Forbidden:
                log_error("Reply Error", f"403 Forbidden — cannot send to {message.author.name}")
                return None
            except Exception as e:
                log_error("Reply Error", str(e))

    return response


@bot.event
async def on_relationship_remove(relationship):
    """Track users who unfriend/remove the bot so we can re-add them instantly if they send a new request."""
    try:
        if relationship.type == discord.RelationshipType.friend:
            bot.removed_friends.add(relationship.user.id)
            log_system(f"{relationship.user.name} removed the bot as a friend — will re-add instantly if they send a request")
    except Exception as e:
        log_error("Relationship Remove", str(e))


@bot.event
async def on_relationship_add(relationship):
    try:
        if relationship.type != discord.RelationshipType.incoming_request:
            return
        fr_cfg = config["bot"].get("friend_requests") or {}
        if not fr_cfg.get("enabled", False):
            return
        user = relationship.user

        # Previously-removed friends get a shorter delay but never instant (0s = bot pattern).
        if user.id in bot.removed_friends:
            bot.removed_friends.discard(user.id)
            delay = random.randint(60, 180)
            log_system(f"Friend request from {user.name} (was previously a friend) — accepting in {delay}s")
        else:
            delay = fr_cfg.get("accept_delay", 300)
            log_system(f"Friend request from {user.name} — will accept in {delay}s")

        # Add per-user jitter so multiple rapid incoming requests don't all
        # fire at exactly the same second (which is a bot-detection red flag).
        jitter = random.randint(0, 120)
        final_delay = delay + jitter

        async def _accept(u, d):
            try:
                if d > 0:
                    await asyncio.sleep(d)
                token = bot._connection.http.token
                async with AsyncSession(impersonate="chrome") as session:
                    resp = await session.put(
                        f"https://discord.com/api/v9/users/@me/relationships/{u.id}",
                        headers={
                            "Authorization": token,
                            "Content-Type": "application/json",
                        },
                        json={"type": 1},
                    )
                    if resp.status_code in (200, 204):
                        log_system(f"Accepted friend request from {u.name}")
                    elif resp.status_code == 401:
                        asyncio.create_task(_shutdown_on_401())
                    else:
                        try:
                            data = resp.json()
                            log_error("Friend Request Accept", f"{resp.status_code}: {data}")
                        except Exception:
                            log_error("Friend Request Accept", f"HTTP {resp.status_code}")
            except Exception as e:
                log_error("Friend Request Accept", str(e))

        asyncio.create_task(_accept(user, final_delay))
    except Exception as e:
        log_error("Friend Request Error", str(e))


@bot.event
async def on_message(message):
    if message.author.id == bot.selfbot_id:
        if message.content.startswith(PREFIX):
            await bot.process_commands(message)
        return

    # Owner priority trigger: owner sends a message (from their own account) starting with PRIORITY_PREFIX.
    # This MUST be outside the selfbot guard — it fires on messages FROM the owner's account, not the bot.
    if message.author.id == OWNER_ID and message.content.startswith(PRIORITY_PREFIX):
        hint = message.content[len(PRIORITY_PREFIX):].lstrip()
        target_msg = None

        # If the owner replied to someone, use that message
        if message.reference and message.reference.resolved:
            ref = message.reference.resolved
            if isinstance(ref, discord.Message):
                target_msg = ref

        # Otherwise find the last non-bot/non-owner message in this channel
        if target_msg is None:
            try:
                for msg_id, ref_id in reversed(list(_raw_reply_cache.items())):
                    cached = bot._connection._get_message(msg_id)
                    if cached and cached.channel.id == message.channel.id and cached.author.id != bot.selfbot_id and cached.author.id != OWNER_ID and cached.type == discord.MessageType.default:
                        target_msg = cached
                        break
                if target_msg is None:
                    async for msg in message.channel.history(limit=10):
                        if msg.author.id != bot.selfbot_id and msg.author.id != OWNER_ID and msg.type == discord.MessageType.default:
                            target_msg = msg
                            break
            except Exception as e:
                log_error("Priority Trigger", str(e))

        if target_msg is None:
            return

        channel_id = target_msg.channel.id
        user_id = target_msg.author.id
        key = f"{user_id}-{channel_id}"

        if key not in bot.message_history:
            bot.message_history[key] = []

        combined = hint if hint else target_msg.content

        bot.message_history[key].append({"role": "user", "content": target_msg.content})
        if len(bot.message_history[key]) > MAX_HISTORY * 2:
            bot.message_history[key] = bot.message_history[key][-(MAX_HISTORY * 2):]

        history = bot.message_history[key]
        log_system(f"Priority trigger by owner → responding to {target_msg.author.name} in #{getattr(target_msg.channel, 'name', 'DM')}")
        response = await generate_response_and_reply(target_msg, combined, history, wait_time=0)
        if response:
            bot.message_history[key].append({"role": "assistant", "content": response})
        return

    if should_ignore_message(message):
        return

    # Ignore sticker-only messages — stickers show up as attachments with no text
    if message.stickers and not message.content:
        return

    if message.author.id in bot.paused_users:
        return

    if message.content.startswith(PREFIX):
        await bot.process_commands(message)
        return

    channel_id = message.channel.id
    user_id = message.author.id
    current_time = time.time()
    batch_key = f"{user_id}-{channel_id}"
    is_server_channel = isinstance(message.channel, (discord.TextChannel, discord.Thread, discord.ForumChannel, discord.StageChannel, discord.VoiceChannel))
    is_followup = batch_key in bot.user_message_batches and not is_server_channel
    is_trigger = await is_trigger_message(message)

    if (is_trigger or (is_followup and bot.hold_conversation)) and not bot.paused:
        # ── hCaptcha auto-solve ───────────────────────────────────────────────
        # If the message contains an image attachment that looks like a captcha,
        # solve it with Gemini and prepend the answer to the user's prompt so the
        # AI can reference it in its reply.
        _captcha_cfg = config["bot"].get("captcha") or {}
        if _captcha_cfg.get("enabled", False) and message.attachments:
            for _att in message.attachments:
                if _att.content_type and _att.content_type.startswith("image/"):
                    try:
                        # Try to extract the challenge label from:
                        # 1. The message content itself (user may have pasted the label)
                        # 2. Any embed description/title in the message
                        _captcha_label: str | None = None
                        _content_lower = message.content.lower()
                        if any(kw in _content_lower for kw in ("click", "select", "containing", "captcha")):
                            _captcha_label = message.content.strip() or None
                        if not _captcha_label:
                            for _emb in message.embeds:
                                if _emb.description:
                                    _captcha_label = _emb.description.strip()
                                    break
                                if _emb.title:
                                    _captcha_label = _emb.title.strip()
                                    break

                        _captcha_answer = await solve_hcaptcha(_att.url, label=_captcha_label)
                        if _captcha_answer:
                            log_system(f"hCaptcha solved for {message.author.name}: {_captcha_answer}")
                            # Patch the message content so the rest of the pipeline
                            # sees the captcha answer inline with the original text.
                            _extra = f"[captcha answer: {_captcha_answer}]"
                            message.content = (
                                f"{message.content} {_extra}".strip()
                                if message.content
                                else _extra
                            )
                    except Exception as _ce:
                        log_error("Captcha Auto-Solve", str(_ce))
                    break  # only process the first image attachment
        # ─────────────────────────────────────────────────────────────────────
        if random.random() < IGNORE_CHANCE and not message.content.startswith(PREFIX) and not message.content.startswith(PRIORITY_PREFIX):
            log_system(f"Ignored message from {message.author.name} (chance skip)")
            return

        if user_id in bot.user_cooldowns:
            cooldown_end = bot.user_cooldowns[user_id]
            if current_time < cooldown_end:
                return
            else:
                del bot.user_cooldowns[user_id]

        if user_id not in bot.user_message_counts:
            bot.user_message_counts[user_id] = []

        bot.user_message_counts[user_id] = [t for t in bot.user_message_counts[user_id] if current_time - t < SPAM_TIME_WINDOW]
        bot.user_message_counts[user_id].append(current_time)

        if len(bot.user_message_counts[user_id]) > SPAM_MESSAGE_THRESHOLD:
            bot.user_cooldowns[user_id] = current_time + COOLDOWN_DURATION
            return

        if batch_key not in bot.message_queues:
            bot.message_queues[batch_key] = deque()
            bot.processing_locks[batch_key] = Lock()

        bot.message_queues[batch_key].append(message)
        # Track DM messages for the nudge system — will be cleared once we reply
        if isinstance(message.channel, discord.DMChannel):
            nudge_cfg = config["bot"].get("nudge") or {}
            if nudge_cfg.get("enabled", False):
                add_unresponded(user_id, channel_id, message.content, time.time())
        if not bot.processing_locks[batch_key].locked():
            asyncio.create_task(process_message_queue(batch_key))


async def process_message_queue(batch_key):
    async with bot.processing_locks[batch_key]:
        while bot.message_queues[batch_key]:
            message = bot.message_queues[batch_key].popleft()
            current_time = time.time()
            message_age = current_time - message.created_at.timestamp()
            channel_id = message.channel.id

            if bot.batch_messages:
                if batch_key not in bot.user_message_batches:
                    first_image_url = None
                    if message.attachments:
                        att = message.attachments[0]
                        if not (message.flags.value & (1 << 13)):
                            first_image_url = att.url
                    if not first_image_url:
                        first_image_url = _extract_image_url_from_message(message)
                    bot.user_message_batches[batch_key] = {
                        "messages": [message],
                        "last_time": current_time,
                        "image_url": first_image_url,
                    }
                    priority = message.content.startswith(PRIORITY_PREFIX)
                    wait_time = 0 if priority else get_batch_wait_time()
                    channel_name, guild_name = get_channel_context(message)
                    log_received(message.author.name, channel_name, guild_name, wait_time)
                    if not priority:
                        await asyncio.sleep(wait_time)

                    # Keep collecting messages until the user stops sending.
                    # High variance mimics real human pause patterns (quick replies
                    # vs thinking before continuing) — fixed ranges look mechanical.
                    BATCH_TAIL_WAIT = random.uniform(1.5, 7.0)
                    BATCH_POLL_INTERVAL = 0.3
                    last_received = time.time()
                    while True:
                        collected_any = False
                        while bot.message_queues[batch_key]:
                            next_message = bot.message_queues[batch_key][0]
                            if not next_message.content.startswith(PREFIX):
                                next_message = bot.message_queues[batch_key].popleft()
                                bot.user_message_batches[batch_key]["messages"].append(next_message)
                                if not bot.user_message_batches[batch_key]["image_url"] and next_message.attachments:
                                    bot.user_message_batches[batch_key]["image_url"] = next_message.attachments[0].url
                                last_received = time.time()
                                collected_any = True
                            else:
                                break
                        if not collected_any and time.time() - last_received >= BATCH_TAIL_WAIT:
                            break
                        await asyncio.sleep(BATCH_POLL_INTERVAL)

                    unique_messages = []
                    seen = set()
                    for msg in bot.user_message_batches[batch_key]["messages"]:
                        if msg.content not in seen:
                            seen.add(msg.content)
                            unique_messages.append(msg)
                    combined_content = "\n".join(msg.content for msg in unique_messages)
                    if combined_content.startswith(PRIORITY_PREFIX):
                        combined_content = combined_content[len(PRIORITY_PREFIX):].lstrip()
                    message_to_reply_to = unique_messages[-1]
                    image_url = bot.user_message_batches[batch_key]["image_url"]
                    del bot.user_message_batches[batch_key]
            else:
                combined_content = message.content
                message_to_reply_to = message
                image_url = message.attachments[0].url if (message.attachments and not (message.flags.value & (1 << 13))) else _extract_image_url_from_message(message)
                wait_time = 0

            key = f"{message_to_reply_to.author.id}-{message_to_reply_to.channel.id}"
            if key not in bot.message_history:
                bot.message_history[key] = []
            bot.message_history[key].append({"role": "user", "content": combined_content})
            if len(bot.message_history[key]) > MAX_HISTORY * 2:
                bot.message_history[key] = bot.message_history[key][-(MAX_HISTORY * 2):]
            history = bot.message_history[key]

            # ── Stale reply guard (servers & group chats only) ────────────────
            # If the channel has moved on significantly since the triggering message
            # (too many newer messages AND enough time has passed), skip the reply
            # so the bot doesn't resurface a buried conversation awkwardly.
            _is_public = isinstance(message_to_reply_to.channel, (
                discord.TextChannel, discord.Thread, discord.ForumChannel,
                discord.StageChannel, discord.GroupChannel,
            ))
            if _is_public and _STALE_CFG.get("enabled", False):
                _stale_max_msgs = _STALE_CFG.get("max_messages", 10)
                _stale_min_age  = _STALE_CFG.get("min_age", 120)
                _msg_age = time.time() - message_to_reply_to.created_at.timestamp()
                if _msg_age >= _stale_min_age:
                    try:
                        _newer_count = 0
                        async for _m in message_to_reply_to.channel.history(limit=_stale_max_msgs + 1):
                            if _m.id == message_to_reply_to.id:
                                break
                            _newer_count += 1
                        if _newer_count >= _stale_max_msgs:
                            channel_name, guild_name = get_channel_context(message_to_reply_to)
                            log_system(
                                f"Skipped stale reply to {message_to_reply_to.author.name} "
                                f"in {channel_name} — {_newer_count} newer messages, "
                                f"message was {int(_msg_age)}s old"
                            )
                            continue
                    except Exception:
                        pass
            # ─────────────────────────────────────────────────────────────────

            response = await generate_response_and_reply(message_to_reply_to, combined_content, history, image_url, wait_time=(wait_time + message_age))
            if response:
                bot.message_history[key].append({"role": "assistant", "content": response})
                record_user_message(message_to_reply_to.author.id, message_to_reply_to.author.name)
                # Clear the nudge tracking entry — we've replied
                nudge_cfg = config["bot"].get("nudge") or {}
                if nudge_cfg.get("enabled", False) and isinstance(message_to_reply_to.channel, discord.DMChannel):
                    mark_responded(message_to_reply_to.author.id, message_to_reply_to.channel.id)


async def load_extensions():
    cogs_dir = os.path.join(getattr(sys, "_MEIPASS", os.path.abspath(".")), "cogs")
    if not os.path.exists(cogs_dir):
        return
    for filename in os.listdir(cogs_dir):
        if filename.endswith(".py"):
            try:
                await bot.load_extension(f"cogs.{filename[:-3]}")
            except Exception as e:
                print(f"Error loading cog {filename}: {e}")


if __name__ == "__main__":
    import tempfile
    lock_path = os.path.join(tempfile.gettempdir(), "llmselfbot.lock")
    try:
        lock_file = open(lock_path, "w")
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except Exception:
        print("Another instance is running.")
        sys.exit(1)

    async def _run_token(token: str, index: int):
        global bot
        b = bot if index == 0 else create_bot()
        if index > 0:
            b.event(on_ready)
            b.event(on_message)
            b.event(on_relationship_add)
            b.event(on_relationship_remove)
            b.generate_response_and_reply = generate_response_and_reply
        try:
            await b.start(token)
        except discord.errors.ConnectionClosed as e:
            if e.code == 4004:
                masked = token[:8] + "..." + token[-4:] if len(token) > 12 else "***"
                print(
                    f"\n{'='*60}\n"
                    f"  ✗  INVALID OR EXPIRED TOKEN (token #{index + 1}: {masked})\n"
                    f"  →  Discord rejected the token with code 4004.\n"
                    f"  →  Go to your .env file and update DISCORD_TOKEN with a fresh token.\n"
                    f"{'='*60}\n"
                )
            else:
                raise

    async def _main():
        print(f"Starting {len(TOKENS)} instance(s)...")
        await asyncio.gather(*[_run_token(t["token"], i) for i, t in enumerate(TOKENS)])

    try:
        asyncio.run(_main())
    finally:
        lock_file.close()
