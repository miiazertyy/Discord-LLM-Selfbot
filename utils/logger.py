from colorama import Fore, Back, Style
from datetime import datetime
import shutil


def get_width():
    columns, _ = shutil.get_terminal_size()
    return columns


def separator(char="─"):
    print(f"{Fore.CYAN}{char * (get_width() - 2)}{Style.RESET_ALL}")


def timestamp():
    return f"{Fore.LIGHTBLACK_EX}{datetime.now().strftime('%H:%M:%S')}{Style.RESET_ALL}"


def log_incoming(author: str, channel: str, guild: str, content: str):
    print(
        f"{timestamp()} "
        f"{Fore.WHITE}{Style.BRIGHT}{author}{Style.RESET_ALL} "
        f"{Fore.LIGHTBLACK_EX}in #{channel} ({guild}){Style.RESET_ALL}"
        f"\n  {Fore.GREEN}▸{Style.RESET_ALL} {content}"
    )


def log_response(author: str, chunk: str, model: str = None):
    model_tag = f" {Fore.LIGHTBLACK_EX}[{model}]{Style.RESET_ALL}" if model else ""
    print(
        f"{timestamp()} "
        f"{Fore.CYAN}{Style.BRIGHT}Responding to {author}{Style.RESET_ALL}{model_tag}"
        f"\n  {Fore.CYAN}▸{Style.RESET_ALL} {chunk}"
    )


def log_rate_limit(wait: int, model: str = None):
    model_tag = f" on {model}" if model else ""
    print(
        f"{timestamp()} {Fore.YELLOW}{Style.BRIGHT}⚠ RATE LIMITED{model_tag} — waiting {wait}s{Style.RESET_ALL}"
    )


def log_model_fallback(from_model: str, to_model: str):
    print(
        f"{timestamp()} {Fore.YELLOW}⟳ Model fallback: {Style.BRIGHT}{from_model}{Style.NORMAL} → {to_model}{Style.RESET_ALL}"
    )


def log_error(context: str, error: str):
    print(
        f"{timestamp()} {Fore.RED}{Style.BRIGHT}✗ {context}:{Style.RESET_ALL} {Fore.RED}{error}{Style.RESET_ALL}"
    )


def log_system(msg: str):
    print(f"{timestamp()} {Fore.MAGENTA}⚙ {msg}{Style.RESET_ALL}")


def log_cooldown(username: str, remaining: int):
    print(
        f"{timestamp()} {Fore.YELLOW}⏱ {username} is on cooldown for {remaining}s{Style.RESET_ALL}"
    )


def log_received(author: str, channel: str, guild: str, wait: int):
    if wait == 0:
        print(
            f"{timestamp()} "
            f"{Fore.WHITE}{Style.BRIGHT}{author}{Style.RESET_ALL} "
            f"{Fore.LIGHTBLACK_EX}in #{channel} ({guild}){Style.RESET_ALL} "
            f"{Fore.RED}→ priority, responding immediately{Style.RESET_ALL}"
        )
        return
    wait_str = f"{wait}s" if wait < 60 else f"{wait // 60}m {wait % 60}s" if wait % 60 else f"{wait // 60}m"
    print(
        f"{timestamp()} "
        f"{Fore.WHITE}{Style.BRIGHT}{author}{Style.RESET_ALL} "
        f"{Fore.LIGHTBLACK_EX}in #{channel} ({guild}){Style.RESET_ALL} "
        f"{Fore.MAGENTA}→ waiting {wait_str} before responding{Style.RESET_ALL}"
    )
