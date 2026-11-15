"""OpenAI models through the Codex CLI: paid by a ChatGPT plan, not by the API.

The twin of `claude_cli.py`: one `codex exec` per call, signed in with the
owner's ChatGPT plan (`codex login`, or `codex login --device-auth` on a
machine with no browser). Calls count against the plan's Codex limits.

Codex is a coding agent and cannot be started with no tools at all, so it is
shut in instead:

- The owner's own Codex setup is ignored (config, rules, project notes), it
  runs in an empty folder with a read-only sandbox, and every feature that
  carries a tool is switched off: no shell, no apps, no plugins, no browser.
- For a plain answer its last tool host is off too, so nothing it calls can
  run. Research keeps that host, which is how these models search the web; it
  reads no files (checked 2026-11-15 on Codex 0.169).
- OPENAI_API_KEY is hidden from it, and a CLI signed in with an API key is
  refused: either would bill the API.
- Sources work as in `claude_cli`: the model marks each sourced passage and a
  mark is kept only when one of the run's own searches returned that URL.

The models offered are the ones the CLI's own catalogue lists, so new ones
appear when the CLI is updated.
"""

import base64
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from pydantic import BaseModel, ValidationError

from . import claude_cli, config
from .ai_errors import AiUnavailable
from .claude import Pieces

log = logging.getLogger("clipper.codex_cli")

TIMEOUT = 30 * 60
MINIMUM = (0, 169, 0)   # older CLIs cannot read today's model catalogue and are refused the current models
# Any of these would make the CLI use the API (and its billing) instead of the plan.
HIDDEN = ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL")
# Everything that gives the agent a tool or loads someone's add-ons. Names a version does not know are skipped.
TOOL_FEATURES = ("shell_tool", "unified_exec", "apps", "plugins", "remote_plugin", "hooks", "multi_agent",
                 "multi_agent_v2", "image_generation", "view_image", "browser_use", "browser_use_external",
                 "browser_use_full_cdp_access", "in_app_browser", "computer_use", "goals", "memories",
                 "tool_suggest", "skill_search", "workspace_dependencies", "personality")
CODE_HOST = "code_mode_host"    # runs the model's tool calls, web search among them
EFFORTS = {"low": "low", "medium": "medium", "high": "high", "xhigh": "xhigh", "max": "max"}
IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}

SIGN_IN = ("The Codex CLI is not signed in to your ChatGPT plan. On a server: run `docker compose exec worker "
           "codex login --device-auth` and follow the link. On the Mac: run `codex login`.")

_features: dict[str, set[str]] = {}


def binary() -> str | None:
    """Where the CLI is, or None. CODEX_CLI names it when it is not on the PATH."""
    return config.codex_cli() or shutil.which("codex") or next(
        (str(p) for p in (Path("/usr/local/bin/codex"), Path.home() / ".local/bin/codex") if p.exists()), None)


def home() -> str | None:
    """In Docker, the folder `codex login` keeps the sign-in in, made if missing: the image points
    CODEX_HOME at the data folder, which outlives the container, and the CLI refuses a folder that
    does not exist. Elsewhere None: the CLI uses the owner's own ~/.codex."""
    if not config.IN_DOCKER:
        return None
    folder = Path(os.environ.get("CODEX_HOME") or config.data_dir() / "cache" / "codex-cli")
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        log.warning("could not create the Codex CLI folder %s", folder)
    return str(folder)


def _env() -> dict:
    env = {k: v for k, v in os.environ.items() if k not in HIDDEN}
    folder = home()
    if folder:
        env["CODEX_HOME"] = folder
    return env


def _tell(path: str, *args: str, errors: bool = True) -> str:
    """What a short CLI command prints (no model is run); `errors` adds what it printed as errors."""
    try:
        done = subprocess.run([path, *args], capture_output=True, text=True, env=_env(), timeout=30)
    except (OSError, subprocess.SubprocessError) as err:
        raise AiUnavailable(f"The Codex CLI did not answer ({err.__class__.__name__}).") from err
    return f"{done.stdout}\n{done.stderr}" if errors else done.stdout


def check() -> None:
    """Raise AiUnavailable when the CLI is missing, too old, signed out or signed in with an API key."""
    path = binary()
    if path is None:
        raise AiUnavailable("The Codex CLI is not installed here.")
    found = re.search(r"(\d+)\.(\d+)\.(\d+)", _tell(path, "--version"))
    if found and tuple(map(int, found.groups())) < MINIMUM:
        raise AiUnavailable(f"The Codex CLI here is version {found.group(0)}; the current models need "
                            f"{'.'.join(map(str, MINIMUM))} or later. Update it with `codex update`.")
    status = _tell(path, "login", "status").lower()
    if "not logged in" in status or "logged in" not in status:
        raise AiUnavailable(SIGN_IN)
    if "chatgpt" not in status:
        raise AiUnavailable("The Codex CLI is signed in with an API key, which would bill the API. Run `codex "
                            "logout`, then `codex login` with your ChatGPT account.")


def models() -> list[tuple[str, str]]:
    """(id, name) of the models the CLI's catalogue lists, best first as the catalogue orders them."""
    path = binary()
    if path is None:
        raise AiUnavailable("The Codex CLI is not installed here.")
    try:
        listed = json.loads(_tell(path, "debug", "models", errors=False)).get("models") or []
    except (ValueError, AttributeError):
        return []
    listed = [m for m in listed if isinstance(m, dict) and m.get("slug") and m.get("visibility") == "list"]
    listed.sort(key=lambda m: m.get("priority") or 99)
    return [(m["slug"], m.get("display_name") or m["slug"]) for m in listed]


def _switched_off(path: str, research: bool) -> list[str]:
    """`--disable` flags for every tool-bearing feature this CLI version has."""
    if path not in _features:
        _features[path] = {line.split()[0] for line in _tell(path, "features", "list").splitlines() if line.split()}
    names = [n for n in TOOL_FEATURES if n in _features[path]]
    if not research and CODE_HOST in _features[path]:
        names.append(CODE_HOST)
    return [flag for name in names for flag in ("--disable", name)]


def _failure(message: str) -> Exception:
    """The CLI's error text, often the API's JSON error as a string, as the right kind of error."""
    status, text = None, message.strip() or "no message"
    try:
        inner = json.loads(text)
        status = inner.get("status")
        text = (inner.get("error") or {}).get("message") or text
    except (ValueError, AttributeError):
        pass
    low = text.lower()
    if status in (401, 403) or "not logged in" in low or "unauthorized" in low or "sign in" in low:
        return AiUnavailable(f"{SIGN_IN} ({text})")
    if status == 429 or "usage limit" in low or "rate limit" in low or "quota" in low:
        return AiUnavailable(f"Your ChatGPT plan's Codex usage limit is reached. Wait, or put this tier on "
                             f"another provider in Settings. ({text})")
    if isinstance(status, int) and status >= 500 or "failed to connect" in low or "stream disconnected" in low:
        return AiUnavailable(f"OpenAI is unavailable right now: {text}")
    if "not supported when using codex" in low:
        return RuntimeError(f"{text} Pick another model for this tier in Settings, or update the Codex CLI.")
    return RuntimeError(f"Codex CLI error: {text}")


def _prompt(content, folder: str) -> tuple[str, list[str]]:
    """Anthropic-shaped content -> the prompt text and the image files to attach."""
    if isinstance(content, str):
        return content, []
    texts, images = [], []
    for block in content:
        if block.get("type") == "text":
            texts.append(block["text"])
        elif block.get("type") == "image":
            source = block["source"]
            file = Path(folder, f"image-{len(images) + 1}{IMAGE_TYPES.get(source['media_type'], '.png')}")
            file.write_bytes(base64.b64decode(source["data"]))
            images.append(str(file))
    return "\n\n".join(texts), images


def _run(model: str, system: str, content, effort: str, research: bool = False,
         schema: dict | None = None) -> tuple[str, list[dict], dict]:
    """One `codex exec` call: its last message, the events it printed and its token usage."""
    check()   # a signed-out CLI retries its connection for ever instead of saying so
    path = binary()
    with tempfile.TemporaryDirectory(prefix="clipper-codex-") as folder:
        work = Path(folder, "work")      # empty: the agent's working folder
        work.mkdir()
        Path(folder, "system.txt").write_text(system, encoding="utf-8")
        prompt, images = _prompt(content, folder)
        command = [path, "exec", "--json", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config",
                   "--ignore-rules", "--sandbox", "read-only", "--color", "never", "-C", str(work), "-m", model,
                   "-c", f"model_reasoning_effort={json.dumps(EFFORTS.get(effort, 'medium'))}",
                   "-c", f"model_instructions_file={json.dumps(str(Path(folder, 'system.txt')))}",
                   "-c", "project_doc_max_bytes=0",
                   "-c", f"web_search={json.dumps('live' if research else 'disabled')}",
                   *_switched_off(path, research)]
        if schema is not None:
            Path(folder, "schema.json").write_text(json.dumps(schema), encoding="utf-8")
            command += ["--output-schema", str(Path(folder, "schema.json"))]
        for image in images:
            command += ["--image", image]
        command.append("-")   # the prompt comes on stdin
        try:
            done = subprocess.run(command, input=prompt, capture_output=True, text=True, encoding="utf-8",
                                  env=_env(), cwd=work, timeout=TIMEOUT)
        except FileNotFoundError as err:
            raise AiUnavailable("The Codex CLI could not be started.") from err
        except subprocess.TimeoutExpired as err:
            raise RuntimeError(f"The Codex CLI gave no answer in {TIMEOUT // 60} minutes.") from err

    events = []
    for line in done.stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    finished = next((e for e in reversed(events) if e.get("type") == "turn.completed"), None)
    messages = [e["item"].get("text") or "" for e in events
                if e.get("type") == "item.completed" and (e.get("item") or {}).get("type") == "agent_message"]
    if finished is None or not messages:
        # "error" events alone are not final (the CLI also reports its reconnections that way).
        failed = next((e for e in reversed(events) if e.get("type") == "turn.failed"), None) \
            or next((e for e in reversed(events) if e.get("type") == "error"), None)
        if failed is not None:
            raise _failure(str((failed.get("error") or {}).get("message") or failed.get("message") or ""))
        tail = (done.stderr or done.stdout or "").strip()[-300:]
        raise RuntimeError(f"The Codex CLI stopped without an answer (exit {done.returncode}): {tail}")
    usage = finished.get("usage") or {}
    cached = usage.get("cached_input_tokens") or 0
    return messages[-1], events, {"input": max((usage.get("input_tokens") or 0) - cached, 0),
                                  "output": usage.get("output_tokens") or 0, "cache_read": cached}


def strict_schema(output_format: type[BaseModel]) -> dict:
    """The answer's JSON schema in the strict form OpenAI's structured outputs demand."""
    from openai.lib._pydantic import to_strict_json_schema

    return to_strict_json_schema(output_format)


def ask_model(model: str, system: str, content, output_format: type[BaseModel],
              effort: str = "high", cache: bool = False) -> tuple[BaseModel, dict]:
    """`content` as in `claude.ask_model`. `cache` is accepted for symmetry: OpenAI caches by itself."""
    text, _, usage = _run(model, system, content, effort, schema=strict_schema(output_format))
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return output_format.model_validate_json(text), usage
    except ValidationError as err:
        raise RuntimeError(f"The model's answer did not match the expected shape: {err.errors()[0]['msg']}") \
            from err


def found_pages(events: list[dict]) -> tuple[dict[str, tuple[str, str]], int]:
    """Every page the run's searches returned, {normalised url: (url, title)}, and how many searches ran."""
    pages: dict[str, tuple[str, str]] = {}
    searches = 0
    for event in events:
        item = event.get("item") or {}
        if event.get("type") != "item.completed" or item.get("type") != "web_search":
            continue
        action = item.get("action") or {}
        searches += 1 if action.get("type") == "search" else 0
        hits = list(item.get("results") or [])
        if action.get("url"):      # a page it opened to read
            hits.append({"url": action["url"], "title": ""})
        for hit in hits:
            if isinstance(hit, dict) and hit.get("url"):
                pages.setdefault(claude_cli.same_page(hit["url"]), (hit["url"], hit.get("title") or ""))
    return pages, searches


def search_model(model: str, system: str, prompt: str, max_searches: int = 8,
                 effort: str = "high") -> tuple[Pieces, int, dict]:
    """The model searches the web and writes; its text in cited pieces, the search count and usage."""
    text, events, usage = _run(model, system + claude_cli.CITING.format(max_searches=max_searches), prompt, effort,
                               research=True)
    pages, searches = found_pages(events)
    pieces, dropped = claude_cli.cited_pieces(claude_cli.SOURCES_TAIL.sub("", text), pages)
    if dropped:
        log.warning("dropped %d source marks that matched no search result", dropped)
    usage["searches"] = searches
    return pieces, searches, usage
