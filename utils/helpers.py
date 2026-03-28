import os
import sys
import yaml

def clear_console():
    """Clear the console screen."""
    os.system("cls" if os.name == "nt" else "clear")


def resource_path(relative_path):
    if getattr(sys, "frozen", False):
        base_path = os.path.dirname(sys.executable)
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
        # Go up one level since helpers.py is in utils/
        base_path = os.path.dirname(base_path)
    return os.path.join(base_path, relative_path)


def get_env_path():
    return resource_path("config/.env")


def load_config():
    config_path = resource_path("config/config.yaml")
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as file:
            config = yaml.safe_load(file)

        return config

    else:

        print(
            "Config file not found. Please provide a config file in config/config.yaml"
        )
        sys.exit(1)


def load_tokens() -> list[dict]:
    """Return all Discord tokens from the .env file as a list of dicts with 'token' and 'proxy'.
    Supports DISCORD_TOKEN_1/DISCORD_PROXY_1, DISCORD_TOKEN_2/DISCORD_PROXY_2, ...
    Also supports the legacy DISCORD_TOKEN / DISCORD_PROXY fallback.
    If no proxy is set for a token, 'proxy' will be None (local IP used)."""
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=get_env_path(), override=True)

    tokens = []
    i = 1
    while True:
        t = os.getenv(f"DISCORD_TOKEN_{i}")
        if not t:
            break
        proxy = os.getenv(f"DISCORD_PROXY_{i}", "").strip() or None
        tokens.append({"token": t.strip(), "proxy": proxy})
        i += 1

    # Fall back to the legacy single-token key
    if not tokens:
        t = os.getenv("DISCORD_TOKEN")
        if t:
            proxy = os.getenv("DISCORD_PROXY", "").strip() or None
            tokens.append({"token": t.strip(), "proxy": proxy})

    if not tokens:
        print("No Discord token(s) found in config/.env. Please set DISCORD_TOKEN_1 (or DISCORD_TOKEN).")
        sys.exit(1)

    return tokens


def load_instructions():
    instructions_path = resource_path("config/instructions.txt")
    if os.path.exists(instructions_path):
        with open(instructions_path, "r", encoding="utf-8", errors="replace") as file:
            instructions = file.read()

        return instructions
    else:
        print("Instructions file not found. Using default instructions.")

        return ""
