import random
from curl_cffi.requests import AsyncSession

# curl_cffi only supports specific named impersonation targets — not arbitrary
# Chrome versions. This list contains the targets that are actually available
# across recent curl_cffi releases (0.6.x – 0.7.x). Using anything outside
# this set produces the "Impersonating chromeXXX is not supported" error.
CHROME_VERSIONS: list[str] = ["110", "116", "119", "120", "123", "124"]


def fetch_chrome_versions(pool_size: int = 5) -> None:
    """
    No-op kept for backwards compatibility.
    curl_cffi impersonate targets are a fixed set determined by the library
    version, not by the real Chrome release channel, so fetching the live
    version from Google's API and building a pool from it always risks
    producing an unsupported target string.  We use the static allowlist above
    instead and log a notice so callers aren't surprised.
    """
    print(f"[Session] Using static curl_cffi Chrome target pool: {CHROME_VERSIONS}")

_PLATFORM_DATA = {
    "Windows": {
        "ua_platform": "Windows NT 10.0; Win64; x64",
        "sec_platform": "Windows",
        "discord_tz": "America/New_York",
    },
    "macOS": {
        "ua_platform": "Macintosh; Intel Mac OS X 10_15_7",
        "sec_platform": "macOS",
        "discord_tz": "America/Los_Angeles",
    },
    "Linux": {
        "ua_platform": "X11; Linux x86_64",
        "sec_platform": "Linux",
        "discord_tz": "Europe/London",
    },
}


def _random_chrome_version() -> str:
    return random.choice(CHROME_VERSIONS)


def _random_platform() -> dict:
    return random.choice(list(_PLATFORM_DATA.values()))


def make_chrome_headers(token: str, extra: dict = None) -> dict:
    """
    Build a realistic set of Chrome browser headers for Discord API requests.
    Randomises Chrome version and platform on every call.
    """
    ver = _random_chrome_version()
    plat = _random_platform()

    headers = {
        "Authorization": token,
        "User-Agent": (
            f"Mozilla/5.0 ({plat['ua_platform']}) "
            f"AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{ver}.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Content-Type": "application/json",
        "sec-ch-ua": (
            f'"Chromium";v="{ver}", '
            f'"Google Chrome";v="{ver}", '
            f'"Not-A.Brand";v="99"'
        ),
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": f'"{plat["sec_platform"]}"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "X-Debug-Options": "bugReporterEnabled",
        "X-Discord-Locale": "en-US",
        "X-Discord-Timezone": plat["discord_tz"],
        "Origin": "https://discord.com",
        "Referer": "https://discord.com/channels/@me",
    }

    if extra:
        headers.update(extra)

    return headers


def build_session(token: str, extra_headers: dict = None, proxy: str = None) -> AsyncSession:
    """
    Return a curl_cffi AsyncSession that impersonates Chrome.
    This gives us a realistic JA3/TLS fingerprint automatically.
    If proxy is provided (e.g. 'http://user:pass@host:port' or 'socks5://host:port'),
    all requests will be routed through it. Otherwise the local IP is used.
    Use as an async context manager:  async with build_session(token) as s: ...
    """
    ver = _random_chrome_version()
    kwargs = {"impersonate": f"chrome{ver}"}
    if proxy:
        kwargs["proxies"] = {"https": proxy, "http": proxy}
    session = AsyncSession(**kwargs)
    session.headers.update(make_chrome_headers(token, extra_headers))
    return session
