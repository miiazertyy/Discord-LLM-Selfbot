import os
import io
import asyncio
import discord
import shutil
import re
import random
import sys
import time
import requests
import aiohttp
import utils.ai as ai_module

from utils.helpers import (
    clear_console,
    resource_path,
    get_env_path,
    load_instructions,
    load_config,
)

from utils.db import init_db, get_channels, get_ignored_users
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

from utils.mood import get_mood, get_mood_prompt, mood_loop, shift_mood
from utils.memory import init_memory, get_memory, set_memory, format_memory_for_prompt
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

from utils.ai import init_ai, generate_response, generate_response_image, extract_memory
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

TOKEN = os.getenv("DISCORD_TOKEN")
PREFIX = config["bot"]["prefix"]
OWNER_ID = config["bot"]["owner_id"]
TRIGGER = config["bot"]["trigger"].lower().split(",")
DISABLE_MENTIONS = config["bot"]["disable_mentions"]

bot = commands.Bot(command_prefix=PREFIX, help_command=None)
bot.retry_queue = deque()

bot.owner_id = OWNER_ID
bot.active_channels = set(get_channels())
bot.ignore_users = get_ignored_users()
bot.message_history = {}
bot.paused = False
bot.allow_dm = config["bot"]["allow_dm"]
bot.allow_gc = config["bot"]["allow_gc"]
bot.realistic_typing = config["bot"]["realistic_typing"]
bot.anti_age_ban = config["bot"]["anti_age_ban"]
bot.batch_messages = config["bot"]["batch_messages"]
bot.hold_conversation = config["bot"]["hold_conversation"]
bot.user_message_counts = {}
bot.user_cooldowns = {}

bot.instructions = load_instructions()

bot.message_queues = {}
bot.processing_locks = {}
bot.user_message_batches = {}

bot.active_conversations = {}
CONVERSATION_TIMEOUT = 150.0

SPAM_MESSAGE_THRESHOLD = 5
SPAM_TIME_WINDOW = 10.0
COOLDOWN_DURATION = 60.0

MAX_HISTORY = 15

IGNORE_CHANCE = config["bot"]["ignore_chance"]
PRIORITY_PREFIX = config["bot"]["priority_prefix"]

REFUSAL_PHRASES = [
    "i'm sorry, but i can't",
    "i cannot help with that",
    "i'm not able to",
    "as an ai",
    "i don't feel comfortable",
    "i can't help with that",
    "i'm unable to",
    "i'm sorry, but i can't continue this conversation."
    "i'm sorry, but I can’t share that."
    "i'm sorry, but I can't help with that"
]

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
    await asyncio.sleep(3)  # Give the bot a moment to fully connect

    for key, data in pending.items():
        try:
            user_id = int(data["user_id"])
            channel_id = int(data["channel_id"])
            history = data.get("history", [])
            content = data["content"]

            # Skip if already replied
            if history and history[-1].get("role") == "assistant":
                continue

            # Try to fetch user
            try:
                user = await bot.fetch_user(user_id)
            except Exception as e:
                log_error("Pending Reply", f"Could not fetch user {user_id}: {e}")
                continue

            bot.message_history[key] = history
            last_msg = None
            channel = None

            # Try DM first
            try:
                dm = await user.create_dm()
                async for msg in dm.history(limit=30):
                    if msg.author.id == user_id:
                        last_msg = msg
                        channel = dm
                        break
            except Exception:
                pass

            # If not found in DM, try the original channel (GC or server)
            if last_msg is None:
                try:
                    channel = bot.get_channel(channel_id)
                    if channel is None:
                        # For GCs, try private channels
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

            log_system(f"Replying to pending message from {user.name}")
            response = await generate_response_and_reply(last_msg, content, history)
            if response:
                bot.message_history[key].append({"role": "assistant", "content": response})
        except Exception as e:
            log_error("Pending Reply Error", str(e))
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
                        async with aiohttp.ClientSession() as session:
                            resp = await session.put(
                                f"https://discord.com/api/v9/users/@me/relationships/{u.id}",
                                headers={
                                    "Authorization": token,
                                    "Content-Type": "application/json",
                                },
                                json={"type": 1},
                            )
                            if resp.status in (200, 204):
                                log_system(f"Accepted friend request from {u.name}")
                            else:
                                data = await resp.json()
                                log_error("Friend Request Error", f"{resp.status}: {data}")
                    except Exception as e:
                        log_error("Friend Request Loop", str(e))

                asyncio.create_task(_accept())
    except Exception as e:
        log_error("Friend Request Loop", str(e))


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

    # Reply to any messages that were pending when the bot updated/restarted
    asyncio.create_task(_reply_pending_messages())

    # Auto-accept friend requests if enabled
    fr_cfg = config["bot"].get("friend_requests") or {}
    if fr_cfg.get("enabled", False):
        asyncio.create_task(_friend_request_loop())


async def setup_hook():
    bot.generate_response_and_reply = generate_response_and_reply
    await load_extensions()

bot.setup_hook = setup_hook


def should_ignore_message(message):
    return (
        message.author.id in bot.ignore_users
        or message.author.id == bot.selfbot_id
        or message.author.bot
    )


def is_trigger_message(message):
    mentioned = (
        bot.user.mentioned_in(message)
        and "@everyone" not in message.content
        and "@here" not in message.content
    )
    replied_to = (
        message.reference
        and message.reference.resolved
        and message.reference.resolved.author.id == bot.selfbot_id
    )
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


async def generate_response_and_reply(message, prompt, history, image_url=None, wait_time=0):
    memory = get_memory(message.author.id)
    memory_block = format_memory_for_prompt(memory)
    mood_cfg = config["bot"]["mood"]
    mood_block = f"\n\n[Right now: {get_mood_prompt()}]" if mood_cfg.get("enabled", True) else ""
    enriched_instructions = bot.instructions + mood_block + memory_block

    late_reply_cfg = config["bot"]["late_reply"]
    late_opener = ""
    if late_reply_cfg.get("enabled", True) and wait_time >= late_reply_cfg.get("threshold", 300):
        late_opener = get_late_opener(prompt)

    max_retries = 3
    response = None

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
                    ALLOWED_MEMORY_KEYS = {"name", "age", "location", "job", "hobby", "game", "relationship_status", "nationality", "language_skill"}
                    JUNK_VALUES = {"yes", "no", "there", "here", "playing", "maybe", "idk", "a lot", "too much", "not really", "kind of", "sort of", "nah", "yeah"}
                    facts = await extract_memory(prompt, response)
                    for key, value in facts.items():
                        value = str(value).strip()
                        if not value:
                            continue
                        if key not in ALLOWED_MEMORY_KEYS:
                            continue
                        if value.lower() in JUNK_VALUES:
                            continue
                        if len(value) < 2:
                            continue
                        set_memory(message.author.id, key, value)
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
            continue

    if not response:
        log_error("AI Error", "All retries exhausted, waiting 2 minutes before final attempt...")
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

    # Voice message — triggers when user asks to hear the bot's voice
    tts_cfg = config["bot"].get("tts") or {}
    late_cfg = config["bot"]["late_reply"]
    french_indicators = late_cfg.get("french_indicators", [])
    prompt_is_french = any(word in prompt.lower().split() for word in french_indicators)
    if tts_cfg.get("enabled", True) and is_tts_request(prompt) and not prompt_is_french:
        try:
            # Regenerate a short natural spoken response instead of the deflecting one
            spoken_instructions = (
                enriched_instructions
                + "\n\n[IMPORTANT: You are sending a voice message. "
                "Talk exactly how a real 19-year-old girl would speak out loud in a casual voice message to a friend. "
                "Short, natural, zero cringe. No names, no greetings, no filler spam. "
                "Just say what you'd say, like you hit record mid-thought. 1-2 sentences max.]"
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
                    # Use raw Discord API to send as a proper voice message bubble
                    # (requires flags=1<<13, waveform and duration_secs like Vencord does)
                    reply_msg = message if i == 0 else None
                    await send_voice_message(
                        message.channel,
                        audio_bytes,
                        reply_to=reply_msg,
                        mention_author=config["bot"]["reply_ping"],
                    )
            return spoken_response
        except Exception as e:
            import traceback
            print(f"[TTS] Failed to send voice message: {e}")
            traceback.print_exc()
            # Fall through to normal text response if TTS fails

    chunks = split_response(response)
    if len(chunks) > 3:
        chunks = chunks[:3]

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
                # First chunk: 2-8s pre-delay, subsequent chunks: 12-18s to avoid spam detection
                pre_delay = random.uniform(2, 8) if i == 0 else random.uniform(12, 18)
                await asyncio.sleep(pre_delay)
                async with message.channel.typing():
                    cps = random.uniform(7, 18)
                    await asyncio.sleep(len(chunk) / cps)
            else:
                # Even without realistic typing, add a gap between chunks
                if i > 0:
                    await asyncio.sleep(random.uniform(12, 18))

            if isinstance(message.channel, discord.DMChannel):
                await message.channel.send(chunk)
            else:
                await message.reply(chunk, mention_author=config["bot"]["reply_ping"])

        except discord.errors.HTTPException as e:
            print(f"{datetime.now().strftime('[%H:%M:%S]')} Error replying to message, original message may have been deleted.")
            print_separator()
            await webhook_log(message, e)
        except discord.errors.Forbidden as e:
            print(f"{datetime.now().strftime('[%H:%M:%S]')} Missing permissions to send message, bot may be muted.")
            print_separator()
            await webhook_log(message, e)
        except Exception as e:
            print(f"{datetime.now().strftime('[%H:%M:%S]')} Error: {e}")
            print_separator()
            await webhook_log(message, e)

    return response


@bot.event
async def on_relationship_add(relationship):
    """Auto-accept incoming friend requests with a delay."""
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

        # Use the HTTP API directly — discord.py-self's r.accept() sends the wrong request type
        token = bot._connection.http.token
        async with aiohttp.ClientSession() as session:
            resp = await session.put(
                f"https://discord.com/api/v9/users/@me/relationships/{user.id}",
                headers={
                    "Authorization": token,
                    "Content-Type": "application/json",
                },
                json={"type": 1},
            )
            if resp.status in (200, 204):
                log_system(f"Accepted friend request from {user.name}")
            else:
                data = await resp.json()
                log_error("Friend Request Error", f"{resp.status}: {data}")
    except Exception as e:
        log_error("Friend Request Error", str(e))


@bot.event
async def on_message(message):
    if message.author.id == bot.selfbot_id:
        if message.content.startswith(PREFIX):
            await bot.process_commands(message)
        return

    if should_ignore_message(message):
        return

    if message.content.startswith(PREFIX):
        await bot.process_commands(message)
        return

    channel_id = message.channel.id
    user_id = message.author.id
    current_time = time.time()

    batch_key = f"{user_id}-{channel_id}"
    is_followup = batch_key in bot.user_message_batches
    is_trigger = is_trigger_message(message)

    if (is_trigger or (is_followup and bot.hold_conversation)) and not bot.paused:

        if random.random() < IGNORE_CHANCE:
            return

        if user_id in bot.user_cooldowns:
            cooldown_end = bot.user_cooldowns[user_id]
            if current_time < cooldown_end:
                remaining = int(cooldown_end - current_time)
                log_cooldown(message.author.name, remaining)
                return
            else:
                del bot.user_cooldowns[user_id]

        if user_id not in bot.user_message_counts:
            bot.user_message_counts[user_id] = []

        bot.user_message_counts[user_id] = [
            timestamp
            for timestamp in bot.user_message_counts[user_id]
            if current_time - timestamp < SPAM_TIME_WINDOW
        ]

        bot.user_message_counts[user_id].append(current_time)

        if len(bot.user_message_counts[user_id]) > SPAM_MESSAGE_THRESHOLD:
            bot.user_cooldowns[user_id] = current_time + COOLDOWN_DURATION
            log_cooldown(message.author.name, int(COOLDOWN_DURATION))
            bot.user_message_counts[user_id] = []
            return

        if channel_id not in bot.message_queues:
            bot.message_queues[channel_id] = deque()
            bot.processing_locks[channel_id] = Lock()

        bot.message_queues[channel_id].append(message)

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
                    first_image_url = (
                        message.attachments[0].url if message.attachments else None
                    )
                    bot.user_message_batches[batch_key] = {
                        "messages": [],
                        "last_time": current_time,
                        "image_url": first_image_url,
                    }
                    bot.user_message_batches[batch_key]["messages"].append(message)

                    priority = message.content.startswith(PRIORITY_PREFIX)
                    wait_time = 0 if priority else get_batch_wait_time()

                    channel_name, guild_name = get_channel_context(message)
                    log_received(message.author.name, channel_name, guild_name, wait_time)

                    if not priority:
                        elapsed = 0.0
                        interval = 1.0
                        while elapsed < wait_time:
                            await asyncio.sleep(min(interval, wait_time - elapsed))
                            elapsed += interval
                            if any(
                                m.author.id == message.author.id and m.content.startswith(PRIORITY_PREFIX)
                                for m in bot.message_queues[channel_id]
                            ):
                                wait_time = elapsed
                                log_received(message.author.name, channel_name, guild_name, 0)
                                break

                    while bot.message_queues[channel_id]:
                        next_message = bot.message_queues[channel_id][0]
                        if (
                            next_message.author.id == message.author.id
                            and not next_message.content.startswith(PREFIX)
                        ):
                            next_message = bot.message_queues[channel_id].popleft()
                            if next_message.content not in [
                                m.content
                                for m in bot.user_message_batches[batch_key]["messages"]
                            ]:
                                bot.user_message_batches[batch_key]["messages"].append(
                                    next_message
                                )
                            if (
                                not bot.user_message_batches[batch_key]["image_url"]
                                and next_message.attachments
                            ):
                                bot.user_message_batches[batch_key]["image_url"] = (
                                    next_message.attachments[0].url
                                )
                        else:
                            break

                    messages_to_process = bot.user_message_batches[batch_key]["messages"]
                    seen = set()
                    unique_messages = []
                    for msg in messages_to_process:
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
                image_url = message.attachments[0].url if message.attachments else None
                wait_time = 0

            for mention in message_to_reply_to.mentions:
                combined_content = combined_content.replace(
                    f"<@{mention.id}>", f"@{mention.display_name}"
                )

            key = f"{message_to_reply_to.author.id}-{message_to_reply_to.channel.id}"
            if key not in bot.message_history:
                bot.message_history[key] = []

            bot.message_history[key].append(
                {"role": "user", "content": combined_content}
            )
            history = bot.message_history[key]

            total_wait = wait_time + message_age

            if message_to_reply_to.channel.id in bot.active_channels or (
                isinstance(message_to_reply_to.channel, discord.DMChannel)
                and bot.allow_dm
            ) or (
                isinstance(message_to_reply_to.channel, discord.GroupChannel)
                and bot.allow_gc
            ):
                response = await generate_response_and_reply(
                    message_to_reply_to, combined_content, history, image_url,
                    wait_time=total_wait
                )
                if response:
                    bot.message_history[key].append(
                        {"role": "assistant", "content": response}
                    )


async def notify_active_conversations(message: str):
    now = time.time()
    notified_channels = set()
    for conv_key, last_time in bot.active_conversations.items():
        if now - last_time > CONVERSATION_TIMEOUT:
            continue
        try:
            user_id, channel_id = map(int, conv_key.split("-"))
            if channel_id in notified_channels:
                continue
            channel = bot.get_channel(channel_id)
            if channel is None:
                channel = await bot.fetch_channel(channel_id)
            await channel.send(message)
            notified_channels.add(channel_id)
        except Exception:
            pass


async def load_extensions():
    if getattr(sys, "frozen", False):
        cogs_dir = os.path.join(sys._MEIPASS, "cogs")
    else:
        cogs_dir = os.path.join(os.path.abspath("."), "cogs")

    if not os.path.exists(cogs_dir):
        print(f"Warning: Cogs directory not found at {cogs_dir}. Skipping cog loading.")
        return

    clear_console()

    for filename in os.listdir(cogs_dir):
        if filename.endswith(".py"):
            cog_name = f"cogs.{filename[:-3]}"
            try:
                print(f"Loading cog: {cog_name}")
                await bot.load_extension(cog_name)
            except Exception as e:
                print(f"Error loading cog {cog_name}: {e}")


if __name__ == "__main__":
    import tempfile

    lock_path = os.path.join(tempfile.gettempdir(), "llmselfbot.lock")

    if sys.platform == "win32":
        import msvcrt
        try:
            lock_file = open(lock_path, "w")
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            print("Another instance is already running. Exiting.")
            sys.exit(1)
    else:
        import fcntl
        try:
            lock_file = open(lock_path, "w")
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Another instance is already running. Exiting.")
            sys.exit(1)

    try:
        bot.run(TOKEN, log_handler=None)
    finally:
        lock_file.close()
