"""Which background track suits a video, if any: from the library or found online.

Two small calls on the quick tier. The first turns the video's tone into a few
plain search words for Openverse; the app then measures the tracks it finds
(tempo, density, brightness, dynamics) itself. The second ranks the library and
those candidates together. The model never hears audio, so both calls stay at a
few hundred tokens. "No music" is a valid answer.
"""

from pydantic import BaseModel, Field

from .. import llm


class Searches(BaseModel):
    words: list[str] = Field(description="2 or 3 searches of one or two plain words each: a genre or an "
                                         "instrument, e.g. ukulele, ambient, pizzicato, lounge, piano")


class Fit(BaseModel):
    ref: str = Field(description="The track's reference exactly as given, e.g. lib-3 or web-5f7af4b0")
    fit: int = Field(description="0 to 10")
    reason: str = Field(description="One short sentence")


class Pick(BaseModel):
    choice: str | None = Field(description="The reference of the track to use, or null when the video is better "
                                           "without music")
    reason: str = Field(description="One or two sentences, for the creator")
    level_db: int = Field(description="Suggested music level under the voice, -28 (barely there) to -14 (present)")
    ranking: list[Fit] = Field(description="The best tracks, best first, at most 8")


TONE = """\
The video belongs to a faceless animated YouTube channel: deadpan comic narration over \
simple drawn animation. Music sits under the voice, so it must never compete with it: \
instrumental, steady, not too busy or bright, matching the tone (curious, dry, playful)."""

SEARCH = TONE + """

Give a few search words to find background music for this video in an open music \
library that matches titles and tags, not moods: use plain genre or instrument words."""

SYSTEM = TONE + """ Judge each track only from what you are told about it. A title \
that suggests singing (a song name, "feat.", "vocal", a cover) is a bad sign; \
"instrumental" in a title is a good one. Library tracks are already downloaded; online \
ones are free to use but CC BY ones need a credit line in the description.

No music is often the better choice: for a Short of under a minute that relies on the \
punchline and the sound effects, when every track clashes with the tone, or when a track \
would make a serious or sad passage feel flippant. Say so when it applies."""


def _words(features: dict | None) -> tuple[str, str]:
    f = features or {}
    return ", ".join(f.get("words", [])) or "not measured", f"{f['bpm']} bpm; " if f.get("bpm") else ""


def _length(seconds: float) -> str:
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def describe(track) -> str:
    words, tempo = _words(track.features)
    genres = ", ".join(track.genres or []) or "unknown genre"
    licence = {"by": "CC BY, credit needed", "cc0": "CC0", "pdm": "public domain"}.get(
        track.license, "uploaded by you, licence unknown: the creator must check it")
    return (f'- lib-{track.id}: "{track.name}" (in your library, {licence}; {genres}; {words}; '
            f'{tempo}{_length(track.duration)})')


def describe_online(item: dict) -> str:
    words, tempo = _words(item.get("features"))
    genres = ", ".join(item.get("genres") or []) or "unknown genre"
    licence = {"by": "CC BY, credit needed", "cc0": "CC0", "pdm": "public domain"}.get(item["license"], item["license"])
    return (f'- web-{item["id"][:8]}: "{item["title"]}" by {item["creator"]} (online, {licence}; {genres}; {words}; '
            f'{tempo}{_length(item["duration"])})')


def _video(title: str, format: str, duration: float, narration: str, chapters: list[str]) -> str:
    kind = "Short, vertical, under a minute" if format == "short" else "long video"
    return (f"Title: {title}\nFormat: {kind}, {duration / 60:.1f} minutes\n"
            + (f"Chapters: {'; '.join(chapters)}\n" if chapters else "") + f"Opening narration: {narration}")


def searches(title: str, format: str, duration: float, narration: str, chapters: list[str]) -> list[str]:
    found = llm.ask("music", SEARCH, f"<video>\n{_video(title, format, duration, narration, chapters)}\n</video>",
                    Searches, effort="low")
    return [w.strip()[:40] for w in found.words if w.strip()][:3]


def suggest(title: str, format: str, duration: float, narration: str, chapters: list[str], tracks: list,
            online: list[dict]) -> Pick:
    lines = [describe(t) for t in tracks] + [describe_online(i) for i in online]
    video = _video(title, format, duration, narration, chapters)
    return llm.ask("music", SYSTEM, f"<video>\n{video}\n</video>\n\n<tracks>\n" + "\n".join(lines) + "\n</tracks>",
                   Pick, effort="low")
