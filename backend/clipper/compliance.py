"""Check a rendered clip against the campaign brief before it is posted.

Claude sees the brief, the clip's facts (length, hook, post caption, what is
said) and three frames, and answers one line per requirement in the brief. The
result is advice for the human reviewer; it never approves or rejects a clip.
"""

import base64
import subprocess
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from . import llm
from .ffutil import require


class Check(BaseModel):
    requirement: str = Field(description="The requirement, in a few words, as the brief states it")
    status: Literal["pass", "fail", "manual"]
    note: str = Field(description="One sentence saying what was seen, or what to check by hand")


class Review(BaseModel):
    checks: list[Check]
    summary: str = Field(description="One or two sentences for the person deciding whether to post")


SYSTEM = """\
You review a short vertical clip against a campaign brief before it is posted for a \
paid clipping campaign. A submission that breaks a stated requirement is rejected and \
earns nothing, so the person posting needs to know exactly which requirements are met, \
which are broken, and which they have to verify themselves.

Go through the brief and list every distinct requirement it places on the clip or on \
its post: length, required or banned hashtags and mentions, required on-screen elements \
such as a watermark or logo, banned topics or words, tone, caption content, disclosure \
labels. Give each one a status:
- pass: the materials show the requirement is met
- fail: the materials show it is broken
- manual: it cannot be judged from what you were given, for example account \
requirements, posting times, which platform, music, or anything that happens outside \
the clip

Judge only against what the brief states. Do not add requirements of your own, and \
leave out anything the brief is silent on. If a requirement is ambiguous, mark it \
manual and say what to check. You see three frames, not the whole video: for a visual \
requirement that must hold throughout, say so in the note."""


def _frame(video: Path, t: float) -> bytes | None:
    proc = subprocess.run(
        [require("ffmpeg"), "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(video),
         "-frames:v", "1", "-vf", "scale=540:-2", "-f", "image2pipe", "-vcodec", "mjpeg",
         "-q:v", "4", "-"],
        capture_output=True,
    )
    return proc.stdout if proc.returncode == 0 and proc.stdout else None


def check_clip(video: Path, clip: dict, spoken: str, brief: str,
               captions_burned: bool, hook_seconds: float) -> dict:
    """Return {verdict, summary, checks, checked_at}. verdict: pass | review | fail."""
    seconds = float(clip.get("seconds") or (clip["end"] - clip["start"]))
    hook = (clip.get("hook") or "").strip()
    facts = "\n".join([
        f"Length: {seconds:.1f} seconds",
        "Format: 1080x1920 vertical, 30 fps, mp4",
        (f"Hook text on screen for the first {hook_seconds:g} seconds: \"{hook}\""
         if hook and hook_seconds > 0 else "Hook text on screen: none"),
        f"Burned-in captions of the speech: {'yes' if captions_burned else 'no'}",
        f"Post caption: \"{(clip.get('caption') or '').strip()}\"",
        f"What is said in the clip: \"{spoken}\"",
    ])
    content: list[dict] = [{
        "type": "text",
        "text": (f"<campaign_brief>\n{brief.strip()}\n</campaign_brief>\n\n<clip>\n{facts}\n</clip>\n\n"
                 "Three frames from the clip follow: near the start, the middle, and near the end."),
    }]
    for t in (min(1.0, seconds / 4), seconds / 2, max(0.0, seconds - 1.0)):
        image = _frame(video, t)
        if image:
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/jpeg",
                "data": base64.standard_b64encode(image).decode()}})

    review = llm.ask("brief_check", SYSTEM, content, Review, effort="medium")
    checks = [c.model_dump() for c in review.checks]
    order = {"fail": 0, "manual": 1, "pass": 2}
    checks.sort(key=lambda c: order[c["status"]])
    statuses = {c["status"] for c in checks}
    verdict = "fail" if "fail" in statuses else "review" if "manual" in statuses else "pass"
    return {"verdict": verdict, "summary": review.summary.strip(), "checks": checks,
            "checked_at": int(time.time())}
