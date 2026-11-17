"""Version 2: themes as the home of the work, statistics and lessons, ideas from what is proven, prompt files."""

from datetime import timedelta

import pytest
from idea_jobs import generate
from sqlalchemy import select

from clipper import buffer, posting, prompts
from clipper.db import session_scope
from clipper.db.models import Idea, Post, Script, Storyboard, Theme, VoiceTake, utcnow
from clipper.studio import ideas, learning, themes, youtube

# --- prompts --------------------------------------------------------------------------------

def test_every_prompt_file_parses_and_names_its_values():
    names = prompts.names()
    assert len(names) >= 40 and "idea_from_theme" in names and "theme_insights" in names
    for name in names:
        meta = prompts.meta(name)
        body = prompts.default(name)
        assert meta.title and meta.step in prompts.STEPS, name
        for variable in meta.variables:
            assert "{" + variable + "}" in body, (name, variable)
            assert len(meta.variable_help.get(variable, "")) > 15, (name, variable)   # explained on the page
        assert not prompts.problems(name, body), name
        assert "{prompt:" not in prompts.render(name), name       # every include resolves


def test_render_fills_known_values_only_and_includes_other_prompts():
    text = prompts.render("script_write", plain="PLAIN RULES")
    assert "PLAIN RULES" in text and prompts.default("script_spoken")[:40] in text
    assert "{plain}" not in text
    # braces that are not a value given stay as written
    assert prompts.render("idea_check") == prompts.default("idea_check")


def test_an_edit_wins_until_reset_and_cannot_break_the_prompt(app_env):
    original = str(prompts.Prompt("idea_generate"))
    with pytest.raises(prompts.PromptError, match="audience_rules} is missing"):
        prompts.save("idea_generate", "Only this. No audience.")
    with pytest.raises(prompts.PromptError, match="not a value the code fills in"):
        prompts.save("idea_generate", "For {audience_rules} and {theme_name}.")
    assert not prompts.overridden("idea_generate")
    assert prompts.save("idea_generate", "Only this, for {audience_rules}. Braces {like this} stay.") == []
    assert prompts.overridden("idea_generate")
    assert ideas.GENERATE.format(audience_rules="X") == "Only this, for X. Braces {like this} stay."   # call time
    assert "-" in prompts.diff("idea_generate")
    assert prompts.save("idea_generate", prompts.default("idea_generate")) == []    # the default again
    assert not prompts.overridden("idea_generate")
    prompts.reset("idea_generate")
    assert str(prompts.Prompt("idea_generate")) == original
    with pytest.raises(prompts.PromptError):
        prompts.save("idea_generate", "   ")
    with pytest.raises(prompts.PromptError):
        prompts.text("../settings")


def test_prompts_api(client):
    listed = client.get("/api/v1/prompts").json()
    assert listed[0]["step"] == "Themes and ideas" and all(p["text"] for p in listed)
    write = next(p for p in listed if p["name"] == "script_write")
    assert write["tier"] == "writing" and write["includes"] == ["script_spoken"] and not write["overridden"]
    assert write["variable_help"]["plain"].startswith("The plain-language rules")
    saved = client.put("/api/v1/prompts/script_write", json={"text": "Write well. {plain}"}).json()
    assert saved["overridden"] and saved["problems"] == []
    broken = client.put("/api/v1/prompts/script_write", json={"text": "Write well."})
    assert broken.status_code == 400 and "{plain} is missing" in broken.json()["detail"]
    reset = client.delete("/api/v1/prompts/script_write").json()
    assert not reset["overridden"] and reset["text"] == reset["default"]
    assert client.get("/api/v1/prompts/nope").status_code == 404


# --- themes ----------------------------------------------------------------------------------

def _theme(db, name="Odd history") -> Theme:
    return themes.create(db, name)


def test_the_board_follows_the_work_from_idea_to_published(app_env):
    with session_scope() as db:
        theme = _theme(db)
        db.add_all([Idea(title="Waiting", theme=theme.name, theme_id=theme.id, status="inbox"),
                    Idea(title="Kept", theme=theme.name, theme_id=theme.id, status="new")])
        writing = Script(title="Writing", theme_id=theme.id, sections=[])
        voice = Script(title="Voice", theme_id=theme.id, status="approved", sections=[])
        animating = Script(title="Animating", theme_id=theme.id, status="approved", sections=[])
        ready = Script(title="Ready", theme_id=theme.id, status="approved", sections=[])
        out = Script(title="Out", theme_id=theme.id, status="approved", sections=[])
        db.add_all([writing, voice, animating, ready, out])
        db.flush()
        db.add(VoiceTake(script_id=animating.id, source="kokoro", script_hash="x", chosen=True))
        db.add(Storyboard(script_id=ready.id, review="approved", video_path="/x.mp4"))
        sent = Storyboard(script_id=out.id, review="approved", video_path="/y.mp4")
        db.add(sent)
        db.flush()
        db.add(Post(kind="storyboard", storyboard_id=sent.id, channel_id="c", service="tiktok", status="sent"))
        db.flush()
        board = themes.board(db, theme)
    stages = {i["title"]: i["stage"] for i in board["items"]}
    assert stages == {"Writing": "script", "Voice": "voice", "Animating": "animation", "Ready": "ready",
                      "Out": "published"}
    assert board["inbox"] == 1 and board["counts"]["ideas"] == 1
    assert themes.next_step(board["counts"], board["inbox"])["stage"] == "ready"


def test_themes_api_create_rename_merge_and_archive(client, monkeypatch):
    monkeypatch.setattr(ideas, "generate", lambda theme, count, existing, seed="": [
        ideas.Draft(title=f"Idea {i}", hook="h", expected="e", answer="a", angle="x", format="single",
                    search_query="q",
                    beats=[]) for i in range(count)])
    a = client.post("/api/v1/themes", json={"name": "Odd history", "description": "Strange rules"}).json()
    b = client.post("/api/v1/themes", json={"name": "Odd laws"}).json()
    assert client.post("/api/v1/themes", json={"name": "odd history"}).status_code == 409
    made = generate(client, count=2, theme_id=a["id"])
    assert all(i["theme_id"] == a["id"] and i["theme"] == "Odd history" for i in made)
    assert len(client.get(f"/api/v1/ideas?theme_id={a['id']}").json()) == 2

    renamed = client.patch(f"/api/v1/themes/{a['id']}", json={"name": "Odd rules", "color": "teal"}).json()
    assert renamed["name"] == "Odd rules" and renamed["color"] == "teal"
    assert {i["theme"] for i in client.get("/api/v1/ideas").json()} == {"Odd rules"}
    assert client.delete(f"/api/v1/themes/{a['id']}").status_code == 409        # it has ideas

    moved = client.patch(f"/api/v1/ideas/{made[0]['id']}", json={"theme_id": b["id"]}).json()
    assert moved["theme_id"] == b["id"] and moved["theme"] == "Odd laws"
    merged = client.post(f"/api/v1/themes/{a['id']}/merge", json={"into": b["id"]}).json()
    assert merged["id"] == b["id"] and merged["inbox"] == 2
    assert client.get(f"/api/v1/themes/{a['id']}").status_code == 404

    board = client.get(f"/api/v1/themes/{b['id']}").json()
    assert board["theme"]["next"]["stage"] == "ideas" and board["items"] == []
    archived = client.patch(f"/api/v1/themes/{b['id']}", json={"status": "archived"}).json()
    assert archived["status"] == "archived"


# --- statistics and lessons ------------------------------------------------------------------

def _published(db, theme: Theme, title: str, views: int | None, likes: int = 0, **idea) -> Storyboard:
    i = Idea(title=title, theme=theme.name, theme_id=theme.id, status="approved", hook=f"Hook of {title}", **idea)
    db.add(i)
    db.flush()
    script = Script(title=title, theme_id=theme.id, idea_id=i.id, status="approved", sections=[])
    db.add(script)
    db.flush()
    board = Storyboard(script_id=script.id, title=title, review="approved", video_path="/v.mp4", end=40.0)
    db.add(board)
    db.flush()
    values = {} if views is None else {"views": views, "likes": likes}
    db.add(Post(kind="storyboard", storyboard_id=board.id, channel_id="c", service="tiktok", status="sent",
                buffer_id=f"b{board.id}", sent_at=utcnow() - timedelta(days=3), metrics={"values": values}))
    return board


def test_ranking_is_arithmetic_against_the_median(app_env):
    with session_scope() as db:
        theme = _theme(db)
        _published(db, theme, "Big", 9000, likes=900)
        _published(db, theme, "Middle", 3000)
        _published(db, theme, "Small", 1000)
        _published(db, theme, "Unknown", None)
        table = learning.ranked(learning.published(db, theme.id))
        summary = learning.summary(db, theme.id)
    assert [v["title"] for v in table["videos"]] == ["Big", "Middle", "Small", "Unknown"]
    big = table["videos"][0]
    assert table["median_views"] == 3000 and big["vs_median"] == 3.0 and big["engagement"] == 10.0
    assert "3.0x the theme's median of 3,000" in big["lines"]
    assert table["videos"][-1]["lines"] == ["No statistics yet"]
    assert summary == {"published": 4, "with_views": 3, "total_views": 13000, "median_views": 3000,
                       "best_title": "Big", "best_views": 9000}


def test_lessons_need_enough_videos_and_keep_the_model_honest(app_env, monkeypatch):
    seen = {}

    def fake_ask(task, system, content, output_format, effort="high", cache=False):
        seen.update(task=task, system=str(system), content=content)
        return learning.Lessons(summary="Questions win.", works=["Question titles [1]"], avoid=["Lists [3]"],
                                try_next=["More questions"], confidence="high")

    monkeypatch.setattr(learning.llm, "ask", fake_ask)
    with session_scope() as db:
        theme = _theme(db)
        _published(db, theme, "Big", 9000)
        _published(db, theme, "Middle", 3000)
        theme_id = theme.id
    with pytest.raises(LookupError):
        learning.insights(theme_id)
    with session_scope() as db:
        _published(db, db.get(Theme, theme_id), "Small", 1000)
    result = learning.insights(theme_id)
    assert seen["task"] == "theme_insights" and "[1] \"Big\"" in seen["content"] and "median_views=\"3000\"" \
        in seen["content"]
    assert result["confidence"] == "medium" and result["based_on"] == 3      # three videos cannot be "high"
    with session_scope() as db:
        assert db.get(Theme, theme_id).insights["works"] == ["Question titles [1]"]


def test_craft_columns_show_for_measured_videos_and_stay_blank_for_older_ones(app_env, monkeypatch):
    from clipper.db.models import VoiceTake
    from clipper.studio import script as script_mod

    sections = [{"key": "a", "heading": "Short", "short": True,
                 "text": "Your fridge never made cold. But here is the twist. It moves heat. So it warms the room."}]
    stored = {"hash": script_mod.text_hash(sections), "topic": "fridge", "checked_at": "now", "dropped": 0,
              "roles": [{"n": n, "role": r, "title_question": False}
                        for n, r in enumerate(["hook", "rehook", "twist", "payoff"], 1)]}
    with session_scope() as db:
        theme = _theme(db)
        measured = _published(db, theme, "Measured", 5000)
        script = db.get(Script, measured.script_id)
        script.sections, script.hold, script.video = sections, stored, "short"
        take = VoiceTake(script_id=script.id, source="kokoro", voice="am_michael", script_hash=stored["hash"],
                         alignment={"wpm": 158})
        db.add(take)
        db.flush()
        measured.take_id, measured.format = take.id, "short"
        _published(db, theme, "Older", 1000)
        rows = {r["title"]: r for r in learning.published(db, theme.id)}
        table = learning._video_table(learning.ranked(list(rows.values()))["videos"])
    m = rows["Measured"]
    assert (m["wpm"], m["opening"], m["first_twist"]) == (158, "hook > rehook > twist", 2)
    assert isinstance(m["hold_score"], int)
    assert all(rows["Older"][k] is None for k in learning.CRAFT)
    assert "opens hook > rehook > twist; pace 158 wpm" in table
    assert "Craft: hold not measured; pace not measured" in table
    assert "at least 3 videos" in str(learning.prompts.Prompt("theme_insights"))


def test_proven_examples_put_the_evidence_first_and_new_ideas_say_what_they_build_on(client, monkeypatch):
    with session_scope() as db:
        theme = _theme(db)
        _published(db, theme, "Big", 9000)
        db.add(Idea(title="Kept and approved", theme=theme.name, theme_id=theme.id, status="approved"))
        db.add(Idea(title="Rejected", theme=theme.name, theme_id=theme.id, status="skipped"))
        theme_id = theme.id
    seen = {}

    def fake_proven(theme, about, count, existing, examples, lessons, rejected, hint=""):
        seen.update(examples=examples, rejected=rejected, hint=hint)
        return [ideas.ProvenDraft(title="Next big", hook="h", expected="e", answer="a", angle="x", format="single",
                                  search_query="q", beats=[], inspired_by=[1, 1, 9], why_now="Same hook as Big")]

    monkeypatch.setattr(ideas, "generate_proven", fake_proven)
    made = generate(client, count=1, theme_id=theme_id, mode="proven", hint="kings")
    assert seen["examples"].startswith("[1] \"Big\" (video, published: 9,000 views")
    assert "Kept and approved" in seen["examples"] and seen["rejected"] == ["Rejected"] and seen["hint"] == "kings"
    assert made[0]["inspired_by"] == [{"kind": "video", "id": made[0]["inspired_by"][0]["id"], "title": "Big",
                                       "evidence": made[0]["inspired_by"][0]["evidence"]}]
    assert made[0]["notes"] == "Builds on: Same hook as Big" and made[0]["status"] == "inbox"

    learnt = client.get(f"/api/v1/themes/{theme_id}/learning").json()
    assert learnt["with_views"] == 1 and not learnt["can_learn"] and learnt["proven"][0]["title"] == "Big"
    assert client.post(f"/api/v1/themes/{theme_id}/insights").status_code == 409


def test_nothing_proven_is_said_plainly(client):
    theme = client.post("/api/v1/themes", json={"name": "Empty"}).json()
    answer = client.post("/api/v1/ideas/generate", json={"count": 2, "theme_id": theme["id"], "mode": "proven"})
    assert answer.status_code == 409 and "Nothing is proven" in answer.json()["detail"]


def test_buffer_statistics_are_read_in_batches_at_most_daily(app_env, monkeypatch):
    monkeypatch.setenv("BUFFER_STUDIO_API_KEY", "key")
    asked = []

    def fake_metrics(ids, kind):
        asked.append(sorted(ids))
        return {i: {"at": "2026-11-15T10:00:00Z", "values": {"views": 1200.0, "likes": 30.0}} for i in ids}

    monkeypatch.setattr(buffer, "metrics", fake_metrics)
    with session_scope() as db:
        theme = _theme(db)
        board = _published(db, theme, "Out", None)
        old = Post(kind="storyboard", storyboard_id=board.id, channel_id="c", service="youtube", status="sent",
                   buffer_id="old", sent_at=utcnow() - timedelta(days=90))
        db.add(old)
    assert posting.sync_metrics() == 1 and asked == [[f"b{board.id}"]]     # posts older than 60 days are left
    assert posting.sync_metrics() == 0 and len(asked) == 1                 # asked again tomorrow, not now
    posting.sync_metrics(force=True)
    assert len(asked) == 2
    with session_scope() as db:
        post = db.scalar(select(Post).where(Post.buffer_id == f"b{board.id}"))
        assert post.metrics == {"values": {"views": 1200.0, "likes": 30.0}} and post.metrics_at is not None


def test_buffer_metrics_query_reads_types_and_values(monkeypatch):
    monkeypatch.setattr(buffer, "call", lambda query, variables, kind: {"data": {
        "p0": {"id": "a", "metricsUpdatedAt": "2026-11-15T10:00:00Z",
               "metrics": [{"type": "views", "value": 10.0}, {"type": "likes", "value": 2.0}]},
        "p1": {"id": "b", "metricsUpdatedAt": None, "metrics": None}}})
    found = buffer.metrics(["a", "b"], "storyboard")
    assert found == {"a": {"at": "2026-11-15T10:00:00Z", "values": {"views": 10.0, "likes": 2.0}}}


def test_youtube_links_and_hand_published_statistics(client, monkeypatch):
    assert youtube.video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=3") == "dQw4w9WgXcQ"
    assert youtube.video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert youtube.video_id("https://youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert youtube.video_id("https://example.com/watch?v=dQw4w9WgXcQ") is None
    monkeypatch.setenv("YOUTUBE_API_KEY", "key")
    monkeypatch.setattr(youtube, "_get", lambda endpoint, **params: {"items": [
        {"id": "dQw4w9WgXcQ", "statistics": {"viewCount": "5000", "likeCount": "40"}}]})
    with session_scope() as db:
        theme = _theme(db)
        script = Script(title="Long one", theme_id=theme.id, status="approved", sections=[])
        db.add(script)
        db.flush()
        board = Storyboard(script_id=script.id, format="long", review="approved", video_path="/l.mp4",
                           published_at=utcnow(), end=300.0)
        db.add(board)
        db.flush()
        board_id, theme_id = board.id, theme.id
    bad = client.patch(f"/api/v1/storyboards/{board_id}", json={"published_url": "https://example.com/x"})
    assert bad.status_code == 400
    with session_scope() as db:
        db.get(Storyboard, board_id).published_url = "https://youtu.be/dQw4w9WgXcQ"
    assert learning.refresh_youtube() == 1
    learnt = client.get(f"/api/v1/themes/{theme_id}/learning").json()
    video = learnt["videos"][0]
    assert video["views"] == 5000 and video["likes"] == 40 and video["networks"][0]["name"] == "YouTube (by hand)"
    assert learning.refresh_youtube() == 0          # read at most once a day


def test_a_manual_refresh_is_a_job_and_cannot_be_spammed(client):
    first = client.post("/api/v1/themes/stats/refresh").json()
    assert first["kind"] == "stats" and first["stages"] == ["stats"]
    again = client.post("/api/v1/themes/stats/refresh").json()
    assert again["id"] == first["id"]                  # still queued: the same job


def test_the_schedule_panel_starts_from_the_theme_channels(client):
    with session_scope() as db:
        theme = _theme(db)
        theme.channels = ["chan-tiktok"]
        script = Script(title="Short", theme_id=theme.id, status="approved", sections=[])
        db.add(script)
        db.flush()
        board = Storyboard(script_id=script.id, format="short", review="approved", video_path="/s.mp4")
        db.add(board)
        db.flush()
        board_id = board.id
    draft = client.get("/api/v1/posting/draft", params={"kind": "storyboard", "ref_id": board_id}).json()
    assert draft["channels"] == ["chan-tiktok"]


# --- v2.1: deleting a theme, banners, choosing what to build on ------------------------------

def test_deleting_a_theme_removes_everything_in_it(client, app_env):
    from clipper import config
    from clipper.studio import voice

    with session_scope() as db:
        theme = _theme(db, "Doomed")
        board = _published(db, theme, "Gone", 100)
        script_id, theme_id = board.script_id, theme.id
        db.add(VoiceTake(script_id=script_id, source="kokoro", script_hash="x", chosen=True))
        other = _theme(db, "Kept")
        db.add(Idea(title="Stays", theme=other.name, theme_id=other.id))
    folder = voice.script_media_dir(script_id)
    folder.mkdir(parents=True)
    (folder / "voice.wav").write_bytes(b"x")
    (config.studio_dir() / "themes" / f"theme-{theme_id}").mkdir(parents=True)

    contents = client.get(f"/api/v1/themes/{theme_id}/contents").json()
    assert contents == {"ideas": 1, "scripts": 1, "takes": 1, "videos": 1, "blockers": []}
    refused = client.delete(f"/api/v1/themes/{theme_id}")
    assert refused.status_code == 409 and "Type the theme's name" in refused.json()["detail"]
    assert client.delete(f"/api/v1/themes/{theme_id}", params={"confirm": "doomed"}).status_code == 204
    assert not folder.exists() and not (config.studio_dir() / "themes" / f"theme-{theme_id}").exists()
    with session_scope() as db:
        assert db.scalar(select(Script).where(Script.id == script_id)) is None
        assert db.scalars(select(Post)).all() == [] and db.scalars(select(Storyboard)).all() == []
        assert [i.title for i in db.scalars(select(Idea))] == ["Stays"]


def test_a_post_still_waiting_blocks_the_deletion(client):
    with session_scope() as db:
        theme = _theme(db, "Busy")
        board = _published(db, theme, "Out", 10)
        db.add(Post(kind="storyboard", storyboard_id=board.id, channel_id="c", service="youtube", status="scheduled"))
        theme_id = theme.id
    contents = client.get(f"/api/v1/themes/{theme_id}/contents").json()
    assert "waiting on Buffer" in contents["blockers"][0]
    refused = client.delete(f"/api/v1/themes/{theme_id}", params={"confirm": "Busy"})
    assert refused.status_code == 409 and "undo" in refused.json()["detail"]


def test_a_new_theme_gets_a_banner_job_and_the_drawing_is_checked(client, app_env, monkeypatch):
    from clipper import jobs
    from clipper.studio import banner

    made = client.post("/api/v1/themes", json={"name": "Odd history"}).json()
    assert made["banner_job"]["kind"] == "banner" and made["banner"] is None
    good = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1600 480"><rect width="1600" height="480" '
            'fill="#cde"/><circle cx="800" cy="240" r="90" fill="#fc0" stroke="#222" stroke-width="4"/></svg>')
    answers = iter(['<svg viewBox="0 0 100 100"><text>hi</text></svg>', good])
    monkeypatch.setattr(banner.llm, "ask", lambda *a, **k: banner.Drawing(svg=next(answers)))

    class Rep(jobs.Reporter):
        lines: list = []

        def info(self, message):
            self.lines.append(message)

        def warn(self, message):
            self.lines.append("WARN " + message)

    assert banner.draw(made["id"], Rep()) == "Banner drawn"
    assert any("not usable" in line for line in Rep.lines)
    picture = client.get(f"/api/v1/themes/{made['id']}/banner.jpg")
    assert picture.status_code == 200 and picture.headers["content-type"] == "image/jpeg"
    import io

    from PIL import Image
    assert Image.open(io.BytesIO(picture.content)).size == (1600, 480)
    removed = client.delete(f"/api/v1/themes/{made['id']}/banner").json()
    assert removed["banner"] is None


def test_banners_can_be_turned_off_and_uploaded(client):
    import io

    from PIL import Image

    from clipper import settings as settings_mod

    current = settings_mod.load()
    settings_mod.save(current.model_copy(update={"theme_banners": False}))
    made = client.post("/api/v1/themes", json={"name": "Quiet"}).json()
    assert made["banner_job"] is None
    assert client.post(f"/api/v1/themes/{made['id']}/banner", json={}).status_code == 409
    png = io.BytesIO()
    Image.new("RGB", (800, 800), (200, 10, 10)).save(png, "PNG")
    up = client.put(f"/api/v1/themes/{made['id']}/banner", files={"file": ("mine.png", png.getvalue(), "image/png")})
    assert up.status_code == 200 and up.json()["banner"]["by"] == "you"
    bad = client.put(f"/api/v1/themes/{made['id']}/banner", files={"file": ("x.png", b"nope", "image/png")})
    assert bad.status_code == 400


def test_you_choose_which_proven_examples_new_ideas_build_on(client, monkeypatch):
    with session_scope() as db:
        theme = _theme(db)
        _published(db, theme, "Big", 9000)
        kept = Idea(title="Kept and approved", theme=theme.name, theme_id=theme.id, status="approved")
        db.add(kept)
        db.flush()
        theme_id, kept_id = theme.id, kept.id
    board = client.get(f"/api/v1/themes/{theme_id}").json()
    keys = [p["key"] for p in board["proven"]]
    assert keys[0].startswith("video:") and f"idea:{kept_id}" in keys
    seen = {}

    def fake_proven(theme, about, count, existing, examples, lessons, rejected, hint=""):
        seen["examples"] = examples
        return [ideas.ProvenDraft(title="Next", hook="h", expected="e", answer="a", angle="x", format="single",
                    search_query="q",
                                  beats=[], inspired_by=[1], why_now="Same")]

    monkeypatch.setattr(ideas, "generate_proven", fake_proven)
    made = generate(client, count=1, theme_id=theme_id, mode="proven", examples=[f"idea:{kept_id}"])
    assert seen["examples"].startswith("[1] \"Kept and approved\"") and "Big" not in seen["examples"]
    assert made[0]["inspired_by"][0]["title"] == "Kept and approved"


def test_board_items_say_whether_the_voice_is_done(client):
    with session_scope() as db:
        theme = _theme(db)
        script = Script(title="Voiced", theme_id=theme.id, status="approved",
                        sections=[{"key": "a", "heading": "A", "text": "one two three", "short": False}])
        db.add(script)
        db.flush()
        db.add(VoiceTake(script_id=script.id, source="kokoro", voice="am_michael", script_hash="x", chosen=True,
                         duration=12.5))
        theme_id = theme.id
    item = client.get(f"/api/v1/themes/{theme_id}").json()["items"][0]
    assert item["voiced"] and item["words"] == 3 and item["take"] == {"source": "kokoro", "voice": "am_michael",
                                                                      "duration": 12.5}


def test_ideas_are_written_by_a_job_you_can_come_back_to(client, monkeypatch):
    from clipper import worker

    seen = {}

    def fake_generate(theme, count, existing, seed=""):
        seen["theme"] = theme
        return [ideas.Draft(title=f"Idea {i}", hook="h", expected="e", answer="a", angle="x", format="single",
                    search_query="q",
                            beats=[]) for i in range(count)]

    monkeypatch.setattr(ideas, "generate", fake_generate)
    theme = client.post("/api/v1/themes", json={"name": "Odd rules"}).json()
    first = client.post("/api/v1/ideas/generate", json={"count": 2, "theme_id": theme["id"], "hint": "kings"}).json()
    again = client.post("/api/v1/ideas/generate", json={"count": 5, "theme_id": theme["id"]}).json()
    assert first["kind"] == "ideas" and first["theme_id"] == theme["id"] and again["id"] == first["id"]
    listed = [j for j in client.get("/api/v1/jobs").json() if j["kind"] == "ideas"]
    assert listed[0]["state"] == "queued" and listed[0]["theme_id"] == theme["id"]   # what the page shows on return
    assert client.get(f"/api/v1/ideas?theme_id={theme['id']}").json() == []

    worker.run_job(first["id"])
    done = client.get(f"/api/v1/jobs/{first['id']}").json()
    assert done["state"] == "succeeded" and done["message"] == "2 new ideas waiting to be sorted"
    assert "Lean towards: kings" in seen["theme"]
    made = client.get(f"/api/v1/ideas?theme_id={theme['id']}").json()
    assert sorted(i["title"] for i in made) == ["Idea 0", "Idea 1"] and {i["status"] for i in made} == {"inbox"}
