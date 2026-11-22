import json
import subprocess
import wave

import numpy as np
import pytest

from clipper import llm
from clipper.db import session_scope
from clipper.db.models import Idea, Script, Storyboard, VoiceTake
from clipper.jobs import ConsoleReporter
from clipper.settings import Settings
from clipper.studio import animate, hold, visual_qa, voice
from clipper.studio import script as script_mod

CUE_TEXT = """SETUP: Two identical phones start one download
RULE: One phone loses its network permission
OUTCOME A: The allowed phone fills its progress ring
OUTCOME B: The blocked phone stays at zero
LOOP: Both phones return to the empty start"""


@pytest.fixture()
def micro_script(app_env):
    with session_scope() as db:
        row = Script(
            title="Same phone, hidden rule",
            theme="Digital life",
            format="single",
            video="short",
            form="micro_short",
            story_shape="parallel",
            target_seconds=15,
            target_minutes=1,
            sections=[{"key": "visual", "heading": "Visual loop", "text": CUE_TEXT, "short": True}],
            research={
                "notes": "A visual product demonstration with no factual number.",
                "sources": [{"n": 1, "url": "https://example.test", "title": "Product docs"}],
                "searches": 1,
            },
        )
        db.add(row)
        db.flush()
        return row.id


def test_micro_start_has_a_cue_stage_and_enforces_12_to_18_seconds(client):
    with session_scope() as db:
        idea = Idea(
            title="Two phones",
            theme="Digital life",
            hook="Same start",
            answer="Permission changes it",
            angle="Compare the outcome",
            format="single",
            video="short",
            form="micro_short",
            story_shape="parallel",
            beats=[],
        )
        db.add(idea)
        db.flush()
        idea_id = idea.id
    assert (
        client.post(f"/api/v1/ideas/{idea_id}/script", json={"form": "micro_short", "target_seconds": 11}).status_code
        == 422
    )
    assert (
        client.post(f"/api/v1/ideas/{idea_id}/script", json={"form": "micro_short", "target_seconds": 19}).status_code
        == 422
    )
    started = client.post(f"/api/v1/ideas/{idea_id}/script", json={"form": "micro_short", "target_seconds": 15})
    assert started.status_code == 202
    assert started.json()["job"]["stages"] == ["research", "story_map", "write", "factcheck", "hold", "cue"]


def test_micro_writer_uses_the_visual_contract_without_narration(app_env, micro_script, monkeypatch):
    seen = {}

    def fake_ask(task, system, content, output_format, effort="high"):
        seen.update(task=task, system=str(system), content=content, output=output_format)
        return script_mod.MicroDraft(
            title="Same phone, hidden rule",
            sections=[script_mod.MicroSection(heading="Visual loop", text=CUE_TEXT, short=True)],
        )

    monkeypatch.setattr(llm, "ask", fake_ask)
    script_mod.write(micro_script, ConsoleReporter())
    assert seen["output"] is script_mod.MicroDraft
    assert "silent vertical concept test" in seen["system"]
    assert "Do not imitate" in seen["system"] and "normal captions" in seen["system"]
    with session_scope() as db:
        script = db.get(Script, micro_script)
        assert [cue["label"] for cue in script_mod.micro_cues(script.sections)] == list(script_mod.MICRO_CUE_LABELS)


def test_silent_cue_take_is_real_48khz_audio_with_no_whisper_or_wpm(client, micro_script, monkeypatch):
    monkeypatch.setattr(
        voice.transcribe_mod,
        "transcribe_isolated",
        lambda *args, **kwargs: pytest.fail("silent cues must not call Whisper"),
    )
    hold.run(micro_script, ConsoleReporter())
    voice.cue(micro_script, ConsoleReporter())
    takes = client.get(f"/api/v1/scripts/{micro_script}/takes").json()
    assert len(takes) == 1
    take = takes[0]
    assert take["source"] == "silent" and take["chosen"] and take["ready"]
    assert take["duration"] == 15 and take["alignment"]["mode"] == "designed_cues"
    assert take["alignment"]["spoken_words"] == 0 and take["alignment"]["wpm"] is None
    assert [point["kind"] for point in take["alignment"]["checkpoints"]] == [
        "first_event",
        "rule",
        "reveal",
        "consequence",
        "payoff",
    ]
    with session_scope() as db:
        path = db.get(VoiceTake, take["id"]).path
    with wave.open(path, "rb") as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth(), wav.getnframes()) == (48_000, 1, 2, 720_000)
        assert set(wav.readframes(500)) == {0}
    timeline = client.get(f"/api/v1/takes/{take['id']}/timeline").json()
    assert timeline[0]["s"] == 0 and timeline[-1]["e"] == 15
    assert {token["cue"] for token in timeline} == {"setup", "rule", "outcome_a", "outcome_b", "loop"}
    assert (
        client.post(
            f"/api/v1/scripts/{micro_script}/takes/ai", json={"engine": "kokoro", "voice": "am_michael"}
        ).status_code
        == 409
    )


def test_micro_storyboard_defaults_are_silent_and_freeze_visual_checkpoints(client, micro_script, tmp_path):
    hold.run(micro_script, ConsoleReporter())
    voice.cue(micro_script, ConsoleReporter())
    assert client.post(f"/api/v1/scripts/{micro_script}/storyboards", json={"format": "long"}).status_code == 400
    assert (
        client.post(
            f"/api/v1/scripts/{micro_script}/storyboards", json={"format": "short", "caption_mode": "on"}
        ).status_code
        == 400
    )
    made = client.post(f"/api/v1/scripts/{micro_script}/storyboards", json={"format": "short", "style": "flat"})
    assert made.status_code == 202
    board = made.json()
    assert board["form"] == "micro_short" and board["caption_mode"] == "off"
    assert board["effects"] is False and board["starter_mode"] == board["riser_mode"] == "off"
    assert board["duration"] == 15 and board["section_key"] == "visual"

    preview = client.get(f"/api/v1/storyboards/{board['id']}/sound-preview", params={"effects": False, "seconds": 15})
    assert preview.status_code == 200 and preview.headers["content-type"] == "audio/mp4"
    preview_path = tmp_path / "silent-preview.m4a"
    preview_path.write_bytes(preview.content)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type:format=duration", "-of", "json",
         str(preview_path)], capture_output=True, text=True, check=True)
    media = json.loads(probe.stdout)
    assert [stream["codec_type"] for stream in media["streams"]] == ["audio"]
    assert float(media["format"]["duration"]) == pytest.approx(15, abs=0.1)
    with session_scope() as db:
        row = db.get(Storyboard, board["id"])
        snapshot = animate.render_snapshot(
            row,
            Settings(),
            {
                "first_visual_event_s": 0.0,
                "loop_seam": {"passed": True, "similarity": 1.0},
            },
        )
    assert snapshot["wpm"] is None and snapshot["timing_mode"] == "designed_cues"
    assert [point["kind"] for point in snapshot["micro_checkpoints"]] == [
        "first_event",
        "rule",
        "reveal",
        "consequence",
        "payoff",
    ]


def test_micro_storyboard_prompt_and_loop_seam_are_explicit():
    prompt = str(script_mod.MICRO_WRITE)
    assert "LOOP:" in prompt and "Do not imitate" in prompt

    frame = np.full((160, 90, 3), 20, dtype=np.uint8)
    frame[40:90, 25:65] = 220
    observer = visual_qa.Observer([{"s": 0, "e": 15}], 15, loop=True)
    observer.observe(0.0, frame)
    observer.observe(14.9, frame.copy())
    result = observer.finish()
    assert result["loop_seam"]["passed"] and result["loop_seam"]["similarity"] == 1.0

    changed = frame.copy()
    changed[:, :] = 240
    observer = visual_qa.Observer([{"s": 0, "e": 15}], 15, loop=True)
    observer.observe(0.0, frame)
    observer.observe(14.9, changed)
    result = observer.finish()
    assert not result["loop_seam"]["passed"]
    assert any("final visual differs" in warning for warning in result["warnings"])
