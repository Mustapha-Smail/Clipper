"""The Claude CLI provider, against a fake `claude` process."""

import json
import subprocess
from types import SimpleNamespace as NS

import pytest
from pydantic import BaseModel, Field

from clipper import claude_cli, llm
from clipper import settings as settings_mod
from clipper.ai_errors import AiUnavailable
from clipper.db import session_scope
from clipper.db.models import AiUsage


class Answer(BaseModel):
    word: str
    score: int = Field(ge=0, le=10)


def lines(*events: dict) -> str:
    return "\n".join(json.dumps(e) for e in events) + "\n"


def result(**more) -> dict:
    return {"type": "result", "subtype": "success", "is_error": False, "result": "", "api_error_status": None,
            "usage": {"input_tokens": 12, "output_tokens": 30, "cache_read_input_tokens": 100,
                      "cache_creation_input_tokens": 400}, **more}


@pytest.fixture()
def cli(monkeypatch):
    """Stands in for the process: records how it was started, prints `cli.out`."""
    state = NS(out="", calls=[], system="")

    def run(command, **kwargs):
        if kwargs.get("cwd"):
            state.system = open(f"{kwargs['cwd']}/system.txt", encoding="utf-8").read()
        state.calls.append(NS(command=command, **kwargs))
        return NS(stdout=state.out, stderr="", returncode=0)

    monkeypatch.setattr(claude_cli, "binary", lambda: "/usr/local/bin/claude")
    monkeypatch.setattr(claude_cli.subprocess, "run", run)
    return state


def test_a_structured_answer_is_validated_and_the_api_key_stays_hidden(cli, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-would-bill-the-api")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "plan-token")
    cli.out = lines({"type": "system", "subtype": "init"}, result(structured_output={"word": "blue", "score": 7}))
    answer, usage = claude_cli.ask_model("claude-haiku-5-5", "Answer briefly.", "Sky?", Answer, effort="low")
    assert answer == Answer(word="blue", score=7)
    assert usage == {"input": 12, "output": 30, "cache_read": 100, "cache_write": 400, "searches": 0}

    call = cli.calls[0]
    assert "ANTHROPIC_API_KEY" not in call.env and call.env["CLAUDE_CODE_OAUTH_TOKEN"] == "plan-token"
    flags = call.command
    assert flags[:3] == ["/usr/local/bin/claude", "-p", "--safe-mode"] and "--bare" not in flags
    assert flags[flags.index("--tools") + 1] == "" and "--allowedTools" not in flags      # it can only answer
    assert flags[flags.index("--model") + 1] == "claude-haiku-5-5" and flags[flags.index("--effort") + 1] == "low"
    assert json.loads(flags[flags.index("--json-schema") + 1])["required"] == ["word", "score"]
    assert cli.system == "Answer briefly."
    assert json.loads(call.input) == {"type": "user", "message": {"role": "user", "content": "Sky?"}}


def test_images_go_in_as_content_blocks(cli):
    blocks = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}},
              {"type": "text", "text": "What is drawn?"}]
    cli.out = lines(result(structured_output={"word": "circle", "score": 9}))
    claude_cli.ask_model("claude-haiku-5-5", "sys", blocks, Answer)
    assert json.loads(cli.calls[0].input)["message"]["content"] == blocks


def test_an_answer_of_the_wrong_shape_or_missing_is_an_error(cli):
    cli.out = lines(result(structured_output={"word": "blue", "score": 99}))
    with pytest.raises(RuntimeError, match="expected shape"):
        claude_cli.ask_model("m", "sys", "x", Answer)
    cli.out = lines(result(structured_output=None))
    with pytest.raises(RuntimeError, match="cut off"):
        claude_cli.ask_model("m", "sys", "x", Answer)
    cli.out = "not json at all\n"
    with pytest.raises(RuntimeError, match="without an answer"):
        claude_cli.ask_model("m", "sys", "x", Answer)


@pytest.mark.parametrize(("event", "expected"), [
    ({"result": "Not logged in · Please run /login"}, "not signed in"),
    ({"result": "Failed to authenticate. API Error: 401 OAuth access token is invalid.", "api_error_status": 401},
     "CLAUDE_CODE_OAUTH_TOKEN"),
    ({"result": "You've hit your limit", "api_error_status": 429}, "usage limit is reached, back at"),
    ({"result": "Overloaded", "api_error_status": 529}, "unavailable right now"),
])
def test_outages_are_reported_as_ai_unavailable(cli, event, expected):
    limits = {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": 1794794400}}
    cli.out = lines(limits, result(is_error=True, **event))
    with pytest.raises(AiUnavailable, match=expected):
        claude_cli.ask_model("m", "sys", "x", Answer)


def test_other_failures_are_plain_errors_and_a_missing_cli_is_an_outage(cli, monkeypatch):
    cli.out = lines(result(is_error=True, result="The model m does not exist.", api_error_status=404))
    with pytest.raises(RuntimeError, match="does not exist") as caught:
        claude_cli.ask_model("m", "sys", "x", Answer)
    assert not isinstance(caught.value, AiUnavailable)
    monkeypatch.setattr(claude_cli, "binary", lambda: None)
    with pytest.raises(AiUnavailable, match="not installed"):
        claude_cli.ask_model("m", "sys", "x", Answer)


def search_events(text: str) -> str:
    hits = [{"title": "Tower history", "url": "https://tower.example/history"},
            {"title": "Atlas", "url": "https://atlas.example/eiffel/"}]
    return lines(
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "WebSearch",
                                                       "input": {"query": "eiffel tower completed"}}]}},
        {"type": "user", "tool_use_result": {"query": "q", "results": [{"tool_use_id": "t", "content": hits}]},
         "message": {"content": [{"type": "tool_result", "content": "Links: " + json.dumps(hits)}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}},
        result(result=text))


def test_research_keeps_only_sources_a_search_really_returned(cli):
    cli.out = search_events(
        "Finished in March 1889 [[https://tower.example/history|whatever the model calls it]]. "
        "It was the tallest structure [[https://atlas.example/eiffel|Atlas]][[https://tower.example/history|x]]. "
        "It is made of chocolate [[https://invented.example/fact|Made up]]. No source here.\n\n"
        "**Sources:**\n- [Tower history](https://tower.example/history)\n- [Atlas](https://atlas.example/eiffel/)\n")
    pieces, searches, usage = claude_cli.search_model("claude-sonnet-5-5", "Research this.", "Eiffel", max_searches=3)
    assert searches == 1 and usage["searches"] == 1
    assert pieces == [
        ("Finished in March 1889", [("https://tower.example/history", "Tower history")]),
        (". It was the tallest structure", [("https://atlas.example/eiffel/", "Atlas"),
                                            ("https://tower.example/history", "Tower history")]),
        (". It is made of chocolate", []),          # the invented source is gone, and counted
        (". No source here.", []),
    ]
    flags = cli.calls[0].command
    assert flags[flags.index("--tools") + 1] == "WebSearch" and flags[flags.index("--allowedTools") + 1] == "WebSearch"
    assert "--json-schema" not in flags
    assert cli.system.startswith("Research this.") and "at most 3 web searches" in cli.system


def test_calls_on_the_plan_are_logged_at_no_cost_and_offer_no_prices(app_env, cli, monkeypatch):
    settings_mod.save(settings_mod.Settings(ai_quick_provider="claude_cli", ai_quick_model="claude-haiku-5-5"))
    cli.out = lines(result(structured_output={"word": "ok", "score": 1}))
    assert llm.ask("prop_check", "sys", "look", Answer).word == "ok"
    cli.out = lines(result(is_error=True, result="You've hit your limit", api_error_status=429))
    with pytest.raises(llm.AiUnavailable):
        llm.ask("prop_check", "sys", "look", Answer)
    with session_scope() as db:
        rows = db.query(AiUsage).order_by(AiUsage.id).all()
        assert [(r.provider, r.ok, r.cost_usd, r.outage) for r in rows] == [("claude_cli", True, 0.0, False),
                                                                            ("claude_cli", False, None, True)]
        assert rows[0].input_tokens == 412 and rows[0].cache_read_tokens == 100
    assert llm.health()["ok"] is False

    monkeypatch.setattr(claude_cli, "check", lambda: None)
    offered = llm.available_models("claude_cli", refresh=True)
    assert offered["error"] is None and all(m["price"] is None and m["live"] for m in offered["models"])

    def signed_out():
        raise AiUnavailable(claude_cli.SIGN_IN)
    monkeypatch.setattr(claude_cli, "check", signed_out)
    assert "setup-token" in llm.available_models("claude_cli", refresh=True)["error"]


def test_check_reads_the_login_without_running_a_model(monkeypatch):
    monkeypatch.setattr(claude_cli, "binary", lambda: "/usr/local/bin/claude")
    seen = []

    def run(command, **kwargs):
        seen.append(command)
        return NS(stdout=json.dumps({"loggedIn": len(seen) == 1}), stderr="", returncode=0)

    monkeypatch.setattr(claude_cli.subprocess, "run", run)
    claude_cli.check()
    with pytest.raises(AiUnavailable, match="not signed in"):
        claude_cli.check()
    assert seen[0] == ["/usr/local/bin/claude", "auth", "status"]

    def hangs(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 30)
    monkeypatch.setattr(claude_cli.subprocess, "run", hangs)
    with pytest.raises(AiUnavailable, match="did not answer"):
        claude_cli.check()
