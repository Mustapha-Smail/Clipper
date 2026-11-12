"""Every AI call in the app goes through here.

A feature names its task ("script_write", "idea_check"...). The task belongs to
one of three tiers, and Settings say which provider and model run each tier:

  writing   long, careful work you read: scripts, storyboards, moments, ideas
  quick     short judgements and small drawings: checks, props, terms
  research  web search with citations: script research, fact-check lookups

Putting the quick tier on a small model is the main way to spend less. Every
call is logged (tokens, web searches, estimated cost) so Settings can show
where the money goes.
"""

import logging
import time

from pydantic import BaseModel

from . import claude, config, openai_compat
from .db import session_scope
from .db.models import AiUsage

log = logging.getLogger("clipper.llm")

TASKS = {
    # task: (tier, what it is, for the usage table)
    "moments": ("writing", "Choosing clip moments"),
    "ideas": ("writing", "Suggesting ideas"),
    "script_write": ("writing", "Writing scripts"),
    "rewrite": ("writing", "Rewriting a section"),
    "factcheck": ("writing", "Listing and checking claims"),
    "storyboard": ("writing", "Planning storyboards"),
    "idea_check": ("quick", "Checking ideas"),
    "campaign": ("quick", "Reading campaign listings"),
    "brief_check": ("quick", "Checking clips against briefs"),
    "verdicts": ("quick", "Reading fact-check results"),
    "props": ("quick", "Drawing props"),
    "music": ("quick", "Suggesting music"),
    "research": ("research", "Researching scripts"),
    "verify": ("research", "Looking up claims"),
}

# Dollars per million tokens (input, output), for the estimate only. Cached input
# is counted at a tenth of the input price; each web search at one cent.
PRICES = {
    "claude-fable-5-2": (8.0, 40.0), "claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "gpt-6-astra": (10.0, 50.0), "gpt-6.1-sol": (2.0, 10.0), "gpt-6.1-terra": (1.0, 5.0), "gpt-6-luna": (0.10, 0.50),
}
SEARCH_PRICE = 0.01

PROVIDERS = {"anthropic": claude, "openai": openai_compat}

KNOWN = {   # shown first in Settings, with a short description
    "anthropic": {"claude-opus-5-5": "Opus 5.5 · best writing", "claude-sonnet-5-5": "Sonnet 5.5 · balanced",
                  "claude-haiku-5-5": "Haiku 5.5 · fast and cheapest",
                  "claude-fable-5-2": "Fable 5.2 · most capable, priciest"},
    "openai": {"gpt-6-astra": "GPT-6 Astra · flagship", "gpt-6.1-sol": "GPT-6.1 Sol · balanced",
               "gpt-6.1-terra": "GPT-6.1 Terra · cheaper", "gpt-6-luna": "GPT-6 Luna · fast and cheapest"},
}
_model_cache: dict[str, tuple[float, list[dict]]] = {}


def _live_ids(provider: str) -> list[str]:
    """Model ids the account can use, from the provider's models endpoint."""
    if provider == "anthropic":
        import anthropic
        return [m.id for m in anthropic.Anthropic().models.list(limit=100)]
    client = openai_compat._client()
    ids = [m.id for m in client.models.list()]
    if not config.openai_base_url():   # OpenAI lists embeddings, audio and image models too
        ids = [i for i in ids if i.startswith(("gpt-", "o")) and not any(
            x in i for x in ("audio", "realtime", "tts", "transcribe", "image", "embedding", "search"))]
    return ids


def available_models(provider: str, refresh: bool = False) -> dict:
    """Models to offer for a provider: known ones first with prices, then the rest of the account's."""
    cached = _model_cache.get(provider)
    if cached and not refresh and time.monotonic() - cached[0] < 3600:
        models, error = cached[1], None
    else:
        error = None
        try:
            live = _live_ids(provider)
        except Exception as err:  # noqa: BLE001 - no key, offline, or a server without /models
            live, error = [], str(err).split("\n")[0][:200]
        known = KNOWN.get(provider, {})
        models = [{"id": k, "label": v, "price": PRICES.get(k), "live": k in live} for k, v in known.items()]
        models += [{"id": i, "label": i, "price": PRICES.get(i), "live": True} for i in sorted(live) if i not in known]
        if not error:
            _model_cache[provider] = (time.monotonic(), models)
    return {"models": models, "error": error}


def route(task: str) -> tuple[str, str, str]:
    """(tier, provider, model) for a task, from Settings."""
    from . import settings as settings_mod

    tier = TASKS[task][0]
    settings = settings_mod.load()
    return tier, getattr(settings, f"ai_{tier}_provider"), getattr(settings, f"ai_{tier}_model")


def estimate(model: str, usage: dict) -> float | None:
    price = PRICES.get(model)
    if price is None:
        return None
    tokens = (usage.get("input", 0) * price[0] + usage.get("cache_read", 0) * price[0] * 0.1
              + usage.get("output", 0) * price[1]) / 1e6
    return round(tokens + usage.get("searches", 0) * SEARCH_PRICE, 5)


def _record(task: str, tier: str, provider: str, model: str, usage: dict, started: float,
            error: str | None = None) -> None:
    try:
        with session_scope() as db:
            db.add(AiUsage(task=task, tier=tier, provider=provider, model=model,
                           input_tokens=usage.get("input", 0), output_tokens=usage.get("output", 0),
                           cache_read_tokens=usage.get("cache_read", 0), searches=usage.get("searches", 0),
                           cost_usd=estimate(model, usage) if not error else None, ok=error is None,
                           error=(error or "")[:500], duration_s=round(time.monotonic() - started, 2)))
    except Exception:  # noqa: BLE001 - the log must never break the call it describes
        log.warning("could not record AI usage", exc_info=True)


def ask(task: str, system: str, content, output_format: type[BaseModel], effort: str = "high") -> BaseModel:
    """A structured answer for `task`, from whichever model its tier uses."""
    tier, provider, model = route(task)
    started = time.monotonic()
    try:
        parsed, usage = PROVIDERS[provider].ask_model(model, system, content, output_format, effort)
    except Exception as err:
        _record(task, tier, provider, model, {}, started, str(err) or err.__class__.__name__)
        raise
    _record(task, tier, provider, model, usage, started)
    return parsed


class Sources:
    """Numbers web sources in the order they are first cited: [1], [2]..."""

    def __init__(self, existing: list[dict] | None = None):
        self.items: list[dict] = list(existing or [])
        self._by_url = {s["url"]: s["n"] for s in self.items}

    def number(self, url: str, title: str) -> int:
        if url not in self._by_url:
            n = len(self.items) + 1
            self.items.append({"n": n, "url": url, "title": title or url})
            self._by_url[url] = n
        return self._by_url[url]


def search(task: str, system: str, prompt: str, max_searches: int = 8, effort: str = "high",
           sources: Sources | None = None) -> tuple[str, Sources, int]:
    """Let the research model search the web and write notes.

    Returns the notes with [n] after each cited passage, the numbered sources
    (extending `sources` when given) and how many searches were made.
    """
    tier, provider, model = route(task)
    started = time.monotonic()
    try:
        pieces, searches, usage = PROVIDERS[provider].search_model(model, system, prompt, max_searches, effort)
    except Exception as err:
        _record(task, tier, provider, model, {}, started, str(err) or err.__class__.__name__)
        raise
    _record(task, tier, provider, model, usage, started)
    sources = sources or Sources()
    parts = []
    for text, cited in pieces:
        parts.append(text)
        numbers = []
        for url, title in cited:
            n = sources.number(url, title)
            if n not in numbers:
                numbers.append(n)
        parts.append("".join(f"[{n}]" for n in numbers))
    notes = "".join(parts).strip()
    if not notes:
        raise RuntimeError("The research model returned no notes.")
    return notes, sources, searches
