"""Wave-one strategy compatibility and daily metric observation contracts."""

from datetime import UTC, datetime, timedelta

from alembic import command
from sqlalchemy import inspect, select, text

from clipper import db as db_mod
from clipper import posting
from clipper.db import session_scope
from clipper.db.models import Idea, Post, Script, Storyboard, VideoMetricSnapshot, record_metric_snapshot
from clipper.studio import voice


def test_strategy_migration_backfills_legacy_values_and_keeps_shape_unknown(app_env):
    """An upgraded 2.6 row retains runtime intent without inventing a story shape."""
    with session_scope() as db:
        idea = Idea(title="Old short", video="short", story_shape="parallel")
        db.add(idea)
        db.flush()
        script = Script(idea_id=idea.id, title="Old long", video="long", story_shape="explain", sections=[])
        db.add(script)
        db.flush()
        board = Storyboard(script_id=script.id, format="long", story_shape="scorecard")
        db.add(board)
        db.flush()
        ids = idea.id, script.id, board.id

    command.downgrade(db_mod._alembic_config(), "f4b92d7186ce")
    before = inspect(db_mod.get_engine())
    assert "video_metric_snapshots" not in before.get_table_names()
    assert "form" not in {column["name"] for column in before.get_columns("ideas")}

    command.upgrade(db_mod._alembic_config(), "head")
    after = inspect(db_mod.get_engine())
    assert {"form", "story_shape", "parent_idea_id", "lineage_kind"} <= {
        column["name"] for column in after.get_columns("ideas")}
    assert {"form", "story_shape", "caption_mode"} <= {
        column["name"] for column in after.get_columns("storyboards")}
    assert "video_metric_snapshots" in after.get_table_names()
    with db_mod.get_engine().connect() as conn:
        idea = conn.execute(text("SELECT form, story_shape, lineage_kind FROM ideas WHERE id = :id"),
                            {"id": ids[0]}).one()
        script = conn.execute(text("SELECT form, story_shape FROM scripts WHERE id = :id"), {"id": ids[1]}).one()
        board = conn.execute(text("SELECT form, story_shape, caption_mode FROM storyboards WHERE id = :id"),
                             {"id": ids[2]}).one()
    assert idea == ("short", None, "root")
    assert script == ("long", None)
    assert board == ("long", None, "default")


def test_form_shape_and_caption_mode_copy_at_each_creation_boundary(client, app_env):
    """Form maps to the old video field while intent is copied, never linked live."""
    with session_scope() as db:
        idea = Idea(title="The same start", theme="A", format="single", video="long", form="long")
        db.add(idea)
        db.flush()
        idea_id = idea.id

    changed = client.patch(f"/api/v1/ideas/{idea_id}", json={"form": "micro_short", "story_shape": "parallel"})
    assert changed.status_code == 200
    assert changed.json()["form"] == "micro_short"
    assert changed.json()["video"] == "short"
    assert changed.json()["story_shape"] == "parallel"

    started = client.post(f"/api/v1/ideas/{idea_id}/script", json={"target_seconds": 20})
    assert started.status_code == 202
    payload = started.json()
    assert payload["form"] == "micro_short" and payload["video"] == "short"
    assert payload["story_shape"] == "parallel"
    script_id = payload["id"]

    with session_scope() as db:
        script = db.get(Script, script_id)
        script.sections = [{"key": "a", "heading": "Open", "text": "Same start.", "short": True}]
        take = voice_take(script)
        db.add(take)
        db.flush()
        folder = voice.take_dir(script.id, take.id)
        folder.mkdir(parents=True)
        (folder / "timeline.json").write_text(
            '[{"section":"a","w":"Same","s":0.1,"e":0.4},{"section":"a","w":"start.","s":0.5,"e":0.8}]')

    made = client.post(f"/api/v1/scripts/{script_id}/storyboards",
                       json={"format": "short", "section_key": "a", "caption_mode": "off"})
    assert made.status_code == 202
    board = made.json()
    assert (board["form"], board["story_shape"], board["caption_mode"], board["format"]) == \
        ("micro_short", "parallel", "off", "short")

    # Later idea edits do not rewrite a started script or storyboard.
    client.patch(f"/api/v1/ideas/{idea_id}", json={"video": "long", "story_shape": None})
    with session_scope() as db:
        assert (db.get(Script, script_id).form, db.get(Script, script_id).story_shape) == ("micro_short", "parallel")
        assert (db.get(Storyboard, board["id"]).form, db.get(Storyboard, board["id"]).story_shape) == \
            ("micro_short", "parallel")


def voice_take(script: Script):
    from clipper.db.models import VoiceTake

    return VoiceTake(script_id=script.id, source="kokoro", voice="test", path="/voice.wav", duration=1.5,
                     script_hash="current", chosen=True,
                     alignment={"coverage": 1.0, "sections": [], "missing": [], "extra": [],
                                "spoken_words": 2, "script_hash": "current", "wpm": 120})


def test_snapshot_retries_are_idempotent_and_partial_youtube_updates_do_not_erase_counts(app_env):
    with session_scope() as db:
        script = Script(title="S", sections=[])
        db.add(script)
        db.flush()
        board = Storyboard(script_id=script.id)
        db.add(board)
        db.flush()
        at = datetime(2026, 11, 21, 10, tzinfo=UTC)
        record_metric_snapshot(db, storyboard_id=board.id, source_key="youtube:video", service="youtube",
                               observed_at=at, views=100, likes=7, comments=3)
        # The Analytics path has no public likes/comments fields. It must update
        # its supplied watch data without destroying the Data API observation.
        record_metric_snapshot(db, storyboard_id=board.id, source_key="youtube:video", service="youtube",
                               observed_at=at + timedelta(hours=2), views=120, minutes_watched=12.5,
                               average_view_s=6.0, average_view_pct=42.0)
        record_metric_snapshot(db, storyboard_id=board.id, source_key="youtube:video", service="youtube",
                               observed_at=at + timedelta(days=1), views=None)

    with session_scope() as db:
        rows = list(db.scalars(select(VideoMetricSnapshot).order_by(VideoMetricSnapshot.observed_day)))
    assert len(rows) == 2
    assert (rows[0].views, rows[0].likes, rows[0].comments, rows[0].minutes_watched) == (120, 7, 3, 12.5)
    assert rows[1].views is None and rows[1].likes is None


def test_buffer_metric_refresh_writes_one_snapshot_per_source_and_day(app_env, monkeypatch):
    monkeypatch.setenv("BUFFER_STUDIO_API_KEY", "key")
    with session_scope() as db:
        script = Script(title="S", sections=[])
        db.add(script)
        db.flush()
        board = Storyboard(script_id=script.id)
        db.add(board)
        db.flush()
        db.add(Post(kind="storyboard", storyboard_id=board.id, channel_id="channel", service="tiktok",
                    status="sent", buffer_id="buffer-1"))

    monkeypatch.setattr(posting.buffer, "metrics", lambda ids, kind: {
        "buffer-1": {"at": "2026-11-21T10:00:00Z", "values": {"views": 10, "likes": 2}}})
    assert posting.sync_metrics(force=True) == 1
    monkeypatch.setattr(posting.buffer, "metrics", lambda ids, kind: {
        "buffer-1": {"at": "2026-11-21T11:00:00Z", "values": {"views": 12}}})
    assert posting.sync_metrics(force=True) == 1
    with session_scope() as db:
        rows = list(db.scalars(select(VideoMetricSnapshot)))
    assert len(rows) == 1
    assert (rows[0].source_key, rows[0].service, rows[0].views, rows[0].likes) == \
        ("buffer:buffer-1", "tiktok", 12, None)
