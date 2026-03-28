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


async def extract_memory(user_message: str, assistant_reply: str) -> dict:
    """Ask the LLM to extract any new personal facts the user revealed."""
    if not _groq_clients:
        init_ai()

    prompt = (
        f'User message: "{user_message}"\n'
        f'Assistant reply: "{assistant_reply}"\n\n'
        "Extract ONLY concrete, specific facts the USER explicitly stated about themselves. "
        "STRICT RULES:\n"
        "- ONLY extract from the USER message. Ignore everything the assistant said.\n"
        "- Ignore Discord @mentions (e.g. @Alex) — those are not the user's name.\n"
        "- Only save facts with a SPECIFIC, MEANINGFUL value. Examples of good facts:\n"
        "    name=Jake, age=22, location=Paris, job=nurse, hobby=drawing, game=Minecraft, relationship_status=single\n"
        "- REJECT vague or context-dependent values like: yes, no, there, here, playing, maybe, idk, too much, a lot\n"
        "- REJECT transient states: tired, bored, busy, sad, happy — these are moods, not facts.\n"
        "- REJECT anything the user asked as a question — questions reveal nothing about themselves.\n"
        "- REJECT language/nationality unless the user explicitly says 'I am Italian' or 'I speak French'.\n"
        "- Values must be at least 2 meaningful words OR a proper noun (name, city, game title, etc.).\n"
        "- Allowed keys ONLY: name, age, location, job, hobby, game, relationship_status, nationality, language_skill\n"
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
                            "content": "You are a JSON-only fact extractor. You output nothing except valid JSON objects."
                        },
                        {"role": "user", "content": prompt}
                    ],
                    max_tokens=150,
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
        return parsed
    except json.JSONDecodeError:
        return {}
    except Exception as e:
        # Silently ignore rate limit errors — memory extraction is non-critical
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
