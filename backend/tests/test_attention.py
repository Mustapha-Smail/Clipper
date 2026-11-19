import numpy as np
import pytest
from sqlalchemy import inspect

from clipper import db as db_mod
from clipper.db import session_scope
from clipper.db.models import Script, Storyboard
from clipper.jobs import ConsoleReporter
from clipper.settings import Settings
from clipper.studio import animate, chatterbox, sound, voice
from clipper.studio import script as script_mod

SECTIONS = [{"key": "a", "heading": "Open", "text": "Your fridge never made cold. But here is the strange part. "
                                                    "It moves heat out of the box. So the kitchen gets warmer."}]
ROLES = ["hook", "rehook", "twist", "payoff"]


def board(roles=ROLES, sections=SECTIONS):
    words = [{"section": "a", "w": w, "s": i * 0.5, "e": i * 0.5 + 0.4}
             for i, w in enumerate(SECTIONS[0]["text"].split())]
    hold = {"hash": script_mod.text_hash(sections), "topic": "fridge", "checked_at": "now",
            "roles": [{"n": n, "role": r, "title_question": False} for n, r in enumerate(roles, 1)]}
    return {"words": words, "sections": SECTIONS, "hold": hold}


def word_time(data):
    return lambda i: data["words"][i]["s"]


def test_starter_and_risers_land_where_they_should():
    data = board()
    events, notes = animate.attention_events(data, word_time(data), Settings(sound_starter=True, sound_riser=True))
    # Twist "It moves..." starts on word 11 (5.5 s), the payoff "So the..." on word 18 (9.0 s): each riser ends there.
    assert events == [(0.0, "starter"), (5.5 - sound.RISER_SECONDS, "riser"), (9.0 - sound.RISER_SECONDS, "riser")]
    assert any("riser into the twist at 5.5 s" in n for n in notes)


def test_nothing_is_added_when_off_or_when_the_check_is_stale():
    data = board()
    assert animate.attention_events(data, word_time(data), Settings()) == ([], [])
    stale = {**data, "hold": {**data["hold"], "hash": "old"}}
    events, notes = animate.attention_events(stale, word_time(stale), Settings(sound_riser=True))
    assert events == [] and "out of date" in notes[0]


@pytest.mark.parametrize(("mode", "inherited", "expected"), [
    ("default", False, False), ("default", True, True), ("on", False, True), ("on", True, True),
    ("off", False, False), ("off", True, False),
])
def test_per_video_modes_resolve_against_settings(mode, inherited, expected):
    assert animate.effective_attention(mode, inherited) is expected


def test_effects_off_suppresses_even_explicit_attention_sounds():
    data = {**board(), "effects": False, "starter_mode": "on", "riser_mode": "on"}
    events, notes = animate.attention_events(data, word_time(data), Settings())
    assert events == [] and "suppressed" in notes[0]


def test_failed_render_keeps_the_previous_snapshot(app_env):
    with session_scope() as db:
        script = Script(title="No voice", sections=[])
        db.add(script)
        db.flush()
        row = Storyboard(script_id=script.id, title="No voice", style="flat",
                         scenes=[{"start_word": 0, "background": "paper", "camera": "still", "elements": []}],
                         render_meta={"version": 1, "at": "before"})
        db.add(row)
        db.flush()
        board_id = row.id
    with pytest.raises(RuntimeError, match="voice take"):
        animate.render(board_id, Settings(), ConsoleReporter())
    with session_scope() as db:
        assert db.get(Storyboard, board_id).render_meta == {"version": 1, "at": "before"}


def test_render_experiment_migration_goes_down_and_up_on_sqlite(app_env):
    from alembic import command

    command.downgrade(db_mod._alembic_config(), "b6e2c8f4a913")
    columns = {column["name"] for column in inspect(db_mod.get_engine()).get_columns("storyboards")}
    assert not {"starter_mode", "riser_mode", "render_meta"} & columns
    command.upgrade(db_mod._alembic_config(), "head")
    columns = {column["name"] for column in inspect(db_mod.get_engine()).get_columns("storyboards")}
    assert {"starter_mode", "riser_mode", "render_meta"} <= columns


def test_a_twist_too_early_for_a_riser_is_skipped_and_said():
    data = board(["twist", "rehook", "fact", "payoff"])
    events, notes = animate.attention_events(data, word_time(data), Settings(sound_riser=True))
    assert events == [(9.0 - sound.RISER_SECONDS, "riser")] and "too early" in notes[0]


def test_the_attention_sounds_are_synthesised():
    rng = np.random.default_rng(3)
    rise = sound.riser(rng)
    assert len(rise) == int(sound.RATE * sound.RISER_SECONDS)
    assert abs(rise[: sound.RATE // 4]).max() < abs(rise[-sound.RATE // 4:]).max()       # it rises
    assert abs(sound.starter(rng)[:2000]).max() > 0.5                                        # it starts at once
    track = sound.effects_track([(0.0, "starter"), (1.0, "riser")], 3.0)
    assert abs(track[: sound.RATE // 10]).max() > 0


def test_pace_is_words_a_minute_with_pauses():
    timeline = [{"w": "x", "s": i * 0.4, "e": i * 0.4 + 0.3} for i in range(30)]   # 30 words in 11.9 s
    assert voice.pace(timeline) == round(30 * 60 / 11.9)
    assert voice.pace(timeline[:5]) is None


def test_rehooks_get_a_lift_and_a_short_opens_with_appropriate_energy(monkeypatch):
    said = voice.sentences(SECTIONS[0]["text"])
    lifts = voice.lifted(said, ["But here is the strange part."])
    assert lifts == frozenset({1})
    seen = {}

    def ask(task, system, content, fmt, effort="high", cache=False):
        seen.update(system=system, content=content)
        return fmt(marks=[{"n": 0, "mood": "dramatic"}, {"n": 1, "mood": "surprised"}])
    monkeypatch.setattr(chatterbox.llm, "ask", ask)
    out = chatterbox.direct(said, ConsoleReporter(), short=True, lifts=lifts)
    assert "<video>a YouTube Short</video>" in seen["content"] and "1. (rehook) But here" in seen["content"]
    assert "decisive" in seen["system"] and "never manufacture urgency" in seen["system"]
    assert out[1].startswith("[surprised]")
