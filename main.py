import os
import asyncio
import discord
import shutil
import re
import random
import sys
import time
import requests
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


init()


def get_batch_wait_time():
    wait_times = config["bot"]["batch_wait_times"]
    times = [item["time"] for item in wait_times]
    weights = [item["weight"] for item in wait_times]
    return random.choices(times, weights=weights, k=1)[0]


config = load_config()

from utils.ai import init_ai, generate_response, generate_response_image, extract_memory, transcribe_voice
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
    "i’m sorry, but I can’t help with that."
]

def is_refusal(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in REFUSAL_PHRASES)

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
        log_system(f"Starting mood: {get_mood()}")
        asyncio.create_task(mood_loop())

    print_separator()

    if config["bot"]["status"].get("enabled", True):
        asyncio.create_task(random_status_loop())


async def setup_hook():
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
                    facts = await extract_memory(prompt, response)
                    for key, value in facts.items():
                        if value:
                            set_memory(message.author.id, key, str(value))
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

    chunks = split_response(response)
    if len(chunks) > 3:
        chunks = chunks[:3]

    for chunk in chunks:
        if DISABLE_MENTIONS:
            chunk = chunk.replace("@", "@\u200b")

        if bot.anti_age_ban:
            chunk = re.sub(
                r"(?<!\d)([0-9]|1[0-2])(?!\d)|\b(zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b",
                "\u200b", chunk, flags=re.IGNORECASE
            )

        chunk = add_typo(chunk)

        channel_name = getattr(message.channel, 'name', 'DM')
        guild_name = getattr(message.guild, 'name', 'DM')
        log_incoming(message.author.name, channel_name, guild_name, prompt)
        log_response(message.author.name, chunk)
        separator()

        try:
            if bot.realistic_typing:
                await asyncio.sleep(random.uniform(2, 8))
                async with message.channel.typing():
                    cps = random.uniform(7, 18)
                    await asyncio.sleep(len(chunk) / cps)

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
    if relationship.type == discord.RelationshipType.incoming_request:
        user = relationship.user
        log_system(f"Friend request from {user.name} — accepting in 10 minutes")

        async def delayed_accept():
            await asyncio.sleep(600)  # 10 minutes
            try:
                # Re-fetch to make sure request is still pending
                await relationship.accept()
                log_system(f"Accepted friend request from {user.name}")
            except Exception as e:
                log_error("Friend Request Error", str(e))

        asyncio.create_task(delayed_accept())


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
                        next((a.url for a in message.attachments if not a.is_voice_message()), None)
                    )
                    bot.user_message_batches[batch_key] = {
                        "messages": [],
                        "last_time": current_time,
                        "image_url": first_image_url,
                    }
                    bot.user_message_batches[batch_key]["messages"].append(message)

                    priority = message.content.startswith(PRIORITY_PREFIX)
                    wait_time = 0 if priority else get_batch_wait_time()

                    channel_name = getattr(message.channel, 'name', 'DM')
                    guild_name = getattr(message.guild, 'name', 'DM')
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
                image_url = next((a.url for a in message.attachments if not a.is_voice_message()), None)
                wait_time = 0

            # Transcribe voice messages
            for attachment in message_to_reply_to.attachments:
                if attachment.is_voice_message():
                    try:
                        import aiohttp
                        async with aiohttp.ClientSession() as session:
                            async with session.get(attachment.url) as resp:
                                audio_bytes = await resp.read()
                        transcription = await transcribe_voice(audio_bytes)
                        if transcription:
                            combined_content = transcription if not combined_content.strip() else f"{combined_content} [voice: {transcription}]"
                            log_system(f"Voice transcribed for {message_to_reply_to.author.name}: {transcription}")
                    except Exception as e:
                        log_error("Voice Transcription Error", str(e))
                    break

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
    bot.run(TOKEN, log_handler=None)
