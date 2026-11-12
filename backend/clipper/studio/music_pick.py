"""Which background track suits a video, if any. One small call on the quick tier.

The tracks are described by what the app measured itself (tempo, density,
brightness, dynamics) and their genres, so the model never hears audio and the
call stays at a few hundred tokens. "No music" is a valid answer.
"""

from pydantic import BaseModel, Field

from .. import llm


class Fit(BaseModel):
    track_id: int
    fit: int = Field(description="0 to 10")
    reason: str = Field(description="One short sentence")


class Pick(BaseModel):
    choice: int | None = Field(description="The track to use, or null when the video is better without music")
    reason: str = Field(description="One or two sentences, for the creator")
    level_db: int = Field(description="Suggested music level under the voice, -28 (barely there) to -14 (present)")
    ranking: list[Fit] = Field(description="Every track, best first")


SYSTEM = """You choose background music for one video of a faceless animated YouTube channel: \
deadpan comic narration over simple drawn animation. The music sits under the voice, \
so it must never compete with it: instrumental, steady, not too busy or bright, matching \
the tone (curious, dry, playful). Judge each track only from what you are told about it.

No music is often the better choice: for a Short of under a minute that relies on the \
punchline and the sound effects, when every track clashes with the tone, or when a track \
would make a serious or sad passage feel flippant. Say so when it applies."""


def describe(track) -> str:
    f = track.features or {}
    words = ", ".join(f.get("words", [])) or "not measured"
    genres = ", ".join(track.genres or []) or "unknown genre"
    tempo = f"{f['bpm']} bpm; " if f.get("bpm") else ""
    length = f"{int(track.duration // 60)}:{int(track.duration % 60):02d}"
    return f'- id {track.id}: "{track.name}" ({genres}; {words}; {tempo}{length} long)'


def suggest(title: str, format: str, duration: float, narration: str, chapters: list[str], tracks: list) -> Pick:
    video = (f"Title: {title}\nFormat: {'Short, vertical, under a minute' if format == 'short' else 'long video'}"
             f", {duration / 60:.1f} minutes\n" + (f"Chapters: {'; '.join(chapters)}\n" if chapters else "")
             + f"Opening narration: {narration}")
    library = "\n".join(describe(t) for t in tracks)
    return llm.ask("music", SYSTEM, f"<video>\n{video}\n</video>\n\n<tracks>\n{library}\n</tracks>", Pick,
                   effort="low")
