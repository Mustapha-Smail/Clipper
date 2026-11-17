import pytest

from clipper import llm
from clipper.db import session_scope
from clipper.db.models import Idea, Job, Script
from clipper.jobs import ConsoleReporter
from clipper.studio import hold
from clipper.studio import script as script_mod

NOTES = "## Fridges\n- A fridge moves heat out of the box.[1]\n- The coils at the back get warm.[2]"


@pytest.fixture()
def idea(app_env):
    with session_scope() as db:
        row = Idea(title="Your fridge doesn't make cold", theme="Everyday things", hook="Why?", answer="Heat moves.",
                   angle="", format="single", search_query="fridge", beats=[{"text": "Heat pump", "short": True}])
        db.add(row)
        db.flush()
        return row.id


@pytest.fixture()
def fake_claude(monkeypatch):
    """Claude answers from fixed data; records what it was asked."""
    calls = []

    def fake_search(task, system, prompt, max_searches=8, effort="high", sources=None):
        calls.append(("search", prompt))
        sources = sources or llm.Sources()
        if "<claims>" in prompt:
            sources.number("https://example.org/door", "Open door test")
            return "Claim 1 is supported [3].", sources, 2
        sources.number("https://example.org/fridge", "How fridges work")
        sources.number("https://example.org/coils", "Condenser coils")
        return NOTES, sources, 4

    def fake_ask(task, system, content, output_format, effort="high"):
        calls.append((output_format.__name__, content))
        if output_format is script_mod.Draft:
            return script_mod.Draft(title="Your Fridge Has Never Made Cold", sections=[
                script_mod.Section(heading="Cold open", text="Your fridge has never made cold.", short=True),
                script_mod.Section(heading="The trick", text="It moves heat. The coils get warm. "
                                   "An open door warms the kitchen.", short=False)])
        if output_format is script_mod.Claims:
            return script_mod.Claims(claims=[
                script_mod.Claim(section="x", claim="A fridge moves heat", verdict="supported", sources=[1], note=""),
                script_mod.Claim(section="x", claim="An open door warms the kitchen", verdict="not_in_notes",
                                 sources=[], note="Not in notes"),
                script_mod.Claim(section="x", claim="Never made cold", verdict="joke", sources=[], note="")])
        if output_format is script_mod.WebVerdicts:
            return script_mod.WebVerdicts(verdicts=[
                script_mod.WebVerdict(index=1, verdict="supported", sources=[3], note="Confirmed")])
        if output_format is script_mod.Rewrite:
            return script_mod.Rewrite(text="  Shorter version.  ")
        if output_format is hold.Labels:
            return hold.Labels(topic="fridge", sentences=[
                hold.Label(n=1, role="hook"), hold.Label(n=2, role="twist"), hold.Label(n=3, role="fact"),
                hold.Label(n=4, role="payoff"), hold.Label(n=9, role="fact")])
        raise AssertionError(output_format)

    monkeypatch.setattr(llm, "search", fake_search)
    monkeypatch.setattr(llm, "ask", fake_ask)
    return calls


def run_job(job_id: int) -> None:
    """Run a queued job's stages directly, as the worker would."""
    with session_scope() as db:
        job = db.get(Job, job_id)
        stages, script_id = list(job.stages), job.params["script_id"]
    for stage in stages:
        run = hold.run if stage == "hold" else getattr(script_mod, stage)
        run(script_id, ConsoleReporter())


def test_sources_are_numbered_once_in_order():
    sources = llm.Sources([{"n": 1, "url": "https://a", "title": "A"}])
    assert sources.number("https://b", "B") == 2
    assert sources.number("https://a", "A again") == 1
    assert sources.number("https://c", "") == 3 and sources.items[2]["title"] == "https://c"


def test_counts_and_hash_follow_the_text():
    sections = [{"key": "a", "heading": "Open", "text": "One two three"}]
    assert script_mod.word_count(sections) == 3
    edited = [{**sections[0], "text": "One two four"}]
    assert script_mod.text_hash(sections) != script_mod.text_hash(edited)


def test_idea_to_checked_script(client, idea, fake_claude):
    started = client.post(f"/api/v1/ideas/{idea}/script", json={})
    assert started.status_code == 202
    body = started.json()
    assert body["target_minutes"] == 6 and body["job"]["kind"] == "script"
    assert body["job"]["script_id"] == body["id"]
    assert body["job"]["stages"] == ["research", "write", "factcheck", "hold"]
    assert client.get("/api/v1/ideas").json()[0]["status"] == "approved"
    assert client.get("/api/v1/ideas").json()[0]["script_id"] == body["id"]

    # A second job on the same script is refused while one is queued.
    assert client.post(f"/api/v1/scripts/{body['id']}/factcheck").status_code == 409

    run_job(body["job"]["id"])
    with session_scope() as db:
        db.get(Job, body["job"]["id"]).state = "succeeded"

    script = client.get(f"/api/v1/scripts/{body['id']}").json()
    assert script["title"] == "Your Fridge Has Never Made Cold"
    assert [s["n"] for s in script["research"]["sources"]] == [1, 2, 3]   # the web check added source 3
    assert script["words"] == 19 and script["checked_current"]
    verdicts = {c["claim"]: c["verdict"] for c in script["claims"]}
    assert verdicts == {"A fridge moves heat": "supported", "An open door warms the kitchen": "supported",
                        "Never made cold": "joke"}
    assert script["claim_counts"] == {"supported": 2, "contradicted": 0, "unclear": 0, "joke": 1}
    # The hold check ran last: the label for a sentence that does not exist was dropped and counted.
    assert script["hold"]["current"] and script["hold"]["topic"] == "fridge"
    assert [x["role"] for x in script["hold"]["sentences"]] == ["hook", "twist", "fact", "payoff"]
    assert any("1 labels dropped" in x["label"] for x in script["hold"]["lines"])
    # The writer was given only the research notes, and the web check only the uncovered claim.
    assert any(name == "Draft" and NOTES in content for name, content in fake_claude)
    assert [p for kind, p in fake_claude if kind == "search"][1].count("\n") == 2

    approved = client.patch(f"/api/v1/scripts/{body['id']}", json={"status": "approved"}).json()
    assert approved["status"] == "approved"
    sections = script["sections"]
    sections[1]["text"] += " Also, penguins."
    sections.append({"heading": "Outro", "text": "Subscribe.", "short": False})
    saved = client.put(f"/api/v1/scripts/{body['id']}/sections", json={"sections": sections}).json()
    assert saved["status"] == "draft" and not saved["checked_current"]
    assert saved["sections"][2]["key"] and saved["sections"][0]["key"] == sections[0]["key"]

    rewritten = client.post(f"/api/v1/scripts/{body['id']}/rewrite",
                            json={"key": sections[0]["key"], "instruction": "shorter"}).json()
    assert rewritten["text"] == "Shorter version."
    assert client.post(f"/api/v1/scripts/{body['id']}/rewrite",
                       json={"key": "nope", "instruction": "shorter"}).status_code == 404

    text = client.get(f"/api/v1/scripts/{body['id']}/text").text
    assert text.startswith("YOUR FRIDGE HAS NEVER MADE COLD") and "[Outro]\nSubscribe." in text

    listed = client.get("/api/v1/scripts").json()
    assert listed[0]["id"] == body["id"] and listed[0]["job"] is None

    job = client.post(f"/api/v1/scripts/{body['id']}/redraft").json()
    assert job["kind"] == "redraft" and job["stages"] == ["write", "factcheck", "hold"]


def test_write_refuses_without_research(app_env, idea):
    with session_scope() as db:
        row = Script(idea_id=idea, title="T", theme="", sections=[])
        db.add(row)
        db.flush()
        script_id = row.id
    with pytest.raises(RuntimeError, match="Research the script first"):
        script_mod.write(script_id, ConsoleReporter())



def test_spoken_rewrite_keeps_keys_and_can_be_undone(client, idea, fake_claude, monkeypatch):
    body = client.post(f"/api/v1/ideas/{idea}/script", json={}).json()
    run_job(body["job"]["id"])
    with session_scope() as db:
        db.get(Job, body["job"]["id"]).state = "succeeded"
    before = client.get(f"/api/v1/scripts/{body['id']}").json()
    keys = [s["key"] for s in before["sections"]]

    def fake_ask(task, system, content, output_format, effort="high"):
        assert task == "script_write" and "Write for the ear" in system and keys[0] in content
        return script_mod.SpokenScript(sections=[
            script_mod.SpokenSection(key=keys[0], text="So. Your fridge? Never made cold."),
            script_mod.SpokenSection(key="made-up", text="Nope.")])
    monkeypatch.setattr(llm, "ask", fake_ask)
    job = client.post(f"/api/v1/scripts/{body['id']}/spoken").json()
    assert job["stages"] == ["spoken", "hold"]
    script_mod.make_spoken(body["id"], ConsoleReporter())
    with session_scope() as db:
        db.get(Job, job["id"]).state = "succeeded"
    after = client.get(f"/api/v1/scripts/{body['id']}").json()
    assert [s["key"] for s in after["sections"]] == keys                       # same sections, same order
    assert after["sections"][0]["text"] == "So. Your fridge? Never made cold."
    assert after["sections"][1]["text"] == before["sections"][1]["text"]      # not returned: kept
    assert after["can_undo_rewrite"] and not after["checked_current"]

    undone = client.post(f"/api/v1/scripts/{body['id']}/undo-rewrite").json()
    assert undone["sections"][0]["text"] == before["sections"][0]["text"] and undone["can_undo_rewrite"]
    redone = client.post(f"/api/v1/scripts/{body['id']}/undo-rewrite").json()
    assert redone["sections"][0]["text"].startswith("So.")


def test_new_scripts_are_written_for_the_ear():
    assert "Write for the ear" in script_mod.WRITE and "Contractions" in script_mod.WRITE


def test_the_brief_plans_the_hook_the_lock_in_and_the_expected_guess(app_env, idea):
    with session_scope() as db:
        db.get(Idea, idea).expected = "Fridges make cold air"
        row = Script(idea_id=idea, title="Your fridge", theme="Everyday things", sections=[], video="short",
                     target_seconds=50)
        db.add(row)
        db.flush()
        script_id = row.id
    brief = script_mod._brief(script_mod._load(script_id))
    assert "names the topic" in brief and "Lock-in" in brief and "two rehook lines" in brief
    assert "Expected guess: Fridges make cold air" in brief
    with session_scope() as db:
        db.get(Idea, idea).expected = ""
        db.get(Script, script_id).target_seconds = 40
    brief = script_mod._brief(script_mod._load(script_id))
    assert "Expected guess" not in brief and "one rehook line" in brief
