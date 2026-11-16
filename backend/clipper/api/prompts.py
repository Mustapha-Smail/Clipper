"""The system prompts of every AI step: read them, change them, go back to the default."""

from fastapi import APIRouter, HTTPException

from .. import llm, prompts
from . import schemas as s

router = APIRouter(prefix="/prompts", tags=["prompts"])


def _out(item: dict) -> s.PromptOut:
    tier = llm.TASKS.get(item["task"], ("", ""))[0] if item["task"] else ""
    return s.PromptOut(**{k: v for k, v in item.items() if k in s.PromptOut.model_fields}, tier=tier)


def _one(name: str) -> s.PromptOut:
    for item in prompts.catalogue():
        if item["name"] == name:
            return _out(item)
    raise HTTPException(404, "Unknown prompt")


@router.get("", response_model=list[s.PromptOut])
def list_prompts():
    """Every prompt, in the order of the steps, with your edits."""
    return [_out(item) for item in prompts.catalogue()]


@router.get("/{name}", response_model=s.PromptOut)
def get_prompt(name: str):
    return _one(name)


@router.put("/{name}", response_model=s.PromptOut)
def save_prompt(name: str, body: s.PromptSave):
    """Keep your version; it is used from the next AI call on. Problems are reported, not refused."""
    try:
        prompts.save(name, body.text)
    except prompts.PromptError as err:
        raise HTTPException(400 if "Unknown" not in str(err) else 404, str(err)) from err
    return _one(name)


@router.delete("/{name}", response_model=s.PromptOut)
def reset_prompt(name: str):
    """Back to the default shipped with the app."""
    try:
        prompts.reset(name)
    except prompts.PromptError as err:
        raise HTTPException(404, str(err)) from err
    return _one(name)
