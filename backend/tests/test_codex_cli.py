"""The Codex CLI provider, against a fake `codex` process."""

import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from pydantic import BaseModel, Field

from clipper import codex_cli, llm
from clipper import settings as settings_mod
from clipper.ai_errors import AiUnavailable
from clipper.db import session_scope
from clipper.db.models import AiUsage

FEATURES = "shell_tool  stable  true\napps  stable  true\ncode_mode_host  stable  true\nfast_mode  stable  true\n"
CATALOGUE = {"models": [
    {"slug": "gpt-6-luna", "display_name": "GPT-6-Luna", "visibility": "list", "priority": 6},
    {"slug": "gpt-7-nova", "display_name": "GPT-7-Nova", "visibility": "list", "priority": 1},
    {"slug": "codex-auto-review", "display_name": "Review", "visibility": "hide", "priority": 2}]}


class Answer(BaseModel):
    word: str
    score: int | None = Field(None, ge=0, le=10)


def lines(*events: dict) -> str:
    return "\n".join(json.dumps(e) for e in events) + "\n"


def said(text: str) -> dict:
    return {"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": text}}


DONE = {"type": "turn.completed", "usage": {"input_tokens": 9000, "cached_input_tokens": 8000, "output_tokens": 40}}


@pytest.fixture()
def cli(monkeypatch):
    """Stands in for the process: answers the short commands, records the `exec` call, prints `cli.out`."""
    state = NS(out="", version="codex-cli 0.169.0", login="Logged in using ChatGPT", calls=[], files={})
    codex_cli._features.clear()

    def run(command, **kwargs):
        args = command[1:]
        if args == ["--version"]:
            return NS(stdout=state.version, stderr="", returncode=0)
        if args == ["login", "status"]:
            return NS(stdout="", stderr=state.login, returncode=0)
        if args == ["features", "list"]:
            return NS(stdout=FEATURES, stderr="", returncode=0)
        if args == ["debug", "models"]:
            return NS(stdout=json.dumps(CATALOGUE), stderr="noise on the error stream", returncode=0)
        folder = Path(kwargs["cwd"]).parent
        state.files = {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}
        state.calls.append(NS(command=command, **kwargs))
        return NS(stdout=state.out, stderr="", returncode=0)

    monkeypatch.setattr(codex_cli, "binary", lambda: "/usr/local/bin/codex")
    monkeypatch.setattr(codex_cli.subprocess, "run", run)
    return state


def value(flags: list[str], name: str) -> str:
    return next(f.split("=", 1)[1] for f in flags if f.startswith(name + "="))


def test_a_structured_answer_runs_shut_in_and_off_the_api(cli, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-would-bill-the-api")
    notice = {"type": "item.completed", "item": {"type": "error", "message": "Code Mode is unavailable"}}
    cli.out = lines(notice, {"type": "error", "message": "Reconnecting... 1/5"},
                    said('```json\n{"word": "blue", "score": 7}\n```'), DONE)
    answer, usage = codex_cli.ask_model("gpt-6-luna", "Answer briefly.", "Sky?", Answer, effort="max")
    assert answer == Answer(word="blue", score=7)                      # the two notices did not fail the call
    assert usage == {"input": 1000, "output": 40, "cache_read": 8000}

    call = cli.calls[0]
    flags = call.command
    assert "OPENAI_API_KEY" not in call.env and call.input == "Sky?" and flags[-1] == "-"
    assert flags[:2] == ["/usr/local/bin/codex", "exec"]
    assert {"--json", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check"} <= set(flags)
    assert flags[flags.index("--sandbox") + 1] == "read-only" and flags[flags.index("-m") + 1] == "gpt-6-luna"
    # Every tool-bearing feature this version has is off, the tool host too; unknown names are not sent.
    off = [flags[i + 1] for i, f in enumerate(flags) if f == "--disable"]
    assert off == ["shell_tool", "apps", "code_mode_host"]
    assert value(flags, "web_search") == '"disabled"' and value(flags, "model_reasoning_effort") == '"max"'
    assert cli.files["system.txt"] == b"Answer briefly."
    schema = json.loads(cli.files["schema.json"])       # OpenAI's strict form: everything required, nothing extra
    assert schema["required"] == ["word", "score"] and schema["additionalProperties"] is False
    assert Path(call.cwd).name == "work" and str(call.cwd) != str(Path.cwd())


def test_images_are_attached_as_files(cli):
    blocks = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"}},
              {"type": "text", "text": "What is drawn?"}]
    cli.out = lines(said('{"word": "circle", "score": null}'), DONE)
    assert codex_cli.ask_model("gpt-6-luna", "sys", blocks, Answer)[0].word == "circle"
    flags = cli.calls[0].command
    assert Path(flags[flags.index("--image") + 1]).name == "image-1.jpg" and cli.files["image-1.jpg"] == b"ABC"
    assert cli.calls[0].input == "What is drawn?"


@pytest.mark.parametrize(("change", "expected"), [
    ({"login": "Not logged in"}, "not signed in"),
    ({"login": "Logged in using an API key - sk-...abc"}, "would bill the API"),
    ({"version": "codex-cli 0.147.0"}, "0.147.0; the current models need 0.169.0"),
])
def test_a_cli_that_cannot_use_the_plan_is_refused_before_any_call(cli, change, expected):
    vars(cli).update(change)
    with pytest.raises(AiUnavailable, match=expected):
        codex_cli.ask_model("gpt-6-luna", "sys", "x", Answer)
    assert cli.calls == []


def api_error(status: int, message: str) -> str:
    text = json.dumps({"type": "error", "status": status, "error": {"message": message}})
    return lines({"type": "error", "message": text}, {"type": "turn.failed", "error": {"message": text}})


def test_failures_are_sorted_into_outages_and_errors(cli, monkeypatch):
    cli.out = api_error(429, "You've hit your usage limit. Try again at 3:15 PM.")
    with pytest.raises(AiUnavailable, match="usage limit is reached.*3:15 PM"):
        codex_cli.ask_model("m", "sys", "x", Answer)
    cli.out = api_error(503, "upstream unavailable")
    with pytest.raises(AiUnavailable, match="unavailable right now"):
        codex_cli.ask_model("m", "sys", "x", Answer)
    cli.out = api_error(400, "The 'gpt-0' model is not supported when using Codex with a ChatGPT account.")
    with pytest.raises(RuntimeError, match="Pick another model") as caught:
        codex_cli.ask_model("gpt-0", "sys", "x", Answer)
    assert not isinstance(caught.value, AiUnavailable)
    cli.out = lines(said('{"word": 3}'), DONE)
    with pytest.raises(RuntimeError, match="expected shape"):
        codex_cli.ask_model("m", "sys", "x", Answer)
    cli.out = lines({"type": "turn.started"})
    with pytest.raises(RuntimeError, match="without an answer"):
        codex_cli.ask_model("m", "sys", "x", Answer)
    monkeypatch.setattr(codex_cli, "binary", lambda: None)
    with pytest.raises(AiUnavailable, match="not installed"):
        codex_cli.ask_model("m", "sys", "x", Answer)


def test_research_searches_and_keeps_only_sources_it_found(cli):
    search = {"type": "item.completed", "item": {"type": "web_search", "query": "eiffel height", "action": {
        "type": "search", "query": "eiffel height"}, "results": [
            {"type": "text_result", "title": "Key figures", "url": "https://tower.example/figures"}]}}
    opened = {"type": "item.completed", "item": {"type": "web_search", "query": "", "action": {
        "type": "open_page", "url": "https://tower.example/history"}}}
    cli.out = lines(search, opened, said(
        "It is 330 m tall [[https://tower.example/figures|Figures]]. Built in 1889 "
        "[[https://tower.example/history|History]]. Made of cheese [[https://invented.example/x|Nope]].\n\n"
        "Sources:\n- https://tower.example/figures\n"), DONE)
    pieces, searches, usage = codex_cli.search_model("gpt-6.1-sol", "Research this.", "Eiffel", max_searches=4)
    assert searches == 1 and usage["searches"] == 1
    assert pieces == [("It is 330 m tall", [("https://tower.example/figures", "Key figures")]),
                      (". Built in 1889", [("https://tower.example/history", "History")]),
                      (". Made of cheese", []), (".", [])]
    flags = cli.calls[0].command
    off = [flags[i + 1] for i, f in enumerate(flags) if f == "--disable"]
    assert off == ["shell_tool", "apps"]                      # the tool host stays: it runs the searches
    assert value(flags, "web_search") == '"live"' and "--output-schema" not in flags
    assert b"at most 4 web searches" in cli.files["system.txt"]


def test_the_plan_is_logged_at_no_cost_and_offers_the_catalogue_models(app_env, cli):
    settings_mod.save(settings_mod.Settings(ai_quick_provider="codex_cli", ai_quick_model="gpt-6-luna"))
    cli.out = lines(said('{"word": "ok", "score": 1}'), DONE)
    assert llm.ask("prop_check", "sys", "look", Answer).word == "ok"
    with session_scope() as db:
        row = db.query(AiUsage).one()
        assert (row.provider, row.model, row.ok, row.cost_usd) == ("codex_cli", "gpt-6-luna", True, 0.0)

    offered = llm.available_models("codex_cli", refresh=True)
    assert offered["error"] is None and all(m["price"] is None for m in offered["models"])
    by_id = {m["id"]: m for m in offered["models"]}
    assert by_id["gpt-7-nova"]["live"] and by_id["gpt-6-luna"]["live"]       # a model newer than the app knows
    assert "codex-auto-review" not in by_id and not by_id["gpt-6-astra"]["live"]

    cli.login = "Not logged in"
    assert "codex login" in llm.available_models("codex_cli", refresh=True)["error"]
