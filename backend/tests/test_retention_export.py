"""The grace period of unapproved renders, and the export of approved videos."""

import os
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from clipper import jobs
from clipper.db import session_scope
from clipper.db.models import Script, Storyboard, StudioEvent, VoiceTake, as_utc, utcnow
from clipper.settings import Settings
from clipper.studio import export, retention


def words(text: str) -> list[dict]:
    return [{"w": w, "s": i * 0.4, "e": i * 0.4 + 0.3} for i, w in enumerate(text.split())]


def make_board(title="Why LEDs are blue", fmt="short", days_ago=0.0, review="pending", rendered=True) -> int:
    """A storyboard with its render on disk, as the animate job leaves it."""
    with session_scope() as db:
        script = Script(title=title, sections=[{"key": "a", "heading": "A", "text": "One two three."}])
        db.add(script)
        db.flush()
        take = VoiceTake(script_id=script.id, source="kokoro", voice="am_michael", script_hash="x", chosen=True)
        db.add(take)
        db.flush()
        board = Storyboard(script_id=script.id, take_id=take.id, format=fmt, style="ink", title=title,
                           words=words("One two three."), scenes=[{"start_word": 0, "lines": ["0 actor narrator"]}],
                           review=review)
        db.add(board)
        db.flush()
        if rendered:
            folder = retention.render_dir(board)
            folder.mkdir(parents=True)
            (folder / f"{fmt}.mp4").write_bytes(b"v" * 2048)
            (folder / "effects.wav").write_bytes(b"e" * 512)
            board.video_path = str(folder / f"{fmt}.mp4")
            board.rendered_at = utcnow() - timedelta(days=days_ago)
        return board.id


def test_deadline_counts_from_the_render_or_the_last_verdict(app_env):
    board_id = make_board(days_ago=10)
    with session_scope() as db:
        board = db.get(Storyboard, board_id)
        rendered = as_utc(board.rendered_at)        # SQLite hands back naive datetimes
        assert retention.deadline(board, 30) == rendered + timedelta(days=30)
        assert retention.deadline(board, 0) is None                 # 0 = keep forever
        board.review_at = rendered + timedelta(days=5)              # rejected five days later
        assert retention.deadline(board, 30) == rendered + timedelta(days=35)
        board.review = "approved"
        assert retention.deadline(board, 30) is None                # approved videos never expire
        board.review, board.video_path = "pending", None
        assert retention.deadline(board, 30) is None                # nothing rendered, nothing to delete


def test_sweep_deletes_only_renders_past_their_grace_period(app_env):
    old = make_board("Old pending", days_ago=31)
    rejected = make_board("Old rejected", days_ago=45, review="rejected")
    approved = make_board("Old approved", days_ago=90, review="approved")
    recent = make_board("Recent", days_ago=3)
    busy = make_board("Old but rendering again", days_ago=40)
    with session_scope() as db:
        board = db.get(Storyboard, busy)
        jobs.enqueue(db, "animate", "x", {"script_id": board.script_id, "storyboard_id": busy})
        folders = {b.id: retention.render_dir(b) for b in db.scalars(select(Storyboard))}

    assert retention.sweep(Settings(unapproved_keep_days=0)) == []          # switched off
    assert all(f.is_dir() for f in folders.values())

    assert sorted(retention.sweep(Settings())) == [old, rejected]
    assert not folders[old].exists() and not folders[rejected].exists()     # video and effects track gone
    assert all((folders[i] / "short.mp4").is_file() for i in (approved, recent, busy))
    with session_scope() as db:
        board = db.get(Storyboard, rejected)
        assert board.video_path is None and board.review == "pending" and board.expired_at is not None
        assert board.scenes and board.words                                 # the plan is kept
        assert db.get(Storyboard, approved).video_path and db.get(Storyboard, recent).video_path
        events = db.scalars(select(StudioEvent).where(StudioEvent.kind == "expired")).all()
        assert {e.storyboard_id for e in events} == {old, rejected} and all(e.by == "app" for e in events)
        assert "30 days" in events[0].summary
    assert retention.sweep(Settings()) == []                                # nothing left to do


def test_sweep_removes_the_render_of_a_replaced_plan(app_env):
    """Planning again clears video_path but leaves the file: it gets the same grace period."""
    stale, fresh = make_board("Replanned long ago"), make_board("Replanned today")
    with session_scope() as db:
        folders = [retention.render_dir(db.get(Storyboard, i)) for i in (stale, fresh)]
        for i in (stale, fresh):
            db.get(Storyboard, i).video_path = None
    long_ago = (utcnow() - timedelta(days=40)).timestamp()
    for f in folders[0].iterdir():
        os.utime(f, (long_ago, long_ago))
    assert retention.sweep(Settings()) == []
    assert not folders[0].exists() and folders[1].is_dir()


def test_sweep_refuses_a_video_path_outside_the_storyboard_folder(app_env):
    board_id = make_board("Unsafe legacy path", days_ago=40)
    sentinel = app_env / "must-not-delete.mp4"
    sentinel.write_bytes(b"keep me")
    with session_scope() as db:
        board = db.get(Storyboard, board_id)
        board.video_path = str(sentinel)

    assert retention.sweep(Settings()) == []
    assert sentinel.read_bytes() == b"keep me"
    with session_scope() as db:
        board = db.get(Storyboard, board_id)
        assert board.video_path == str(sentinel)
        assert board.expired_at is None


def test_export_needs_approval_and_fills_the_output_folder(client, app_env):
    board_id = make_board("Why LEDs are blue: a 30-year hunt!", fmt="long", days_ago=2)
    out = app_env / "nextcloud"
    body = client.get("/api/v1/settings").json()["settings"]
    assert body["unapproved_keep_days"] == 30
    assert client.put("/api/v1/settings", json={**body, "output_dir": str(out)}).status_code == 200

    board = client.get(f"/api/v1/storyboards/{board_id}").json()
    assert board["expires_at"] is not None and board["export"] is None
    assert client.post(f"/api/v1/storyboards/{board_id}/export").status_code == 409     # not approved yet
    assert not (out / "studio").exists()

    board = client.patch(f"/api/v1/storyboards/{board_id}", json={"review": "approved"}).json()
    assert board["expires_at"] is None
    client.put(f"/api/v1/storyboards/{board_id}/youtube",
               json={"title": "Why LEDs are blue", "description": "It took thirty years.", "tags": ["led", "blue led"]})
    info = client.post(f"/api/v1/storyboards/{board_id}/export")
    assert info.status_code == 200, info.text
    info = info.json()
    name = f"Why-LEDs-are-blue-a-30-year-hunt-{board_id}"
    folder = out / "studio" / name
    assert info["folder"] == str(folder) and not info["stale"]
    assert info["files"] == [f"{name}.mp4", f"{name}.srt", "youtube.txt", "authorship.txt"]
    assert sorted(p.name for p in folder.iterdir()) == sorted(info["files"])            # no temporary file left
    assert (folder / f"{name}.mp4").read_bytes() == b"v" * 2048
    assert "One two three." in (folder / f"{name}.srt").read_text()
    text = (folder / "youtube.txt").read_text()
    assert text.startswith("TITLE\nWhy LEDs are blue\n") and "TAGS\nled, blue led" in text
    assert "Watched and approved" in (folder / "authorship.txt").read_text()

    # Rendered again: the copy in the output folder is of the earlier render, and says so.
    with session_scope() as db:
        db.get(Storyboard, board_id).rendered_at = utcnow()
    assert client.get(f"/api/v1/storyboards/{board_id}").json()["export"]["stale"] is True

    # Taking the approval back restarts the grace period from now, not from the render.
    board = client.patch(f"/api/v1/storyboards/{board_id}", json={"review": "pending"}).json()
    assert board["expires_at"] > (utcnow() + timedelta(days=29)).isoformat()
    assert client.post(f"/api/v1/storyboards/{board_id}/export").status_code == 409


def test_a_short_exports_no_subtitle_file_and_a_missing_render_says_so(client, app_env):
    board_id = make_board("Été à Paris", review="approved")
    with session_scope() as db:
        board = db.get(Storyboard, board_id)
        info = export.run(db, board, Settings())
        assert info["files"] == [f"Ete-a-Paris-{board_id}.mp4", "authorship.txt"]       # captions are burned in
        assert Path(info["folder"]).parent == app_env / "clips" / "studio"              # the default output folder
        Path(board.video_path).unlink()
    answer = client.post(f"/api/v1/storyboards/{board_id}/export")
    assert answer.status_code == 409 and "Render the video again" in answer.json()["detail"]
