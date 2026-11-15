"""Claude through the Claude Code CLI: paid by a Claude subscription, not by the API.

The same two calls as `claude.py`, made by running `claude -p` once per call.
The CLI is signed in with the owner's Claude plan: on the Mac by its own login,
on a server by the token `claude setup-token` prints, kept in `.env` as
CLAUDE_CODE_OAUTH_TOKEN. Calls count against the plan's usage limits, shared
with everything else the owner does with Claude.

What differs from the API:

- The CLI is started bare of the owner's own setup (`--safe-mode`) and of every
  tool, in an empty folder: it only answers. Research gets the web search tool
  and nothing else.
- ANTHROPIC_API_KEY is hidden from it. With the key in its environment the CLI
  would bill the API, which is what this provider exists to avoid.
- Web search returns no citation blocks. The model marks each sourced passage
  with [[URL|title]] and a mark is kept only when that URL was really among the
  results of a search it ran, so a source cannot be made up.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ValidationError

from . import config
from .ai_errors import AiUnavailable
from .claude import Pieces

log = logging.getLogger("clipper.claude_cli")

TIMEOUT = 30 * 60    # seconds for one call; a long script at high effort takes a few minutes
SEARCH_TOOL = "WebSearch"
# Any of these would make the CLI use the API (and its billing) instead of the plan.
HIDDEN = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")

SIGN_IN = ("The Claude CLI is not signed in. On a server: put the token from `claude setup-token` in .env as "
           "CLAUDE_CODE_OAUTH_TOKEN and restart. On the Mac: run `claude` and sign in.")

CITING = """

How to give your sources: right after each passage that states facts from a page you found, write its \
source as [[URL|page title]], with the URL copied exactly from your search results. Several sources: \
several marks in a row. Cite only pages your searches returned, never from memory: a mark whose URL was \
not in your results is deleted. The marks are the sources: add no list of sources at the end. Run at most \
{max_searches} web searches. Answer with the notes only."""

MARK = re.compile(r"\s*\[\[\s*(https?://[^\]|\s]+)\s*(?:\|([^\]]*))?\]\]")
URL = re.compile(r"https?://[^\s\"'\\<>\]\)]+")
# The search tool asks for a closing "Sources:" list of links. Ours are the marks, so that list is cut.
SOURCES_TAIL = re.compile(r"\n[#*\s]*sources?[*\s]*:?[*\s]*\n(?:[^\n]*https?://[^\n]*(?:\n|$)|\s*\n)+\s*$", re.I)


def binary() -> str | None:
    """Where the CLI is, or None. CLAUDE_CLI names it when it is not on the PATH."""
    return config.claude_cli() or shutil.which("claude") or next(
        (str(p) for p in (Path.home() / ".local/bin/claude", Path("/usr/local/bin/claude")) if p.exists()), None)


def _env() -> dict:
    env = {k: v for k, v in os.environ.items() if k not in HIDDEN}
    env["DISABLE_AUTOUPDATER"] = "1"
    if config.IN_DOCKER:
        # The container's home may not be writable (a custom `user:`); the data folder always is.
        env.setdefault("CLAUDE_CONFIG_DIR", str(config.data_dir() / "cache" / "claude-cli"))
    return env


def check() -> None:
    """Raise AiUnavailable when the CLI is missing or signed out. Runs no model."""
    path = binary()
    if path is None:
        raise AiUnavailable("The Claude CLI is not installed here.")
    try:
        done = subprocess.run([path, "auth", "status"], capture_output=True, text=True, env=_env(), timeout=30)
        signed_in = json.loads(done.stdout).get("loggedIn")
    except (OSError, subprocess.SubprocessError, ValueError) as err:
        raise AiUnavailable(f"The Claude CLI did not answer ({err.__class__.__name__}).") from err
    if not signed_in:
        raise AiUnavailable(SIGN_IN)


def _resets(limits: dict | None) -> str:
    """ ", back at 14:00 on 16 Nov" from the CLI's last word on the plan's limits."""
    at = (limits or {}).get("resetsAt")
    if not at:
        return ""
    return datetime.fromtimestamp(at).astimezone().strftime(", back at %H:%M on %d %b")


def _failure(result: dict, limits: dict | None) -> Exception:
    text = str(result.get("result") or "").strip() or "no message"
    status = result.get("api_error_status")
    low = text.lower()
    if status in (401, 403) or "/login" in low or "not logged in" in low or "authenticate" in low:
        return AiUnavailable(f"{SIGN_IN} ({text})")
    if status == 429 or "usage limit" in low or "hit your limit" in low or "rate limit" in low:
        return AiUnavailable(f"Your Claude plan's usage limit is reached{_resets(limits)}. Wait, or put this tier "
                             f"on the API in Settings. ({text})")
    if isinstance(status, int) and status >= 500:
        return AiUnavailable(f"Claude is unavailable right now ({status}): {text}")
    return RuntimeError(f"Claude CLI error: {text}")


def _run(model: str, system: str, content, effort: str, tools: str = "",
         schema: dict | None = None) -> tuple[dict, list[dict]]:
    """One `claude -p` call: its result event and every event it printed."""
    path = binary()
    if path is None:
        raise AiUnavailable("The Claude CLI is not installed here. Install Claude Code, or pick another provider "
                            "for this tier in Settings.")
    message = {"type": "user", "message": {"role": "user", "content": content}}
    with tempfile.TemporaryDirectory(prefix="clipper-claude-") as folder:
        # A file, not an argument: Linux refuses a single argument over 128 KB.
        Path(folder, "system.txt").write_text(system, encoding="utf-8")
        command = [path, "-p", "--safe-mode", "--no-session-persistence",
                   "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                   "--system-prompt-file", "system.txt", "--model", model, "--effort", effort, "--tools", tools]
        if tools:
            command += ["--allowedTools", tools, "--permission-prompts", "none"]
        if schema is not None:
            command += ["--json-schema", json.dumps(schema)]
        try:
            done = subprocess.run(command, input=json.dumps(message) + "\n", capture_output=True, text=True,
                                  encoding="utf-8", env=_env(), cwd=folder, timeout=TIMEOUT)
        except FileNotFoundError as err:
            raise AiUnavailable("The Claude CLI could not be started.") from err
        except subprocess.TimeoutExpired as err:
            raise RuntimeError(f"The Claude CLI gave no answer in {TIMEOUT // 60} minutes.") from err

    events = []
    for line in done.stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    result = next((e for e in reversed(events) if e.get("type") == "result"), None)
    if result is None:
        tail = (done.stderr or done.stdout or "").strip()[-300:]
        raise RuntimeError(f"The Claude CLI stopped without an answer (exit {done.returncode}): {tail}")
    limits = next((e.get("rate_limit_info") for e in reversed(events) if e.get("type") == "rate_limit_event"), None)
    if result.get("is_error"):
        raise _failure(result, limits)
    if result.get("subtype") != "success":
        raise RuntimeError(f"The Claude CLI did not finish ({result.get('subtype')}).")
    if result.get("stop_reason") == "refusal":
        raise RuntimeError("Claude declined to process this request.")
    return result, events


def _usage(result: dict, searches: int = 0) -> dict:
    usage = result.get("usage") or {}
    return {"input": usage.get("input_tokens") or 0, "output": usage.get("output_tokens") or 0,
            "cache_read": usage.get("cache_read_input_tokens") or 0,
            "cache_write": usage.get("cache_creation_input_tokens") or 0, "searches": searches}


def ask_model(model: str, system: str, content, output_format: type[BaseModel],
              effort: str = "high", cache: bool = False) -> tuple[BaseModel, dict]:
    """`content` as in `claude.ask_model`. `cache` is accepted for symmetry: the CLI caches by itself."""
    result, _ = _run(model, system, content, effort, schema=output_format.model_json_schema())
    answer = result.get("structured_output")
    if answer is None:
        raise RuntimeError("Claude's answer was cut off or came back in no usable shape.")
    try:
        return output_format.model_validate(answer), _usage(result)
    except ValidationError as err:
        raise RuntimeError(f"Claude's answer did not match the expected shape: {err.errors()[0]['msg']}") from err


def _same(url: str) -> str:
    return url.split("#")[0].rstrip("/").lower()


def found_pages(events: list[dict]) -> tuple[dict[str, tuple[str, str]], int]:
    """Every page the run's searches returned, {normalised url: (url, title)}, and how many searches ran."""
    pages: dict[str, tuple[str, str]] = {}
    searches = 0
    for event in events:
        blocks = (event.get("message") or {}).get("content")
        if not isinstance(blocks, list):
            continue
        if event.get("type") == "assistant":
            searches += sum(1 for b in blocks if b.get("type") == "tool_use" and b.get("name") == SEARCH_TOOL)
            continue
        if event.get("type") != "user":
            continue
        listed = (event.get("tool_use_result") or {}) if isinstance(event.get("tool_use_result"), dict) else {}
        for group in listed.get("results") or []:
            for hit in (group.get("content") or []) if isinstance(group, dict) else []:
                if isinstance(hit, dict) and hit.get("url"):
                    pages.setdefault(_same(hit["url"]), (hit["url"], hit.get("title") or ""))
        for block in blocks:   # the same list as text, in case the structured copy changes shape
            if isinstance(block, dict) and block.get("type") == "tool_result":
                for url in URL.findall(json.dumps(block.get("content"), ensure_ascii=False)):
                    pages.setdefault(_same(url), (url, ""))
    return pages, searches


def cited_pieces(text: str, pages: dict[str, tuple[str, str]]) -> tuple[Pieces, int]:
    """Cut the notes at each [[URL|title]] mark. Marks to pages no search returned are dropped and counted."""
    pieces: Pieces = []
    cursor = dropped = 0
    for mark in MARK.finditer(text):
        passage = text[cursor:mark.start()]
        cursor = mark.end()
        page = pages.get(_same(mark.group(1)))
        if page is None:
            dropped += 1
        source = [(page[0], page[1] or (mark.group(2) or "").strip())] if page else []
        if not passage.strip() and pieces:      # a second mark for the passage before
            if source and source[0] not in pieces[-1][1]:
                pieces[-1][1].extend(source)
        else:
            pieces.append((passage, source))
    if text[cursor:].strip():
        pieces.append((text[cursor:], []))
    return pieces, dropped


def search_model(model: str, system: str, prompt: str, max_searches: int = 8,
                 effort: str = "high") -> tuple[Pieces, int, dict]:
    """Claude searches the web and writes; its text in cited pieces, the search count and usage."""
    result, events = _run(model, system + CITING.format(max_searches=max_searches), prompt, effort,
                          tools=SEARCH_TOOL)
    pages, searches = found_pages(events)
    pieces, dropped = cited_pieces(SOURCES_TAIL.sub("", str(result.get("result") or "")), pages)
    if dropped:
        log.warning("dropped %d source marks that matched no search result", dropped)
    return pieces, searches, _usage(result, searches)
