import random
import asyncio
from utils.helpers import load_config
from utils.logger import log_system

current_mood = "chill"


def get_mood():
    return current_mood


def get_mood_prompt():
    config = load_config()
    moods = config["bot"]["mood"]["moods"]
    return moods.get(current_mood, "You are in a normal mood.")


def shift_mood():
    global current_mood
    config = load_config()
    moods = config["bot"]["mood"]["moods"]
    current_mood = random.choice(list(moods.keys()))
    log_system(f"Mood shifted to: {current_mood}")


async def mood_loop():
    while True:
        config = load_config()
        mood_config = config["bot"]["mood"]
        wait = random.randint(
            mood_config["shift_interval_min"],
            mood_config["shift_interval_max"]
        )
        await asyncio.sleep(wait)
        shift_mood()
