import io
import re
from groq import AsyncGroq
from os import getenv
from utils.helpers import load_config, get_env_path
from dotenv import load_dotenv

TTS_MODEL = "canopylabs/orpheus-v1-english"
TTS_MAX_CHARS = 180  # Orpheus hard limit is 200, stay safe with 180


def _get_client() -> AsyncGroq:
    env_path = get_env_path()
    load_dotenv(dotenv_path=env_path)
    return AsyncGroq(api_key=getenv("GROQ_API_KEY"))


def _clean_text_for_tts(text: str) -> str:
    """Strip markdown, emojis, mentions and URLs so they aren't read aloud."""
    text = re.sub(r"<@!?\d+>", "", text)
    text = re.sub(r"(\*{1,3}|_{1,3}|`{1,3})", "", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(
        r"[\U00010000-\U0010ffff"
        r"\U0001F300-\U0001F5FF"
        r"\U0001F600-\U0001F64F"
        r"\U0001F680-\U0001F6FF"
        r"\u2600-\u26FF\u2700-\u27BF]+",
        "", text, flags=re.UNICODE
    )
    text = text.replace("—", "").replace("–", "")
    return text.strip()


def _chunk_text(text: str, max_chars: int = TTS_MAX_CHARS) -> list[str]:
    """
    Split text into chunks of max_chars, breaking on sentence boundaries
    where possible so the audio sounds natural between chunks.
    """
    if len(text) <= max_chars:
        return [text]

    chunks = []
    sentences = re.split(r'(?<=[.!?])\s+', text)
    current = ""

    for sentence in sentences:
        if len(sentence) > max_chars:
            words = sentence.split()
            for word in words:
                if len(current) + len(word) + 1 > max_chars:
                    if current:
                        chunks.append(current.strip())
                    current = word
                else:
                    current = f"{current} {word}".strip()
        elif len(current) + len(sentence) + 1 > max_chars:
            if current:
                chunks.append(current.strip())
            current = sentence
        else:
            current = f"{current} {sentence}".strip()

    if current:
        chunks.append(current.strip())

    return [c for c in chunks if c]


async def generate_voice_message(text: str) -> list[bytes] | None:
    """
    Generate voice message audio using Groq Orpheus TTS.
    Returns a list of wav byte chunks (one per 180-char segment), or None on failure.
    Because Orpheus has a 200-char limit, long responses are split and each
    chunk is generated separately then returned as individual audio files.
    """
    config = load_config()
    tts_cfg = config["bot"].get("tts") or {}

    if not tts_cfg.get("enabled", True):
        return None

    voice = tts_cfg.get("voice", "autumn")
    tones = tts_cfg.get("tones", ["[casual]", "[warm]"])
    tone_prefix = " ".join(tones)

    cleaned = _clean_text_for_tts(text)
    if not cleaned:
        return None

    # Reserve space for tone prefix only on first chunk
    first_max = TTS_MAX_CHARS - len(tone_prefix) - 1
    text_chunks = _chunk_text(cleaned, max_chars=first_max)

    client = _get_client()
    audio_chunks = []

    for i, chunk in enumerate(text_chunks):
        tts_input = f"{tone_prefix} {chunk}" if i == 0 else chunk

        try:
            response = await client.audio.speech.create(
                model=TTS_MODEL,
                voice=voice,
                input=tts_input,
                response_format="wav",
            )
            audio_chunks.append(await response.aread())
        except Exception as e:
            print(f"[TTS] Error on chunk {i + 1}/{len(text_chunks)}: {e}")
            if i == 0:
                return None
            break

    return audio_chunks if audio_chunks else None
