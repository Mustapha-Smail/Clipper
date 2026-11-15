"""Buffer's GraphQL API: list the connected channels, create, read and delete a post.

Buffer (buffer.com) holds the connections to the social networks and publishes at
the scheduled time. This module only talks to it; what may be posted, and when
it is asked, is decided in `posting.py`.

  BUFFER_CLIPPING_API_KEY  personal key for clips (Buffer > Settings > API)
  BUFFER_STUDIO_API_KEY    personal key for Studio videos, which may be a different account
  BUFFER_API_KEY           legacy fallback for both

Limits on the Free plan: 150 requests per 15 minutes, 600 a day, and 1,000 per
channel per 30 days. The last one is the tight one, so callers must not poll.
"""

import json
import urllib.error
import urllib.request

from . import config

URL = "https://api.buffer.com"
TIMEOUT = 120            # createPost fetches the video before it answers
BATCH = 20               # posts read in one request (see `posts`)

POST_FIELDS = "id status dueAt sentAt externalLink error { message }"
# Service-specific fields a video post needs. Instagram: a Reel, also shown on the profile grid.
METADATA = {
    "instagram": lambda ai, title, category: {
        "type": "reel", "shouldShareToFeed": True, "isAiGenerated": ai},
    "tiktok": lambda ai, title, category: {"isAiGenerated": ai},
    "youtube": lambda ai, title, category: {
        "title": title, "categoryId": category, "isAiGenerated": ai},
}
KEY_NAMES = {"clip": "BUFFER_CLIPPING_API_KEY", "storyboard": "BUFFER_STUDIO_API_KEY"}


class BufferError(RuntimeError):
    """Buffer refused or could not be reached; the message is fit to show."""

    def __init__(self, message: str, retry_after: int | None = None):
        super().__init__(message)
        self.retry_after = retry_after


def configured(kind: str | None = None) -> bool:
    return bool(config.buffer_api_key(kind))


def call(query: str, variables: dict | None = None, kind: str | None = None) -> dict:
    """One request. Returns the whole answer ({data, errors}); raises on transport and account errors."""
    key = config.buffer_api_key(kind)
    key_name = KEY_NAMES.get(kind, "BUFFER_API_KEY")
    if not key:
        raise BufferError(f"{key_name} is not set in .env")
    request = urllib.request.Request(
        URL, method="POST", data=json.dumps({"query": query, "variables": variables or {}}).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "clipper"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310 - a fixed https URL
            answer = json.loads(response.read())
    except urllib.error.HTTPError as err:
        if err.code == 429:
            wait = int(err.headers.get("Retry-After") or 0) or None
            raise BufferError("Buffer's request limit is reached"
                              + (f"; it resets in {wait // 60 + 1} min" if wait else ""), wait) from err
        if err.code == 401:
            raise BufferError(f"Buffer refused the key: check {key_name} in .env") from err
        raise BufferError(f"Buffer answered HTTP {err.code}") from err
    except (OSError, ValueError) as err:
        raise BufferError(f"Could not reach Buffer: {err}") from err
    codes = {(e.get("extensions") or {}).get("code") for e in answer.get("errors") or []}
    if "UNAUTHORIZED" in codes:
        raise BufferError(f"Buffer refused the key: check {key_name} in .env")
    if "RATE_LIMIT_EXCEEDED" in codes:
        raise BufferError("Buffer's request limit is reached")
    return answer


def _data(answer: dict) -> dict:
    if answer.get("data") is None:
        first = (answer.get("errors") or [{}])[0].get("message") or "no answer"
        raise BufferError(f"Buffer: {first}")
    return answer["data"]


def channels(kind: str) -> list[dict]:
    """Every connected channel of the account, whatever the network."""
    orgs = _data(call("{ account { organizations { id name } } }", kind=kind))["account"]["organizations"]
    found = []
    for org in orgs:
        data = _data(call(
            "query($org: OrganizationId!) { channels(input: {organizationId: $org}) { id name displayName service "
            "type isDisconnected isLocked isQueuePaused timezone postingSchedule { day times paused } } }",
            {"org": org["id"]}, kind))
        for channel in data["channels"]:
            slots = sum(len(day["times"]) for day in channel["postingSchedule"] if not day.get("paused"))
            found.append({
                "id": channel["id"], "service": channel["service"],
                "name": channel["displayName"] or channel["name"], "type": channel["type"],
                "timezone": channel["timezone"], "weekly_slots": slots,
                "schedule": [{"day": day["day"], "times": [] if day.get("paused") else sorted(day["times"])}
                             for day in channel["postingSchedule"]],
                "disconnected": channel["isDisconnected"], "locked": channel["isLocked"],
                "queue_paused": channel["isQueuePaused"],
            })
    return found


def _post(node: dict) -> dict:
    return {"id": node["id"], "status": node["status"], "due_at": node.get("dueAt"), "sent_at": node.get("sentAt"),
            "link": node.get("externalLink"), "error": (node.get("error") or {}).get("message")}


def create_post(channel_id: str, service: str, text: str, video_url: str, mode: str,
                due_at: str | None = None, ai_generated: bool = False, *, kind: str,
                title: str = "", category_id: str = "22") -> dict:
    """Create one video post. mode: "queue" (next free slot), "at" (due_at, ISO 8601 UTC) or
    "draft" (kept in Buffer's drafts until you schedule it there)."""
    if service not in METADATA:
        raise BufferError(f"Posting to {service} is not supported yet")
    post = {
        "channelId": channel_id, "text": text, "schedulingType": "automatic",
        "mode": "customScheduled" if mode == "at" else "addToQueue",
        "assets": [{"video": {"url": video_url}}],
        "metadata": {service: METADATA[service](ai_generated, title, category_id)},
    }
    if mode == "at":
        post["dueAt"] = due_at
    if mode == "draft":
        post["saveToDraft"] = True
    answer = call("mutation($input: CreatePostInput!) { createPost(input: $input) { __typename "
                  f"... on PostActionSuccess {{ post {{ {POST_FIELDS} }} }} ... on MutationError {{ message }} }} }}",
                  {"input": post}, kind)
    result = _data(answer)["createPost"]
    if not result.get("post"):
        raise BufferError(f"Buffer refused the post: {result.get('message') or result.get('__typename')}")
    return _post(result["post"])


def posts(ids: list[str], kind: str) -> dict[str, dict | None]:
    """The current state of several posts in as few requests as possible (the 30-day limit is
    counted in requests). None for a post Buffer no longer has."""
    found: dict[str, dict | None] = {}
    for start in range(0, len(ids), BATCH):
        group = ids[start:start + BATCH]
        query = "query(" + ", ".join(f"$p{i}: PostId!" for i in range(len(group))) + ") { " + " ".join(
            f"p{i}: post(input: {{id: $p{i}}}) {{ {POST_FIELDS} }}" for i in range(len(group))) + " }"
        answer = call(query, {f"p{i}": post_id for i, post_id in enumerate(group)}, kind)
        data = answer.get("data") or {}
        gone = {str((e.get("path") or [""])[0]) for e in answer.get("errors") or []
                if (e.get("extensions") or {}).get("code") == "NOT_FOUND"}
        for i, post_id in enumerate(group):
            node = data.get(f"p{i}")
            if node:
                found[post_id] = _post(node)
            elif f"p{i}" in gone:
                found[post_id] = None
            elif answer.get("data") is None:
                # One missing post nulls the whole answer and only the first error is reported:
                # ask about the others one by one (rare: only after a post was deleted in Buffer).
                found.update(_one(post_id, kind))
            # anything else (a server error on that field): leave it out, and ask again later
    return found


def _one(post_id: str, kind: str) -> dict[str, dict | None]:
    answer = call(f"query($p: PostId!) {{ post(input: {{id: $p}}) {{ {POST_FIELDS} }} }}", {"p": post_id}, kind)
    node = (answer.get("data") or {}).get("post")
    if node:
        return {post_id: _post(node)}
    codes = {(e.get("extensions") or {}).get("code") for e in answer.get("errors") or []}
    return {post_id: None} if "NOT_FOUND" in codes else {}


def delete_post(post_id: str, kind: str) -> None:
    answer = call("mutation($input: DeletePostInput!) { deletePost(input: $input) { __typename "
                  "... on DeletePostSuccess { id } ... on MutationError { message } } }",
                  {"input": {"id": post_id}}, kind)
    codes = {(e.get("extensions") or {}).get("code") for e in answer.get("errors") or []}
    if "NOT_FOUND" in codes:
        return          # already gone: that is what was asked
    result = _data(answer)["deletePost"]
    if result.get("__typename") != "DeletePostSuccess":
        raise BufferError(f"Buffer would not delete the post: {result.get('message') or result.get('__typename')}")
