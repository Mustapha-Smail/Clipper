"""Scheduling approved Shorts on Buffer: the gate, the job, and what happens afterwards.

Buffer and the bucket are replaced by fakes: no test touches the network.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from clipper import buffer, hosting, jobs, posting
from clipper.db import session_scope
from clipper.db.models import Clip, Moment, Post, utcnow

TIKTOK = {"id": "chan-tiktok", "service": "tiktok", "name": "mychannel", "type": "account", "timezone": "Europe/Paris",
          "weekly_slots": 14, "disconnected": False, "locked": False, "queue_paused": False}
INSTAGRAM = {**TIKTOK, "id": "chan-insta", "service": "instagram"}


class Log(jobs.Reporter):
    def __init__(self):
        self.lines = []

    def info(self, message):
        self.lines.append(message)

    def warn(self, message):
        self.lines.append("WARN " + message)


@pytest.fixture()
def ready(monkeypatch):
    monkeypatch.setenv("BUFFER_API_KEY", "test-key")
    for name in hosting.REQUIRED:
        monkeypatch.setenv(name, "https://pub-test.r2.dev" if name == "R2_PUBLIC_URL" else "x")


@pytest.fixture()
def clip(video, app_env):
    """An approved vertical clip of the first moment, with a file on disk."""
    file = app_env / "clip.mp4"
    file.write_bytes(b"video" * 1000)
    with session_scope() as db:
        moment = db.scalars(select(Moment).order_by(Moment.position)).first()
        row = Clip(video_id=video, moment_id=moment.id, path=str(file), seconds=30, size_bytes=5000, status="approved")
        db.add(row)
        db.flush()
        return row.id


@pytest.fixture()
def world(monkeypatch):
    """A fake bucket and a fake Buffer that remember what they were asked."""
    state = {"bucket": {}, "posts": {}, "refuse": set(), "deleted": []}

    def put(path, key, progress=None):
        state["bucket"][key] = path.read_bytes()
        return hosting.public_url(key)

    def create_post(channel_id, service, text, video_url, mode, due_at=None, ai_generated=False):
        if service in state["refuse"]:
            raise buffer.BufferError("Buffer refused the post: Failed to fetch video")
        post_id = f"buf-{len(state['posts']) + 1}"
        status = "draft" if mode == "draft" else "scheduled"
        state["posts"][post_id] = {"id": post_id, "status": status, "due_at": due_at or "2026-12-01T17:00:00.000Z",
                                   "sent_at": None, "link": None, "error": None, "service": service,
                                   "text": text, "url": video_url, "ai": ai_generated}
        return state["posts"][post_id]

    monkeypatch.setattr(hosting, "put", put)
    monkeypatch.setattr(hosting, "check_public", lambda url: None)
    monkeypatch.setattr(hosting, "delete", lambda key: state["bucket"].pop(key, None))
    monkeypatch.setattr(buffer, "create_post", create_post)
    monkeypatch.setattr(buffer, "posts", lambda ids: {i: state["posts"].get(i) for i in ids})
    monkeypatch.setattr(buffer, "delete_post", lambda post_id: state["deleted"].append(post_id))
    return state


def ask(clip_id, channels=(TIKTOK,), **options):
    options = {"text": "A caption #tag", "mode": "queue", "due_at": None, "ai_label": False, **options}
    with session_scope() as db:
        job = posting.request(db, "clip", clip_id, list(channels), **options)
        return job.params["post_ids"]


def statuses():
    with session_scope() as db:
        return [p.status for p in db.scalars(select(Post).order_by(Post.id))]


# --- the pieces that talk to the outside -------------------------------------------------

def test_requests_are_signed_like_the_published_aws_examples():
    key, secret = "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    now = datetime(2013, 5, 24, tzinfo=UTC)
    empty = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    get = hosting.sign("GET", "examplebucket.s3.amazonaws.com", "/test.txt", {"Range": "bytes=0-9"}, empty,
                       key, secret, now, "us-east-1")
    assert get.endswith("Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41")
    put = hosting.sign("PUT", "examplebucket.s3.amazonaws.com", "/test%24file.text",
                       {"Date": "Fri, 24 May 2013 00:00:00 GMT", "x-amz-storage-class": "REDUCED_REDUNDANCY"},
                       "44ce7dd67c959e0d3524ffac1771dfbba87d2b6b4b4e99e42034a8b803f8b072",
                       key, secret, now, "us-east-1")
    assert put.endswith("Signature=98ad721746da40c64f1a55b78f14c238d841ea1380cd77a1b5971af0ece108bd")


def test_hosted_names_cannot_be_guessed_and_setup_gaps_are_named(monkeypatch):
    for name in hosting.REQUIRED:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("BUFFER_API_KEY", raising=False)
    assert posting.readiness() == {"ready": False, "buffer_key": False, "hosting": False,
                                   "missing": ["BUFFER_API_KEY", *hosting.REQUIRED],
                                   "services": ["instagram", "tiktok"]}
    first, second = hosting.new_key("Le noyé de Bertioga !"), hosting.new_key("Le noyé de Bertioga !")
    assert first != second and first.startswith("posts/") and first.endswith("/le-noy-de-bertioga.mp4")
    assert len(first.split("/")[1]) >= 20
    monkeypatch.setenv("R2_PUBLIC_URL", "http://insecure.example")
    with pytest.raises(hosting.HostingError, match="https"):
        hosting.public_url(first)


def test_buffer_posts_carry_what_each_network_needs(monkeypatch):
    sent = []

    def call(query, variables=None):
        sent.append(variables["input"])
        return {"data": {"createPost": {"__typename": "PostActionSuccess", "post": {
            "id": "p1", "status": "scheduled", "dueAt": "2026-12-01T17:00:00.000Z", "sentAt": None,
            "externalLink": None, "error": None}}}}

    monkeypatch.setattr(buffer, "call", call)
    made = buffer.create_post("c1", "instagram", "Hello", "https://pub.r2.dev/a.mp4", "at",
                              "2026-12-01T17:00:00.000Z", ai_generated=True)
    assert made == {"id": "p1", "status": "scheduled", "due_at": "2026-12-01T17:00:00.000Z", "sent_at": None,
                    "link": None, "error": None}
    assert sent[0]["metadata"] == {"instagram": {"type": "reel", "shouldShareToFeed": True, "isAiGenerated": True}}
    assert sent[0]["mode"] == "customScheduled" and sent[0]["dueAt"] == "2026-12-01T17:00:00.000Z"
    assert sent[0]["assets"] == [{"video": {"url": "https://pub.r2.dev/a.mp4"}}]

    buffer.create_post("c2", "tiktok", "Hello", "https://pub.r2.dev/a.mp4", "draft")
    assert sent[1]["metadata"] == {"tiktok": {"isAiGenerated": False}}
    assert sent[1]["mode"] == "addToQueue" and sent[1]["saveToDraft"] is True and "dueAt" not in sent[1]
    with pytest.raises(buffer.BufferError, match="not supported"):
        buffer.create_post("c3", "youtube", "Hello", "https://pub.r2.dev/a.mp4", "queue")

    monkeypatch.setattr(buffer, "call", lambda q, v=None: {"data": {"createPost": {
        "__typename": "InvalidInputError", "message": "Failed to fetch video"}}})
    with pytest.raises(buffer.BufferError, match="Failed to fetch video"):
        buffer.create_post("c1", "tiktok", "Hello", "https://pub.r2.dev/a.mp4", "queue")


def test_reading_posts_is_one_request_and_knows_a_deleted_post(monkeypatch):
    calls = []

    def call(query, variables=None):
        calls.append(variables)
        return {"data": {"p0": {"id": "a", "status": "sent", "dueAt": None, "sentAt": "2026-12-01T17:00:05.000Z",
                                "externalLink": "https://tiktok.example/v/1", "error": None},
                         "p1": None, "p2": None},
                "errors": [{"path": ["p1"], "extensions": {"code": "NOT_FOUND"}},
                           {"path": ["p2"], "extensions": {"code": "UNEXPECTED"}}]}

    monkeypatch.setattr(buffer, "call", call)
    found = buffer.posts(["a", "b", "c"])
    assert len(calls) == 1 and calls[0] == {"p0": "a", "p1": "b", "p2": "c"}
    assert found["a"]["status"] == "sent" and found["a"]["link"] == "https://tiktok.example/v/1"
    assert found["b"] is None            # deleted in Buffer
    assert "c" not in found              # a server error: unknown, asked again later


def test_one_deleted_post_does_not_hide_the_others_of_its_batch(monkeypatch):
    """Seen live: Buffer nulls the whole answer and reports only the first missing post."""
    asked = []

    def call(query, variables=None):
        asked.append(variables)
        if len(variables) > 1:
            return {"data": None, "errors": [{"path": ["p1"], "extensions": {"code": "NOT_FOUND"}}]}
        if variables["p"] == "a":
            return {"data": {"post": {"id": "a", "status": "scheduled", "dueAt": "2026-12-01T17:00:00.000Z",
                                      "sentAt": None, "externalLink": None, "error": None}}}
        return {"data": None, "errors": [{"path": ["post"], "extensions": {"code": "NOT_FOUND"}}]}

    monkeypatch.setattr(buffer, "call", call)
    found = buffer.posts(["a", "b", "c"])
    assert found["a"]["status"] == "scheduled" and found["b"] is None and found["c"] is None
    assert asked[1:] == [{"p": "a"}, {"p": "c"}]          # one batch, then only the unresolved ones


# --- the gate ---------------------------------------------------------------------------

def test_only_an_approved_vertical_clip_can_be_scheduled(ready, clip, world):
    with session_scope() as db:
        db.get(Clip, clip).status = "pending"
    with pytest.raises(posting.PostingError, match="approved"):
        ask(clip)
    with session_scope() as db:
        row = db.get(Clip, clip)
        row.status, row.format = "approved", "horizontal"
    with pytest.raises(posting.PostingError, match="vertical"):
        ask(clip)
    with session_scope() as db:
        db.get(Clip, clip).format = "vertical"
    with pytest.raises(posting.PostingError, match="5 minutes"):
        ask(clip, mode="at", due_at=utcnow() + timedelta(minutes=1))
    with pytest.raises(posting.PostingError, match="disconnected"):
        ask(clip, channels=[{**TIKTOK, "disconnected": True}])
    with pytest.raises(posting.PostingError, match="too long"):
        ask(clip, text="x" * 2201)
    assert statuses() == []              # nothing was queued by any of the refusals

    assert len(ask(clip, channels=[TIKTOK, INSTAGRAM])) == 2
    with pytest.raises(posting.PostingError, match="already has a post waiting"):
        ask(clip)


def test_nothing_is_set_up_nothing_is_queued(clip, monkeypatch):
    monkeypatch.delenv("BUFFER_API_KEY", raising=False)
    with pytest.raises(posting.PostingError, match="BUFFER_API_KEY"):
        ask(clip)


def test_approval_is_checked_again_when_the_job_starts(ready, clip, world):
    ids = ask(clip)
    with session_scope() as db:
        db.get(Clip, clip).status = "pending"      # as a re-render would
    with pytest.raises(posting.PostingError, match="approved"):
        posting.upload(ids, Log())
    assert statuses() == ["failed"] and world["bucket"] == {} and world["posts"] == {}


# --- the job -----------------------------------------------------------------------------

def test_one_upload_serves_every_channel_and_a_refusal_does_not_stop_the_others(ready, clip, world):
    world["refuse"].add("instagram")
    ids = ask(clip, channels=[TIKTOK, INSTAGRAM], ai_label=True)
    log = Log()
    posting.upload(ids, log)
    assert len(world["bucket"]) == 1
    message = posting.schedule(ids, log)
    assert statuses() == ["scheduled", "failed"] and "1 scheduled, 1 refused" in message
    made = world["posts"]["buf-1"]
    assert made["service"] == "tiktok" and made["text"] == "A caption #tag" and made["ai"] is True
    assert made["url"].startswith("https://pub-test.r2.dev/posts/")
    assert any(line.startswith("WARN Instagram") for line in log.lines)
    assert len(world["bucket"]) == 1                 # TikTok still needs the video


def test_when_buffer_takes_nothing_the_job_fails_and_the_video_comes_down(ready, clip, world):
    world["refuse"].add("tiktok")
    ids = ask(clip)
    posting.upload(ids, Log())
    with pytest.raises(posting.PostingError, match="none of the posts"):
        posting.schedule(ids, Log())
    assert statuses() == ["failed"] and world["bucket"] == {}


def test_a_sent_post_gets_its_link_and_the_hosted_copy_is_removed(ready, clip, world):
    ids = ask(clip, mode="at", due_at=utcnow() + timedelta(hours=1))
    posting.upload(ids, Log())
    posting.schedule(ids, Log())
    assert posting.sync(due_only=True) == 0          # not due yet: Buffer is not even asked about it
    world["posts"]["buf-1"].update(status="sent", sent_at="2026-12-01T17:00:09.000Z",
                                   link="https://www.tiktok.com/@me/video/1")
    with session_scope() as db:
        db.get(Post, ids[0]).due_at = utcnow() - timedelta(minutes=1)
    log = Log()
    assert posting.sync(log, due_only=True) == 1
    with session_scope() as db:
        post = db.get(Post, ids[0])
        assert post.status == "sent" and post.link.endswith("/video/1") and post.media_key is None
        assert post.sent_at is not None
    assert world["bucket"] == {} and "was posted" in log.lines[0]


def test_a_post_deleted_in_buffer_is_noticed_by_the_full_pass(ready, clip, world):
    ids = ask(clip, mode="draft")
    posting.upload(ids, Log())
    posting.schedule(ids, Log())
    assert statuses() == ["draft"]
    world["posts"].clear()
    assert posting.sync() == 1
    assert statuses() == ["removed"] and world["bucket"] == {}


def test_cancelling_deletes_the_post_on_buffer_and_the_video(ready, clip, world, client):
    ids = ask(clip)
    posting.upload(ids, Log())
    posting.schedule(ids, Log())
    refused = client.patch(f"/api/v1/clips/{clip}", json={"status": "rejected"})
    assert refused.status_code == 409 and "Cancel it" in refused.json()["detail"]

    cancelled = client.delete(f"/api/v1/posts/{ids[0]}").json()
    assert cancelled["status"] == "cancelled" and cancelled["hosted"] is False
    assert world["deleted"] == ["buf-1"] and world["bucket"] == {}
    assert client.delete(f"/api/v1/posts/{ids[0]}").status_code == 409
    assert client.patch(f"/api/v1/clips/{clip}", json={"status": "rejected"}).status_code == 200


def test_a_post_cancelled_before_the_worker_ran_never_reaches_buffer(ready, clip, world):
    ids = ask(clip)
    with session_scope() as db:
        posting.cancel(db, db.get(Post, ids[0]))
    log = Log()
    posting.upload(ids, log)
    assert posting.schedule(ids, log) == "Done: nothing was left to schedule"
    assert world["bucket"] == {} and world["posts"] == {} and world["deleted"] == []


# --- the API -----------------------------------------------------------------------------

def test_scheduling_through_the_api(ready, clip, world, client, monkeypatch):
    from clipper.api import posts as posts_api

    monkeypatch.setattr(buffer, "channels", lambda: [TIKTOK, {**TIKTOK, "id": "chan-yt", "service": "youtube"}])
    posts_api._channels.update(at=0.0, when=None, list=[])
    assert client.get("/api/v1/posting").json()["ready"] is True
    assert client.get("/api/v1/system/status").json()["posting"]["ready"] is True
    listed = client.get("/api/v1/posting/channels").json()
    assert [c["id"] for c in listed["channels"]] == ["chan-tiktok"] and listed["others"] == ["youtube"]

    draft = client.get("/api/v1/posting/draft", params={"kind": "clip", "ref_id": clip}).json()
    assert draft == {"text": "One", "ai_label": False, "allowed": True, "reason": ""}

    unknown = client.post("/api/v1/posts", json={"kind": "clip", "ref_id": clip, "channel_ids": ["nope"]})
    assert unknown.status_code == 400
    job = client.post("/api/v1/posts", json={"kind": "clip", "ref_id": clip, "channel_ids": ["chan-tiktok"],
                                             "text": draft["text"], "mode": "queue"})
    assert job.status_code == 202 and job.json()["kind"] == "post"
    assert job.json()["stages"] == ["upload", "schedule"]
    listed = client.get("/api/v1/posts", params={"clip_id": clip}).json()
    assert [(p["status"], p["service"], p["title"]) for p in listed] == [("queued", "tiktok", "First")]
    again = client.post("/api/v1/posts", json={"kind": "clip", "ref_id": clip, "channel_ids": ["chan-tiktok"]})
    assert again.status_code == 409 and "already has a post waiting" in again.json()["detail"]
