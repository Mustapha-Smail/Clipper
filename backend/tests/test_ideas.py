from datetime import UTC, datetime, timedelta

import pytest
from idea_jobs import generate

from clipper.db.models import Idea
from clipper.studio import ideas, youtube

RECENT = (datetime.now(UTC) - timedelta(days=200)).isoformat()
OLD = (datetime.now(UTC) - timedelta(days=365 * 6)).isoformat()


def video(views, subs, published=RECENT, duration=600, title="A video"):
    return {"id": f"v{views}", "title": title, "channel": f"ch{subs}", "subscribers": subs, "views": views,
            "published": published, "duration_s": duration}


def judgement(**over):
    base = dict(hook_score=8, hook_reason="", easy_score=8, easy_reason="",
                drawable_score=9, drawable_reason="", facts_score=9, facts_reason="",
                ad_friendly=True, ad_reason="", angle_taken=False, angle_reason="", big_recent_same="")
    return {**base, **over}


def idea(research=None, judged=None, shorts=3):
    beats = [{"text": f"beat {i}", "short": i < shorts} for i in range(5)]
    return Idea(title="Your fridge doesn't make cold", hook="", answer="", angle="", format="single",
                search_query="how does a fridge work", beats=beats, research=research, judgement=judged)


def research(*videos):
    return {"query": "q", "checked_at": RECENT, "videos": list(videos)}


def test_unchecked_idea_has_no_score():
    result = ideas.assess(idea())
    assert result["score"] is None and result["breakdown"] == []


def test_breakdown_adds_up_and_full_marks_need_demand_room_and_a_strong_idea():
    strong = ideas.assess(idea(research(video(3_000_000, 5_000_000, OLD), video(2_000_000, 2_000_000, OLD),
                                        video(1_500_000, 50_000, OLD), video(900_000, 30_000, OLD),
                                        video(700_000, 20_000, OLD)),
                               judgement(hook_score=10, easy_score=10, drawable_score=10, facts_score=10), shorts=5))
    assert strong["score"] == 100 and strong["complete"]
    assert round(sum(line["points"] for line in strong["breakdown"])) == strong["score"]
    assert strong["demand"]["reference_views"] == 1_500_000
    assert strong["demand"]["best_small_channel"] == "ch50000"


def test_shorts_do_not_count_as_demand_and_no_small_channel_scores_no_room():
    result = ideas.assess(idea(research(video(50_000_000, 10_000, duration=40), video(20_000, 5_000_000)),
                               judgement()))
    lines = {line["label"]: line["points"] for line in result["breakdown"]}
    assert result["demand"]["reference_views"] == 20_000
    assert lines["Room: no channel under 100K subscribers in the results"] == 0


def test_penalties_are_shown_as_lines_and_flags():
    clean = ideas.assess(idea(research(video(500_000, 50_000)), judgement()))
    risky = ideas.assess(idea(research(video(500_000, 50_000)),
                              judgement(ad_friendly=False, ad_reason="War footage", big_recent_same="Big video",
                                        facts_score=3, facts_reason="Rests on a legend")))
    labels = [line["label"] for line in risky["breakdown"]]
    assert "Likely to get limited ads" in labels
    assert "A channel over 1M subscribers did this angle recently" in labels
    assert len(risky["flags"]) == 3
    assert risky["score"] < clean["score"] - 25


def test_without_youtube_the_score_is_partial_and_says_so():
    top = judgement(hook_score=10, easy_score=10, drawable_score=10, facts_score=10)
    result = ideas.assess(idea(None, top, shorts=5))
    assert not result["complete"] and result["score"] == 50
    assert any("YOUTUBE_API_KEY" in f for f in result["flags"])


def test_new_hook_total_is_code_owned_and_each_dimension_has_a_score_line():
    judged = judgement(hook_score=10, context_score=2, lean_score=1, turn_score=0,
                       promise_score=2, first_visual_score=1)
    result = ideas.assess(idea(None, judged, shorts=0))
    hook_lines = [line for line in result["breakdown"] if line["label"].startswith("Hook ")]
    assert hook_lines == [
        {"label": "Hook context 2/2", "points": 3.0},
        {"label": "Hook reason to lean in 1/2", "points": 1.5},
        {"label": "Hook truthful turn 0/2", "points": 0.0},
        {"label": "Hook promise matches answer 2/2", "points": 3.0},
        {"label": "Hook first visual 1/2", "points": 1.5},
    ]
    # The five dimensions contribute 9 points, not the untrusted legacy total's 15.
    legacy = ideas.assess(idea(None, judgement(hook_score=10), shorts=0))
    assert result["score"] == legacy["score"] - 6


def test_finalise_hook_score_sums_dimensions_and_old_judgements_keep_their_total():
    fresh = ideas.Judgement(**judgement(hook_score=9, context_score=2, lean_score=2, turn_score=1,
                                        promise_score=2, first_visual_score=0))
    assert ideas.finalise_hook_score(fresh).hook_score == 7
    old = ideas.Judgement(**judgement(hook_score=8))
    assert ideas.finalise_hook_score(old).hook_score == 8


def test_iso_durations():
    assert youtube.seconds("PT1H2M3S") == 3723
    assert youtube.seconds("PT45S") == 45
    assert youtube.seconds("P1DT1S") == 86401
    assert youtube.seconds("") == 0


def test_research_joins_search_videos_and_channels(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "test")
    calls = []

    def fake_get(endpoint, **params):
        calls.append(endpoint)
        if endpoint == "search":
            return {"items": [{"id": {"videoId": "a"}}, {"id": {"videoId": "b"}}]}
        if endpoint == "videos":
            return {"items": [
                {"id": "a", "snippet": {"title": "A", "channelId": "c1", "channelTitle": "One",
                                        "publishedAt": "2020-01-01T00:00:00Z"},
                 "statistics": {"viewCount": "1200"}, "contentDetails": {"duration": "PT5M"}},
                {"id": "b", "snippet": {"title": "B", "channelId": "c2", "channelTitle": "Two",
                                        "publishedAt": "2024-01-01T00:00:00Z"},
                 "statistics": {"viewCount": "99"}, "contentDetails": {"duration": "PT30S"}}]}
        return {"items": [{"id": "c1", "statistics": {"subscriberCount": "4000"}},
                          {"id": "c2", "statistics": {"hiddenSubscriberCount": True}}]}

    monkeypatch.setattr(youtube, "_get", fake_get)
    result = youtube.research("fridge")
    assert calls == ["search", "videos", "channels"]
    assert result["videos"][0] == {"id": "a", "title": "A", "channel": "One", "subscribers": 4000, "views": 1200,
                                   "published": "2020-01-01T00:00:00Z", "duration_s": 300}
    assert result["videos"][1]["subscribers"] is None


def test_missing_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    with pytest.raises(youtube.YouTubeError, match="YOUTUBE_API_KEY"):
        youtube._get("search", q="x")


# --- API --------------------------------------------------------------------------------

def draft(title):
    return ideas.Draft(title=title, hook="Why?", expected="e", answer="Because.", angle="Funny", format="single",
                       search_query="how does it work",
                       beats=[ideas.Beat(text="one", short=True), ideas.Beat(text="two", short=False)])


@pytest.fixture()
def fake_claude(monkeypatch):
    seen = {}

    def fake_generate(theme, count, existing, seed=""):
        seen.update(theme=theme, existing=existing, seed=seed)
        return [draft(seed or f"Idea {i}") for i in range(count)]

    monkeypatch.setattr(ideas, "generate", fake_generate)
    monkeypatch.setattr(ideas, "judge", lambda idea, research: ideas.Judgement(**judgement()))
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    return seen


def test_generate_validate_decide_and_edit(client, fake_claude):
    made = generate(client, count=3)
    assert [i["title"] for i in made] == ["Idea 0", "Idea 1", "Idea 2"]
    assert fake_claude["theme"] == "How everyday things secretly work"
    assert made[0]["score"] is None

    generate(client, count=1, theme="Odd history")
    assert fake_claude["existing"] == ["Idea 0", "Idea 1", "Idea 2"]   # Claude is told what you already have

    checked = client.post(f"/api/v1/ideas/{made[1]['id']}/validate").json()
    assert checked["score"] == 39 and not checked["complete"] and checked["validated_at"]
    listed = client.get("/api/v1/ideas").json()
    assert listed[0]["id"] == made[1]["id"]                             # checked ideas come first

    approved = client.patch(f"/api/v1/ideas/{made[1]['id']}", json={"status": "approved", "notes": "Go"}).json()
    assert approved["status"] == "approved" and approved["score"] == 39
    edited = client.patch(f"/api/v1/ideas/{made[1]['id']}", json={"title": "Better title"}).json()
    assert edited["score"] is None and edited["validated_at"] is None  # the old check no longer applies

    assert edited["expected"] == "e"                                   # the writer's guess came through
    checked = client.post(f"/api/v1/ideas/{made[1]['id']}/validate").json()
    guessed = client.patch(f"/api/v1/ideas/{made[1]['id']}", json={"expected": "It just gets cold"}).json()
    assert guessed["expected"] == "It just gets cold" and guessed["validated_at"] is None   # a new guess, a new check
    assert client.get("/api/v1/ideas").json()[0]["expected"] in ("It just gets cold", "e")

    mine = client.post("/api/v1/ideas", json={"text": "Why barcodes have no prices"}).json()
    assert mine["origin"] == "you" and mine["title"] == "Why barcodes have no prices"
    assert client.delete(f"/api/v1/ideas/{mine['id']}").status_code == 204
    assert client.get("/api/v1/system/status").json()["youtube_key"] is False


def test_theme_suggestions_avoid_existing_themes(client, monkeypatch):
    from clipper import llm
    seen = {}

    def fake_ask(task, system, content, output_format, effort="high"):
        seen.update(task=task, content=content)
        return ideas.ThemeIdeas(themes=[ideas.ThemeIdea(theme=f"Theme {i}", why="Because", example="Ex")
                                        for i in range(7)])
    monkeypatch.setattr(llm, "ask", fake_ask)
    monkeypatch.setattr(ideas, "generate", lambda theme, count, existing, seed="": [
        ideas.Draft(title="T", hook="h", expected="e", answer="a", angle="x", format="single", search_query="q",
                    beats=[])])
    generate(client, count=1, theme="Odd history")

    themes = client.post("/api/v1/ideas/themes", json={}).json()
    assert len(themes) == 5 and themes[0] == {"theme": "Theme 0", "why": "Because", "example": "Ex"}
    assert seen["task"] == "themes" and "Odd history" in seen["content"]
    assert "How everyday things secretly work" in seen["content"] and "five different themes" in seen["content"]
    client.post("/api/v1/ideas/themes", json={"hint": "money"})
    assert "leaning towards: money" in seen["content"]


def test_an_idea_that_needs_background_says_so_and_old_checks_are_not_penalised():
    hard = ideas.assess(idea(None, judgement(easy_score=3, easy_reason="needs the Julian calendar")))
    assert any("Julian calendar" in f for f in hard["flags"])
    assert {"label": "Easy to follow 3/10", "points": 3.0} in hard["breakdown"]
    old = judgement()
    del old["easy_score"], old["easy_reason"]
    before = ideas.assess(idea(None, old))
    assert any(line["label"].startswith("Easy to follow: not checked") for line in before["breakdown"])
