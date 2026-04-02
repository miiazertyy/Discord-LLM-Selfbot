"""
utils/captcha.py
~~~~~~~~~~~~~~~~
hCaptcha solver powered by Google Gemini's vision API.

Handles both challenge types:
  - Grid challenges: "select all images containing a bus" → returns tile indices e.g. "1,3,7"
  - Text/alphanumeric challenges: returns the raw text shown

Config (add to config.yaml under bot:):
    captcha:
        enabled: true
        gemini_model: "gemini-1.5-flash"   # best for spatial/grid tasks

Env (add to .env):
    GEMINI_API_KEY=your_key_here

Usage:
    from utils.captcha import solve_hcaptcha

    # Basic — just an image
    answer = await solve_hcaptcha(image_bytes_or_url)

    # Better — pass the challenge label for context
    answer = await solve_hcaptcha(image_bytes_or_url, label="please click each image containing a bus")
"""

import re
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
_gemini_model: str = "gemini-1.5-flash"
_initialized: bool = False

_GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models"
    "/{model}:generateContent?key={key}"
)

_MAX_RETRIES = 2
_TIMEOUT = 20  # seconds — captchas need to be fast


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
    _gemini_model = captcha_cfg.get("gemini_model", "gemini-1.5-flash")

    _initialized = True
    log_system(f"Captcha solver ready (model: {_gemini_model})")


def _ensure_init() -> None:
    if not _initialized:
        init_captcha()


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def _build_prompt(label: str | None) -> str:
    """
    Build the right prompt depending on whether we have a challenge label.

    hCaptcha has two main formats:
      1. Grid (3x3 or 4x4): "select all images containing X"
         → We want comma-separated 1-based indices, left-to-right top-to-bottom
      2. Text/alphanumeric: just read the characters shown
    """
    if label:
        # Normalise label
        label = label.strip().rstrip(".")
        return (
            f'This is an hCaptcha challenge. The instruction is: "{label}".\n\n'
            "The image is a grid of tiles (usually 3x3 or 4x4). "
            "Number each tile left-to-right, top-to-bottom starting at 1.\n"
            "Identify every tile that matches the instruction.\n\n"
            "Rules:\n"
            "- Reply ONLY with the matching tile numbers separated by commas. Example: 1,4,7\n"
            "- If NO tiles match, reply with exactly: none\n"
            "- Do NOT include explanations, punctuation, or any other text.\n"
            "- Be strict — only include tiles you are confident about."
        )
    else:
        return (
            "This is an hCaptcha image.\n\n"
            "If it shows a grid of images with a selection task:\n"
            "  - Number tiles left-to-right, top-to-bottom starting at 1.\n"
            "  - Reply with the comma-separated numbers of matching tiles. Example: 2,5,9\n"
            "  - If none match, reply: none\n\n"
            "If it shows text or alphanumeric characters to type:\n"
            "  - Reply with only the exact characters shown, nothing else.\n\n"
            "Do NOT explain your answer. Reply with only the raw answer."
        )


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------

def _parse_response(raw: str) -> str | None:
    """
    Clean up Gemini's response into a usable captcha answer.

    Handles cases where Gemini returns extra explanation text
    instead of a clean answer.
    """
    text = raw.strip()

    # If it looks like a clean tile index list already, return as-is
    if re.fullmatch(r"[\d,\s]+", text):
        # Normalise: remove spaces, deduplicate, sort
        indices = sorted(set(int(x) for x in re.findall(r"\d+", text)))
        return ",".join(str(i) for i in indices)

    # "none" or "no tiles" etc.
    if re.search(r"\bnone\b|\bno (tiles?|images?|match)\b", text, re.IGNORECASE):
        return "none"

    # Gemini sometimes wraps the answer in a sentence like:
    # "The matching tiles are 1, 4, and 7."
    # Try to extract a number sequence from anywhere in the response
    numbers = re.findall(r"\b(\d{1,2})\b", text)
    if numbers:
        indices = sorted(set(int(n) for n in numbers if 1 <= int(n) <= 16))
        if indices:
            return ",".join(str(i) for i in indices)

    # For text captchas — strip everything except alphanumerics
    alphanumeric = re.sub(r"[^A-Za-z0-9]", "", text)
    if alphanumeric:
        return alphanumeric

    return None


# ---------------------------------------------------------------------------
# Image fetcher
# ---------------------------------------------------------------------------

async def _fetch_image(url: str) -> tuple[bytes, str] | None:
    """Download an image from a URL. Returns (bytes, mime_type) or None."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=15),
                headers={"User-Agent": "Mozilla/5.0"},
            ) as resp:
                if resp.status != 200:
                    log_error("Captcha Solver", f"Image fetch failed (HTTP {resp.status})")
                    return None
                mime = resp.content_type or "image/png"
                # Normalise mime type
                if "jpeg" in mime or "jpg" in mime:
                    mime = "image/jpeg"
                elif "webp" in mime:
                    mime = "image/webp"
                else:
                    mime = "image/png"
                data = await resp.read()
                return data, mime
    except Exception as e:
        log_error("Captcha Solver", f"Image download error: {e}")
        return None


# ---------------------------------------------------------------------------
# Core solver
# ---------------------------------------------------------------------------

async def solve_hcaptcha(
    image: bytes | str,
    *,
    label: str | None = None,
) -> str | None:
    """
    Solve an hCaptcha challenge with Gemini vision.

    Parameters
    ----------
    image:
        Raw image bytes OR a URL string pointing to the captcha image.
    label:
        The challenge instruction text shown above the grid, e.g.
        "please click each image containing a bus".
        Passing this dramatically improves accuracy.

    Returns
    -------
    str | None
        For grid captchas: comma-separated tile indices e.g. "1,4,7", or "none".
        For text captchas: the alphanumeric string shown.
        None on complete failure.
    """
    _ensure_init()

    if not _initialized or not _gemini_api_key:
        log_error("Captcha Solver", "Not initialised — cannot solve captcha.")
        return None

    # ── Resolve image ────────────────────────────────────────────────────────
    mime_type = "image/png"

    if isinstance(image, str):
        result = await _fetch_image(image)
        if result is None:
            return None
        image_bytes, mime_type = result
    else:
        image_bytes = image

    b64_image = base64.b64encode(image_bytes).decode("utf-8")
    prompt = _build_prompt(label)

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {
                        "inlineData": {
                            "mimeType": mime_type,
                            "data": b64_image,
                        }
                    },
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.1,   # low temp = more deterministic answers
            "maxOutputTokens": 64, # answers are always short
        },
    }

    url = _GEMINI_URL.format(model=_gemini_model, key=_gemini_api_key)

    # ── Call Gemini with fast retries ────────────────────────────────────────
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=_TIMEOUT),
                ) as resp:
                    data = await resp.json()

                    if resp.status != 200:
                        err = data.get("error", {}).get("message", str(data))
                        log_error("Captcha Solver", f"Gemini error (attempt {attempt}): {err}")
                        if resp.status in (429, 500, 503) and attempt < _MAX_RETRIES:
                            await asyncio.sleep(1.5)
                            continue
                        return None

                    raw = (
                        data.get("candidates", [{}])[0]
                        .get("content", {})
                        .get("parts", [{}])[0]
                        .get("text", "")
                        .strip()
                    )

                    if not raw:
                        log_error("Captcha Solver", "Gemini returned empty response.")
                        return None

                    answer = _parse_response(raw)

                    if answer:
                        log_system(
                            f"Captcha solved"
                            + (f" [{label[:40]}]" if label else "")
                            + f": '{answer}'"
                        )
                        return answer
                    else:
                        log_error("Captcha Solver", f"Could not parse Gemini response: '{raw}'")
                        return None

        except asyncio.TimeoutError:
            log_error("Captcha Solver", f"Gemini timed out (attempt {attempt})")
            if attempt < _MAX_RETRIES:
                await asyncio.sleep(1.0)
        except Exception as e:
            log_error("Captcha Solver", f"Request failed (attempt {attempt}): {e}")
            if attempt < _MAX_RETRIES:
                await asyncio.sleep(1.0)

    log_error("Captcha Solver", "All retry attempts exhausted.")
    return None


# ---------------------------------------------------------------------------
# Discord message helper
# ---------------------------------------------------------------------------

async def solve_captcha_from_message(message, *, label: str | None = None) -> str | None:
    """
    Convenience helper for your on_message flow.

    Scans a Discord message for captcha images (attachments first, then embeds)
    and solves the first one found.

    Pass `label` if you've already extracted the challenge text from the message
    content or a nearby embed — it significantly improves accuracy.
    """
    for att in getattr(message, "attachments", []):
        if att.content_type and att.content_type.startswith("image/"):
            return await solve_hcaptcha(att.url, label=label)

    for embed in getattr(message, "embeds", []):
        if embed.image and embed.image.url:
            return await solve_hcaptcha(embed.image.url, label=label)
        if embed.thumbnail and embed.thumbnail.url:
            return await solve_hcaptcha(embed.thumbnail.url, label=label)

    return None
