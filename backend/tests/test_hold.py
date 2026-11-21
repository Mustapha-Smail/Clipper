from test_scripts import fake_claude, idea, run_job  # noqa: F401 - fixtures are used by name

from clipper import llm, prompts
from clipper.ai_errors import AiUnavailable
from clipper.db import session_scope
from clipper.db.models import Job, Script, VoiceTake
from clipper.jobs import STAGES, ConsoleReporter
from clipper.studio import hold
from clipper.studio import script as script_mod


def say(words: int, start: str = "") -> str:
    """A sentence of exactly `words` words."""
    lead = start.split()
    return " ".join(lead + ["blah"] * (words - len(lead))) + "."


def stored_for(sections: list[dict], roles: list[str], topic: str = "fridge", asked: tuple = ()) -> dict:
    return {"hash": script_mod.text_hash(sections), "topic": topic, "dropped": 0, "checked_at": "now",
            "roles": [{"n": i, "role": r, "title_question": i in asked} for i, r in enumerate(roles, 1)]}


def by_label(result: dict, start: str) -> dict:
    return next(x for x in result["lines"] if x["label"].startswith(start))


def test_a_short_that_starts_slow_loses_points_line_by_line():
    # 150 words a minute: 2.5 words a second.
    text = " ".join([say(10), say(10, "fridge"), say(20), say(20), say(10), say(50), say(10, "Moving on,"),
                     say(10)])
    sections = [{"key": "a", "heading": "Short", "text": text}]
    roles = ["context", "hook", "fact", "fact", "twist", "fact", "fact", "payoff"]
    result = hold.score(sections, stored_for(sections, roles), "short")

    assert result["current"] and result["stats"] == {"seconds": 56, "first_twist": 24, "longest_gap": 28,
                                                      "topic_at": 2, "timing": "estimated", "wpm": 150}
    assert by_label(result, "Topic named only in sentence 2")["points"] == -12        # 4 s x 3
    assert by_label(result, "First twist at 24 s")["points"] == -20                    # 14 s over, capped
    assert by_label(result, "Longest stretch")["points"] == 0                          # 28 s, within 30
    assert by_label(result, "1 filler sentence")["points"] == -3                       # the stock transition
    assert by_label(result, "No stakes")["points"] == -10
    assert not any(x["label"].startswith("Ends on") for x in result["lines"])
    assert result["score"] == 100 + sum(x["points"] for x in result["lines"]) == 55
    assert result["verdict"] == "likely to lose people"


def test_a_long_script_with_a_sag_a_repeated_question_and_a_weak_ending():
    opening = " ".join([say(10, "fridge"), say(10), say(10), say(25)])
    body = " ".join([say(100), say(150), say(10), say(10)])
    sections = [{"key": "a", "heading": "Cold open", "text": opening}, {"key": "b", "heading": "Body", "text": body}]
    roles = ["hook", "stakes", "question", "twist", "fact", "fact", "question", "fact"]
    result = hold.score(sections, stored_for(sections, roles, asked=(3, 7)), "long")

    assert by_label(result, "Topic named in sentence 1")["points"] == 0
    assert by_label(result, "First twist at 12 s")["points"] == 0
    gap = by_label(result, "Longest stretch without a new question: 110 s")       # sentence 4 to 7: 275 words
    assert gap["points"] == -20 and "Between sentences 4 and 7" in gap["detail"]
    assert by_label(result, "The title's question is asked 2 times")["points"] == -5
    assert by_label(result, "Ends on fact")["points"] == -10
    assert not any(x["label"].startswith("No stakes") for x in result["lines"])
    assert result["score"] == 100 + sum(x["points"] for x in result["lines"]) == 65
    assert [s["section"] for s in result["sentences"]][:5] == ["a", "a", "a", "a", "b"]


def test_an_edit_makes_the_check_out_of_date():
    sections = [{"key": "a", "heading": "Short", "text": say(30, "fridge")}]
    stored = {**stored_for(sections, ["hook"]), "score": 70}
    assert hold.score(sections, stored, "short")["current"]
    edited = [{**sections[0], "text": sections[0]["text"] + " More."}]
    assert hold.score(edited, stored, "short") == {"current": False, "checked_at": "now", "score": None,
                                                   "previous_score": 70}
    assert hold.score(sections, None) is None


def test_labels_for_missing_sentences_are_dropped_and_counted(app_env, monkeypatch):
    sections = [{"key": "a", "heading": "Short", "text": f"{say(8, 'fridge')} {say(8)}"}]
    with session_scope() as db:
        row = Script(title="Fridges", theme="", sections=sections, video="short")
        db.add(row)
        db.flush()
        script_id = row.id

    def fake_ask(task, system, content, output_format, effort="high"):
        assert task == "hold" and "1. fridge blah" in content and "2. blah" in content
        return hold.Labels(topic="Fridges", sentences=[
            hold.Label(n=1, role="hook"), hold.Label(n=2, role="payoff"), hold.Label(n=0, role="fact"),
            hold.Label(n=7, role="twist"), hold.Label(n=2, role="fact")])
    monkeypatch.setattr(llm, "ask", fake_ask)
    assert hold.run(script_id, ConsoleReporter())
    with session_scope() as db:
        stored = db.get(Script, script_id).hold
    assert stored["dropped"] == 3 and [r["role"] for r in stored["roles"]] == ["hook", "payoff"]
    result = hold.score(sections, stored, "short")
    assert by_label(result, "0 sentences without a label, 3 labels dropped")["points"] == 0
    assert by_label(result, "Topic named in sentence 1")       # "Fridges" matches "fridge"


def test_the_job_succeeds_when_the_ai_is_down(client, idea, fake_claude, monkeypatch):  # noqa: F811
    body = client.post(f"/api/v1/ideas/{idea}/script", json={}).json()
    run_job(body["job"]["id"])
    with session_scope() as db:
        db.get(Job, body["job"]["id"]).state = "succeeded"
        db.get(Script, body["id"]).hold = None

    def down(*args, **kwargs):
        raise AiUnavailable("no credit")
    monkeypatch.setattr(llm, "ask", down)
    job = client.post(f"/api/v1/scripts/{body['id']}/hold")
    assert job.status_code == 202 and job.json()["stages"] == ["hold"]

    class Log(ConsoleReporter):
        warned: list = []

        def warn(self, message):
            self.warned.append(message)
    assert hold.run(body["id"], Log()) is False
    assert "AI is unavailable: no credit" in Log.warned[0]
    assert client.get(f"/api/v1/scripts/{body['id']}").json()["hold"] is None    # the page shows "not checked"


def test_the_stage_follows_every_rewrite_and_the_prompt_is_complete():
    for kind in ("script", "redraft", "spoken", "simpler", "hold"):
        assert STAGES[kind][-1] == "hold"
    assert llm.TASKS["hold"][0] == "quick"
    prompt = prompts.Prompt("script_hold")
    assert "{" not in str(prompt) and "rehook" in str(prompt) and "promise" in str(prompt)


def test_promises_loops_and_empty_rehooks_are_arithmetic_with_one_cap():
    sections = [{"key": "a", "heading": "Short", "text": " ".join(say(5, f"fridge {n}") for n in range(1, 9))}]
    stored = stored_for(sections, ["hook", "question", "rehook", "question", "fact", "fact", "fact", "payoff"])
    stored.update(
        promise={"verdict": "missing", "answer_sentence": None, "reason": "The title is never answered."},
        loops=[
            {"id": "main", "question": "Does it make cold?", "opener_sentence": 1,
             "resolver_sentence": None, "main": True},
            {"id": "a", "question": "Where does heat go?", "opener_sentence": 2,
             "resolver_sentence": None, "main": False},
            {"id": "b", "question": "Why are coils warm?", "opener_sentence": 3,
             "resolver_sentence": None, "main": False},
        ],
        rehooks=[{"sentence": 3, "delivers_new_information": False, "opens_loop": None, "reason": "Vague"}],
    )
    result = hold.score(sections, stored, "short")
    assert by_label(result, "The title promise is not paid")["points"] == -15
    assert by_label(result, "2 secondary loops left open")["points"] == -8   # the main loop is not counted again
    assert by_label(result, "1 empty rehook")["points"] == -5
    assert by_label(result, "3 loops open at once")["points"] == -2          # combined additions stop at 30
    additions = [line for line in result["lines"] if line["label"].startswith(
        ("The title", "2 secondary", "1 empty", "3 loops"))]
    assert sum(line["points"] for line in additions) == -30


def test_measured_timing_uses_only_a_chosen_matching_take(app_env):
    sections = [{"key": "a", "heading": "Short", "text": " ".join([say(25, "fridge"), say(25), say(25)])}]
    with session_scope() as db:
        script = Script(title="Fridge", theme="", sections=sections, video="short")
        db.add(script)
        db.flush()
        script.hold = stored_for(sections, ["hook", "twist", "payoff"])
        take = VoiceTake(script_id=script.id, source="recording", voice="me.wav",
                         script_hash=script_mod.text_hash(sections), chosen=True,
                         alignment={"wpm": 100, "script_hash": script_mod.text_hash(sections)})
        db.add(take)
        db.flush()
        result = hold.score_script(script)
        assert result["stats"]["timing"] == "measured" and result["stats"]["wpm"] == 100
        assert result["stats"]["first_twist"] == 15
        take.script_hash = "old"
        fallback = hold.score_script(script)
        assert fallback["stats"]["timing"] == "estimated" and fallback["stats"]["wpm"] == 150
        assert fallback["stats"]["first_twist"] == 10


def test_explicit_promise_checkpoints_drop_bad_refs_and_preserve_order():
    sections = [{"key": "a", "heading": "Short", "text": " ".join(say(4, str(i)) for i in range(1, 6))}]
    promise, dropped, warnings = hold._promise_data(
        {"verdict": "paid", "promise_sentence": 2,
         "consequence_sentences": [{"sentence": 3}, {"sentence": 99}, {"sentence": 2}],
         "payoff_sentence": 5}, len(hold.sentences(sections)))
    assert dropped == 2 and promise["consequence_sentences"] == [{"sentence": 3, "lane": None}]
    assert promise["payoff_sentence"] == 5 and "does not refer" in warnings[0] and "out of order" in warnings[1]


def test_parallel_promise_cannot_claim_both_lane_consequences_when_one_is_missing():
    sections = [{"key": "a", "heading": "Short", "text": " ".join(say(4, str(i)) for i in range(1, 6))}]
    stored = stored_for(sections, ["hook", "consequence", "consequence", "twist", "payoff"])
    stored.update(story_shape="parallel", lane_ids=["a", "b"], promise={
        "verdict": "paid", "promise_sentence": 1,
        "consequence_sentences": [{"sentence": 2, "lane": "a"}], "payoff_sentence": 5,
    })
    result = hold.score(sections, stored, "short")
    assert result["promise"]["consequence_verdict"] == "partial"
    assert result["promise"]["verdict"] == "paid"  # title promise is independent from lane consequences
    assert any("Only some lane consequences" in line["label"] for line in result["lines"])


def test_non_parallel_checkpoint_drops_lane_metadata():
    promise, dropped, warnings = hold._promise_data(
        {"consequence_sentences": [{"sentence": 2, "lane": "a"}]}, 3, story_shape="explain")
    assert promise["consequence_sentences"] == [] and dropped == 1
    assert "non-parallel" in warnings[0]


def test_render_checkpoints_resolve_sentence_times_and_evidence():
    sections = [{"key": "a", "heading": "Short", "text": "One starts. Two turns. Three lands."}]
    stored = stored_for(sections, ["hook", "twist", "payoff"])
    stored["promise"] = {"promise_sentence": 1, "payoff_sentence": 3, "verdict": "paid"}
    words = [{"section": "a", "w": token, "s": i * 0.5, "e": i * 0.5 + 0.2}
             for i, token in enumerate("One starts. Two turns. Three lands.".split())]
    points = hold.checkpoints(sections, stored, words)
    assert [(p["kind"], p["sentence"], p["time_s"]) for p in points] == [
        ("promise", 1, 0.0), ("twist", 2, 1.0), ("payoff", 3, 2.0)]
    assert points[0]["evidence"] == "One starts."
