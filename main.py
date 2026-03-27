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
    load_tokens,
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
from utils.memory import init_memory, get_memory, set_memory, delete_memory, format_memory_for_prompt
from utils.tts import generate_voice_message
from utils.tts_trigger import is_tts_request
from utils.voice_send import send_voice_message

init()

config = load_config()

from utils.ai import init_ai, generate_response, generate_response_image, extract_memory, detect_memory_deletion, transcribe_voice, summarize_history
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

ALLOWED_MEMORY_KEYS = {"name", "age", "location", "job", "hobby", "game", "relationship_status", "nationality", "language_skill"}
JUNK_VALUES = {"yes", "no", "there", "here", "playing", "maybe", "idk", "a lot", "too much", "not really", "kind of", "sort of", "nah", "yeah"}

REFUSAL_PHRASES = ["i'm sorry, but i can't", "i cannot help with that", "as an ai", "i don't feel comfortable", "i'm unable to"]

def get_batch_wait_time():
    wait_times = config["bot"]["batch_wait_times"]
    times = [item["time"] for item in wait_times]
    weights = [item["weight"] for item in wait_times]
    return random.choices(times, weights=weights, k=1)[0]

def create_bot() -> commands.Bot:
    b = commands.Bot(command_prefix=PREFIX, help_command=None, self_bot=True)
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
    return b

bot = create_bot()

# --- Shared Logic ---

def is_trigger_message(message):
    # Check for Priority Prefix first
    if message.content.startswith(PRIORITY_PREFIX):
        return True

    mentioned = (
        bot.user.mentioned_in(message)
        and "@everyone" not in message.content
        and "@here" not in message.content
    )
    replied_to = (
        message.reference
        and message.reference.resolved
        and message.reference.resolved.author.id == bot.user.id
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

    result = (content_has_trigger or mentioned or replied_to or is_dm or is_group_dm or in_conversation)
    if result:
        bot.active_conversations[conv_key] = time.time()
    return result

async def generate_response_and_reply(message, prompt, history, image_url=None, wait_time=0):
    uid = message.author.id
    if uid not in bot._memory_cache:
        bot._memory_cache[uid] = get_memory(uid)
    
    memory_block = format_memory_for_prompt(bot._memory_cache[uid])
    mood_block = f"\n\n[Right now: {get_mood_prompt()}]" if _MOOD_CFG.get("enabled", True) else ""
    enriched_instructions = bot.instructions + mood_block + memory_block

    # Check for priority
    is_priority = prompt.startswith(PRIORITY_PREFIX)
    clean_prompt = prompt[len(PRIORITY_PREFIX):].lstrip() if is_priority else prompt

    # AI generation logic (shortened for brevity, similar to your original)
    try:
        if bot.realistic_typing and not is_priority:
            await asyncio.sleep(random.uniform(1, 3))
            async with message.channel.typing():
                response = await generate_response(clean_prompt, enriched_instructions, history)
        else:
            response = await generate_response(clean_prompt, enriched_instructions, history)

        if response:
            if DISABLE_MENTIONS:
                response = response.replace("@", "@\u200b")
            
            # Send logic
            if isinstance(message.channel, discord.DMChannel):
                await message.channel.send(response)
            else:
                await message.reply(response, mention_author=config["bot"]["reply_ping"])
            return response
    except Exception as e:
        log_error("Reply Error", str(e))
    return None

async def process_message_queue(channel_id):
    async with bot.processing_locks[channel_id]:
        while bot.message_queues[channel_id]:
            message = bot.message_queues[channel_id].popleft()
            priority = message.content.startswith(PRIORITY_PREFIX)
            
            # Skip batching/waiting if priority prefix is used
            if not priority and bot.batch_messages:
                await asyncio.sleep(get_batch_wait_time())
            
            key = f"{message.author.id}-{channel_id}"
            if key not in bot.message_history:
                bot.message_history[key] = []
            
            bot.message_history[key].append({"role": "user", "content": message.content})
            response = await generate_response_and_reply(message, message.content, bot.message_history[key])
            if response:
                bot.message_history[key].append({"role": "assistant", "content": response})

# --- Events ---

@bot.event
async def on_ready():
    clear_console()
    print(f"{Fore.CYAN}Logged in as {bot.user.name}{Style.RESET_ALL}")
    if config["bot"]["mood"].get("enabled", True):
        asyncio.create_task(mood_loop())

@bot.event
async def on_message(message):
    if message.author.id == bot.user.id or message.author.bot:
        return

    if message.content.startswith(PREFIX):
        await bot.process_commands(message)
        return

    if is_trigger_message(message) and not bot.paused:
        # Check ignore chance (but not for priority messages)
        if not message.content.startswith(PRIORITY_PREFIX):
            if random.random() < IGNORE_CHANCE:
                return

        cid = message.channel.id
        if cid not in bot.message_queues:
            bot.message_queues[cid] = deque()
            bot.processing_locks[cid] = Lock()
        
        bot.message_queues[cid].append(message)
        if not bot.processing_locks[cid].locked():
            asyncio.create_task(process_message_queue(cid))

# --- Main Runner ---

async def _run_token(token, index):
    # For multiple tokens, we need to ensure they all use the same event logic
    local_bot = bot if index == 0 else create_bot()
    if index > 0:
        local_bot.event(on_ready)
        local_bot.event(on_message)
    
    try:
        await local_bot.start(token)
    except Exception as e:
        log_error("Startup", f"Failed to start token {index+1}: {e}")

async def _main():
    tasks = [_run_token(t, i) for i, t in enumerate(TOKENS)]
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass
