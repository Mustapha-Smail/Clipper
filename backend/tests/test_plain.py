"""Plain language: the audience setting, its rules in every prompt, the readability check and 'Make it simpler'."""

from test_scripts import fake_claude, idea, run_job  # noqa: F401 - fixtures are used by name

from clipper import llm
from clipper import settings as settings_mod
from clipper.db import session_scope
from clipper.db.models import Job
from clipper.jobs import ConsoleReporter
from clipper.studio import ideas, plain
from clipper.studio import script as script_mod

DENSE = [{"text": (
    "Meet Dionysius Exiguus. He's a sixth-century monk, born in Scythia Minor, which is the area of modern Dobruja, "
    "shared by Romania and Bulgaria, and he's working in Rome. In 525, Dionysius built a new one. A 95-year table, "
    "covering the years 532 to 626. The old table counted its years from the emperor Diocletian, who instigated the "
    "last major persecution of Christians in the Roman Empire. The old table ended at Diocletian year 247. His began "
    "at AD 532.")}]
SIMPLE = [{"text": (
    "Easter is never on the same day two years in a row. Why? Easter follows the moon. Wait for the first full moon "
    "of spring, and Easter is the Sunday after. But the church doesn't look at the real moon. It uses a moon "
    "calendar worked out long ago, and sometimes it's a day off. That one day can push Easter back four whole weeks.")}]


def test_the_check_explains_every_point_and_spares_plain_words():
    dense = plain.check(DENSE, "everyone", "Your Calendar Year Is Based on a Monk's Bad Math")
    simple = plain.check(SIMPLE, "everyone", "Why Easter Never Stays Put")
    assert simple["score"] >= 85 and simple["verdict"] == "plain"
    assert simple["names"] == []                                   # Easter, Sunday: everyday words
    assert dense["score"] < 65 and dense["verdict"] == "hard to follow"
    assert dense["score"] == 100 + sum(line["points"] for line in dense["lines"])
    assert {"Dionysius", "Exiguus", "Scythia", "Dobruja"} <= set(dense["names"])
    assert plain.check(DENSE, "enthusiasts", "x")["score"] > dense["score"]          # the bar depends on who watches
    assert plain.check([{"text": "Too short."}]) is None


def test_a_date_is_one_number_and_plurals_are_one_name():
    assert plain._numbers("2025: April 20. In 2027, March 28.") == ["April 20", "March 28", "2025", "2027"]
    assert plain._names(["The LED and the LEDs.", "Then Gauss's formula."], set()) == ["LED", "Gauss"]


def test_the_audience_reaches_every_prompt(app_env):
    settings_mod.save(settings_mod.Settings(audience="enthusiasts"))
    assert "know its basic terms" in plain.rules() and "know its basic terms" in plain.idea_rules()
    settings_mod.save(settings_mod.Settings())
    assert "12-year-old" in plain.rules()
    assert "{" not in ideas.GENERATE.format(audience_rules=plain.idea_rules())
    assert "12-year-old" in script_mod.WRITE.format(plain=plain.rules())


def test_make_it_simpler_keeps_keys_and_can_be_undone(client, idea, fake_claude, monkeypatch):  # noqa: F811
    body = client.post(f"/api/v1/ideas/{idea}/script", json={}).json()
    run_job(body["job"]["id"])
    with session_scope() as db:
        db.get(Job, body["job"]["id"]).state = "succeeded"
    before = client.get(f"/api/v1/scripts/{body['id']}").json()
    keys = [s["key"] for s in before["sections"]]
    seen = {}

    def fake_ask(task, system, content, output_format, effort="high"):
        seen.update(task=task, system=system, content=content)
        return script_mod.SpokenScript(sections=[
            script_mod.SpokenSection(key=keys[0], text="Your fridge? It moves heat.")])
    monkeypatch.setattr(llm, "ask", fake_ask)
    job = client.post(f"/api/v1/scripts/{body['id']}/simpler").json()
    assert job["stages"] == ["simpler"]
    script_mod.make_simpler(body["id"], ConsoleReporter())
    with session_scope() as db:
        db.get(Job, job["id"]).state = "succeeded"
    assert seen["task"] == "simplify" and "12-year-old" in seen["system"] and "<research_notes>" in seen["content"]
    after = client.get(f"/api/v1/scripts/{body['id']}").json()
    assert [s["key"] for s in after["sections"]] == keys and after["sections"][0]["text"].startswith("Your fridge?")
    assert after["can_undo_rewrite"] and not after["checked_current"]
    undone = client.post(f"/api/v1/scripts/{body['id']}/undo-rewrite").json()
    assert undone["sections"][0]["text"] == before["sections"][0]["text"]
    assert "simplify" in llm.TASKS
