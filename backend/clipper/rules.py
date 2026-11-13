"""Clipping without AI: moments and brief checks from plain rules, used when the AI is unavailable.

Not as good as a model's judgement, but explainable and free. Every clip gets
the points that made it, written into its reason, so the reviewer sees why it
was picked ("Picked by rules"); every brief check line says what was looked at.
Human review stays the gate for both, as for the AI's picks.

Moments: each run of transcript lines that fits the length range is scored on
  - its opening line: a question, "you", a number, a turn of phrase that makes
    people curious (+); starting mid-thought ("and", "so", "he") or on filler (-)
  - its body: numbers, strong words, an explanation or a payoff ("because",
    "turns out", "which means")
  - its ending: a finished sentence (+), cut mid-sentence (-)
  - its delivery: faster than the speaker's usual pace (+), long silences (-)
then the best runs that do not overlap are kept, strongest first.

Brief checks: the length limits, the hashtags and @mentions the brief asks for
(or bans) against the post caption, and quoted words it bans against what is
said; anything else is left to check by hand.
"""

import re
import statistics
import time

RULES_MARK = "Picked by rules (AI unavailable)"

QUESTION = re.compile(r"\?\s*$")
NUMBER = re.compile(r"\b\d[\d,.]*\b|\b(?:hundred|thousand|million|billion|percent|half|twice|double)\b", re.I)
YOU = re.compile(r"\byou(?:r|'re|'ve|'ll)?\b", re.I)
CURIOUS = re.compile(r"\b(?:but|actually|nobody|no one|never|secret|mistake|wrong|truth|why|how|most people|"
                     r"the reason|here's|imagine|what if|the problem|the thing is|surprising|weird|crazy)\b", re.I)
MID_THOUGHT = re.compile(r"^(?:and|so|but|because|then|it|that|this|he|she|they|which|also|or|those|these|him|"
                         r"her|them|its)\b", re.I)
FILLER = re.compile(r"^(?:um+|uh+|er+|like|you know|yeah|okay|ok|right|well|so yeah|i mean)\b[,\s]*", re.I)
PAYOFF = re.compile(r"\b(?:because|that's why|the answer|turns out|which means|the point|the key|the lesson|"
                    r"so if you|the result|in the end|that's how)\b", re.I)
STRONG = re.compile(r"\b(?:never|always|best|worst|biggest|most|only|every|free|money|secret|first|last|"
                    r"dangerous|illegal|impossible|guaranteed)\b", re.I)
STOP = set("a an and are as at be by for from how i in is it its of on or that the this to was what when where "
           "which who why will with you your my our we they he she not no do does did can could would should "
           "about into over after before just than then there their them these those".split())


def _ends_sentence(text: str) -> bool:
    return text.rstrip("\"')").endswith((".", "?", "!"))


def _score(lines: list[dict], a: int, b: int, words: list[dict], pace: float) -> tuple[int, list[str]]:
    """Points for the clip made of lines a..b, and the reasons, one per adjustment."""
    first, last = lines[a]["text"].strip(), lines[b]["text"].strip()
    body = " ".join(line["text"] for line in lines[a:b + 1])
    points, why = 0, []

    def add(n: int, reason: str) -> None:
        nonlocal points
        if n:
            points += n
            why.append(f"{'+' if n > 0 else ''}{n} {reason}")

    if QUESTION.search(first):
        add(3, "opens on a question")
    if YOU.search(first):
        add(2, "speaks to the viewer")
    if NUMBER.search(first):
        add(2, "a number up front")
    if CURIOUS.search(first):
        add(2, "a curious turn in the first line")
    if MID_THOUGHT.match(first):
        add(-3, "starts mid-thought")
    elif FILLER.match(first):
        add(-2, "starts on filler")
    add(min(3, len(NUMBER.findall(body)) // 2), "numbers in it")
    if PAYOFF.search(body):
        add(2, "explains or pays off")
    add(min(2, len(STRONG.findall(body)) // 2), "strong words")
    add(2 if _ends_sentence(last) else -2, "ends on a full sentence" if _ends_sentence(last) else "ends mid-sentence")
    start, end = lines[a]["start"], lines[b]["end"]
    spoken = [w for w in words if start <= w["s"] < end]
    if pace and end > start:
        rate = len(spoken) / (end - start)
        if rate > pace * 1.12:
            add(1, "brisk delivery")
        elif rate < pace * 0.85:
            add(-1, "slow delivery")
    gaps = sum(1 for x, y in zip(spoken, spoken[1:], strict=False) if y["s"] - x["e"] > 1.8)
    add(-min(3, gaps), "long silences")
    return points, why


def _pace(words: list[dict]) -> float:
    """The speaker's usual words per second, over 30-second stretches."""
    if not words:
        return 0.0
    rates, t, end = [], words[0]["s"], words[-1]["e"]
    while t < end:
        n = sum(1 for w in words if t <= w["s"] < t + 30)
        if n:
            rates.append(n / 30)
        t += 30
    return statistics.median(rates) if rates else 0.0


def _clean_start(text: str) -> str:
    return FILLER.sub("", text.strip()).strip()


def _cut(text: str, n: int) -> str:
    words = text.split()
    return " ".join(words[:n]).rstrip(",;:") + ("…" if len(words) > n else "")


def _hashtags(title: str) -> str:
    keys = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9']+", title) if w.lower() not in STOP and len(w) > 2]
    tags = list(dict.fromkeys(w.replace("'", "").capitalize() for w in keys))[:3]
    return " ".join(f"#{t}" for t in tags)


def pick_moments(lines: list[dict], words: list[dict], title: str, duration: float, n: int, min_len: int,
                 max_len: int, taken: list[tuple[float, float]] | None = None) -> tuple[list[dict], list[str]]:
    """Choose up to n moments by rules. Returns (moments, warnings) like `moments.picks_to_moments`."""
    pace = _pace(words)
    candidates = []
    for a in range(len(lines)):
        best = None
        for b in range(a, len(lines)):
            length = lines[b]["end"] - lines[a]["start"]
            if length > max_len * 1.05:
                break
            if length < min_len * 0.95:
                continue
            points, why = _score(lines, a, b, words, pace)
            if best is None or points > best[0]:
                best = (points, why, b)
        if best is not None:
            candidates.append((best[0], a, best[2], best[1]))
    candidates.sort(key=lambda c: (-c[0], c[1]))
    from .moments import PAD_AFTER, PAD_BEFORE

    moments, used = [], list(taken or [])
    for points, a, b, why in candidates:
        start = max(0.0, lines[a]["start"] - PAD_BEFORE)
        end = min(duration, lines[b]["end"] + PAD_AFTER)
        if any(start < t1 and end > t0 for t0, t1 in used):
            continue
        used.append((start, end))
        first = _clean_start(lines[a]["text"])
        hook = _cut(first.rstrip("."), 8)
        caption = _cut(first, 22)
        tags = _hashtags(title)
        moments.append({
            "start": round(start, 2), "end": round(end, 2), "hook": hook,
            "title": _cut(first.rstrip(".?!"), 7),
            "caption": f"{caption} {tags}".strip(),
            "score": max(1, min(10, 3 + points // 2)),
            "reason": f"{RULES_MARK}: {points} points: " + "; ".join(why or ["nothing stood out"]),
        })
        if len(moments) == n:
            break
    warnings = [] if moments else ["No part of the transcript fits the length range"]
    return moments, warnings


# --- brief checks ---------------------------------------------------------------------------------

BANNING = re.compile(r"\b(?:no|not|don't|do not|never|avoid|ban(?:ned)?|prohibited|forbidden|without)\b", re.I)
RANGE = re.compile(r"(\d+)\s*(?:-|–|to)\s*(\d+)\s*(s|sec|secs|seconds|min|mins|minutes)\b", re.I)
AT_MOST = re.compile(r"(?:at most|max(?:imum)?|under|up to|no (?:longer|more) than|shorter than)\s*(\d+)\s*"
                     r"(s|sec|secs|seconds|min|mins|minutes)\b", re.I)
AT_LEAST = re.compile(r"(?:at least|min(?:imum)?|over|longer than|no shorter than)\s*(\d+)\s*"
                      r"(s|sec|secs|seconds|min|mins|minutes)\b", re.I)


def _seconds(value: str, unit: str) -> float:
    return float(value) * (60 if unit.lower().startswith("min") else 1)


def check_clip(clip: dict, spoken: str, brief: str) -> dict:
    """The brief checked by rules: {verdict, summary, checks, checked_at}, like `compliance.check_clip`."""
    seconds = float(clip.get("seconds") or (clip["end"] - clip["start"]))
    caption = (clip.get("caption") or "").lower()
    said = f"{clip.get('hook') or ''} {spoken} {caption}".lower()
    checks = []
    low = high = None
    if m := RANGE.search(brief):
        low, high = _seconds(m.group(1), m.group(3)), _seconds(m.group(2), m.group(3))
    if m := AT_MOST.search(brief):
        high = _seconds(m.group(1), m.group(2))
    if m := AT_LEAST.search(brief):
        low = _seconds(m.group(1), m.group(2))
    if low is not None or high is not None:
        ok = (low is None or seconds >= low - 0.5) and (high is None or seconds <= high + 0.5)
        limits = f"{low:g} to {high:g} s" if low is not None and high is not None else \
            f"at most {high:g} s" if high is not None else f"at least {low:g} s"
        checks.append({"requirement": f"Length {limits}", "status": "pass" if ok else "fail",
                       "note": f"The clip lasts {seconds:.0f} s."})
    for sentence in re.split(r"(?<=[.!?\n])\s+", brief):
        banned = bool(BANNING.search(sentence))
        for tag in re.findall(r"[#@][\w.]+", sentence):
            tag = tag.rstrip(".")
            present = tag.lower() in caption
            if banned:
                checks.append({"requirement": f"No {tag}", "status": "fail" if present else "pass",
                               "note": "It is in the post caption." if present else "Not in the post caption."})
            else:
                checks.append({"requirement": f"Include {tag}", "status": "pass" if present else "fail",
                               "note": "It is in the post caption." if present else "Add it to the post caption."})
        if banned:
            for phrase in re.findall(r"[\"“']([^\"”']{2,40})[\"”']", sentence):
                present = phrase.lower() in said
                checks.append({"requirement": f"Never say \"{phrase}\"", "status": "fail" if present else "pass",
                               "note": "It is said or written in the clip." if present else "Not said or written."})
    checks.append({"requirement": "Everything else in the brief", "status": "manual",
                   "note": "Checked by rules only (the AI is unavailable): read the brief and check the rest by hand."})
    order = {"fail": 0, "manual": 1, "pass": 2}
    checks.sort(key=lambda c: order[c["status"]])
    failed = sum(1 for c in checks if c["status"] == "fail")
    verdict = "fail" if failed else "review"
    summary = (f"Checked by rules because the AI is unavailable: {failed} broken requirement"
               f"{'s' if failed != 1 else ''} found; the rest needs your eyes.")
    return {"verdict": verdict, "summary": summary, "checks": checks, "checked_at": int(time.time()),
            "by": "rules"}
