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
    # French
    r"\bmessage vocal\b",
    r"\benvoi.{0,10}vocal\b",
    r"\bta voix\b",
    r"\bentendre ta voix\b",
    r"\bt'entendre\b",
    r"\bprouve.{0,20}(pas (un )?bot|humaine?|r.elle?)\b",
    r"\bt'es (un )?bot\b",
    r"\bc'est (un )?bot\b",
    r"\bdis (quelque chose|bonjour|coucou|salut)\b",
    r"\bparle.{0,10}moi\b",
]

_compiled = [re.compile(p, re.IGNORECASE) for p in TTS_TRIGGER_PATTERNS]


def is_tts_request(text: str) -> bool:
    """Returns True if the message is asking the bot to send a voice message."""
    return any(pattern.search(text) for pattern in _compiled)
