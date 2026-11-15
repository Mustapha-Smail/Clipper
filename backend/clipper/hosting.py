"""A public home for a video while a scheduled post waits: a Cloudflare R2 bucket.

Buffer has no upload: it fetches each video from a public https URL, when the
post is created and again when it goes out, which can be days later. The app
runs on localhost, so an approved video is copied to a bucket under a name
nobody can guess, and removed once the post is sent or cancelled.

R2 speaks the S3 API. Two calls are all this needs (PutObject, DeleteObject),
so they are signed here (AWS Signature Version 4) instead of pulling in an SDK.

  R2_ACCOUNT_ID          the Cloudflare account that owns the bucket
  R2_ACCESS_KEY_ID       an R2 API token with "Object Read & Write" on that bucket
  R2_SECRET_ACCESS_KEY
  R2_BUCKET              the bucket's name
  R2_PUBLIC_URL          its public address: the r2.dev "Public Development URL", or a custom domain
  R2_ENDPOINT            optional: only for a bucket created in a jurisdiction (EU: https://<account>.eu.r2.cloudflarestorage.com)
"""

import hashlib
import hmac
import http.client
import re
import secrets
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlsplit

from . import config

REQUIRED = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET", "R2_PUBLIC_URL")
REGION, SERVICE = "auto", "s3"
CHUNK = 1024 * 1024
TIMEOUT = 120


class HostingError(RuntimeError):
    """The video could not be put online or removed; the message says what to check."""


_env = config.r2


def missing() -> list[str]:
    """The variables still to set in .env before a video can be put online."""
    return [name for name in REQUIRED if not _env(name)]


def configured() -> bool:
    return not missing()


def endpoint() -> str:
    return (_env("R2_ENDPOINT") or f"https://{_env('R2_ACCOUNT_ID')}.r2.cloudflarestorage.com").rstrip("/")


def public_url(key: str) -> str:
    base = _env("R2_PUBLIC_URL").rstrip("/")
    if not base.startswith("https://"):
        raise HostingError("R2_PUBLIC_URL must start with https:// (Buffer only fetches https)")
    return f"{base}/{quote(key)}"


def new_key(name: str) -> str:
    """A path nobody can guess: the bucket is public, the names are the only lock."""
    plain = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower()[:60] or "video"
    return f"posts/{secrets.token_urlsafe(18)}/{plain}.mp4"


# --- AWS Signature Version 4 ------------------------------------------------------

def _hmac(key: bytes, text: str) -> bytes:
    return hmac.new(key, text.encode(), hashlib.sha256).digest()


def sign(method: str, host: str, path: str, headers: dict[str, str], payload_hash: str,
         access_key: str, secret_key: str, now: datetime,
         region: str = REGION, service: str = SERVICE) -> str:
    """The Authorization header for one request. `path` is already URI-encoded; no query string."""
    stamp, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    signed = {k.lower(): " ".join(str(v).split()) for k, v in headers.items()}
    signed.update({"host": host, "x-amz-date": stamp, "x-amz-content-sha256": payload_hash})
    names = ";".join(sorted(signed))
    canonical = "\n".join([method, path, "", *(f"{k}:{signed[k]}" for k in sorted(signed)), "", names, payload_hash])
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    key = _hmac(_hmac(_hmac(_hmac(f"AWS4{secret_key}".encode(), day), region), service), "aws4_request")
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return f"AWS4-HMAC-SHA256 Credential={access_key}/{scope},SignedHeaders={names},Signature={signature}"


def _request(method: str, key: str, headers: dict[str, str], payload_hash: str, body=None) -> tuple[int, str]:
    if not configured():
        raise HostingError("The R2 bucket is not set up: " + ", ".join(missing()) + " missing in .env")
    target = urlsplit(endpoint())
    path = "/" + quote(f"{_env('R2_BUCKET')}/{key}", safe="/-_.~")
    now = datetime.now(UTC)
    sent = dict(headers)
    sent["Authorization"] = sign(method, target.netloc, path, headers, payload_hash,
                                 _env("R2_ACCESS_KEY_ID"), _env("R2_SECRET_ACCESS_KEY"), now)
    sent["x-amz-date"] = now.strftime("%Y%m%dT%H%M%SZ")
    sent["x-amz-content-sha256"] = payload_hash
    connection = http.client.HTTPSConnection(target.netloc, timeout=TIMEOUT)
    try:
        connection.request(method, path, body=body, headers=sent)
        response = connection.getresponse()
        return response.status, response.read().decode(errors="replace")
    except OSError as err:
        raise HostingError(f"Could not reach the R2 bucket: {err}") from err
    finally:
        connection.close()


def _explain(status: int, body: str) -> str:
    code = re.search(r"<Code>([^<]+)</Code>", body)
    hints = {
        "SignatureDoesNotMatch": "R2_SECRET_ACCESS_KEY does not match R2_ACCESS_KEY_ID",
        "InvalidAccessKeyId": "R2_ACCESS_KEY_ID is not known to this account",
        "AccessDenied": "the R2 token cannot write to this bucket (it needs Object Read & Write on it)",
        "NoSuchBucket": "there is no bucket of that name in this account (check R2_BUCKET and R2_ACCOUNT_ID)",
    }
    if _env("R2_ENDPOINT"):
        # A bucket outside the jurisdiction of the endpoint answers AccessDenied, as a missing permission would.
        hints["AccessDenied"] += ("; or the bucket is not in the jurisdiction of R2_ENDPOINT "
                                  "(remove that line if unsure)")
    name = code.group(1) if code else f"HTTP {status}"
    return hints.get(name, name)


def _chunks(path: Path, progress):
    total, sent = max(1, path.stat().st_size), 0
    with path.open("rb") as file:
        while chunk := file.read(CHUNK):
            yield chunk
            sent += len(chunk)
            if progress:
                progress(min(1.0, sent / total))


def put(path: Path, key: str, progress=None) -> str:
    """Copy a video into the bucket. Returns the public URL it can be fetched at."""
    url = public_url(key)       # a bad R2_PUBLIC_URL should stop us before the upload, not after
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(CHUNK):
            digest.update(chunk)
    headers = {"Content-Type": "video/mp4", "Content-Length": str(path.stat().st_size)}
    status, body = _request("PUT", key, headers, digest.hexdigest(), body=_chunks(path, progress))
    if status not in (200, 201):
        raise HostingError(f"The upload to R2 was refused: {_explain(status, body)}")
    return url


def delete(key: str) -> None:
    status, body = _request("DELETE", key, {}, hashlib.sha256(b"").hexdigest())
    if status not in (200, 204, 404):
        raise HostingError(f"R2 would not delete the file: {_explain(status, body)}")


def check_public(url: str) -> None:
    """Buffer fetches without credentials: make sure anyone can, before handing it the URL."""
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "clipper"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - https, checked in public_url
            if response.status == 200:
                return
            found = f"HTTP {response.status}"
    except urllib.error.HTTPError as err:
        found = f"HTTP {err.code}"
    except OSError as err:
        found = str(err)
    raise HostingError(
        f"The uploaded video is not reachable at its public address ({found}). In the bucket's settings, "
        "enable the Public Development URL (or connect a custom domain) and put that address in R2_PUBLIC_URL.")
