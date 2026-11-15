"""Phase 4: looks for the ink style, and the checks before publishing."""

import numpy as np
from PIL import Image

from clipper.db import session_scope
from clipper.db.models import Idea, Script, Storyboard, StudioEvent, VoiceTake
from clipper.studio import compliance, looks


def words(text: str) -> list[dict]:
    return [{"w": w, "s": i * 0.4, "e": i * 0.4 + 0.3} for i, w in enumerate(text.split())]


def test_looks_redraw_sprites_without_moving_them():
    sprite = Image.new("RGBA", (40, 30), (0, 0, 0, 0))
    sprite.paste((29, 28, 26, 255), (10, 10, 30, 20))      # an ink stroke
    blue = looks.blueprint_sprite(sprite)
    r, g, b, a = blue.getpixel((20, 15))
    assert min(r, g, b) > 220 and a == 255                 # dark ink becomes white
    assert blue.getpixel((2, 2))[3] == 0                   # empty stays empty
    cut = looks.cutout(sprite, 4)
    assert cut.size == (40 + 10, 30 + 10)                  # grows evenly, so the centre does not move
    assert cut.getpixel((25 - 15 + 5, 25))[3] > 200        # the card margin left of the stroke
    assert looks.pop_scale(0, None, 0.3) == 0 and looks.pop_scale(1, None, 0.3) == 1
    assert looks.pop_scale(1, 0.3, 0.3) == 0               # gone after its erase time
    layer = Image.new("RGBA", (200, 120), (0, 0, 0, 0))
    layer.paste((0, 0, 0, 255), (50, 30, 100, 60))
    shadow = np.asarray(looks.shadow(layer, 1080))
    assert shadow[..., 3].max() > 0 and looks.shadow(Image.new("RGBA", (20, 20)), 1080) is None
    assert looks.get("nonsense").name == "ink" and not looks.get("paper").boil


def test_word_and_line_changes_are_counted():
    assert compliance.words_changed("the cat sat on the mat", "the dog sat on the mat") == 1
    assert compliance.words_changed("a b c", "a b c d e") == 2
    before = [{"start_word": 0, "note": "", "camera": "wide", "lines": ["0 actor narrator", "1 draw fridge"]}]
    after = [{"start_word": 0, "note": "", "camera": "wide", "lines": ["0 actor narrator", "1 draw oven", "2 jump"]}]
    assert compliance.lines_changed(before, after) == 2


def test_reading_grade_and_kids_advice():
    assert compliance.grade("The cat sat. The dog ran. We had fun.") < 3
    hard = ("Monetary authorities consistently underestimated inflationary expectations, "
            "precipitating considerable macroeconomic instability throughout the subsequent decade.")
    assert compliance.grade(hard) > 12


def make_video(db, title: str, text: str, music_id=None, look="ink", origin="claude") -> Storyboard:
    idea = Idea(title=title, origin=origin)
    db.add(idea)
    db.flush()
    script = Script(idea_id=idea.id, title=title, sections=[{"key": "a", "heading": "A", "text": text}])
    db.add(script)
    db.flush()
    take = VoiceTake(script_id=script.id, source="kokoro", voice="am_michael", script_hash="x", chosen=True)
    db.add(take)
    db.flush()
    board = Storyboard(script_id=script.id, take_id=take.id, format="short", style="ink", look=look, title=title,
                       words=words(text), music_id=music_id, video_path="/tmp/x.mp4",
                       scenes=[{"start_word": 0, "note": "", "camera": "wide", "page": False, "cut": False,
                                "lines": ["0 actor narrator", "1 draw fridge", '2 who caesar "Julius Caesar"']}])
    db.add(board)
    db.flush()
    return board


def test_before_publish_explains_every_point(client, app_env):
    text = "Why does the calendar drift? The war over dates lasted for centuries and the government taxed it."
    with session_scope() as db:
        first = make_video(db, "Why the calendar drifts", text)
        second = make_video(db, "Why the calendar drifts again", text, origin="you")
        ids = first.id, second.id, second.script_id
    # your work goes in the log through the API
    r = client.put(f"/api/v1/scripts/{ids[2]}/sections",
                   json={"sections": [{"key": "a", "heading": "A", "text": text.replace("centuries", "ages"),
                                       "short": False}]})
    assert r.status_code == 200
    assert client.patch(f"/api/v1/storyboards/{ids[1]}", json={"look": "paper"}).json()["look"] == "paper"
    assert client.patch(f"/api/v1/storyboards/{ids[1]}", json={"review": "approved"}).status_code == 200
    with session_scope() as db:
        kinds = [(e.kind, e.by, e.amount) for e in db.query(StudioEvent).order_by(StudioEvent.id)]
    assert kinds == [("script_edit", "you", 1), ("look", "you", 0), ("review", "you", 0)]

    check = client.get(f"/api/v1/storyboards/{ids[1]}/publish-check").json()
    sim = check["similarity"]
    assert sim["nearest"]["id"] == ids[0] and sim["score"] == sum(x["points"] for x in sim["lines"])
    assert any("first words" in x["text"] for x in sim["lines"])
    assert not any(x["text"].startswith("Same look") for x in sim["lines"])     # paper vs ink
    assert check["kids"]["verdict"] == "general" and any("war" in x["text"] for x in check["kids"]["lines"])
    assert check["disclosure"]["verdict"] == "none"
    assert any("Julius Caesar" in x["text"] for x in check["disclosure"]["lines"])
    parts = {(x["part"], x["yours"]) for x in check["authorship"]["lines"]}
    assert ("Idea", True) in parts and ("Review", True) in parts
    assert check["checklist"][0]["done"] is True
    log = client.get(f"/api/v1/storyboards/{ids[1]}/authorship.txt").text
    assert "Edited the script (1 words)" in log and "Chose the paper look" in log

    # a worker job adds its own line
    with session_scope() as db:
        compliance.record_job(db, "storyboard", {"script_id": ids[2], "storyboard_id": ids[1]})
    with session_scope() as db:
        assert db.query(StudioEvent).filter_by(kind="plan", by="ai").count() == 1


def test_a_cloned_voice_asks_you_to_check():
    board = Storyboard(style="ink", scenes=[], take=VoiceTake(source="elevenlabs", voice="Narrator clone"))
    assert compliance.disclosure(None, board)["verdict"] == "check"


def test_youtube_text_is_written_only_for_approved_videos(client, app_env, monkeypatch):
    from clipper import llm
    from clipper.studio import publish

    def fake_ask(task, system, content, output_format, effort="high", cache=False):
        assert task == "description" and "drift" in content
        return output_format(titles=["Why the calendar <drifts>", "b", "c"],
                             opening="Calendars drift.\n\nHere is why.", hashtags=["#history", "Calendar Facts"],
                             tags=["calendar", "Calendar", "leap year"])

    monkeypatch.setattr(llm, "ask", fake_ask)
    text = "Why does the calendar drift? It drifts a little every year."
    with session_scope() as db:
        board = make_video(db, "Calendar drift", text)
        board.review = "pending"
        script = board.script
        script.research = {"notes": "", "sources": [{"n": 1, "url": "https://a.org", "title": "A"},
                                                    {"n": 2, "url": "https://b.org", "title": "B"}]}
        script.claims = [{"section": "a", "claim": "x", "verdict": "supported", "sources": [2], "note": ""}]
        board_id = board.id
    assert client.post(f"/api/v1/storyboards/{board_id}/youtube").status_code == 409
    client.patch(f"/api/v1/storyboards/{board_id}", json={"review": "approved"})
    out = client.post(f"/api/v1/storyboards/{board_id}/youtube").json()
    assert out["title"] == "Why the calendar drifts" and out["tags"] == ["calendar", "leap year"]
    assert out["description"].startswith("Calendars drift.")
    assert "[2] B: https://b.org" in out["description"] and "https://a.org" not in out["description"]
    assert out["description"].endswith("#history #CalendarFacts") and out["problems"] == []
    saved = client.put(f"/api/v1/storyboards/{board_id}/youtube",
                       json={"title": "x" * 120, "description": "Mine.", "tags": ["a"]}).json()
    assert saved["edited"] and any("100" in p for p in saved["problems"])
    assert client.get(f"/api/v1/storyboards/{board_id}").json()["youtube"]["description"] == "Mine."
    check = client.get(f"/api/v1/storyboards/{board_id}/publish-check").json()
    assert ("Description", True) in {(x["part"], x["yours"]) for x in check["authorship"]["lines"]}
    assert publish.fit_tags(["a" * 99] * 10) == ["a" * 99]          # duplicates dropped
    assert len(",".join(publish.fit_tags([f"tag number {i}" for i in range(60)]))) <= 500


def test_only_ready_long_videos_can_be_marked_published_manually(client, app_env):
    with session_scope() as db:
        long = make_video(db, "A long video", "A complete narration.")
        long.format, long.review = "long", "approved"
        short = make_video(db, "A Short", "A short narration.")
        short.review = "approved"
        long_id, short_id = long.id, short.id

    missing = client.patch(f"/api/v1/storyboards/{long_id}", json={"published": True})
    assert missing.status_code == 409 and "title and description" in missing.json()["detail"]

    with session_scope() as db:
        db.get(Storyboard, long_id).youtube = {
            "title": "A long video", "description": "A complete description.", "problems": []}
    published = client.patch(f"/api/v1/storyboards/{long_id}", json={"published": True})
    assert published.status_code == 200 and published.json()["published_at"] is not None
    assert published.json()["publish_ready"] is True
    refused = client.patch(f"/api/v1/storyboards/{long_id}", json={"review": "pending"})
    assert refused.status_code == 409 and "back to Approved" in refused.json()["detail"]

    back = client.patch(f"/api/v1/storyboards/{long_id}", json={"published": False})
    assert back.status_code == 200 and back.json()["published_at"] is None
    assert client.patch(f"/api/v1/storyboards/{long_id}", json={"review": "pending"}).status_code == 200
    short_mark = client.patch(f"/api/v1/storyboards/{short_id}", json={"published": True})
    assert short_mark.status_code == 409 and "Only long videos" in short_mark.json()["detail"]
