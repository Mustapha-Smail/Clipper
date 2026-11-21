"""Small authenticated Openverse transport shared by music and sound effects.

Credentials are optional and stay in the environment. A failed token request
falls back to the anonymous API instead of taking media search down. The last
rate-limit headers are observable for the UI/log later; no credential value is
ever included in an exception or returned value.
"""

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .. import config

API = "https://api.openverse.org/v1/"
TOKEN_API = API + "auth_tokens/token/"
USER_AGENT = "clipper-studio/2.6 (self-hosted video tool)"

_lock = threading.Lock()
_token: dict[str, str | float] = {"value": "", "expires": 0.0}
_rate: dict[str, str | int | None] = {"limit": None, "remaining": None, "reset": None,
                                      "authenticated": False}


def _integer(value: str | None) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _remember(headers, authenticated: bool) -> None:
    """Keep only non-sensitive rate information from the last response."""
    if headers is None:
        return
    lowered = {str(k).lower(): str(v) for k, v in headers.items()}
    with _lock:
        _rate.update({
            "limit": _integer(lowered.get("x-ratelimit-limit")),
            "remaining": _integer(lowered.get("x-ratelimit-remaining")),
            "reset": lowered.get("x-ratelimit-reset") or lowered.get("retry-after"),
            "authenticated": authenticated,
        })


def rate_limit() -> dict:
    """The effective tier reported by Openverse's last media response."""
    with _lock:
        return dict(_rate)


def _request_token() -> str:
    client_id, secret = config.openverse_client()
    if not client_id or not secret:
        return ""
    now = time.monotonic()
    with _lock:
        if _token["value"] and float(_token["expires"]) > now + 30:
            return str(_token["value"])
    body = urllib.parse.urlencode({"client_id": client_id, "client_secret": secret,
                                   "grant_type": "client_credentials"}).encode()
    request = urllib.request.Request(TOKEN_API, data=body,
                                     headers={"User-Agent": USER_AGENT,
                                              "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            answer = json.load(response)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError):
        return ""                         # anonymous search remains useful
    value = str(answer.get("access_token") or "")
    seconds = max(60, int(answer.get("expires_in") or 3600)) if value else 0
    with _lock:
        _token.update(value=value, expires=now + seconds)
    return value


def get_json(url: str) -> dict:
    """Read one Openverse JSON object, authenticated when configured."""
    token = _request_token()
    headers = {"User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            _remember(response.headers, bool(token))
            answer = json.load(response)
            if not isinstance(answer, dict):
                raise ValueError("not an object")
            return answer
    except urllib.error.HTTPError as err:
        _remember(err.headers, bool(token))
        if err.code == 429:
            wait = (err.headers or {}).get("Retry-After")
            suffix = f" Try again after {wait} seconds." if str(wait or "").isdigit() else " Wait a few minutes."
            raise RuntimeError("Openverse is limiting requests from this machine." + suffix) from err
        raise RuntimeError(f"Openverse error ({err.code})") from err
    except ValueError as err:
        raise RuntimeError("Openverse sent an answer that could not be read") from err
    except (urllib.error.URLError, TimeoutError) as err:
        raise RuntimeError("Could not reach Openverse. Check your connection.") from err


def reset_for_tests() -> None:
    with _lock:
        _token.update(value="", expires=0.0)
        _rate.update(limit=None, remaining=None, reset=None, authenticated=False)
