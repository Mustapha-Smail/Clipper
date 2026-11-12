"""The OpenAI provider, and any server that speaks the OpenAI API (OpenRouter, Ollama...).

With no OPENAI_BASE_URL the calls go to OpenAI itself through the Responses
API: structured answers with `responses.parse`, web search with the
`web_search` tool and its `url_citation` annotations. With a base URL set they
go through Chat Completions, which compatible servers implement; those have no
web search, so the Research tier needs Claude or OpenAI itself.
"""

import json
import re
from contextlib import contextmanager

from pydantic import BaseModel, ValidationError

from . import config
from .claude import Pieces

EFFORTS = {"low": "low", "medium": "medium", "high": "high", "xhigh": "xhigh", "max": "high"}


def _client():
    import openai

    key = config.openai_api_key()
    base = config.openai_base_url()
    if not key and not base:
        raise RuntimeError("No OpenAI key. Put OPENAI_API_KEY in .env and restart, or pick Claude for this tier.")
    return openai.OpenAI(api_key=key or "not-needed", base_url=base or None, timeout=600)


@contextmanager
def _api_errors():
    import openai

    try:
        yield
    except openai.AuthenticationError as err:
        raise RuntimeError("The OpenAI-compatible API rejected the key in OPENAI_API_KEY.") from err
    except openai.RateLimitError as err:
        if "insufficient_quota" in str(err):
            raise RuntimeError("Your OpenAI quota is used up. Add credit in the OpenAI dashboard, then retry.") from err
        raise RuntimeError("OpenAI rate limit reached. Wait a minute and retry.") from err
    except openai.APIStatusError as err:
        raise RuntimeError(f"OpenAI API error ({err.status_code}): {err.message}") from err
    except openai.APIConnectionError as err:
        raise RuntimeError("Could not reach the OpenAI-compatible API. Check the base URL and your connection.") \
            from err


def _responses_content(content) -> list[dict] | str:
    """Anthropic-shaped content blocks -> Responses API input parts."""
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if block.get("type") == "text":
            parts.append({"type": "input_text", "text": block["text"]})
        elif block.get("type") == "image":
            src = block["source"]
            parts.append({"type": "input_image", "image_url": f"data:{src['media_type']};base64,{src['data']}"})
    return parts


def _chat_content(content) -> list[dict] | str:
    """Anthropic-shaped content blocks -> Chat Completions message parts."""
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if block.get("type") == "text":
            parts.append({"type": "text", "text": block["text"]})
        elif block.get("type") == "image":
            src = block["source"]
            parts.append({"type": "image_url",
                          "image_url": {"url": f"data:{src['media_type']};base64,{src['data']}"}})
    return parts


def _responses_usage(response) -> dict:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    cached = getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", 0) or 0
    return {"input": (usage.input_tokens or 0) - cached, "output": usage.output_tokens or 0, "cache_read": cached}


def ask_model(model: str, system: str, content, output_format: type[BaseModel],
              effort: str = "high", cache: bool = False) -> tuple[BaseModel, dict]:
    """`cache` is accepted for symmetry: OpenAI caches long prompts by itself."""
    client = _client()
    if not config.openai_base_url():
        with _api_errors():
            response = client.responses.parse(
                model=model, instructions=system,
                input=[{"role": "user", "content": _responses_content(content)}],
                text_format=output_format, reasoning={"effort": EFFORTS.get(effort, "medium")})
        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            raise RuntimeError(f"OpenAI returned no usable answer (status: {getattr(response, 'status', '?')}).")
        return parsed, _responses_usage(response)

    # A compatible server: Chat Completions with a JSON schema, validated here.
    messages = [{"role": "system", "content": system}, {"role": "user", "content": _chat_content(content)}]
    with _api_errors():
        try:
            response = client.chat.completions.parse(model=model, messages=messages, response_format=output_format)
            message = response.choices[0].message
            parsed = message.parsed
            text = message.content or ""
        except Exception as err:  # noqa: BLE001 - some servers lack json_schema support: ask for plain JSON
            import openai
            if not isinstance(err, (openai.BadRequestError, ValidationError, json.JSONDecodeError)):
                raise
            schema = json.dumps(output_format.model_json_schema())
            messages[0]["content"] = f"{system}\n\nAnswer with one JSON object matching this JSON schema:\n{schema}"
            response = client.chat.completions.create(model=model, messages=messages,
                                                      response_format={"type": "json_object"})
            parsed, text = None, response.choices[0].message.content or ""
    if parsed is None:
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
        try:
            parsed = output_format.model_validate_json(text)
        except ValidationError as err:
            raise RuntimeError(f"The model's answer did not match the expected shape: {err.errors()[0]['msg']}") \
                from err
    usage = getattr(response, "usage", None)
    cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0 if usage else 0
    return parsed, ({"input": (usage.prompt_tokens or 0) - cached, "output": usage.completion_tokens or 0,
                     "cache_read": cached} if usage else {})


def search_model(model: str, system: str, prompt: str, max_searches: int = 8,
                 effort: str = "high") -> tuple[Pieces, int, dict]:
    if config.openai_base_url():
        raise RuntimeError("Web research needs Claude or OpenAI itself: OpenAI-compatible servers have no web "
                           "search. Pick another provider for the Research tier in Settings.")
    client = _client()
    with _api_errors():
        response = client.responses.create(
            model=model, instructions=f"{system}\n\nUse web search; do not run more than {max_searches} searches.",
            input=prompt, tools=[{"type": "web_search"}], reasoning={"effort": EFFORTS.get(effort, "medium")})
    pieces: Pieces = []
    searches = 0
    for item in getattr(response, "output", []) or []:
        if item.type == "web_search_call":
            searches += 1
        elif item.type == "message":
            for part in item.content:
                if getattr(part, "type", "") != "output_text":
                    continue
                text, cursor = part.text, 0
                notes = sorted((a for a in (part.annotations or []) if getattr(a, "type", "") == "url_citation"),
                               key=lambda a: a.end_index)
                for note in notes:   # cut the text after each cited passage
                    if note.end_index > cursor:
                        pieces.append((text[cursor:note.end_index], [(note.url, getattr(note, "title", "") or "")]))
                        cursor = note.end_index
                    elif pieces:
                        pieces[-1][1].append((note.url, getattr(note, "title", "") or ""))
                if cursor < len(text):
                    pieces.append((text[cursor:], []))
    if not pieces:
        raise RuntimeError("OpenAI returned no research text.")
    usage = _responses_usage(response)
    usage["searches"] = searches
    return pieces, searches, usage
