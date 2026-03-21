import re

TTS_TRIGGER_PATTERNS = [
    # English
    r"\bhear your voice\b",
    r"\bsend.{0,10}voice( message)?\b",
    r"\bvoice message\b",
    r"\bprove.{0,20}(not an? ai|human|real)\b",
    r"\bare you (an? ai|a bot|real)\b",
    r"\bsay something\b",
    r"\btalk to me\b",
    r"\bcan (i|we) hear (you|ur)\b",
    r"\bspeak\b",
    r"\blet me hear (you|ur voice)\b",
    r"\byou('re| are) (a )?bot\b",
    r"\bnot (a )?real (person|human)\b",
    r"\bprove (ur|you'?re) (human|real|not a bot)\b",
]

_compiled = [re.compile(p, re.IGNORECASE) for p in TTS_TRIGGER_PATTERNS]


def is_tts_request(text: str) -> bool:
    """Returns True if the message is asking the bot to send a voice message."""
    return any(pattern.search(text) for pattern in _compiled)
