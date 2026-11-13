"""Plain language: who the videos are for, the writing rules that follow from it, and a readability check.

The audience is a setting (`Settings.audience`). Ideas, research and scripts are
written for it, and every script gets a plain-language check: plain arithmetic
on the narration (sentence length, school grade, long words, numbers and names
per minute), every deduction in its lines. It advises; it never rewrites.
"""

import re

from ..settings import Settings
from ..settings import load as load_settings

WORDS_PER_MINUTE = 150

AUDIENCES = {
    "everyone": ("Everyone", "a curious 12-year-old, or an adult who left school at 16: no background in the "
                             "subject, watching on a phone, hearing each sentence once"),
    "curious": ("Curious adults", "a curious adult with a general education and no special knowledge of the "
                                  "subject"),
    "enthusiasts": ("Enthusiasts", "people who already like the subject and know its basic terms"),
}

# Targets of the check, per audience: school grade, words per sentence, share of long words (3+ syllables),
# numbers per minute, different names per minute.
TARGETS = {
    "everyone": {"grade": 7.0, "sentence": 14.0, "long": 10.0, "numbers": 4.0, "names": 3.0},
    "curious": {"grade": 9.0, "sentence": 17.0, "long": 13.0, "numbers": 5.0, "names": 5.0},
    "enthusiasts": {"grade": 11.0, "sentence": 20.0, "long": 16.0, "numbers": 8.0, "names": 8.0},
}
NUMBER_EVERY = {"everyone": 20, "curious": 12, "enthusiasts": 0}      # seconds between numbers, 0 = no limit


def level(settings: Settings | None = None) -> str:
    settings = settings or load_settings()
    return settings.audience if settings.audience in AUDIENCES else "everyone"


def audience(settings: Settings | None = None) -> str:
    """Who the videos are for, as one phrase for a prompt."""
    return AUDIENCES[level(settings)][1]


def rules(settings: Settings | None = None) -> str:
    """The plain-language rules shared by the writer, the section rewrite and 'Make it simpler'."""
    lv = level(settings)
    every = NUMBER_EVERY[lv]
    numbers = (f"- Numbers: about one every {every} seconds at most, rounded (\"about 1,500 years\", not \"1,493 "
               "years\"), each one compared to something the viewer can picture." if every else
               "- Numbers: round them unless the exact figure is the point, and compare big ones to something "
               "the viewer can picture.")
    return f"""\
Who listens: {AUDIENCES[lv][1]}. They hear each sentence once, with no rewind, so:
- Tell one idea, the one thing to understand. Cut any true detail that does not serve it, \
however interesting.
- Every name, place, date or technical term: keep it only if the story needs it, then explain \
it in the same sentence with everyday words. Otherwise drop it or replace it ("a monk", not \
"Dionysius Exiguus of Scythia Minor"; "the first day of spring", not "the vernal equinox").
{numbers}
- Mostly short sentences (under 15 words) of common words; a long one now and then for rhythm.
- Every mechanism gets an everyday comparison the viewer has lived ("like a spreadsheet", \
"like a leaky bucket").
- When the answer lands, say it plainly; close by saying it again in one sentence.
- Jokes must be obvious enough to land for someone half-listening: no in-jokes, no references \
that need background."""


def idea_rules(settings: Settings | None = None) -> str:
    """What makes an idea work for the audience, for the idea, theme and check prompts."""
    return f"""\
Who watches: {audience(settings)}. Every idea is for them:
- It starts from something this viewer already knows from daily life (an object at home, a habit, \
a word they say, a holiday, food, something from films), never from a term they would have to look up.
- Its surprising answer fits in one sentence they would understand and repeat to a friend, with no \
background.
- Its title uses everyday words only.
- Prefer a question many people have wondered about over a clever corner of the subject that only \
experts care about. The twist comes from the angle (a funny consequence, a wrong common belief, a \
hidden history), not from obscurity."""


# --- the check ------------------------------------------------------------------------------

SENTENCE = re.compile(r"[^.!?…]+[.!?…]*")
WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*|\d[\d,.]*")
NUMBER = re.compile(r"\b\d[\d,.]*\b|\b(?:hundred|thousand|million|billion|trillion)s?\b", re.I)
COMMON_CAPS = set("I I'm I've I'd I'll OK Okay Mr Mrs Ms Dr St".split())
EVERYDAY = set("""January February March April May June July August September October November December
Monday Tuesday Wednesday Thursday Friday Saturday Sunday Christmas Easter New Year God TV""".split())
MONTH = "January|February|March|April|May|June|July|August|September|October|November|December"
DATE = re.compile(rf"\b(?:{MONTH})\s+\d{{1,2}}(?:st|nd|rd|th)?\b|\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:{MONTH})\b")


def syllables(word: str) -> int:
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return 1
    if len(w) <= 3:
        return 1
    w = re.sub(r"(?:[^laeiouy]es|[^laeiouy]ed|[^laeiouy]e)$", "", w)
    w = re.sub(r"^y", "", w)
    return max(1, len(re.findall(r"[aeiouy]{1,2}", w)))


def _sentences(text: str) -> list[str]:
    out = []
    for para in re.split(r"\n+", text):
        out += [s.strip() for s in SENTENCE.findall(para) if WORD.search(s)]
    return out


def _stem(word: str) -> str:
    return re.sub(r"['’]s$", "", word.strip("'’"))


def _names(sentences: list[str], known: set[str]) -> list[str]:
    """Capitalised words that do not start a sentence: names, places, titles. Months, days, everyday
    holidays and the video's own subject (the words of its title) are not counted."""
    found: dict[str, None] = {}
    for sentence in sentences:
        words = WORD.findall(sentence)
        for word in words[1:]:
            stem = _stem(word)
            single = stem[:-1] if stem.endswith("s") else stem          # LEDs is LED
            if word[0].isupper() and word not in COMMON_CAPS and stem not in EVERYDAY and single not in EVERYDAY \
                    and stem.lower() not in known and single.lower() not in known and len(stem) > 1 \
                    and single not in found and stem + "s" not in found:
                found.setdefault(stem, None)
    return list(found)


def _numbers(text: str) -> list[str]:
    """Numbers as a listener meets them: a date ("April 20") is one thing to hold, not two."""
    dates = DATE.findall(text)
    rest = DATE.sub(" ", text)
    return dates + NUMBER.findall(rest)


def check(sections: list[dict], lv: str = "everyone", title: str = "") -> dict | None:
    """How easy the narration is to follow on one listen, 0 to 100, every deduction explained."""
    text = "\n".join(s.get("text", "") for s in sections)
    sentences = _sentences(text)
    words = [w for s in sentences for w in WORD.findall(s) if not w[0].isdigit()]
    if len(words) < 20:
        return None
    lv = lv if lv in TARGETS else "everyone"
    target = TARGETS[lv]
    minutes = max(0.2, (len(words) + len(NUMBER.findall(text))) / WORDS_PER_MINUTE)
    syl = [syllables(w) for w in words]
    per_sentence = len(words) / len(sentences)
    grade = 0.39 * per_sentence + 11.8 * sum(syl) / len(words) - 15.59
    long_share = 100 * sum(1 for n in syl if n >= 3) / len(words)
    numbers = _numbers(text)
    names = _names(sentences, {_stem(w).lower() for w in WORD.findall(title)})
    numbers_pm, names_pm = len(numbers) / minutes, len(names) / minutes
    lines: list[dict] = []

    def deduct(over: float, per: float, cap: int, label: str, detail: str) -> None:
        points = min(cap, round(over * per))
        if points > 0:
            lines.append({"points": -points, "label": label, "detail": detail})

    deduct(grade - target["grade"], 6, 30, f"Reading level: school grade {grade:.0f}",
           f"Aim for grade {target['grade']:.0f} or below: shorter sentences, shorter words.")
    deduct(per_sentence - target["sentence"], 2, 15, f"Sentences average {per_sentence:.0f} words",
           f"Aim for {target['sentence']:.0f} or fewer; split the long ones.")
    deduct(long_share - target["long"], 2, 15, f"{long_share:.0f}% long words",
           f"Words of three syllables or more; aim for {target['long']:.0f}% or fewer.")
    deduct(numbers_pm - target["numbers"], 6, 25, f"{numbers_pm:.1f} numbers a minute",
           f"{len(numbers)} in all; aim for {target['numbers']:.0f} a minute or fewer, rounded.")
    deduct(names_pm - target["names"], 6, 25, f"{names_pm:.1f} names a minute",
           f"{len(names)} different names ({', '.join(names[:8])}{'…' if len(names) > 8 else ''}); "
           f"aim for {target['names']:.0f} a minute or fewer, each explained.")
    score = max(0, 100 + sum(x["points"] for x in lines))
    hardest = sorted(sentences, key=lambda s: len(WORD.findall(s)), reverse=True)[:3]
    return {
        "score": score,
        "verdict": "plain" if score >= 85 else "a bit dense" if score >= 65 else "hard to follow",
        "audience": AUDIENCES[lv][0],
        "lines": lines,
        "stats": {"grade": round(grade, 1), "sentence_words": round(per_sentence, 1),
                  "long_words": round(long_share, 1), "numbers_per_minute": round(numbers_pm, 1),
                  "names_per_minute": round(names_pm, 1), "minutes": round(minutes, 1)},
        "names": names[:20],
        "hardest": [s for s in hardest if len(WORD.findall(s)) > target["sentence"]],
    }
