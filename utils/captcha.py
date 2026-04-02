"""
utils/captcha.py
~~~~~~~~~~~~~~~~
hCaptcha solver powered by Google Gemini's vision API.

Drop this file in your utils/ folder.

Config (add to config.yaml under bot:):
    captcha:
        enabled: true
        gemini_model: "gemini-2.0-flash"   # any Gemini vision model

Env (add to .env):
    GEMINI_API_KEY=your_key_here

Usage:
    from utils.captcha import solve_hcaptcha

    # image_data: raw bytes OR a URL string
    answer = await solve_hcaptcha(image_data)
    if answer:
        # fill in the captcha field with `answer`
        ...
"""

import base64
import asyncio
import aiohttp
from os import getenv
from dotenv import load_dotenv
from utils.helpers import get_env_path, load_config
from utils.logger import log_system, log_error

# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------
_gemini_api_key: str | None = None
_gemini_model: str = "gemini-2.0-flash"
_initialized: bool = False

_GEMINI_API_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models"
    "/{model}:generateContent?key={key}"
)

# How many times to retry on transient errors before giving up
_MAX_RETRIES = 3


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

def init_captcha() -> None:
    """Load API key and model from .env / config.yaml. Call once at startup."""
    global _gemini_api_key, _gemini_model, _initialized

    load_dotenv(dotenv_path=get_env_path(), override=True)
    _gemini_api_key = getenv("GEMINI_API_KEY")

    if not _gemini_api_key:
        log_error("Captcha Solver", "GEMINI_API_KEY not set in .env — captcha solving disabled.")
        return

    cfg = load_config()
    captcha_cfg = cfg.get("bot", {}).get("captcha", {})
    _gemini_model = captcha_cfg.get("gemini_model", "gemini-2.0-flash")

    _initialized = True
    log_system(f"Captcha solver ready (model: {_gemini_model})")


def _ensure_init() -> None:
    if not _initialized:
        init_captcha()


# ---------------------------------------------------------------------------
# Core solver
# ---------------------------------------------------------------------------

async def solve_hcaptcha(
    image: bytes | str,
    *,
    prompt: str | None = None,
) -> str | None:
    """
    Solve an hCaptcha image challenge with Gemini vision.

    Parameters
    ----------
    image:
        Either raw image bytes or a URL string pointing to the captcha image.
    prompt:
        Optional override for the instruction sent to Gemini.
        Defaults to a generic hCaptcha text-extraction prompt.

    Returns
    -------
    str | None
        The solved captcha text (stripped), or None on failure.
    """
    _ensure_init()

    if not _initialized or not _gemini_api_key:
        log_error("Captcha Solver", "Not initialised — cannot solve captcha.")
        return None

    # ── Resolve image bytes ──────────────────────────────────────────────────
    image_bytes: bytes
    mime_type: str = "image/png"

    if isinstance(image, str):
        # Treat as URL — download it
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(image, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        log_error("Captcha Solver", f"Image fetch failed (HTTP {resp.status})")
                        return None
                    mime_type = resp.content_type or "image/png"
                    image_bytes = await resp.read()
        except Exception as e:
            log_error("Captcha Solver", f"Failed to download captcha image: {e}")
            return None
    else:
        image_bytes = image

    b64_image = base64.b64encode(image_bytes).decode("utf-8")

    # ── Build prompt ─────────────────────────────────────────────────────────
    instruction = prompt or (
        "This is an hCaptcha image challenge. "
        "Look carefully at the image and extract any text or alphanumeric code shown. "
        "If it is a grid-based 'select all images that match' captcha, respond with the "
        "comma-separated grid positions (e.g. '1,3,5') of the matching tiles, numbered "
        "left-to-right, top-to-bottom starting at 1. "
        "If it is a text captcha, respond with only the exact text shown, no punctuation, "
        "no explanation — just the raw answer."
    )

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": instruction},
                    {
                        "inlineData": {
                            "mimeType": mime_type,
                            "data": b64_image,
                        }
                    },
                ]
            }
        ]
    }

    url = _GEMINI_API_URL.format(model=_gemini_model, key=_gemini_api_key)

    # ── Send to Gemini with retries ──────────────────────────────────────────
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    data = await resp.json()

                    if resp.status != 200:
                        err_msg = data.get("error", {}).get("message", str(data))
                        log_error("Captcha Solver", f"Gemini API error (attempt {attempt}): {err_msg}")
                        if resp.status in (429, 500, 503):
                            await asyncio.sleep(2 ** attempt)
                            continue
                        return None

                    # Extract the text answer
                    try:
                        answer = (
                            data["candidates"][0]["content"]["parts"][0]["text"]
                            .strip()
                        )
                        log_system(f"Captcha solved: '{answer}'")
                        return answer
                    except (KeyError, IndexError) as e:
                        log_error("Captcha Solver", f"Unexpected Gemini response shape: {e} | {data}")
                        return None

        except asyncio.TimeoutError:
            log_error("Captcha Solver", f"Gemini request timed out (attempt {attempt})")
            await asyncio.sleep(2 ** attempt)
        except Exception as e:
            log_error("Captcha Solver", f"Request failed (attempt {attempt}): {e}")
            await asyncio.sleep(2 ** attempt)

    log_error("Captcha Solver", "All retry attempts exhausted.")
    return None


# ---------------------------------------------------------------------------
# Convenience: solve from a Discord attachment or embed URL
# ---------------------------------------------------------------------------

async def solve_captcha_from_message(message) -> str | None:
    """
    Helper for your existing on_message flow.
    Checks if a Discord message contains a captcha image (attachment or embed)
    and tries to solve it automatically.

    Returns the solved text or None if not applicable / failed.
    """
    # Check attachments first
    for att in getattr(message, "attachments", []):
        if att.content_type and att.content_type.startswith("image/"):
            return await solve_hcaptcha(att.url)

    # Then embeds
    for embed in getattr(message, "embeds", []):
        if embed.image and embed.image.url:
            return await solve_hcaptcha(embed.image.url)
        if embed.thumbnail and embed.thumbnail.url:
            return await solve_hcaptcha(embed.thumbnail.url)

    return None
