import subprocess

import numpy as np
import pytest

from clipper.db import session_scope
from clipper.db.models import Job, Script
from clipper.jobs import ConsoleReporter
from clipper.settings import Settings
from clipper.studio import voice

SECTIONS = [
    {"key": "a", "heading": "Cold open", "text": "Your fridge has never made cold.", "short": True},
    {"key": "b", "heading": "The trick", "text": "It moves heat out of the box and into your kitchen.", "short": False},
]


def spoken(text: str, start: float = 0.0, step: float = 0.4) -> list[dict]:
    return [{"w": w, "s": round(start + i * step, 3), "e": round(start + i * step + 0.3, 3)}
            for i, w in enumerate(text.split())]


def test_a_faithful_reading_is_fully_matched_and_timed():
    words = spoken("Your fridge has never made cold. It moves heat out of the box and into your kitchen.")
    result = voice.match(SECTIONS, words)
    assert result["coverage"] == 1.0 and result["missing"] == [] and result["extra"] == []
    assert [(s["key"], s["start"]) for s in result["sections"]] == [("a", 0.0), ("b", 2.4)]
    assert result["timeline"][6] == {"section": "b", "w": "It", "s": 2.4, "e": 2.7}


def test_skipped_lines_and_asides_are_reported():
    words = spoken("Your fridge has never made cold. Sorry let me start that again. It moves heat into your kitchen.")
    result = voice.match(SECTIONS, words)
    assert [m["text"] for m in result["missing"]] == ["out of the box and"]
    assert [e["text"] for e in result["extra"]] == ["Sorry let me start that again."]
    assert result["coverage"] == pytest.approx(12 / 17, abs=0.001)
    # Skipped words still get times, between the words around them, in order.
    times = [t["s"] for t in result["timeline"]]
    assert times == sorted(times) and all(t["e"] >= t["s"] for t in result["timeline"])


def test_misheard_words_keep_their_place():
    result = voice.match(SECTIONS[:1], spoken("Your fridge has never maid cold."))
    assert result["missing"] == [] and result["coverage"] == pytest.approx(5 / 6, abs=0.001)


def test_long_sections_are_split_at_sentence_ends():
    text = " ".join(f"Sentence number {i} is here." for i in range(40))
    chunks = voice._chunks(text, limit=120)
    assert all(len(c) <= 120 for c in chunks) and " ".join(chunks) == text


# --- the stages and the API -------------------------------------------------------------

@pytest.fixture()
def script(app_env):
    with session_scope() as db:
        row = Script(title="Fridge", theme="", sections=SECTIONS)
        db.add(row)
        db.flush()
        return row.id


@pytest.fixture()
def fake_whisper(monkeypatch):
    def transcribe(src_dir, language, engine, model, force=False, progress=None, media=None):
        assert media.name == "voice.wav" and media.is_file()
        return {"words": spoken("Your fridge has never made cold. It moves heat out of the box and into your kitchen.")}
    monkeypatch.setattr(voice.transcribe_mod, "transcribe_isolated", transcribe)


def tone(seconds: float, rate: int = 24000) -> np.ndarray:
    return (0.2 * np.sin(np.linspace(0, 440 * 2 * np.pi * seconds, int(rate * seconds)))).astype(np.float32)


def run_job(job_id: int) -> None:
    with session_scope() as db:
        job = db.get(Job, job_id)
        stages, take_id = list(job.stages), job.params["take_id"]
    for stage in stages:
        getattr(voice, stage)(take_id, Settings(), ConsoleReporter())
    with session_scope() as db:
        db.get(Job, job_id).state = "succeeded"


def test_ai_take_is_spoken_normalised_and_timed(client, script, fake_whisper, monkeypatch):
    def fake_kokoro(sections, name, speed, out, rep, pauses="natural"):
        assert name == "bm_george" and speed == 1.1 and len(sections) == 2 and pauses == "relaxed"
        voice._write_wav(out, tone(2.0), 24000)
    monkeypatch.setattr(voice, "speak_kokoro", fake_kokoro)

    assert client.post(f"/api/v1/scripts/{script}/takes/ai",
                       json={"engine": "kokoro", "voice": "nobody", "speed": 1}).status_code == 400
    take = client.post(f"/api/v1/scripts/{script}/takes/ai",
                       json={"engine": "kokoro", "voice": "bm_george", "speed": 1.1, "pauses": "relaxed"}).json()
    assert take["job"]["stages"] == ["speak", "align"] and take["job"]["take_id"] == take["id"]
    assert not take["ready"] and take["current"]
    # One job per script at a time.
    assert client.post(f"/api/v1/scripts/{script}/takes/ai",
                       json={"engine": "kokoro", "voice": "bm_george"}).status_code == 409

    run_job(take["job"]["id"])
    done = client.get(f"/api/v1/scripts/{script}/takes").json()[0]
    assert done["ready"] and done["duration"] == pytest.approx(2.0, abs=0.05)
    assert done["alignment"]["coverage"] == 1.0
    audio = client.get(f"/api/v1/takes/{take['id']}/audio")
    assert audio.status_code == 200 and audio.content[:4] == b"RIFF"
    assert len(client.get(f"/api/v1/takes/{take['id']}/timeline").json()) == 17

    chosen = client.post(f"/api/v1/takes/{take['id']}/choose").json()
    assert chosen["chosen"]
    # Editing the script makes the take out of date.
    edited = [{**SECTIONS[0], "text": "Your fridge never made any cold."}, SECTIONS[1]]
    client.put(f"/api/v1/scripts/{script}/sections", json={"sections": edited})
    assert client.get(f"/api/v1/scripts/{script}/takes").json()[0]["current"] is False

    assert client.delete(f"/api/v1/takes/{take['id']}").status_code == 204
    assert not voice.take_dir(script, take["id"]).exists()


def test_recording_upload_is_converted(client, script, fake_whisper, tmp_path):
    source = tmp_path / "me.m4a"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=300:duration=1.5",
                    "-ac", "2", "-ar", "44100", str(source)], check=True)
    assert client.post(f"/api/v1/scripts/{script}/takes/recording",
                       files={"file": ("notes.txt", b"hello", "text/plain")}).status_code == 400
    with source.open("rb") as f:
        take = client.post(f"/api/v1/scripts/{script}/takes/recording",
                           files={"file": ("me.m4a", f, "audio/mp4")}).json()
    assert take["source"] == "recording" and take["voice"] == "me.m4a"
    assert take["job"]["stages"] == ["prepare", "align"]
    run_job(take["job"]["id"])
    done = client.get(f"/api/v1/scripts/{script}/takes").json()[0]
    assert done["ready"] and done["duration"] == pytest.approx(1.5, abs=0.05)
    folder = voice.take_dir(script, take["id"])
    assert (folder / "original.m4a").is_file() and (folder / "voice.wav").is_file()
    client.delete(f"/api/v1/scripts/{script}")
    assert not voice.script_media_dir(script).exists()


def test_elevenlabs_sections_are_joined_with_pauses(app_env, tmp_path, monkeypatch):
    mp3 = tmp_path / "one.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=500:duration=1",
                    "-ar", "44100", str(mp3)], check=True)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test")
    sent = []

    def fake_call(method, path, body=None, accept="application/json"):
        sent.append((path, body))
        return mp3.read_bytes()
    monkeypatch.setattr(voice, "_elevenlabs", fake_call)
    out = tmp_path / "raw.wav"
    voice.speak_elevenlabs(SECTIONS, "voice123", "eleven_multilingual_v2", 1.0, out, ConsoleReporter())
    assert voice.duration_of(out) == pytest.approx(2 + 2 * voice.SECTION_PAUSE, abs=0.1)
    assert sent[0][0] == "/v1/text-to-speech/voice123?output_format=mp3_44100_128"
    assert sent[0][1]["previous_text"] is None and sent[0][1]["next_text"] == SECTIONS[1]["text"]
    assert sent[1][1]["previous_text"] == SECTIONS[0]["text"] and sent[1][1]["voice_settings"]["speed"] == 1.0


def test_voice_options_without_elevenlabs(client, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    options = client.get("/api/v1/voices").json()
    assert {"id": "am_michael", "label": "Michael · American man, calm", "preview": None} in options["kokoro"]
    assert not options["elevenlabs_available"] and options["defaults"]["engine"] == "kokoro"


def test_voice_samples_are_made_once(client, monkeypatch):
    calls = []

    def fake_kokoro(sections, name, speed, out, rep, pauses="natural"):
        calls.append(sections[0]["text"])
        voice._write_wav(out, tone(0.5), 24000)
    monkeypatch.setattr(voice, "speak_kokoro", fake_kokoro)
    body = {"voice": "bm_george", "speed": 1.0, "text": "There is no white LED.   Well, almost none."}
    first = client.post("/api/v1/voices/sample", json=body)
    second = client.post("/api/v1/voices/sample", json=body)
    assert first.status_code == 200 and first.content[:4] == b"RIFF" and second.content == first.content
    assert calls == ["There is no white LED. Well, almost none."]          # cached, spaces tidied
    assert client.post("/api/v1/voices/sample", json={**body, "voice": "nobody"}).status_code == 400



def test_sentences_and_a_narrators_delivery():
    text = "Here's the thing: there's no white LED. Well… almost none. The bulb in your lamp? Blue."
    assert voice.sentences(text) == ["Here's the thing: there's no white LED.", "Well…", "almost none.",
                                     "The bulb in your lamp?", "Blue."]
    rng = np.random.default_rng(0)
    punch_speed, punch_pause = voice.delivery("Not white.", False, rng)
    long_speed, long_pause = voice.delivery(" ".join(["word"] * 20) + ".", False, rng)
    _, question = voice.delivery("Why would it be blue at all?", False, rng)
    _, paragraph = voice.delivery("And that is the whole trick of it.", True, rng)
    assert punch_speed < 1 < long_speed and punch_pause > long_pause
    assert question > long_pause * 1.1 and paragraph > 0.8


def test_kokoro_reads_sentence_by_sentence_with_pauses(app_env, monkeypatch):
    spoken = []

    class FakeKokoro:
        def __init__(self, *a):
            pass

        def create(self, text, voice, speed, lang):
            spoken.append((text, round(speed, 2)))
            return np.ones(2400, dtype=np.float32) * 0.1, 24000
    import sys
    import types
    monkeypatch.setitem(sys.modules, "kokoro_onnx", types.SimpleNamespace(Kokoro=FakeKokoro))
    monkeypatch.setattr(voice, "_download_kokoro", lambda rep: None)
    sections = [{"text": "One idea here. Blue.\n\nA new paragraph."}, {"text": "Next section."}]
    lengths = {}
    for pauses in ("tight", "relaxed"):
        out = app_env / f"{pauses}.wav"
        voice.speak_kokoro(sections, "am_michael", 1.0, out, voice.Reporter(), pauses)
        lengths[pauses] = voice.duration_of(out)
    assert [t for t, _ in spoken[:4]] == ["One idea here.", "Blue.", "A new paragraph.", "Next section."]
    assert lengths["relaxed"] > lengths["tight"] + 0.8      # four pauses, each 30% longer or shorter
