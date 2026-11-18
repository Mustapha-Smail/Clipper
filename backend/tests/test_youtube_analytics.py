"""Connect YouTube (OAuth with PKCE, the stored refresh token) and YouTube Analytics in Learning."""

import base64
import hashlib
import stat
import urllib.parse
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from clipper.db import session_scope
from clipper.db.models import Idea, Post, Script, Storyboard, Theme, VideoAnalytics, utcnow
from clipper.studio import analytics, learning, youtube_auth
from clipper.studio import script as script_mod

SECRET = "1//refresh-token-that-must-never-leak"


@pytest.fixture()
def oauth(app_env, monkeypatch):
    monkeypatch.setenv("YOUTUBE_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setenv("YOUTUBE_OAUTH_CLIENT_SECRET", "client-secret")
    monkeypatch.delenv("YOUTUBE_OAUTH_REDIRECT_URI", raising=False)
    youtube_auth._pending.clear()
    youtube_auth._access.clear()
    youtube_auth.last_error = None
    posted = []

    def fake_post(url, form):
        posted.append((url, dict(form)))
        if url == youtube_auth.REVOKE_URL:
            return 200, {}
        if form["grant_type"] == "authorization_code":
            return 200, {"access_token": "access-1", "expires_in": 3599, "refresh_token": SECRET,
                         "scope": " ".join(youtube_auth.SCOPES)}
        return 200, {"access_token": "access-2", "expires_in": 3599}

    monkeypatch.setattr(youtube_auth, "_post", fake_post)
    monkeypatch.setattr(youtube_auth, "get_json", lambda url, token: {
        "items": [{"id": "UCmine", "snippet": {"title": "My channel"}}]})
    yield posted
    youtube_auth._pending.clear()
    youtube_auth._access.clear()


def _state_of(url: str) -> dict:
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


# --- OAuth ---------------------------------------------------------------------------------------

def test_connect_url_carries_state_pkce_and_both_read_only_scopes(oauth):
    query = _state_of(youtube_auth.start())
    verifier = youtube_auth._pending[query["state"]][0]
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert query["code_challenge"] == expected and query["code_challenge_method"] == "S256"
    assert 43 <= len(verifier) <= 128
    assert query["scope"].split() == list(youtube_auth.SCOPES) and query["access_type"] == "offline"
    assert query["redirect_uri"] == "http://localhost:8000/api/v1/youtube/callback"
    assert query["response_type"] == "code" and query["client_id"] == "client-id"


def test_a_state_is_used_once_and_expires(oauth, monkeypatch):
    state = _state_of(youtube_auth.start())["state"]
    youtube_auth.take_state(state)
    with pytest.raises(youtube_auth.AuthError, match="already used"):
        youtube_auth.take_state(state)
    with pytest.raises(youtube_auth.AuthError):
        youtube_auth.take_state("made-up")
    state = _state_of(youtube_auth.start())["state"]
    later = youtube_auth.time.monotonic() + youtube_auth.STATE_TTL + 5
    monkeypatch.setattr(youtube_auth.time, "monotonic", lambda: later)
    with pytest.raises(youtube_auth.AuthError, match="10 minutes"):
        youtube_auth.take_state(state)


def test_not_configured_is_said_plainly(app_env, monkeypatch):
    monkeypatch.delenv("YOUTUBE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("YOUTUBE_OAUTH_CLIENT_SECRET", raising=False)
    with pytest.raises(youtube_auth.AuthError, match="YOUTUBE_OAUTH_CLIENT_ID"):
        youtube_auth.start()
    assert youtube_auth.status()["configured"] is False


def test_finish_keeps_only_the_refresh_token_owner_only_and_never_shows_it(oauth):
    state = _state_of(youtube_auth.start())["state"]
    verifier = youtube_auth._pending[state][0]
    result = youtube_auth.finish("the-code", state)
    exchange = oauth[0][1]
    assert exchange["code_verifier"] == verifier and exchange["grant_type"] == "authorization_code"
    assert exchange["redirect_uri"] == "http://localhost:8000/api/v1/youtube/callback"
    assert result["connected"] and result["channel_title"] == "My channel" and SECRET not in str(result)
    path = youtube_auth.token_path()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert SECRET in path.read_text() and "access-1" not in path.read_text()


def test_finish_refuses_missing_scopes_and_missing_refresh_token(oauth, monkeypatch):
    monkeypatch.setattr(youtube_auth, "_post", lambda url, form: (200, {
        "access_token": "a", "refresh_token": SECRET, "scope": youtube_auth.SCOPES[1]}))
    with pytest.raises(youtube_auth.AuthError, match="yt-analytics.readonly"):
        youtube_auth.finish("c", _state_of(youtube_auth.start())["state"])
    monkeypatch.setattr(youtube_auth, "_post", lambda url, form: (200, {
        "access_token": "a", "scope": " ".join(youtube_auth.SCOPES)}))
    with pytest.raises(youtube_auth.AuthError, match="no refresh token"):
        youtube_auth.finish("c", _state_of(youtube_auth.start())["state"])
    assert not youtube_auth.status()["connected"] and "refresh token" in youtube_auth.status()["problem"]


def test_access_token_is_cached_refreshed_and_an_expired_grant_asks_to_reconnect(oauth, monkeypatch):
    youtube_auth.finish("c", _state_of(youtube_auth.start())["state"])
    assert youtube_auth.access_token() == "access-1"            # from the exchange, still fresh
    youtube_auth._access["until"] = 0
    assert youtube_auth.access_token() == "access-2"
    refresh = oauth[-1][1]
    assert refresh == {"client_id": "client-id", "client_secret": "client-secret", "refresh_token": SECRET,
                       "grant_type": "refresh_token"}
    youtube_auth._access.clear()
    monkeypatch.setattr(youtube_auth, "_post", lambda url, form: (400, {"error": "invalid_grant"}))
    with pytest.raises(youtube_auth.AuthError, match="expired or was revoked"):
        youtube_auth.access_token()
    state = youtube_auth.status()
    assert state["needs_reconnect"] and not state["connected"] and state["channel_title"] == "My channel"
    assert SECRET not in youtube_auth.token_path().read_text()


def test_an_expiry_seven_days_after_connecting_mentions_testing_status(oauth, monkeypatch):
    youtube_auth.finish("c", _state_of(youtube_auth.start())["state"])
    data = youtube_auth._read()
    data["connected_at"] = (datetime.now(UTC) - timedelta(days=7)).isoformat()
    youtube_auth._write(data)
    youtube_auth.mark_problem("Google refused the saved connection.")
    assert "Testing" in youtube_auth.status()["problem"]


def test_disconnect_revokes_and_deletes(oauth):
    youtube_auth.finish("c", _state_of(youtube_auth.start())["state"])
    result = youtube_auth.disconnect()
    assert result["revoked"] and not result["connected"] and not youtube_auth.token_path().exists()
    assert oauth[-1] == (youtube_auth.REVOKE_URL, {"token": SECRET})


def test_routes_round_trip_and_never_return_the_token(client, oauth):
    assert client.get("/api/v1/youtube/status").json()["connected"] is False
    url = client.post("/api/v1/youtube/connect").json()["url"]
    state = _state_of(url)["state"]
    back = client.get(f"/api/v1/youtube/callback?code=abc&state={state}", follow_redirects=False)
    assert back.status_code == 303 and back.headers["location"] == "/settings?youtube=connected#studio-youtube"
    again = client.get(f"/api/v1/youtube/callback?code=abc&state={state}", follow_redirects=False)
    assert again.headers["location"].startswith("/settings?youtube=failed")
    shown = client.get("/api/v1/youtube/status")
    assert shown.json()["connected"] and shown.json()["channel_title"] == "My channel"
    assert SECRET not in shown.text
    declined = client.get("/api/v1/youtube/callback?error=access_denied&state=x", follow_redirects=False)
    assert declined.headers["location"].startswith("/settings?youtube=failed")
    gone = client.post("/api/v1/youtube/disconnect").json()
    assert gone["connected"] is False and gone["revoked"] is True


# --- matching and fetching -----------------------------------------------------------------------

SECTIONS = [{"key": "a", "heading": "Short", "short": True,
             "text": "Your fridge never made cold. But here is the twist. It moves heat. So it warms the room."}]
ROLES = ["hook", "rehook", "twist", "payoff"]


def _board(db, theme, title, *, link=None, url=None, held=False, buffer_post=True) -> Storyboard:
    script = Script(title=title, theme_id=theme.id, status="approved", sections=SECTIONS, video="short")
    if held:
        script.hold = {"hash": script_mod.text_hash(SECTIONS), "topic": "fridge", "checked_at": "now", "dropped": 0,
                       "roles": [{"n": n, "role": r, "title_question": False} for n, r in enumerate(ROLES, 1)]}
    db.add(script)
    db.flush()
    words = [{"w": w, "section": "a", "s": i * 0.5, "e": i * 0.5 + 0.4}
             for i, w in enumerate(SECTIONS[0]["text"].split())]
    board = Storyboard(script_id=script.id, title=title, format="short", review="approved", video_path="/v.mp4",
                       end=10.0, words=words, published_url=url, published_at=utcnow() if url else None)
    db.add(board)
    db.flush()
    if buffer_post:
        db.add(Post(kind="storyboard", storyboard_id=board.id, channel_id="c", service="youtube", status="sent",
                    buffer_id=f"b{board.id}", link=link, sent_at=utcnow() - timedelta(days=4),
                    metrics={"values": {"views": 100}}))
    return board


def _theme(db) -> Theme:
    theme = Theme(name="Science")
    db.add(theme)
    db.flush()
    return theme


def test_matching_uses_known_youtube_ids_and_names_what_cannot_be_matched(app_env):
    with session_scope() as db:
        theme = _theme(db)
        a = _board(db, theme, "Short on Buffer", link="https://youtube.com/shorts/AAAAAAAAAAA")
        b = _board(db, theme, "Long by hand", url="https://youtu.be/BBBBBBBBBBB", buffer_post=False)
        _board(db, theme, "No link yet", link=None)
        _board(db, theme, "Odd link", link="https://example.com/x")
        found, unmatched = analytics.targets(db)
    assert {(f["storyboard_id"], f["youtube_id"], f["source"]) for f in found} == {
        (a.id, "AAAAAAAAAAA", "buffer"), (b.id, "BBBBBBBBBBB", "linked")}
    assert {u["title"]: u["why"] for u in unmatched} == {"No link yet": "Buffer has not reported its YouTube link",
                                                         "Odd link": "Buffer's link is not a YouTube video"}


def test_watching_at_is_a_straight_line_between_the_nearest_points():
    curve = [[0.0, 1.0], [0.2, 0.8], [0.4, 0.4], [1.0, 0.2]]
    assert analytics.watching_at(curve, 0.1)[0] == pytest.approx(0.9)
    assert analytics.watching_at(curve, 0.3)[0] == pytest.approx(0.6)
    value, how = analytics.watching_at(curve, 0.2)
    assert value == pytest.approx(0.8) and "between" in how
    assert analytics.watching_at([[0.01, 1.1], [1.0, 0.3]], 0.0) == (1.1, "YouTube's first point, 1.10 at 1%")


class FakeYouTube:
    """The two Google endpoints analytics.py calls, answering from tables, recording each call."""

    def __init__(self):
        self.calls = []

    def __call__(self, url, params):
        self.calls.append((url, dict(params)))
        if url == analytics.VIDEOS_URL:
            known = {"AAAAAAAAAAA": ("UCmine", "PT10S"), "CCCCCCCCCCC": ("UCmine", "PT10S"),
                     "OOOOOOOOOOO": ("UCother", "PT30S")}
            return {"items": [{"id": v, "snippet": {"channelId": known[v][0], "publishedAt": "2026-11-10T10:00:00Z"},
                               "contentDetails": {"duration": known[v][1]}}
                              for v in params["id"].split(",") if v in known]}
        if params.get("dimensions") == "video":
            heads = ["video", *analytics.SUMMARY]
            rows = [["AAAAAAAAAAA", 1000, 120.0, 7.2, 72.0], ["CCCCCCCCCCC", 50, 4.0, 4.8, 48.0]]
            ids = params["filters"].removeprefix("video==").split(",")
            return {"columnHeaders": [{"name": h} for h in heads], "rows": [r for r in rows if r[0] in ids]}
        assert params["dimensions"] == "elapsedVideoTimeRatio" and ";audienceType==ORGANIC" in params["filters"]
        if "AAAAAAAAAAA" not in params["filters"]:
            return {"columnHeaders": [{"name": "elapsedVideoTimeRatio"}, {"name": "audienceWatchRatio"},
                                      {"name": "relativeRetentionPerformance"}]}        # no rows: no curve yet
        return {"columnHeaders": [{"name": "elapsedVideoTimeRatio"}, {"name": "audienceWatchRatio"},
                                  {"name": "relativeRetentionPerformance"}],
                "rows": [[1.0, 0.3, 0.5], [0.0, 1.0, 0.5], [0.25, 0.8, 0.6], [0.65, 0.55, 0.4]]}


def _connect(monkeypatch):
    monkeypatch.setenv("YOUTUBE_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setenv("YOUTUBE_OAUTH_CLIENT_SECRET", "client-secret")
    youtube_auth._write({"refresh_token": SECRET, "channel_id": "UCmine", "channel_title": "My channel",
                         "connected_at": datetime.now(UTC).isoformat()})


def test_refresh_batches_calls_and_learning_shows_retention_with_its_arithmetic(client, monkeypatch):
    _connect(monkeypatch)
    fake = FakeYouTube()
    monkeypatch.setattr(analytics, "_get", fake)
    with session_scope() as db:
        theme = _theme(db)
        held = _board(db, theme, "Held", link="https://youtube.com/shorts/AAAAAAAAAAA", held=True)
        _board(db, theme, "Unchecked", link="https://youtu.be/CCCCCCCCCCC")
        _board(db, theme, "Elsewhere", link="https://youtu.be/OOOOOOOOOOO")
        _board(db, theme, "No link", link=None)
        theme_id, held_id = theme.id, held.id

    class Rep(analytics.Reporter):
        lines: list = []

        def info(self, message):
            self.lines.append(("info", message))

        def warn(self, message):
            self.lines.append(("warn", message))

    rep = Rep()
    message = learning.refresh(rep)
    assert "watch time and retention for 2 on YouTube" in message
    kinds = [(url, p.get("dimensions")) for url, p in fake.calls]
    assert kinds.count((analytics.VIDEOS_URL, None)) == 1                         # one videos.list for all
    assert kinds.count((analytics.REPORTS_URL, "video")) == 1                     # one summary query for all
    assert kinds.count((analytics.REPORTS_URL, "elapsedVideoTimeRatio")) == 2     # one curve per own video
    said = " | ".join(m for _, m in rep.lines)
    assert "\"No link\" (Buffer has not reported its YouTube link)" in said
    assert "not on the connected channel: OOOOOOOOOOO" in said and "swipe-away share are not in this API" in said

    with session_scope() as db:
        rows = {r.youtube_id: r for r in db.scalars(select(VideoAnalytics))}
    assert rows["OOOOOOOOOOO"].note == "it is not on the connected channel" and rows["OOOOOOOOOOO"].views is None
    assert rows["AAAAAAAAAAA"].retention[0] == [0.0, 1.0, 0.5]                   # kept in order
    assert rows["CCCCCCCCCCC"].retention is None and "no retention curve" in rows["CCCCCCCCCCC"].note

    learnt = client.get(f"/api/v1/themes/{theme_id}/learning").json()
    videos = {v["title"]: v for v in learnt["videos"]}
    held_row = videos["Held"]
    # first twist (the rehook "But") is word 6 at 2.5 s of 10 s = 25%, a point of the curve: 0.80
    # the payoff ("So") is word 14 at 6.5 s of 10 s = 65%, another point: 0.55
    assert (held_row["avg_viewed"], held_row["kept_at_twist"], held_row["kept_at_payoff"]) == (72.0, 80, 55)
    lines = held_row["retention"]["lines"]
    assert "First twist at 2.5 s (word 6): 2.5 / 10 s = 25% in" in lines[2]
    assert lines[3].startswith("Payoff at 6.5 s (word 14)") and lines[3].endswith("55% still watching")
    unchecked = videos["Unchecked"]
    assert unchecked["avg_viewed"] == 48.0 and unchecked["kept_at_twist"] is None
    assert "no retention curve" in unchecked["retention"]["why"]["kept_at_twist"]
    assert videos["Elsewhere"]["retention"] is None
    assert learnt["stats_sources"]["analytics"] is True
    assert [u["title"] for u in learnt["unmatched"]] == ["No link"]

    with session_scope() as db:
        table = learning._video_table(learning.ranked(learning.published(db, theme_id))["videos"])
    assert "Watch: average viewed 72.0%, 80% still watching at the first twist, 55% still watching at the payoff" \
        in table
    assert "Watch: not measured" in table

    calls = len(fake.calls)
    assert analytics.refresh()["read"] == 0 and len(fake.calls) == calls      # at most once in 20 h
    analytics.refresh(force=True)
    assert len(fake.calls) > calls
    with session_scope() as db:
        assert db.get(Storyboard, held_id) is not None


def test_a_stale_hold_check_leaves_the_turns_blank_and_says_why(app_env, monkeypatch):
    _connect(monkeypatch)
    monkeypatch.setattr(analytics, "_get", FakeYouTube())
    with session_scope() as db:
        theme = _theme(db)
        board = _board(db, theme, "Edited", link="https://youtube.com/shorts/AAAAAAAAAAA", held=True)
        db.get(Script, board.script_id).sections = [{**SECTIONS[0], "text": SECTIONS[0]["text"] + " New end."}]
        theme_id = theme.id
    analytics.refresh()
    with session_scope() as db:
        row = learning.published(db, theme_id)[0]
    assert row["avg_viewed"] == 72.0 and row["kept_at_twist"] is None and row["kept_at_payoff"] is None
    assert "hold check is missing or out of date" in row["retention"]["why"]["kept_at_payoff"]


def test_not_connected_and_refused_connections_never_fail_silently(app_env, monkeypatch):
    assert analytics.refresh() == {"read": 0, "problem": None}
    _connect(monkeypatch)
    with session_scope() as db:
        _board(db, _theme(db), "Out", link="https://youtube.com/shorts/AAAAAAAAAAA")

    def refused(url, params):
        raise youtube_auth.AuthError("Google no longer accepts the connection")

    monkeypatch.setattr(analytics, "_get", refused)
    warned = []

    class Rep(analytics.Reporter):
        def warn(self, message):
            warned.append(message)

    result = analytics.refresh(Rep())
    assert result["problem"] == "YouTube needs reconnecting (Settings)" and "Reconnect" in warned[0]
    assert youtube_auth.status()["needs_reconnect"]
    assert "YouTube needs reconnecting" in learning.refresh()


def test_migration_goes_down_and_up(app_env):
    from alembic import command

    from clipper import db

    command.downgrade(db._alembic_config(), "d9f3b5a1c826")
    command.upgrade(db._alembic_config(), "head")
    with session_scope() as s:
        theme = Theme(name="T")
        s.add(theme)
        s.flush()
        s.add(Idea(title="i", theme=theme.name, theme_id=theme.id))
        assert s.scalars(select(VideoAnalytics)).all() == []
