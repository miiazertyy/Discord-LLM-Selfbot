import random
import asyncio
from utils.logger import log_system

MOODS = ["chill", "playful", "busy", "tired", "annoyed", "flirty"]

MOOD_PROMPTS = {
    "chill": "You are in a relaxed, calm mood. Replies are easy-going and unbothered.",
    "playful": "You are in a playful, energetic mood. More teasing and fun than usual.",
    "busy": "You are a bit busy or distracted. Replies are shorter and slightly impatient.",
    "tired": "You are tired. Replies are slower, shorter, a bit low energy.",
    "annoyed": "You are mildly annoyed. Not rude, but less patient and more blunt.",
    "flirty": "You are in a flirty mood. More suggestive and playful than usual.",
}

current_mood = "chill"


def get_mood():
    return current_mood


def get_mood_prompt():
    return MOOD_PROMPTS[current_mood]


def shift_mood():
    global current_mood
    current_mood = random.choice(MOODS)
    log_system(f"Mood shifted to: {current_mood}")


async def mood_loop():
    while True:
        wait = random.randint(1800, 3600)  # 30–60 min
        await asyncio.sleep(wait)
        shift_mood()
