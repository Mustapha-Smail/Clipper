import json
import subprocess

import numpy as np
import pytest

from clipper import llm
from clipper.db import session_scope
from clipper.db.models import Job, Script, VoiceTake
from clipper.jobs import ConsoleReporter
from clipper.settings import Settings
from clipper.studio import animate, art, voice
from clipper.studio import storyboard as sb

SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><circle cx="256" cy="256" r="180" '
       'fill="#ffd400" stroke="#1b1b1b" stroke-width="12"/></svg>')


def element(**over):
    base = {"kind": "character", "name": "narrator", "slot": "center", "to_slot": None, "size": "medium",
            "pose": "stand", "expression": "neutral", "facing": "right", "talking": False, "text": "",
            "enter": "pop", "enter_word": None, "motion": "none"}
    return {**base, **over}


def test_clean_makes_a_plan_safe_to_render():
    scenes = [
        {"start_word": 7, "note": "", "background": "paper", "camera": "still", "elements": [
            element(kind="prop", name="LED Bulb!", enter_word=99),
            element(kind="arrow", slot="left"),                      # no target: dropped
            element(kind="text", text="  "),                         # empty: dropped
            element(name="ghost"),                                   # unknown cast: becomes person
            element(), element(), element()]},
        {"start_word": 3, "note": "", "background": "grid", "camera": "still", "elements": []},
        {"start_word": 3, "note": "", "background": "grid", "camera": "still", "elements": []},  # duplicate start
    ]
    out = sb.clean(scenes, word_count=10)
    assert [s["start_word"] for s in out] == [0, 7]
    first_of_second = out[1]["elements"][0]
    assert first_of_second["name"] == "led_bulb" and first_of_second["enter_word"] == 9
    assert [e["kind"] for e in out[1]["elements"]] == ["prop", "character", "character", "character"]
    assert out[1]["elements"][1]["name"] == "person"


def test_words_are_numbered_with_sentence_times():
    words = [{"w": "One.", "s": 0.0, "e": 0.3}, {"w": "Two", "s": 1.25, "e": 1.5}, {"w": "three.", "s": 1.6, "e": 2}]
    assert sb.numbered_words(words) == "(0.0s) [0] One. \n(1.2s) [1] Two [2] three."


def test_prop_svgs_are_checked():
    assert art.check_prop_svg("Here you go:\n" + SVG).startswith("<svg")
    for bad in (SVG.replace("<circle", "<script>alert(1)</script><circle"),
                SVG.replace("<circle", '<text x="1">Hi</text><circle'),
                SVG.replace("<circle", '<image href="http://x/y.png"/><circle'),
                SVG.replace(' viewBox="0 0 512 512"', ""), "<div>not svg</div>"):
        with pytest.raises(ValueError):
            art.check_prop_svg(bad)


def test_every_pose_and_expression_draws():
    for pose in art.POSES:
        for expression in art.EXPRESSIONS:
            for variant in ("narrator", "royal"):
                image = art.rasterize(art.character_svg(variant, pose, expression, mouth=1, facing="left"), 80)
                assert image.getbbox() is not None


def test_mouths_follow_the_loudness(tmp_path):
    wav = tmp_path / "v.wav"
    rate = 48000
    loud = 0.5 * np.sin(np.linspace(0, 2 * np.pi * 220, rate)).astype(np.float32)
    voice._write_wav(wav, np.concatenate([np.zeros(rate, dtype=np.float32), loud]), rate)
    levels = animate.mouth_levels(wav, 0.0, 2.0)
    assert len(levels) == 48 and levels[:20].max() == 0 and levels[30:].min() == 2


# --- the whole flow ------------------------------------------------------------------------------

SECTIONS = [{"key": "a", "heading": "Cold open", "text": "Your fridge has never made cold.", "short": True},
            {"key": "b", "heading": "Trick", "text": "It moves heat out.", "short": False}]


@pytest.fixture()
def script_with_take(app_env):
    with session_scope() as db:
        script = Script(title="Fridge", theme="", sections=SECTIONS)
        db.add(script)
        db.flush()
        take = VoiceTake(script_id=script.id, source="kokoro", voice="am_michael", script_hash="x", chosen=True,
                         alignment={"coverage": 1.0, "sections": [], "missing": [], "extra": [],
                                    "spoken_words": 10, "script_hash": "x", "wpm": 160})
        db.add(take)
        db.flush()
        folder = voice.take_dir(script.id, take.id)
        folder.mkdir(parents=True)
        wav = folder / "voice.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=200:duration=5",
                        "-ar", "48000", "-ac", "1", str(wav)], check=True)
        take.path, take.duration = str(wav), 5.0
        timeline = [{"section": "a", "w": w, "s": 0.3 + i * 0.4, "e": 0.6 + i * 0.4}
                    for i, w in enumerate("Your fridge has never made cold.".split())]
        timeline += [{"section": "b", "w": w, "s": 3 + i * 0.3, "e": 3.25 + i * 0.3}
                     for i, w in enumerate("It moves heat out.".split())]
        (folder / "timeline.json").write_text(json.dumps(timeline))
        return script.id


@pytest.fixture()
def fake_claude(monkeypatch):
    def fake_ask(task, system, content, output_format, effort="high"):
        if output_format is sb.Plan:
            return sb.Plan(scenes=[
                sb.Scene(start_word=0, note="host", elements=[sb.Element(**element(talking=True, pose="point_right")),
                                                               sb.Element(**element(kind="prop", name="fridge",
                                                                                    slot="right", text="fridge"))]),
                sb.Scene(start_word=3, note="cold", background="sky", camera="zoom_in",
                         elements=[sb.Element(**element(kind="number", text="0 cold", slot="top")),
                                   sb.Element(**element(kind="arrow", slot="top", to_slot="center")),
                                   sb.Element(**element(kind="prop", name="snowflake", motion="spin",
                                                        enter_word=4))])],
                new_props=[sb.PropRequest(name="fridge", description="A white fridge"),
                           sb.PropRequest(name="snowflake", description="A snowflake")])
        if output_format is sb.Drawing:
            return sb.Drawing(svg=SVG)
        raise AssertionError(output_format)
    monkeypatch.setattr(llm, "ask", fake_ask)


def run_job(job_id: int) -> None:
    with session_scope() as db:
        job = db.get(Job, job_id)
        stages, board_id = list(job.stages), job.params["storyboard_id"]
    for stage in stages:
        if stage == "animate":
            animate.render(board_id, Settings(encoder="x264", bitrate_mbps=2), ConsoleReporter())
        else:
            getattr(sb, {"plan": "plan", "draw": "draw_missing"}[stage])(board_id, ConsoleReporter())
    with session_scope() as db:
        db.get(Job, job_id).state = "succeeded"


def test_short_from_section_to_video(client, script_with_take, fake_claude):
    sid = script_with_take
    assert client.post(f"/api/v1/scripts/{sid}/storyboards", json={"format": "short"}).status_code == 400
    board = client.post(f"/api/v1/scripts/{sid}/storyboards",
                        json={"format": "short", "section_key": "a", "style": "flat"}).json()
    assert board["title"] == "Fridge · Cold open" and len(board["words"]) == 6
    assert board["words"][0]["s"] == pytest.approx(0.15) and board["duration"] == pytest.approx(3.05, abs=0.01)
    assert board["job"]["stages"] == ["plan", "draw"] and board["job"]["storyboard_id"] == board["id"]

    run_job(board["job"]["id"])
    planned = client.get(f"/api/v1/storyboards/{board['id']}").json()
    assert planned["scene_count"] == 2 and planned["missing_props"] == []
    assets = {a["name"]: a for a in client.get("/api/v1/assets").json()}
    assert assets["fridge"]["drawn"] and assets["fridge"]["used_in"] == 1

    still = client.get(f"/api/v1/storyboards/{board['id']}/still", params={"t": 2.0, "width": 270})
    assert still.status_code == 200 and still.content[:4] == b"\x89PNG"
    assert client.get("/api/v1/assets/fridge/image", params={"size": 64}).content[:4] == b"\x89PNG"
    assert client.get("/api/v1/assets/cast.png", params={"pose": "shrug"}).status_code == 200

    # Edit: a new prop name joins the library undrawn, and bad values are refused.
    scenes = planned["scenes"]
    scenes[0]["elements"][1]["name"] = "ice_cube"
    edited = client.put(f"/api/v1/storyboards/{board['id']}/scenes", json={"scenes": scenes}).json()
    assert edited["missing_props"] == ["ice_cube"]
    scenes[0]["elements"][0]["pose"] = "moonwalk"
    assert client.put(f"/api/v1/storyboards/{board['id']}/scenes", json={"scenes": scenes}).status_code == 400

    assert client.patch(f"/api/v1/storyboards/{board['id']}", json={"review": "approved"}).status_code == 409
    job = client.post(f"/api/v1/storyboards/{board['id']}/render").json()
    assert job["stages"] == ["draw", "animate"]
    run_job(job["id"])
    done = client.get(f"/api/v1/storyboards/{board['id']}").json()
    assert done["has_video"] and done["review"] == "pending" and done["missing_props"] == []
    assert done["render_meta"]["version"] == 1 and done["render_meta"]["wpm"] == 160
    assert done["render_meta"]["starter"] is False and done["render_meta"]["riser"] is False
    video = animate.video_path({"script_id": sid, "id": board["id"], "format": "short"})
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height",
                            "-of", "json", str(video)], capture_output=True, text=True, check=True)
    streams = json.loads(probe.stdout)["streams"]
    assert {"codec_type": "video", "width": 1080, "height": 1920} in streams
    assert any(st["codec_type"] == "audio" for st in streams)
    assert client.patch(f"/api/v1/storyboards/{board['id']}", json={"review": "approved"}).json()["review"] == \
        "approved"

    assert client.delete(f"/api/v1/storyboards/{board['id']}").status_code == 204
    assert not video.parent.exists()


def test_attention_modes_round_trip_and_are_logged(client, script_with_take, fake_claude):
    from sqlalchemy import select

    from clipper.db.models import StudioEvent

    board = client.post(f"/api/v1/scripts/{script_with_take}/storyboards",
                        json={"format": "short", "section_key": "a", "style": "flat"}).json()
    run_job(board["job"]["id"])
    assert board["starter_mode"] == board["riser_mode"] == "default"
    changed = client.patch(f"/api/v1/storyboards/{board['id']}",
                           json={"starter_mode": "on", "riser_mode": "off"}).json()
    assert changed["starter_mode"] == "on" and changed["riser_mode"] == "off"
    assert changed["sound_starter_default"] is False and changed["sound_riser_default"] is False
    with session_scope() as db:
        events = list(db.scalars(select(StudioEvent).where(StudioEvent.storyboard_id == board["id"],
                                                           StudioEvent.kind == "sound")))
    assert len(events) == 2 and {event.by for event in events} == {"you"}


# --- long videos, sound, subtitles ---------------------------------------------------------------

from clipper.studio import sound  # noqa: E402


def test_subtitles_follow_sentences_and_line_lengths():
    words = [{"w": w, "s": i * 0.3, "e": i * 0.3 + 0.25} for i, w in enumerate(
        ("InGaN glows blue at 450 nm. " + "this sentence keeps going and going with many more words than one "
         "line of subtitles can hold at once.").split())]
    text = sound.srt(words)
    cues = text.strip().split("\n\n")
    assert cues[0].splitlines()[1:] == ["00:00:00,000 --> 00:00:01,790", "InGaN glows blue at 450 nm."]
    assert all(len(line) <= 42 for cue in cues for line in cue.splitlines()[2:])
    assert all(len(cue.splitlines()) <= 4 for cue in cues)


def test_chapters_start_at_zero_and_check_youtube_rules():
    words = [{"w": "a", "s": 0.4, "e": 0.6, "section": "x"}, {"w": "b", "s": 15.0, "e": 15.2, "section": "y"},
             {"w": "c", "s": 75.5, "e": 76, "section": "z"}]
    result = sound.chapters(words, {"x": "Cold open", "y": "The trick", "z": "Outro"})
    assert result["text"] == "0:00 Cold open\n0:15 The trick\n1:15 Outro" and result["valid"]
    short = sound.chapters(words[:2], {"x": "A", "y": "B"})
    assert not short["valid"] and "at least 3" in short["problems"][0]


def test_effects_follow_entrances_without_piling_up():
    scenes = [{"start_word": 0, "elements": [element(), element(kind="prop", name="a"), element(enter="fade")]},
              {"start_word": 2, "elements": [element(enter="slide"), element(kind="arrow", to_slot="top")]}]
    events = sound.effect_events(scenes, lambda i: [0.0, 0.5, 1.0][i], 5.0)
    assert events == [(0.0, "character"), (1.0, "arrow")]     # entrances at the same moment share one sound
    scenes[1]["elements"][1]["enter"] = "none"                    # arrows always draw themselves, with a zip
    assert sound.effect_events(scenes, lambda i: [0.0, 0.5, 1.0][i], 5.0)[1] == (1.0, "arrow")
    track = sound.effects_track([(0.1, "prop"), (0.5, "prop"), (0.9, "number"), (1.3, "bounce")], 2.0)
    assert len(track) == 2 * sound.RATE and 0 < abs(track).max() < 1


def test_long_video_is_planned_per_chapter(client, script_with_take, fake_claude, monkeypatch):
    sid = script_with_take
    with session_scope() as db:   # the take must match the script for a whole-script video
        from clipper.studio import script as script_mod
        db.get(VoiceTake, 1).script_hash = script_mod.text_hash(SECTIONS)
    calls = []
    original = sb._plan_chunk

    def counting(system, title, props, words, chapter, turns=""):
        calls.append((chapter, len(words)))
        return original(system, title, props, words, chapter, turns)
    monkeypatch.setattr(sb, "_plan_chunk", counting)

    assert client.post(f"/api/v1/scripts/{sid}/storyboards",
                       json={"format": "long", "section_key": "a"}).status_code == 400
    board = client.post(f"/api/v1/scripts/{sid}/storyboards", json={"format": "long", "style": "flat"}).json()
    assert len(board["words"]) == 10 and board["words"][-1]["section"] == "b"
    run_job(board["job"]["id"])
    assert sorted(calls) == [("Cold open", 6), ("Trick", 4)]
    planned = client.get(f"/api/v1/storyboards/{board['id']}").json()
    # Each chapter's plan is shifted to its place: the second chapter starts on word 6.
    assert [s["start_word"] for s in planned["scenes"]] == [0, 3, 6, 9]
    assert client.get(f"/api/v1/storyboards/{board['id']}/chapters").json()["text"].startswith("0:00 Cold open")
    srt = client.get(f"/api/v1/storyboards/{board['id']}/subtitles.srt")
    assert srt.status_code == 200 and "Your fridge has never made cold." in srt.text

    # Music: upload, attach, render 16:9 with the music ducked under the voice.
    track = tmp_music()
    with track.open("rb") as f:
        music = client.post("/api/v1/music", files={"file": ("calm.mp3", f, "audio/mpeg")}).json()
    assert music["name"] == "calm" and music["duration"] == pytest.approx(3.0, abs=0.1)
    assert client.post("/api/v1/music", files={"file": ("x.txt", b"no", "text/plain")}).status_code == 400
    patched = client.patch(f"/api/v1/storyboards/{board['id']}",
                           json={"music_id": music["id"], "music_db": -18, "effects": False}).json()
    assert (patched["music_id"], patched["music_db"], patched["effects"]) == (music["id"], -18, False)
    job = client.post(f"/api/v1/storyboards/{board['id']}/render").json()
    run_job(job["id"])
    video = animate.video_path({"script_id": sid, "id": board["id"], "format": "long"})
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height:format=duration",
                                       "-of", "json", str(video)], capture_output=True, text=True, check=True).stdout)
    assert {"width": 1920, "height": 1080} in probe["streams"]
    assert float(probe["format"]["duration"]) == pytest.approx(board["duration"], abs=0.15)

    assert client.delete(f"/api/v1/music/{music['id']}").status_code == 204
    assert client.get(f"/api/v1/storyboards/{board['id']}").json()["music_id"] is None


def tmp_music():
    from clipper import config
    path = config.data_dir() / "calm.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=330:duration=3",
                    str(path)], check=True)
    return path


def test_mix_graph_shapes():
    graph, out = sound.mix_graph(10.0, effects=True, music=True, music_db=-20)
    assert out == "[mixed]" and "sidechaincompress" in graph and "amix=inputs=3" in graph and "[3:a]" in graph
    graph, _ = sound.mix_graph(10.0, effects=False, music=False, music_db=-20)
    assert "amix=inputs=1" in graph and "anullsink" in graph



def test_tracks_are_measured_without_ai(tmp_path):
    calm = tmp_path / "calm.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=110:duration=30", str(calm)],
                   check=True)
    clicks = tmp_path / "clicks.wav"   # a click every 0.25 s: 240 a minute, bright and busy
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    "aevalsrc='if(lt(mod(t,0.25),0.01),random(0)*2-1,0)':s=22050:d=30", str(clicks)], check=True)
    a, b = sound.describe_track(calm), sound.describe_track(clicks)
    assert "dark" in a["words"] and a["onsets_per_s"] < 1
    assert b["onsets_per_s"] > 2 and b["brightness_hz"] > a["brightness_hz"]


def test_music_suggestion_and_sound_preview(client, script_with_take, fake_claude, monkeypatch):
    from clipper.studio import music_pick, music_search
    sid = script_with_take
    board = client.post(f"/api/v1/scripts/{sid}/storyboards",
                        json={"format": "short", "section_key": "a", "style": "flat"}).json()
    run_job(board["job"]["id"])

    monkeypatch.setattr(music_search, "candidates", lambda searches, skip, length="", limit=12: [])
    monkeypatch.setattr(music_pick, "searches", lambda *a: ["ukulele"])
    empty = client.post(f"/api/v1/storyboards/{board['id']}/suggest-music", json={"online": False}).json()
    assert empty["choice"] is None and "No music to choose from" in empty["reason"]

    with tmp_music().open("rb") as f:
        track = client.post("/api/v1/music", files={"file": ("calm.mp3", f, "audio/mpeg")}).json()
    assert track["features"]["words"]                                  # measured on upload

    online = {**OPENVERSE_ITEM_FOR_PICK, "features": {"words": ["slow", "sparse", "warm", "steady"], "bpm": 70}}
    asked = {}

    def fake_candidates(searches, skip, length="", limit=12):
        asked.update(searches=searches, skip=skip)
        return [online]
    monkeypatch.setattr(music_search, "candidates", fake_candidates)
    seen = {}

    def fake_ask(task, system, content, output_format, effort="high"):
        seen.update(task=task, content=content)
        return music_pick.Pick(choice="web-5f7af4b0", reason="The ukulele fits.", level_db=-40, ranking=[
            music_pick.Fit(ref="web-5f7af4b0", fit=8, reason="Light and steady"),
            music_pick.Fit(ref=f"lib-{track['id']}", fit=3, reason="Too dark"),
            music_pick.Fit(ref="web-deadbeef", fit=9, reason="Made up")])
    monkeypatch.setattr(llm, "ask", fake_ask)
    pick = client.post(f"/api/v1/storyboards/{board['id']}/suggest-music").json()
    assert asked["searches"] == ["ukulele"] and seen["task"] == "music"
    assert f"lib-{track['id']}" in seen["content"] and "web-5f7af4b0" in seen["content"]
    assert "CC BY, credit needed" in seen["content"] and "no clear beat" in seen["content"]
    assert "uploaded by you, licence unknown" in seen["content"]
    assert pick["choice"] == "web-5f7af4b0" and pick["level_db"] == -34 and pick["searches"] == ["ukulele"]
    assert [(r["source"], r["fit"]) for r in pick["ranking"]] == [("online", 8), ("library", 3)]   # made-up ref dropped
    assert pick["ranking"][0]["openverse_id"] == online["id"] and pick["ranking"][1]["track_id"] == track["id"]

    preview = client.get(f"/api/v1/storyboards/{board['id']}/sound-preview",
                         params={"music_id": track["id"], "music_db": -18, "seconds": 5})
    assert preview.status_code == 200 and preview.headers["content-type"] == "audio/mp4"
    out = tmp_music().parent / "preview.m4a"
    out.write_bytes(preview.content)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
                           capture_output=True, text=True, check=True)
    assert float(probe.stdout) == pytest.approx(3.05, abs=0.2)           # clipped to the Short's length

    # An online track is previewed straight from its source, before adding it.
    monkeypatch.setattr(music_search, "track", lambda oid: {**online, "audio_url": str(tmp_music())})
    heard = client.get(f"/api/v1/storyboards/{board['id']}/sound-preview",
                       params={"openverse_id": online["id"], "seconds": 5})
    assert heard.status_code == 200


OPENVERSE_ITEM_FOR_PICK = {"id": "5f7af4b0-3629-4345-83ae-9ca5bca2e9d9", "title": "Paper Lantern", "creator": "uke",
                           "license": "by", "license_version": "3.0", "license_url": "", "source": "jamendo",
                           "landing_url": "https://www.jamendo.com/track/9", "audio_url": "https://example.org/a.mp3",
                           "duration": 159.0, "genres": ["folk"], "attribution": ""}


@pytest.mark.parametrize("bpm", [72, 100, 120, 150])
def test_tempo_of_a_click_track(tmp_path, bpm):
    path = tmp_path / "beat.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    f"aevalsrc='if(lt(mod(t,60/{bpm}),0.01),random(0)*2-1,0)':s=22050:d=40", str(path)], check=True)
    assert sound.describe_track(path)["bpm"] == pytest.approx(bpm, abs=3)


def test_online_candidates_skip_known_tracks_and_use_the_cache(app_env, monkeypatch):
    from clipper.studio import music_search
    pages = {"ukulele": [{"id": "a", "landing_url": "u1", "duration": 120, "audio_url": "x"},
                         {"id": "b", "landing_url": "known", "duration": 120, "audio_url": "x"},
                         {"id": "c", "landing_url": "u3", "duration": 12, "audio_url": "x"}],
             "ambient": [{"id": "a", "landing_url": "u1", "duration": 120, "audio_url": "x"},
                         {"id": "d", "landing_url": "u4", "duration": 200, "audio_url": "x"}]}
    monkeypatch.setattr(music_search, "search", lambda q, length="": {"results": pages.get(q, [])})
    measured = []
    monkeypatch.setattr(sound, "describe_track", lambda url, seconds=60: measured.append(url) or {"words": ["slow"]})
    items = music_search.candidates(["ukulele"], {"known"})
    assert [i["id"] for i in items] == ["a", "d"]           # known and too-short skipped, fallback genre added
    assert len(measured) == 2 and items[0]["features"] == {"words": ["slow"]}
    music_search.candidates(["ukulele"], {"known"})
    assert len(measured) == 2                               # measured once, then cached


def test_a_prop_is_looked_at_and_drawn_again_when_it_misses(monkeypatch, app_env):
    asked = []

    def fake_ask(task, system, content, output_format, effort="high", cache=False):
        asked.append(task)
        if task == "props":
            return sb.Drawing(svg=SVG.replace('r="180"', f'r="{150 + 10 * asked.count("props")}"'))
        assert task == "prop_check" and content[1]["type"] == "image"
        return sb.Look(matches=False, quality=4, fix="It needs the handle.")
    monkeypatch.setattr(llm, "ask", fake_ask)
    svg = sb.draw_prop("frying_pan", "a cast iron pan")
    assert asked == ["props", "prop_check", "props"]          # one critique, then the redraw is kept
    assert 'r="170"' in svg and art.current_style(svg)


def test_old_flat_props_are_not_offered_to_the_planner_and_get_redrawn(app_env, monkeypatch):
    from clipper.db.models import Storyboard, StudioAsset
    with session_scope() as db:
        db.add(StudioAsset(name="old_coin", description="a coin", svg=SVG, origin="ai"))
        db.add(StudioAsset(name="new_coin", description="a coin", svg=art.mark_style(SVG), origin="ai"))
        db.add(StudioAsset(name="my_coin", description="a coin", svg=SVG, origin="you"))
        script = Script(title="x", sections=[])
        db.add(script)
        db.flush()
        board = Storyboard(script_id=script.id, format="short", style="ink", title="x", words=[], scenes=[],
                           start=0, end=1)
        db.add(board)
        db.flush()
        board_id = board.id
    assert set(sb.library()) == {"new_coin", "my_coin"}
    drawn = []
    monkeypatch.setattr(sb, "draw_prop", lambda name, desc, instruction="": drawn.append(name) or art.mark_style(SVG))
    monkeypatch.setattr(sb, "prop_names", lambda style, scenes: ["old_coin", "new_coin", "my_coin"])
    monkeypatch.setattr(sb, "find_photos", lambda board_id, rep: None)
    monkeypatch.setattr(sb.places_mod, "prepare", lambda board_id, rep: None)
    sb.draw_missing(board_id, ConsoleReporter())
    assert drawn == ["old_coin"]                    # yours are never redrawn
    with session_scope() as db:
        assert art.current_style(db.get(StudioAsset, "old_coin").svg)
        assert not art.current_style(db.get(StudioAsset, "my_coin").svg)


def test_shading_without_an_outline_is_only_a_wash_in_the_ink_look():
    from clipper.studio import ink
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">'
           '<rect x="56" y="56" width="400" height="400" fill="#4a90d9" stroke="#1b1b1b" stroke-width="12"/>'
           '<rect x="56" y="300" width="400" height="156" fill="#35679c"/></svg>')
    prop = ink.ink_prop(svg)
    fills = [el.get("fill") for el, *_ in prop.shapes]
    assert fills == [ink.PAPER, "none"]            # the shadow no longer paints paper over the lines
    assert prop.wash.count("fill=") >= 2


# --- the script's turns reach the planner ---------------------------------------------------

def _hold_for(sections, roles):
    from clipper.studio import script as script_mod
    return {"hash": script_mod.text_hash(sections), "topic": "fridge", "checked_at": "now",
            "roles": [{"n": i, "role": r, "title_question": False} for i, r in enumerate(roles, 1)]}


def _words():
    return ([{"section": "a", "w": w} for w in SECTIONS[0]["text"].split()]
            + [{"section": "b", "w": w} for w in SECTIONS[1]["text"].split()])


def test_turns_become_word_numbers_only_while_the_check_is_current():
    stored = _hold_for(SECTIONS, ["twist", "payoff"])
    marks, why = sb.turn_marks(_words(), SECTIONS, stored)
    assert marks == {0: "twist", 6: "payoff"} and not why
    # Each chapter gets its own turns, numbered from 0 like its narration.
    assert sb.turns_block(marks, 6, 4) == "<turns>\n[0] payoff\n</turns>\n\n"
    assert sb.turns_block(marks, 0, 6) == "<turns>\n[0] twist\n</turns>\n\n"

    edited = [SECTIONS[0], {**SECTIONS[1], "text": "It moves the heat out."}]
    assert sb.turn_marks(_words(), edited, stored) == ({}, "the script's hold check is missing or out of date")
    assert sb.turn_marks(_words(), SECTIONS, None)[0] == {}
    # The take was timed against other words: nothing is marked rather than marking the wrong word.
    other = _words()
    other[7]["w"] = "pushes"
    marks, why = sb.turn_marks(other, SECTIONS, stored)
    assert marks == {0: "twist"}


def test_a_sentence_after_an_ellipsis_starts_on_the_right_word():
    from clipper.studio import hold
    sections = [{"key": "a", "heading": "S", "text": "Your dinner needs… a toolbox. So how?"}]
    assert hold.sentence_starts(sections) == [("a", 0), ("a", 3), ("a", 5)]


def test_the_planner_gets_the_turns_when_current_and_not_when_stale(client, script_with_take, fake_claude,
                                                                    monkeypatch):
    sid = script_with_take
    from clipper.studio import script as script_mod
    with session_scope() as db:
        db.get(VoiceTake, 1).script_hash = script_mod.text_hash(SECTIONS)
        db.get(Script, sid).hold = _hold_for(SECTIONS, ["hook", "payoff"])
    seen = []
    original = sb._plan_chunk

    def spying(system, title, props, words, chapter, turns=""):
        seen.append((chapter, turns))
        return original(system, title, props, words, chapter, turns)
    monkeypatch.setattr(sb, "_plan_chunk", spying)
    board = client.post(f"/api/v1/scripts/{sid}/storyboards", json={"format": "long", "style": "flat"}).json()
    sb.plan(board["id"], ConsoleReporter())
    assert dict(seen) == {"Cold open": "", "Trick": "<turns>\n[0] payoff\n</turns>\n\n"}

    seen.clear()
    with session_scope() as db:
        db.get(Script, sid).hold = {**db.get(Script, sid).hold, "hash": "old"}
    sb.plan(board["id"], ConsoleReporter())
    assert dict(seen) == {"Cold open": "", "Trick": ""}


def test_big_text_is_kept_for_the_payoff_and_two_other_beats():
    from clipper.studio import beats, film
    shots = [{"start_word": 0, "lines": ['0 write "ONE" top big', '5 write "TWO" big', '9 draw fridge']},
             {"start_word": 10, "lines": ['12 write "THREE" big', '20 write "FOUR" big', '30 write "PAYOFF" big']}]
    lost = sb.limit_big(shots, beats.parse_line, "big", {12: "twist", 20: "rehook", 30: "payoff"})
    assert lost == ["ONE", "TWO"]
    assert shots[0]["lines"][:2] == ['0 write "ONE" top', '5 write "TWO"'] and shots[1]["lines"][2].endswith("big")
    # Without a check: the last one (most likely the payoff) and the first two.
    shots = [{"start_word": 0, "lines": [f'{n} big "W{n}" bar' for n in (0, 4, 8, 12, 16)]}]
    assert sb.limit_big(shots, film.parse_line, "bar", {}) == ["W8", "W12"]
    assert sb.limit_big([{"start_word": 0, "lines": ['0 big "A" bar']}], film.parse_line, "bar", {}) == []
