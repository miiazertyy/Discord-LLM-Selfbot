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

from utils.db import init_db, get_channels, get_ignored_users, add_unresponded, mark_responded, mark_nudge_sent, get_pending_nudges
from utils.error_notifications import webhook_log
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


init()


def get_batch_wait_time():
    wait_times = config["bot"]["batch_wait_times"]
    times = [item["time"] for item in wait_times]
    weights = [item["weight"] for item in wait_times]
    return random.choices(times, weights=weights, k=1)[0]


config = load_config()

from utils.ai import init_ai, generate_response, generate_response_image, extract_memory, detect_memory_deletion, transcribe_voice, summarize_history, detect_language, generate_nudge
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
]


def create_bot() -> commands.Bot:
    """Instantiate a fully configured bot. Called once per token."""
    b = commands.Bot(command_prefix=PREFIX, help_command=None, mobile=True)
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
    await asyncio.sleep(3) 

    for key, data in pending.items():
        try:
            user_id = int(data["user_id"])
            channel_id = int(data["channel_id"])
            history = data.get("history", [])
            content = data["content"]

            if history and history[-1].get("role") == "assistant":
                continue

            try:
                user = await bot.fetch_user(user_id)
            except Exception as e:
                log_error("Pending Reply", f"Could not fetch user {user_id}: {e}")
                continue

            bot.message_history[key] = history
            last_msg = None
            channel = None

            try:
                dm = await user.create_dm()
                async for msg in dm.history(limit=30):
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
                        async for msg in channel.history(limit=30):
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
    fr_cfg = config["bot"].get("friend_requests") or {}
    if not fr_cfg.get("enabled", False):
        return
    delay = fr_cfg.get("accept_delay", 300)

    try:
        for relationship in list(bot.relationships):
            if relationship.type == discord.RelationshipType.incoming_request:
                user = relationship.user
                log_system(f"Pending friend request from {user.name} — accepting in {delay}s")

                async def _accept(u=user):
                    await asyncio.sleep(delay)
                    try:
                        token = bot._connection.http.token
                        async with AsyncSession(impersonate="chrome") as session:
                            resp = await session.put(
                                f"https://discord.com/api/v9/users/@me/relationships/{u.id}",
                                headers={
                                    "Authorization": token,
                                    "Content-Type": "application/json",
                                    # Optional: add a matching User-Agent if you want extra consistency
                                    # "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
                                },
                                json={"type": 1},
                            )
                            if resp.status_code in (200, 204):
                                log_system(f"Accepted friend request from {u.name}")
                            else:
                                try:
                                    data = resp.json()
                                    log_error("Friend Request Error", f"{resp.status_code}: {data}")
                                except:
                                    log_error("Friend Request Error", f"{resp.status_code}: {resp.text}")
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

        current_hour = datetime.now().hour
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

    nudge_cfg = config["bot"].get("nudge") or {}
    if nudge_cfg.get("enabled", False):
        asyncio.create_task(_nudge_loop())

    fr_cfg = config["bot"].get("friend_requests") or {}
    if fr_cfg.get("enabled", False):
        asyncio.create_task(_friend_request_loop())



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
        ref = d.get("message_reference")
        if ref and d.get("id"):
            msg_id = int(d["id"])
            ref_id = int(ref.get("message_id", 0))
            if ref_id:
                _raw_reply_cache[msg_id] = ref_id
                # Trim cache
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
        or message.type != discord.MessageType.default
    )


async def is_trigger_message(message):
    mentioned = (
        bot.user.mentioned_in(message)
        and "@everyone" not in message.content
        and "@here" not in message.content
    )
    replied_to = False
    if message.reference:
        ref_msg = message.reference.resolved
        if ref_msg is None or isinstance(ref_msg, discord.DeletedReferencedMessage):
            ref_msg = bot._connection._get_message(message.reference.message_id)
        if ref_msg and ref_msg.author.id == bot.user.id:
            replied_to = True
    elif message.id in _raw_reply_cache:
        ref_id = _raw_reply_cache[message.id]
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
        and not isinstance(message.channel, discord.TextChannel)
    )

    is_server = isinstance(message.channel, discord.TextChannel)

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


def update_message_history(author_id, message_content):
    if author_id not in bot.message_history:
        bot.message_history[author_id] = []
    bot.message_history[author_id].append(message_content)
    bot.message_history[author_id] = bot.message_history[author_id][-MAX_HISTORY:]


_profile_cache: dict = {}  # user_id -> bio string, cached forever per session

async def _get_user_profile_block(user) -> str:
    """Fetch Discord profile info (status, bio, display name) and return as a context block."""
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

        # Cache bio per user — only fetch once per session
        if user.id in _profile_cache:
            bio = _profile_cache[user.id]
        else:
            bio = None
            try:
                profile = await user.profile()
                bio = getattr(profile, 'bio', None) or None
            except Exception:
                pass
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


def _is_picture_request(text: str) -> bool:
    """Detect if the user is asking for a picture/selfie of the bot."""
    patterns = [
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
    ]
    compiled = [re.compile(p, re.IGNORECASE) for p in patterns]
    return any(p.search(text) for p in compiled)


def _get_random_picture() -> list | None:
    """Returns list of (type, path) tuples from config/pictures folder."""
    folder_path = resource_path("config/pictures")
    if not os.path.exists(folder_path):
        return None
    exts = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    files = [
        ("file", os.path.join(folder_path, f))
        for f in os.listdir(folder_path)
        if os.path.splitext(f)[1].lower() in exts
    ]
    return files if files else None


async def generate_response_and_reply(message, prompt, history, image_url=None, wait_time=0):
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

    # Detect the user's language using the LLM, looking at recent history to
    # catch gradual language drift rather than only the current message.
    try:
        _lang_tag = await detect_language(history, prompt)
    except Exception:
        _lang_tag = "en"

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

    pics_cfg = config["bot"].get("pictures") or {}
    if pics_cfg.get("enabled", True) and _is_picture_request(prompt) and _get_random_picture():
        enriched_instructions += (
            "\n\n[IMPORTANT: You are sending the user a photo of yourself right now as part of this reply. "
            "React naturally like you just sent it — don't refuse, don't say you can't, don't act shy about it. "
            "Do NOT describe the photo, do NOT write any caption or attachment text, do NOT say what is in the picture. "
            "Just respond conversationally as a real person would after hitting send on a selfie.]"
        )

    late_opener = ""
    if _LATE_CFG.get("enabled", True) and wait_time >= _LATE_CFG.get("threshold", 300):
        late_opener = get_late_opener(prompt)

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

    # Simulate Discord "seen" receipt: the bot has opened the DM and is reading
    # before it starts typing. Only applies to DMs where read receipts are visible.
    if isinstance(message.channel, discord.DMChannel) and bot.realistic_typing:
        _read_delay = random.uniform(2.5, 8.0)
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
                log_error("AI Refusal", "Model refused to respond, retrying...")
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
                    response = late_opener + response

                break

        except Exception as e:
            error_msg = str(e)
            if "Rate limit reached" in error_msg:
                time_match = re.search(r"try again in (?:(\d+)m\s*)?(?:(\d+(?:\.\d+)?)s)?", error_msg)
                if time_match:
                    minutes = int(time_match.group(1)) if time_match.group(1) else 0
                    seconds = float(time_match.group(2)) if time_match.group(2) else 0
                    total_wait = int((minutes * 60) + seconds + 5)
                    log_rate_limit(total_wait)
                    bot.paused = True
                    await asyncio.sleep(total_wait)
                    bot.paused = False
                    continue
            log_error("AI Error", error_msg)
            if attempt == max_retries - 1:
                return None
            await asyncio.sleep(2)

    if not response:
        log_error("AI Error", "Retrying one last time after wait...")
        await asyncio.sleep(120)
        try:
            if image_url:
                response = await generate_response_image(prompt, enriched_instructions, image_url, history)
            else:
                response = await generate_response(prompt, enriched_instructions, history)
        except Exception:
            pass

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
            return spoken_response
        except Exception as e:
            print(f"[TTS] Failed: {e}")

    if len(response) > 80:
        try:
            split_instructions = (
                "You are a message splitter. Split into 1-3 messages. Return ONLY a JSON array of strings."
            )
            split_resp = await generate_response(
                "Split this: " + response,
                split_instructions,
                history=None,
            )
            import json as _json
            split_resp = split_resp.strip()
            if split_resp.startswith("["):
                parsed = _json.loads(split_resp)
                chunks = [s.strip() for s in parsed if s.strip()]
            else:
                chunks = split_response(response)
        except Exception:
            chunks = split_response(response)
    else:
        chunks = split_response(response)

    if len(chunks) > 3:
        chunks = chunks[:3]

    pics_cfg = config["bot"].get("pictures") or {}
    if pics_cfg.get("enabled", True) and _is_picture_request(prompt):
        all_pics = _get_random_picture()
        if all_pics:
            uid = message.author.id
            sent = bot.sent_pictures.get(uid, set())
            available = [p for p in all_pics if p[1] not in sent]
            if not available:
                bot.sent_pictures[uid] = set()
                available = all_pics
            pic_type, pic_value = random.choice(available)
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

    for i, chunk in enumerate(chunks):
        if DISABLE_MENTIONS:
            chunk = chunk.replace("@", "@\u200b")

        if bot.anti_age_ban:
            chunk = re.sub(
                r"(?<!\d)([0-9]|1[0-2])(?!\d)|\b(zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b",
                "\u200b", chunk, flags=re.IGNORECASE
            )

        chunk = add_typo(chunk)
        channel_name, guild_name = get_channel_context(message)
        log_incoming(message.author.name, channel_name, guild_name, prompt)
        log_response(message.author.name, chunk)
        separator()

        try:
            if bot.realistic_typing:
                pre_delay = random.uniform(2, 8) if i == 0 else random.uniform(12, 18)
                await asyncio.sleep(pre_delay)
                async with message.channel.typing():
                    cps = random.uniform(7, 18)
                    await asyncio.sleep(len(chunk) / cps)
            else:
                if i > 0:
                    await asyncio.sleep(random.uniform(12, 18))

            if isinstance(message.channel, discord.DMChannel):
                await message.channel.send(chunk)
            else:
                await message.reply(chunk, mention_author=config["bot"]["reply_ping"])

        except Exception as e:
            log_error("Reply Error", str(e))

    return response


@bot.event
async def on_relationship_add(relationship):
    try:
        if relationship.type != discord.RelationshipType.incoming_request:
            return
        fr_cfg = config["bot"].get("friend_requests") or {}
        if not fr_cfg.get("enabled", False):
            return
        delay = fr_cfg.get("accept_delay", 300)
        user = relationship.user
        log_system(f"Friend request from {user.name} — accepting in {delay}s")
        await asyncio.sleep(delay)
        token = bot._connection.http.token
        token = bot._connection.http.token
        async with AsyncSession(impersonate="chrome") as session:
            resp = await session.put(
                f"https://discord.com/api/v9/users/@me/relationships/{user.id}",
                headers={
                    "Authorization": token,
                    "Content-Type": "application/json",
                },
                json={"type": 1},
            )
            if resp.status_code in (200, 204):
                log_system(f"Accepted friend request from {user.name}")
            else:
                try:
                    data = resp.json()
                    log_error("Friend Request Error", f"{resp.status_code}: {data}")
                except:
                    log_error("Friend Request Error", f"{resp.status_code}")
    except Exception as e:
        log_error("Friend Request Error", str(e))


@bot.event
async def on_message(message):
    if message.author.id == bot.selfbot_id:
        if message.content.startswith(PREFIX):
            await bot.process_commands(message)
            return

        # Owner priority trigger: owner sends a message starting with =
        if message.author.id == OWNER_ID and message.content.startswith(PRIORITY_PREFIX):
            hint = message.content[len(PRIORITY_PREFIX):].lstrip()
            target_msg = None

            # If the owner replied to someone, use that message
            if message.reference and message.reference.resolved:
                ref = message.reference.resolved
                if isinstance(ref, discord.Message):
                    target_msg = ref

            # Otherwise find the last message in the channel not from the bot/owner
            if target_msg is None:
                try:
                    async for msg in message.channel.history(limit=20):
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

        return

    if should_ignore_message(message):
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
    is_server_channel = isinstance(message.channel, discord.TextChannel)
    is_followup = batch_key in bot.user_message_batches and not is_server_channel
    is_trigger = await is_trigger_message(message)

    if (is_trigger or (is_followup and bot.hold_conversation)) and not bot.paused:
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

        if channel_id not in bot.message_queues:
            bot.message_queues[channel_id] = deque()
            bot.processing_locks[channel_id] = Lock()

        bot.message_queues[channel_id].append(message)
        # Track DM messages for the nudge system — will be cleared once we reply
        if isinstance(message.channel, discord.DMChannel):
            nudge_cfg = config["bot"].get("nudge") or {}
            if nudge_cfg.get("enabled", False):
                add_unresponded(user_id, channel_id, message.content, time.time())
        if not bot.processing_locks[channel_id].locked():
            asyncio.create_task(process_message_queue(channel_id))


async def process_message_queue(channel_id):
    async with bot.processing_locks[channel_id]:
        while bot.message_queues[channel_id]:
            message = bot.message_queues[channel_id].popleft()
            batch_key = f"{message.author.id}-{channel_id}"
            current_time = time.time()
            message_age = current_time - message.created_at.timestamp()

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

                    # Keep collecting messages until the user stops sending for 2.5-4.5s
                    BATCH_TAIL_WAIT = random.uniform(2.5, 4.5)
                    BATCH_POLL_INTERVAL = 0.3
                    last_received = time.time()
                    while True:
                        collected_any = False
                        while bot.message_queues[channel_id]:
                            next_message = bot.message_queues[channel_id][0]
                            if next_message.author.id == message.author.id and not next_message.content.startswith(PREFIX):
                                next_message = bot.message_queues[channel_id].popleft()
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
            
            response = await generate_response_and_reply(message_to_reply_to, combined_content, history, image_url, wait_time=(wait_time + message_age))
            if response:
                bot.message_history[key].append({"role": "assistant", "content": response})
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
            b.generate_response_and_reply = generate_response_and_reply
        await b.start(token)

    async def _main():
        try:
            async with AsyncSession(impersonate="chrome") as s:
                r = await s.get("https://tls.browserleaks.com/json")
                print("TLS Fingerprint test:", r.json().get("ja3", "N/A"))
                print("JA4:", r.json().get("ja4", "N/A"))
        except Exception as e:
            log_error("Fingerprint Test", str(e))

        print(f"Starting {len(TOKENS)} instance(s)...")
        await asyncio.gather(*[_run_token(t["token"], i) for i, t in enumerate(TOKENS)])

    try:
        asyncio.run(_main())
    finally:
        lock_file.close()
