"""Connect YouTube: OAuth with Google, so the app can read your own channel's YouTube Analytics.

The flow is the authorization code with PKCE and a `state` check. Google sends you back to the app's
callback (`config.youtube_oauth_redirect_uri()`), which goes through the API natively and through nginx
in Docker. Two read-only scopes: `yt-analytics.readonly` for the reports and `youtube.readonly`, which
`reports.query` now requires too and which `channels.list?mine=true` needs to name your channel.

Only the refresh token is kept, in `data/secrets/youtube-oauth.json` (folder 0700, file 0600), never
in the database: the API and the worker both read the data folder in either runtime, a database dump
or backup never carries it, and Disconnect is one delete. Access tokens live in memory for their hour.
The token is never logged and never returned by a route.

A Google Cloud app left in "Testing" gets refresh tokens that expire after 7 days for these scopes; when
Google refuses the saved token the connection is marked for Reconnect, logged and said in the job log.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime

from .. import config

log = logging.getLogger("clipper.studio")

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
SCOPES = ("https://www.googleapis.com/auth/yt-analytics.readonly",
          "https://www.googleapis.com/auth/youtube.readonly")
STATE_TTL = 600            # seconds a Connect click stays valid
TIMEOUT = 20
TESTING_DAYS = 7           # refresh tokens of an app in "Testing" status expire after this
TESTING_NOTE = ("If your Google Cloud app is still in \"Testing\", Google ends the connection after 7 days: "
                "publish the app (README, \"Connect YouTube\") or reconnect each week.")


class AuthError(RuntimeError):
    """The connection is missing, or Google refused it: the owner must connect again."""


class GoogleError(RuntimeError):
    """Google could not be reached or answered with an error that a later try may not repeat."""


_pending: dict[str, tuple[str, float]] = {}       # state -> (PKCE verifier, monotonic time created)
_access: dict = {}                                # {"token", "until"} in memory only
_lock = threading.Lock()
last_error: str | None = None                     # why the last Connect failed, for the Settings card


# --- the stored connection ---------------------------------------------------------------------

def token_path():
    return config.secrets_dir() / "youtube-oauth.json"


def configured() -> bool:
    client_id, client_secret = config.youtube_oauth_client()
    return bool(client_id and client_secret)


def _read() -> dict:
    try:
        return json.loads(token_path().read_text())
    except (OSError, ValueError):
        return {}


def _write(data: dict) -> None:
    """Atomically, owner-only: the folder 0700 and the file 0600 from the moment it exists."""
    folder = config.secrets_dir()
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    tmp = folder / f".youtube-oauth.{secrets.token_hex(4)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(data, handle)
    os.replace(tmp, token_path())
    os.chmod(token_path(), 0o600)


def status() -> dict:
    """Everything the Settings card shows. Never the token."""
    data = _read()
    connected = bool(data.get("refresh_token"))
    return {
        "configured": configured(),
        "connected": connected,
        "needs_reconnect": bool(data.get("problem")) and not connected,
        "channel_id": data.get("channel_id"),
        "channel_title": data.get("channel_title"),
        "connected_at": data.get("connected_at"),
        "problem": data.get("problem") or (last_error if not connected else None),
        "problem_at": data.get("problem_at"),
        "redirect_uri": config.youtube_oauth_redirect_uri(),
        "testing_note": TESTING_NOTE,
    }


def mark_problem(reason: str) -> None:
    """Google refused the saved token: forget it, keep the channel's name, and say Reconnect."""
    data = _read()
    connected_at = data.get("connected_at")
    days = None
    if connected_at:
        days = (datetime.now(UTC) - datetime.fromisoformat(connected_at)).total_seconds() / 86400
    if days is not None and TESTING_DAYS - 0.5 <= days <= TESTING_DAYS + 1:
        reason += f" It was connected {days:.0f} days ago: {TESTING_NOTE}"
    data.pop("refresh_token", None)
    data.update(problem=reason, problem_at=datetime.now(UTC).isoformat())
    _write(data)
    _access.clear()
    log.warning("YouTube connection needs reconnecting", extra={"reason": reason})


# --- HTTP, kept small so tests can replace it --------------------------------------------------

def _post(url: str, form: dict) -> tuple[int, dict]:
    request = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(), method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as err:
        try:
            return err.code, json.loads(err.read() or b"{}")
        except ValueError:
            return err.code, {}
    except (urllib.error.URLError, TimeoutError) as err:
        raise GoogleError("Could not reach Google. Check the connection.") from err


def get_json(url: str, token: str) -> dict:
    """A GET with the access token. 401 means the token is no good; other errors are Google's words."""
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.load(response)
    except urllib.error.HTTPError as err:
        try:
            error = json.load(err).get("error", {})
        except (ValueError, AttributeError):
            error = {}
        if err.code == 401:
            _access.clear()
            raise AuthError("Google no longer accepts the connection") from err
        reasons = {e.get("reason") for e in error.get("errors", []) if isinstance(e, dict)}
        if reasons & {"quotaExceeded", "rateLimitExceeded", "dailyLimitExceeded"}:
            raise GoogleError("The YouTube quota for today is used up; it resets at midnight Pacific time") from err
        message = error.get("message") if isinstance(error, dict) else None
        raise GoogleError(f"Google answered {err.code}: {message or err.reason}") from err
    except (urllib.error.URLError, TimeoutError) as err:
        raise GoogleError("Could not reach Google. Check the connection.") from err


# --- connecting ----------------------------------------------------------------------------------

def _challenge(verifier: str) -> str:
    """PKCE S256: base64url of the SHA-256 of the verifier, without padding."""
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


def start() -> str:
    """The Google address to send the owner to. Remembers a fresh state and verifier for STATE_TTL."""
    global last_error
    if not configured():
        raise AuthError("Put YOUTUBE_OAUTH_CLIENT_ID and YOUTUBE_OAUTH_CLIENT_SECRET in .env and restart.")
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)           # 86 characters, inside PKCE's 43 to 128
    now = time.monotonic()
    with _lock:
        for key in [k for k, (_, at) in _pending.items() if now - at > STATE_TTL]:
            del _pending[key]
        _pending[state] = (verifier, now)
    last_error = None
    client_id, _ = config.youtube_oauth_client()
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": config.youtube_oauth_redirect_uri(), "response_type": "code",
        "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent",
        "include_granted_scopes": "true", "state": state,
        "code_challenge": _challenge(verifier), "code_challenge_method": "S256"})


def take_state(state: str) -> str:
    """The verifier of a state this app issued, used once; AuthError for an unknown, used or old state."""
    now = time.monotonic()
    with _lock:
        match = next((k for k in _pending if hmac.compare_digest(k.encode(), (state or "").encode())), None)
        if match is None:
            raise AuthError("This sign-in was not started here, was already used, or the app restarted: "
                            "press Connect again.")
        verifier, at = _pending.pop(match)
    if now - at > STATE_TTL:
        raise AuthError("The sign-in took longer than 10 minutes: press Connect again.")
    return verifier


def finish(code: str, state: str) -> dict:
    """Swap the code for tokens, find the channel, keep the refresh token. Returns the status."""
    global last_error
    try:
        verifier = take_state(state)
        client_id, client_secret = config.youtube_oauth_client()
        code_status, answer = _post(TOKEN_URL, {
            "code": code, "client_id": client_id, "client_secret": client_secret, "grant_type": "authorization_code",
            "redirect_uri": config.youtube_oauth_redirect_uri(), "code_verifier": verifier})
        if code_status != 200 or "access_token" not in answer:
            why = answer.get("error_description") or answer.get("error") or code_status
            raise AuthError(f"Google refused the sign-in: {why}")
        granted = set((answer.get("scope") or "").split())
        missing = [s.rsplit("/", 1)[-1] for s in SCOPES if s not in granted]
        if missing:
            raise AuthError(f"Access was not granted to {', '.join(missing)}: connect again and tick every box.")
        refresh = answer.get("refresh_token")
        if not refresh:
            raise AuthError("Google sent no refresh token. Remove the app's access at "
                            "myaccount.google.com/permissions, then connect again.")
        _access.update(token=answer["access_token"], until=time.monotonic() + int(answer.get("expires_in", 3600)) - 60)
        found = get_json(CHANNELS_URL + "?" + urllib.parse.urlencode({"part": "snippet", "mine": "true"}),
                         answer["access_token"]).get("items", [])
        if not found:
            _revoke(refresh)
            raise AuthError("That Google account has no YouTube channel. Sign in with the channel's account "
                            "(or pick the channel when Google asks).")
        channel = found[0]
        _write({"refresh_token": refresh, "channel_id": channel["id"],
                "channel_title": channel.get("snippet", {}).get("title", ""),
                "scopes": sorted(granted & set(SCOPES)), "connected_at": datetime.now(UTC).isoformat()})
        last_error = None
        log.info("YouTube connected", extra={"channel_id": channel["id"]})
        return status()
    except (AuthError, GoogleError) as err:
        last_error = str(err)
        log.warning("YouTube connection failed", extra={"reason": str(err)})
        raise


def access_token() -> str:
    """A valid access token, refreshed when needed. AuthError (Reconnect) when Google refuses the saved one."""
    with _lock:
        if _access.get("token") and _access.get("until", 0) > time.monotonic():
            return _access["token"]
    refresh = _read().get("refresh_token")
    if not refresh:
        raise AuthError("YouTube is not connected")
    if not configured():
        raise AuthError("YOUTUBE_OAUTH_CLIENT_ID and YOUTUBE_OAUTH_CLIENT_SECRET are missing from .env")
    client_id, client_secret = config.youtube_oauth_client()
    code, answer = _post(TOKEN_URL, {"client_id": client_id, "client_secret": client_secret,
                                     "refresh_token": refresh, "grant_type": "refresh_token"})
    if code == 200 and answer.get("access_token"):
        with _lock:
            _access.update(token=answer["access_token"],
                           until=time.monotonic() + int(answer.get("expires_in", 3600)) - 60)
        return answer["access_token"]
    if answer.get("error") in ("invalid_grant", "unauthorized_client", "invalid_client"):
        reason = {"invalid_grant": "Google refused the saved connection: it expired or was revoked. Reconnect YouTube.",
                  "unauthorized_client": "Google no longer accepts this OAuth client. Reconnect YouTube.",
                  "invalid_client": "Google does not know the OAuth client in .env. Check it, then reconnect."}
        mark_problem(reason[answer["error"]])
        raise AuthError(reason[answer["error"]])
    raise GoogleError(f"Google did not refresh the connection ({code}): "
                      f"{answer.get('error_description') or answer.get('error') or 'no reason given'}")


def _revoke(token: str) -> bool:
    try:
        code, _ = _post(REVOKE_URL, {"token": token})
    except GoogleError:
        return False
    return code == 200


def disconnect() -> dict:
    """Revoke the token at Google, then delete it here, whatever Google said. Returns the status with `revoked`."""
    global last_error
    refresh = _read().get("refresh_token")
    revoked = _revoke(refresh) if refresh else False
    try:
        token_path().unlink()
    except FileNotFoundError:
        pass
    _access.clear()
    last_error = None
    log.info("YouTube disconnected", extra={"revoked": revoked})
    return {**status(), "revoked": revoked}
