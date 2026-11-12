from types import SimpleNamespace as NS

import pytest
from pydantic import BaseModel

from clipper import llm, openai_compat
from clipper import settings as settings_mod
from clipper.db import session_scope
from clipper.db.models import AiUsage


class Answer(BaseModel):
    word: str


def test_tasks_follow_their_tier_and_calls_are_logged_with_cost(app_env, monkeypatch):
    settings_mod.save(settings_mod.Settings(ai_quick_provider="openai", ai_quick_model="gpt-6-luna"))
    calls = []

    def fake_openai(model, system, content, output_format, effort, cache=False):
        calls.append(("openai", model))
        return Answer(word="hi"), {"input": 1000, "output": 200, "cache_read": 0}

    def fake_claude(model, system, content, output_format, effort, cache=False):
        calls.append(("anthropic", model))
        raise RuntimeError("Your Anthropic credit balance is too low.")

    monkeypatch.setattr(openai_compat, "ask_model", fake_openai)
    monkeypatch.setattr(llm.claude, "ask_model", fake_claude)
    assert llm.ask("props", "sys", "draw", Answer).word == "hi"
    with pytest.raises(RuntimeError, match="credit"):
        llm.ask("script_write", "sys", "write", Answer)
    assert calls == [("openai", "gpt-6-luna"), ("anthropic", "claude-opus-5-5")]
    with session_scope() as db:
        rows = db.query(AiUsage).order_by(AiUsage.id).all()
        assert [(r.task, r.tier, r.ok) for r in rows] == [("props", "quick", True), ("script_write", "writing", False)]
        assert rows[0].cost_usd == pytest.approx((1000 * 0.10 + 200 * 0.50) / 1e6)
        assert rows[1].cost_usd is None and "credit" in rows[1].error


def test_estimates_cover_cache_and_searches_and_skip_unknown_models():
    assert llm.estimate("claude-sonnet-5-5", {"input": 1e6, "cache_read": 1e6, "output": 1e6, "searches": 3}) == \
        pytest.approx(2 + 0.2 + 10 + 0.03)
    assert llm.estimate("some-local-model", {"input": 10}) is None


def test_search_numbers_cited_sources_in_order(app_env, monkeypatch):
    pieces = [("Fridges move heat.", [("https://a", "A")]),
              (" Coils get warm.", [("https://b", "B"), ("https://a", "A")]),
              (" No source here.", [])]
    monkeypatch.setattr(llm.claude, "search_model", lambda *a: (pieces, 2, {"input": 5, "output": 5, "searches": 2}))
    notes, sources, searches = llm.search("research", "sys", "prompt", sources=llm.Sources([{"n": 1, "url": "https://z",
                                                                                              "title": "Z"}]))
    assert notes == "Fridges move heat.[2] Coils get warm.[3][2] No source here."
    assert [s["url"] for s in sources.items] == ["https://z", "https://a", "https://b"] and searches == 2


# --- the OpenAI provider, against fake clients ----------------------------------------------------

class FakeResponses:
    def __init__(self):
        self.sent = {}

    def parse(self, **kwargs):
        self.sent = kwargs
        return NS(output_parsed=Answer(word="ok"), status="completed",
                  usage=NS(input_tokens=120, output_tokens=30, input_tokens_details=NS(cached_tokens=20)))

    def create(self, **kwargs):
        self.sent = kwargs
        text = "LEDs are blue inside. Phosphor makes them white."
        return NS(usage=NS(input_tokens=50, output_tokens=20, input_tokens_details=NS(cached_tokens=0)), output=[
            NS(type="web_search_call"),
            NS(type="message", content=[NS(type="output_text", text=text, annotations=[
                NS(type="url_citation", url="https://doe.gov", title="DOE", start_index=0, end_index=21)])])])


@pytest.fixture()
def fake_openai(monkeypatch):
    responses = FakeResponses()
    chat = NS(sent={})

    def chat_parse(**kwargs):
        chat.sent = kwargs
        return NS(choices=[NS(message=NS(parsed=None, content='```json\n{"word": "compat"}\n```'))],
                  usage=NS(prompt_tokens=10, completion_tokens=3, prompt_tokens_details=None))

    client = NS(responses=responses, chat=NS(completions=NS(parse=chat_parse)))
    monkeypatch.setattr(openai_compat, "_client", lambda: client)
    return responses, chat


def test_openai_uses_the_responses_api_with_images(fake_openai, monkeypatch):
    responses, _ = fake_openai
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    content = [{"type": "text", "text": "Look"},
               {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"}}]
    answer, usage = openai_compat.ask_model("gpt-6.1-sol", "sys", content, Answer, "max")
    assert answer.word == "ok" and usage == {"input": 100, "output": 30, "cache_read": 20}
    sent = responses.sent
    assert sent["text_format"] is Answer and sent["reasoning"] == {"effort": "high"} and sent["instructions"] == "sys"
    assert sent["input"][0]["content"][1] == {"type": "input_image", "image_url": "data:image/jpeg;base64,QUJD"}


def test_compatible_servers_use_chat_completions_and_validate_json(fake_openai, monkeypatch):
    _, chat = fake_openai
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    answer, usage = openai_compat.ask_model("llama-local", "sys", "hi", Answer)
    assert answer.word == "compat" and usage["input"] == 10
    assert chat.sent["messages"][0] == {"role": "system", "content": "sys"}
    with pytest.raises(RuntimeError, match="no web search"):
        openai_compat.search_model("llama-local", "sys", "find")


def test_openai_web_search_citations_become_pieces(fake_openai, monkeypatch):
    responses, _ = fake_openai
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    pieces, searches, usage = openai_compat.search_model("gpt-6.1-sol", "sys", "find", max_searches=3)
    assert pieces == [("LEDs are blue inside.", [("https://doe.gov", "DOE")]), (" Phosphor makes them white.", [])]
    assert searches == 1 and usage["searches"] == 1
    assert responses.sent["tools"] == [{"type": "web_search"}]


def test_missing_openai_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        openai_compat._client()


# --- usage report, music search, ideas inbox --------------------------------------------------------

def test_usage_report_groups_by_task_and_model(client):
    from clipper.db.models import AiUsage
    with session_scope() as db:
        db.add(AiUsage(task="props", tier="quick", provider="anthropic", model="claude-sonnet-5-5",
                       input_tokens=1000, output_tokens=100, cost_usd=0.003))
        db.add(AiUsage(task="props", tier="quick", provider="openai", model="local-llama", input_tokens=50,
                       output_tokens=5, cost_usd=None))
        db.add(AiUsage(task="research", tier="research", provider="anthropic", model="claude-sonnet-5-5",
                       searches=4, cost_usd=0.05, ok=True))
    report = client.get("/api/v1/system/ai-usage", params={"days": 7}).json()
    assert report["calls"] == 3 and report["cost_usd"] == pytest.approx(0.053)
    tasks = {line["key"]: line for line in report["by_task"]}
    assert tasks["props"]["calls"] == 2 and tasks["props"]["unpriced_calls"] == 1
    assert tasks["research"]["label"] == "Researching scripts" and tasks["research"]["searches"] == 4
    assert report["tiers"]["quick"]["model"] == "claude-haiku-5-5"
    assert "Drawing props" in report["tiers"]["quick"]["tasks"]


OPENVERSE_ITEM = {"id": "5f7af4b0-3629-4345-83ae-9ca5bca2e9d9", "title": "Intro", "creator": "zero-project",
                  "license": "by", "license_version": "3.0",
                  "license_url": "https://creativecommons.org/licenses/by/3.0/",
                  "url": "https://example.org/track.mp3", "foreign_landing_url": "https://www.jamendo.com/track/1",
                  "duration": 222000, "source": "jamendo", "genres": ["electronic"],
                  "attribution": "\"Intro\" by zero-project is licensed under CC BY 3.0."}


def test_music_search_and_import_keep_the_credit(client, monkeypatch, tmp_path):
    import subprocess

    from clipper.studio import music_search
    seen = []

    def fake_get(url):
        seen.append(url)
        if "/audio/?" in url:
            return {"result_count": 1, "page": 1, "page_count": 1, "results": [OPENVERSE_ITEM]}
        return OPENVERSE_ITEM
    monkeypatch.setattr(music_search, "_get_json", fake_get)

    def fake_download(url, target):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=300:duration=2",
                        "-f", "mp3", str(target)], check=True)
    monkeypatch.setattr(music_search, "download", fake_download)

    found = client.get("/api/v1/music/search", params={"q": "calm piano", "length": "medium"}).json()
    assert found["results"][0]["duration"] == 222.0
    assert "license=cc0%2Cpdm%2Cby" in seen[0] and "category=music" in seen[0] and "length=medium" in seen[0]
    track = client.post("/api/v1/music/import", json={"openverse_id": OPENVERSE_ITEM["id"]}).json()
    assert track["license"] == "by" and track["attribution"].startswith("\"Intro\" by zero-project")
    again = client.post("/api/v1/music/import", json={"openverse_id": OPENVERSE_ITEM["id"]}).json()
    assert again["id"] == track["id"]                       # not downloaded twice

    OPENVERSE_ITEM["license"] = "by-nc"
    try:
        assert client.post("/api/v1/music/import", json={"openverse_id": "0" * 36}).status_code == 400
    finally:
        OPENVERSE_ITEM["license"] = "by"


def test_generated_ideas_wait_in_the_inbox(client, monkeypatch):
    from clipper.studio import ideas
    monkeypatch.setattr(ideas, "generate", lambda theme, count, existing, seed="": [
        ideas.Draft(title=seed or f"Idea {i}", hook="h", answer="a", angle="x", format="single", search_query="q",
                    beats=[]) for i in range(1 if seed else count)])
    made = client.post("/api/v1/ideas/generate", json={"count": 2, "theme": "Odd history"}).json()
    assert [i["status"] for i in made] == ["inbox", "inbox"] and made[0]["theme"] == "Odd history"
    mine = client.post("/api/v1/ideas", json={"text": "Why ships float"}).json()
    assert mine["status"] == "new"                           # your own ideas skip the inbox
    kept = client.patch(f"/api/v1/ideas/{made[0]['id']}", json={"status": "new"}).json()
    assert kept["status"] == "new"



def test_model_lists_put_known_models_first_and_survive_errors(monkeypatch):
    llm._model_cache.clear()
    monkeypatch.setattr(llm, "_live_ids", lambda p: ["claude-zeta-1", "claude-haiku-5-5"] if p == "anthropic"
                        else (_ for _ in ()).throw(RuntimeError("No OpenAI key.\nmore")))
    claude_models = llm.available_models("anthropic")
    ids = [m["id"] for m in claude_models["models"]]
    assert ids[:4] == list(llm.KNOWN["anthropic"]) and ids[-1] == "claude-zeta-1"
    assert next(m for m in claude_models["models"] if m["id"] == "claude-haiku-5-5")["live"] is True
    assert next(m for m in claude_models["models"] if m["id"] == "claude-opus-5-5")["live"] is False
    openai_models = llm.available_models("openai")
    assert openai_models["error"] == "No OpenAI key." and [m["id"] for m in openai_models["models"]] == \
        list(llm.KNOWN["openai"])
