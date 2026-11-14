"""Stage 3: choose the moments worth clipping.

The transcript is cut into numbered sentence lines. Claude answers with line
numbers, never raw timestamps, so every clip boundary lands on a real sentence
boundary. The result is written to moments.json, which is meant to be edited by hand
before rendering.
"""

from pydantic import BaseModel, Field

from . import llm

SENTENCE_END = (".", "?", "!")
PAD_BEFORE, PAD_AFTER = 0.15, 0.30  # Whisper word edges are slightly tight


def build_lines(words: list[dict], max_gap: float = 0.8, max_words: int = 40) -> list[dict]:
    """Group words into sentence-like lines: {i, start, end, text}."""
    lines: list[dict] = []
    current: list[dict] = []

    def flush() -> None:
        if current:
            lines.append({
                "i": len(lines),
                "start": current[0]["s"],
                "end": current[-1]["e"],
                "text": " ".join(w["w"] for w in current),
            })
            current.clear()

    for word in words:
        if current and word["s"] - current[-1]["e"] > max_gap:
            flush()
        current.append(word)
        if word["w"].rstrip("\"')").endswith(SENTENCE_END) or len(current) >= max_words:
            flush()
    flush()
    return lines


def format_lines(lines: list[dict]) -> str:
    return "\n".join(
        f"{line['i']} | {line['start']:.1f}-{line['end']:.1f} | {line['text']}" for line in lines
    )


class Pick(BaseModel):
    first_line: int = Field(description="Number of the first transcript line in the clip")
    last_line: int = Field(description="Number of the last transcript line in the clip")
    hook: str = Field(description="On-screen text for the opening seconds, 8 words at most")
    title: str = Field(description="Short working title for the clip")
    caption: str = Field(description="Post caption: one or two sentences, then 3 to 5 hashtags")
    reason: str = Field(description="One sentence on why this moment holds a cold viewer")
    score: int = Field(description="1 to 10: how likely this clip is to be watched to the end")


class Picks(BaseModel):
    picks: list[Pick]


SYSTEM = """\
You choose moments from long-form video transcripts to be cut into short clips for \
social feeds (TikTok, Reels, Shorts, X). The clips are posted for paid campaigns that pay \
per view, so the only thing that matters is whether a stranger scrolling a feed, who \
has never seen the source video, stops and watches to the end.

A moment works when it stands on its own without the surrounding conversation, opens \
on a line that creates curiosity or tension within the first two seconds, and delivers \
a payoff: a strong claim, a number, a story beat, a contrarian take or a concrete \
how-to. It should end on a complete thought, right after the payoff lands.

The transcript is given as numbered lines in the form `number | start-end in seconds | \
text`. Answer with line numbers. Start each clip on the line where the idea begins, \
skipping filler lead-ins, and end it on the line that completes the thought. Use the \
timestamps to keep each clip within the requested length. Clips must not overlap.

The hook is burned into the video as text during the opening seconds. It has to be \
true to what is said in the clip: a hook the clip does not pay off costs retention. \
Plain words only, no emoji.

Return fewer clips than requested if the material does not support that many. A weak \
clip wastes a posting slot.

If a campaign brief is provided, its requirements take priority over everything above \
(required hashtags or mentions, banned topics, length limits, tone)."""


def _user_prompt(lines, title, n, min_len, max_len, brief) -> str:
    parts = []
    if brief:
        parts.append(f"<campaign_brief>\n{brief.strip()}\n</campaign_brief>")
    parts.append(f"<source_title>{title}</source_title>")
    parts.append(f"<transcript>\n{format_lines(lines)}\n</transcript>")
    parts.append(
        f"Choose up to {n} clips, each between {min_len} and {max_len} seconds long, "
        "ordered from strongest to weakest."
    )
    return "\n\n".join(parts)


def ask_claude(lines, title, n, min_len, max_len, brief) -> list[Pick]:
    prompt = _user_prompt(lines, title, n, min_len, max_len, brief)
    return llm.ask("moments", SYSTEM, prompt, Picks).picks


def picks_to_moments(
    picks: list[Pick], lines: list[dict], duration: float, n: int, min_len: int, max_len: int,
    taken: list[tuple[float, float]] | None = None,
) -> tuple[list[dict], list[str]]:
    """Turn line-number picks into timed moments. Returns (moments, warnings).

    `taken` lists time ranges that are already used (moments being kept), so
    new picks that overlap them are skipped.
    """
    moments, warnings, taken = [], [], list(taken or [])
    for pick in sorted(picks, key=lambda p: -p.score):
        if not (0 <= pick.first_line <= pick.last_line < len(lines)):
            warnings.append(f"'{pick.title}': line numbers out of range, skipped")
            continue
        start = max(0.0, lines[pick.first_line]["start"] - PAD_BEFORE)
        end = min(duration, lines[pick.last_line]["end"] + PAD_AFTER)
        length = end - start
        # Sentence boundaries make exact lengths impossible, so allow some slack.
        if length < min_len * 0.75 or length > max_len * 1.25:
            warnings.append(f"'{pick.title}': {length:.0f}s is outside the length range, skipped")
            continue
        if any(start < t_end and end > t_start for t_start, t_end in taken):
            warnings.append(f"'{pick.title}': overlaps a stronger clip, skipped")
            continue
        taken.append((start, end))
        moments.append({
            "start": round(start, 2),
            "end": round(end, 2),
            "hook": pick.hook.strip(),
            "title": pick.title.strip(),
            "caption": pick.caption.strip(),
            "score": pick.score,
            "reason": pick.reason.strip(),
        })
        if len(moments) == n:
            break
    return moments, warnings
