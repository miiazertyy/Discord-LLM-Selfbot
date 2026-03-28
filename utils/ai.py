import sys
import json

from groq import AsyncGroq, RateLimitError
from os import getenv
from dotenv import load_dotenv
from utils.helpers import get_env_path, load_config
from utils.error_notifications import webhook_log, print_error
from utils.logger import log_model_fallback

# Active Groq clients — one per API key
_groq_clients = []   # list of {"client": AsyncGroq, "label": str}
_client_index = 0    # which key is currently active

model = None
groq_models = []
current_model_index = 0


def _active_client():
    """Return the currently active Groq client."""
    return _groq_clients[_client_index]["client"]


def init_ai():
    global _groq_clients, _client_index, model, groq_models, current_model_index
    env_path = get_env_path()
    config = load_config()
    load_dotenv(dotenv_path=env_path, override=True)

    # Load keys: GROQ_API_KEY_1, GROQ_API_KEY_2, ...
    # Falls back to legacy GROQ_API_KEY if no numbered keys are set.
    keys = []
    i = 1
    while True:
        k = getenv(f"GROQ_API_KEY_{i}")
        if not k:
            break
        keys.append((f"GROQ_API_KEY_{i}", k))
        i += 1
    if not keys:
        legacy = getenv("GROQ_API_KEY")
        if legacy:
            keys.append(("GROQ_API_KEY", legacy))

    if not keys:
        print("No GROQ_API_KEY found in .env, exiting.")
        sys.exit(1)

    _groq_clients = [
        {"client": AsyncGroq(api_key=key), "label": label}
        for label, key in keys
    ]
    _client_index = 0

    raw = config["bot"]["groq_models"]
    if isinstance(raw, str):
        groq_models = [m.strip() for m in raw.split(",") if m.strip()]
    else:
        groq_models = list(raw)
    current_model_index = 0
    model = groq_models[0]

    key_count = len(_groq_clients)
    print(f"[AI] Loaded {key_count} Groq API key(s), {len(groq_models)} model(s).")


def _fallback_client():
    """Rotate to the next API key. Returns True if a new key is available."""
    global _client_index
    if len(_groq_clients) <= 1:
        return False
    next_index = _client_index + 1
    if next_index >= len(_groq_clients):
        return False  # All keys exhausted — let model fallback handle it
    old_label = _groq_clients[_client_index]["label"]
    _client_index = next_index
    new_label = _groq_clients[_client_index]["label"]
    print(f"[AI] Rate limited on {old_label}, switching to {new_label}.")
    return True


def fallback_model():
    """Rotate to next model and reset key index."""
    global model, current_model_index, _client_index
    if not groq_models:
        return False
    old_model = model
    current_model_index += 1
    if current_model_index >= len(groq_models):
        current_model_index = 0
        return False
    model = groq_models[current_model_index]
    _client_index = 0  # Reset to first key when switching models
    log_model_fallback(old_model, model)
    return True


async def _create_completion(messages):
    """Attempt completion with automatic key + model fallback on rate limit."""
    if not _groq_clients:
        init_ai()

    while True:
        try:
            response = await _active_client().chat.completions.create(
                model=model,
                messages=messages,
            )
            return response
        except RateLimitError:
            if _fallback_client():
                continue
            if fallback_model():
                continue
            raise
        except Exception as e:
            if "rate" not in str(e).lower() and "429" not in str(e):
                print(f"[AI] {type(e).__name__} on {model}: {e}")
            if _fallback_client():
                continue
            if fallback_model():
                continue
            raise


async def _create_image_completion(image_model, messages):
    """Image description call with key fallback (no model fallback — image model is fixed)."""
    if not _groq_clients:
        init_ai()
    while True:
        try:
            response = await _active_client().chat.completions.create(
                model=image_model,
                messages=messages,
            )
            return response
        except RateLimitError:
            if _fallback_client():
                continue
            raise
        except Exception as e:
            if "rate" not in str(e).lower() and "429" not in str(e):
                print(f"[AI] {type(e).__name__} on image model {image_model}: {e}")
            if _fallback_client():
                continue
            raise


async def _create_transcription(whisper_model, audio_file):
    """Whisper transcription call with key fallback."""
    if not _groq_clients:
        init_ai()
    while True:
        try:
            transcription = await _active_client().audio.transcriptions.create(
                model=whisper_model,
                file=audio_file,
            )
            return transcription
        except RateLimitError:
            if _fallback_client():
                continue
            raise
        except Exception as e:
            if "rate" not in str(e).lower() and "429" not in str(e):
                print(f"[AI] {type(e).__name__} on whisper model {whisper_model}: {e}")
            if _fallback_client():
                continue
            raise


async def generate_response(prompt, instructions, history=None):
    if not _groq_clients:
        init_ai()
    try:
        messages = [{"role": "system", "content": instructions}]
        if history:
            messages += history
        else:
            messages.append({"role": "user", "content": prompt})

        response = await _create_completion(messages)
        return response.choices[0].message.content
    except Exception as e:
        print_error("AI Error", e)
        await webhook_log(None, e)
        raise


async def generate_response_image(prompt, instructions, image_url, history=None):
    if not _groq_clients:
        init_ai()
    try:
        _cfg = load_config()
        _image_model = _cfg["bot"].get("groq_image_model", "meta-llama/llama-4-scout-17b-16e-instruct")
        image_response = await _create_image_completion(
            _image_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"Describe / Explain in detail this image sent by a Discord user to an AI who will be responding to the message '{prompt}' based on your output as the AI cannot see the image. So make sure to tell the AI any key details about the image that you think are important to include in the response, especially any text on screen that the AI should be aware of.",
                        },
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }
            ],
        )

        prompt_with_image = f"{prompt} [Image of {image_response.choices[0].message.content}]"

        if history:
            history.append({"role": "user", "content": prompt_with_image})
            messages = [
                {
                    "role": "system",
                    "content": instructions + " Images will be described to you, with the description wrapped in [|description|], so understand that you are to respond to the description as if it were an image you can see.",
                },
                *history,
            ]
        else:
            history = [{"role": "user", "content": prompt_with_image}]
            messages = [
                {"role": "system", "content": instructions},
                {"role": "user", "content": prompt_with_image},
            ]

        response = await _create_completion(messages)
        history.append({"role": "assistant", "content": response.choices[0].message.content})
        return response.choices[0].message.content
    except Exception as e:
        print_error("AI image Error", e)
        await webhook_log(None, e)
        raise


async def extract_memory(user_message: str, assistant_reply: str, existing_memory: dict = None) -> dict:
    """Ask the LLM to decide what's worth remembering — free-form, no fixed key allowlist.

    The LLM chooses both the key name and value, so it can capture anything that
    would genuinely be useful to recall later (name, job, city, pet, favourite show,
    gaming handle, relationship status, hobbies, etc.). Short snake_case keys only.
    """
    if not _groq_clients:
        init_ai()

    existing_block = ""
    if existing_memory:
        existing_block = (
            "\nAlready stored facts (do NOT re-extract these unless the value changed):\n"
            + "\n".join(f"  {k}: {v}" for k, v in existing_memory.items())
            + "\n"
        )

    prompt = (
        f'User message: "{user_message}"\n'
        f'Assistant reply: "{assistant_reply}"\n'
        f'{existing_block}\n'
        "Extract ONLY concrete, specific facts the USER explicitly stated about themselves "
        "that would be genuinely useful to remember in a future conversation.\n"
        "RULES:\n"
        "- Only extract from the USER message. Ignore the assistant reply.\n"
        "- Ignore Discord @mentions — they are not the user's name.\n"
        "- Ignore transient states: tired, bored, sad, happy, busy.\n"
        "- Ignore vague values: yes, no, maybe, idk, a lot, kind of.\n"
        "- Ignore anything phrased as a question (questions reveal nothing).\n"
        "- Keys must be short, lowercase, snake_case (e.g. 'name', 'city', 'pet_name', 'favourite_game').\n"
        "- Values must be specific and meaningful (proper noun, number, or clear phrase).\n"
        "- You decide which keys matter — no fixed list. Use good judgment.\n"
        "If nothing clearly qualifies, return exactly: {}\n"
        "Return ONLY the JSON object. No explanation, no markdown, no extra text."
    )

    try:
        for _attempt in range(len(_groq_clients)):
            try:
                response = await _active_client().chat.completions.create(
                    model=model,
                    messages=[
                        {
                            "role": "system",
                            "content": "You are a JSON-only memory extractor. Output nothing except a valid JSON object."
                        },
                        {"role": "user", "content": prompt}
                    ],
                    max_tokens=200,
                    temperature=0.1,
                )
                break
            except RateLimitError:
                if not _fallback_client():
                    raise
        text = response.choices[0].message.content.strip()
        text = text.replace("```json", "").replace("```", "").strip()
        if not text.startswith("{"):
            return {}
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            return {}
        # Sanitise keys: lowercase, snake_case, max 40 chars
        clean = {}
        for k, v in parsed.items():
            key = str(k).lower().replace(" ", "_").replace("-", "_")[:40]
            val = str(v).strip()
            if key and val and len(val) >= 2:
                clean[key] = val
        return clean
    except json.JSONDecodeError:
        return {}
    except Exception as e:
        if "429" not in str(e) and "rate" not in str(e).lower():
            print_error("Memory Extract Error", e)
        return {}


async def detect_memory_deletion(user_message: str, current_memory: dict) -> list:
    """Ask the LLM to detect if the user is retracting, correcting, or joking about a stored fact."""
    if not _groq_clients:
        init_ai()
    if not current_memory:
        return []

    memory_lines = "\n".join(f"- {k}: {v}" for k, v in current_memory.items())
    prompt = (
        f'Stored facts about the user:\n{memory_lines}\n\n'
        f'New user message: "{user_message}"\n\n'
        "Does the user's message indicate that any stored fact is WRONG, was a JOKE, should be FORGOTTEN, "
        "or is being CORRECTED? This includes:\n"
        "- Explicit corrections: 'I'm not actually 22', 'my name isn't Jake', 'I lied about my job'\n"
        "- Jokes/retractions: 'lol I was kidding', 'that was a joke', 'I made that up'\n"
        "- Forget requests: 'forget what I said about my age', 'don't remember that', 'ignore that'\n"
        "- Contradictions: if they previously said location=Paris and now say 'I live in Tokyo'\n\n"
        "Return ONLY a JSON array of key names that should be DELETED from memory. "
        "Example: [\"age\", \"location\"]\n"
        "If nothing should be deleted, return exactly: []\n"
        "Return ONLY the JSON array. No explanation, no markdown, no extra text."
    )

    try:
        for _attempt in range(len(_groq_clients)):
            try:
                response = await _active_client().chat.completions.create(
                    model=model,
                    messages=[
                        {
                            "role": "system",
                            "content": "You are a JSON-only memory auditor. You output nothing except valid JSON arrays of strings."
                        },
                        {"role": "user", "content": prompt}
                    ],
                    max_tokens=100,
                    temperature=0.1,
                )
                break
            except RateLimitError:
                if not _fallback_client():
                    raise
        text = response.choices[0].message.content.strip()
        text = text.replace("```json", "").replace("```", "").strip()
        if not text.startswith("["):
            return []
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            return []
        return [k for k in parsed if isinstance(k, str)]
    except json.JSONDecodeError:
        return []
    except Exception as e:
        if "429" not in str(e) and "rate" not in str(e).lower():
            print_error("Memory Deletion Detect Error", e)
        return []


async def transcribe_voice(audio_bytes: bytes, filename: str = "voice.ogg") -> str:
    """Transcribe a voice message using Groq Whisper."""
    if not _groq_clients:
        init_ai()

    try:
        import io
        whisper_model = load_config()["bot"].get("groq_whisper_model", "whisper-large-v3-turbo")
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = filename

        transcription = await _create_transcription(whisper_model, audio_file)
        return transcription.text.strip()
    except Exception as e:
        print_error("Whisper Error", e)
        return ""


async def detect_language(history: list, current_message: str) -> str:
    """Use the LLM to detect the user's language from recent conversation history.
    
    Looks at the last few user turns (not just the current message) so it can
    detect gradual language drift across a conversation.
    Returns a BCP-47 language tag like 'fr', 'en', 'es', 'ar', 'de', etc.
    Falls back to 'en' on any error.
    """
    if not _groq_clients:
        init_ai()

    # Build a compact view of recent user messages so the LLM can see drift
    recent_user_msgs = [
        m["content"] for m in history[-8:] if m.get("role") == "user"
    ]
    # Always include the current message (it may not be in history yet)
    if not recent_user_msgs or recent_user_msgs[-1] != current_message:
        recent_user_msgs.append(current_message)

    sample = "\n".join(f"- {m}" for m in recent_user_msgs[-5:])

    prompt = (
        "Identify the single most likely language the user is writing in based on these recent messages.\n\n"
        f"Messages:\n{sample}\n\n"
        "Reply with ONLY the BCP-47 language tag (e.g. 'en', 'fr', 'es', 'ar', 'de', 'pt', 'it', 'nl', 'ru', 'ja', 'zh').\n"
        "No explanation, no punctuation, no extra text."
    )

    try:
        response = await _active_client().chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "You are a language detector. Output only a BCP-47 language tag.",
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=8,
            temperature=0.0,
        )
        tag = response.choices[0].message.content.strip().lower().split("-")[0]
        # Basic sanity check — reject anything that looks like a sentence
        if len(tag) > 5 or " " in tag:
            return "en"
        return tag
    except Exception:
        return "en"


async def summarize_history(history: list, instructions: str) -> list:
    """Compress long history into summary + recent messages to save tokens."""
    if not _groq_clients:
        init_ai()

    KEEP_RECENT = 6
    if len(history) <= KEEP_RECENT + 2:
        return history

    to_summarize = history[:-KEEP_RECENT]
    recent = history[-KEEP_RECENT:]

    lines = []
    for msg in to_summarize:
        role = "User" if msg["role"] == "user" else "You"
        lines.append(role + ": " + msg["content"])
    transcript = "\n".join(lines)

    summary_prompt = (
        "Here is a conversation transcript:\n\n" + transcript + "\n\n"
        "Write a brief summary of this conversation in 2-3 sentences from your perspective. "
        "Focus on key facts, topics discussed, and the emotional tone. "
        "Write in first person as if you are remembering what was discussed."
    )

    try:
        for _attempt in range(len(_groq_clients)):
            try:
                response = await _active_client().chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": instructions},
                        {"role": "user", "content": summary_prompt},
                    ],
                    max_tokens=200,
                    temperature=0.3,
                )
                break
            except RateLimitError:
                if not _fallback_client():
                    raise
        summary_text = response.choices[0].message.content.strip()
        summary_msg = {
            "role": "assistant",
            "content": "[Earlier in this conversation: " + summary_text + "]"
        }
        return [summary_msg] + recent
    except Exception:
        return history
