"""The Anthropic (Claude) provider. Called through `llm`, never directly by features.

Two shapes of call: `ask_model` returns a validated structured answer;
`search_model` lets Claude search the web and returns its text in pieces, each
with the sources it cites. Web citations and structured output cannot be one
call, so research is a `search_model` followed by an `ask_model`.
"""

from contextlib import contextmanager

from pydantic import BaseModel

# The basic search tool on purpose. The newer one (web_search_20260209) filters
# results in a code sandbox, and its answers came back with no citations, so the
# facts could not be tied to their sources (checked 2026-11-12 on Opus 5.5).
WEB_SEARCH = "web_search_20250305"

Pieces = list[tuple[str, list[tuple[str, str]]]]   # (text, [(url, title)]) in reading order


def _fallback(model: str) -> dict:
    # If a safety classifier wrongly declines the request, the API retries it on
    # Anthropic's recommended fallback model. Haiku has no server-side fallback.
    if model.startswith("claude-haiku"):
        return {}
    return {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}


@contextmanager
def _api_errors():
    import anthropic

    try:
        yield
    except anthropic.AuthenticationError as err:
        raise RuntimeError("Claude rejected the API key. Put a valid ANTHROPIC_API_KEY in .env.") from err
    except TypeError as err:  # the SDK raises this when no credentials are configured
        if "api_key" not in str(err) and "authentication" not in str(err).lower():
            raise
        raise RuntimeError("No Claude API key found. Put ANTHROPIC_API_KEY in .env.") from err
    except anthropic.RateLimitError as err:
        raise RuntimeError("Claude rate limit reached. Wait a minute and retry.") from err
    except anthropic.APIStatusError as err:
        if "credit balance is too low" in str(err.message).lower():
            raise RuntimeError("Your Anthropic credit balance is too low. Add credit in the Claude Console "
                               "(Plans & Billing), then retry.") from err
        raise RuntimeError(f"Claude API error ({err.status_code}): {err.message}") from err
    except anthropic.APIConnectionError as err:
        raise RuntimeError("Could not reach the Claude API. Check your connection.") from err


def _usage(response) -> dict:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    tools = getattr(usage, "server_tool_use", None)
    return {"input": getattr(usage, "input_tokens", 0) or 0, "output": getattr(usage, "output_tokens", 0) or 0,
            "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0,
            "cache_write": getattr(usage, "cache_creation_input_tokens", 0) or 0,
            "searches": getattr(tools, "web_search_requests", 0) or 0 if tools else 0}


def _add(total: dict, more: dict) -> dict:
    return {k: total.get(k, 0) + more.get(k, 0) for k in set(total) | set(more)}


def ask_model(model: str, system: str, content, output_format: type[BaseModel],
              effort: str = "high", cache: bool = False) -> tuple[BaseModel, dict]:
    """One request; the validated structured answer and its token usage.

    `content` is the user message: a string, or a list of content blocks
    (text and base64 images, in Anthropic's shape). With `cache`, the system
    prompt is cached for a few minutes, so calls that share it (the chapters
    of one storyboard) read it at a fraction of the price.
    """
    import anthropic

    client = anthropic.Anthropic()
    with _api_errors():
        response = client.beta.messages.parse(
            model=model,
            max_tokens=16000,
            output_config={"effort": effort},
            **_fallback(model),
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}] if cache else system,
            messages=[{"role": "user", "content": content}],
            output_format=output_format,
        )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined to process this request.")
    if response.stop_reason == "max_tokens" or response.parsed_output is None:
        raise RuntimeError(f"Claude's answer was cut off (stop reason: {response.stop_reason}).")
    return response.parsed_output, _usage(response)


def search_model(model: str, system: str, prompt: str, max_searches: int = 8,
                 effort: str = "high") -> tuple[Pieces, int, dict]:
    """Claude searches the web and writes; returns its text in cited pieces, the search count and usage."""
    import anthropic

    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": prompt}]
    tools = [{"type": WEB_SEARCH, "name": "web_search", "max_uses": max_searches}]
    blocks, usage = [], {}
    with _api_errors():
        for _ in range(6):   # a long search turn can pause; the server resumes it when sent back
            with client.beta.messages.stream(model=model, max_tokens=32000, output_config={"effort": effort},
                                             **_fallback(model), system=system, tools=tools,
                                             messages=messages) as stream:
                response = stream.get_final_message()
            blocks.extend(response.content)
            usage = _add(usage, _usage(response))
            if response.stop_reason != "pause_turn":
                break
            messages.append({"role": "assistant", "content": response.content})
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined to process this request.")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Claude's research notes were cut off.")

    pieces: Pieces = []
    searches = 0
    for block in blocks:
        if block.type == "server_tool_use" and getattr(block, "name", "") == "web_search":
            searches += 1
        elif block.type == "text":
            cited = [(c.url, getattr(c, "title", "") or "") for c in (block.citations or []) if getattr(c, "url", None)]
            pieces.append((block.text, cited))
    usage["searches"] = max(usage.get("searches", 0), searches)
    return pieces, searches, usage
