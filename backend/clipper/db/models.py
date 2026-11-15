"""Database tables.

Large media stays on disk (source videos, transcripts, rendered clips). The
database holds everything that describes it and everything about the work done:
videos, the moments chosen in them, the clips rendered, jobs and their logs,
settings and worker heartbeats.
"""

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; everything stored here is UTC."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


class Base(DeclarativeBase):
    pass


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    url: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    uploader: Mapped[str | None] = mapped_column(Text)
    duration: Mapped[float] = mapped_column(Float)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    brief: Mapped[str] = mapped_column(Text, default="")
    # Per-video override of the "source subtitles" setting; NULL = use the default.
    subtitle_mode: Mapped[str | None] = mapped_column(String(16))
    # Per-video override of the "clip format" setting (vertical|horizontal); NULL = use the default.
    clip_format: Mapped[str | None] = mapped_column(String(12))
    # Result of subtitle detection; NULL = not analysed yet.
    subtitles: Mapped[dict | None] = mapped_column(JSON)
    # The campaign this video is being clipped for, if any.
    campaign_id: Mapped[int | None] = mapped_column(
        ForeignKey("campaigns.id", ondelete="SET NULL", name="fk_videos_campaign_id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    moments: Mapped[list["Moment"]] = relationship(
        back_populates="video", cascade="all, delete-orphan", order_by="Moment.position")
    clips: Mapped[list["Clip"]] = relationship(back_populates="video", cascade="all, delete-orphan")
    campaign: Mapped["Campaign | None"] = relationship()


class Campaign(Base):
    """A paid clipping campaign found on a marketplace, with the terms that matter
    for deciding whether to join it. Terms are read from the listing by Claude;
    `decision` and `notes` belong to the user and survive a re-read."""

    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str | None] = mapped_column(String(500), unique=True)
    source: Mapped[str] = mapped_column(String(40), default="")        # marketplace name
    title: Mapped[str] = mapped_column(Text)
    brand: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(16), default="clipping")   # clipping|ugc|other
    payout_model: Mapped[str] = mapped_column(String(16), default="cpm")  # cpm|per_post|retainer|pool|other
    listing_status: Mapped[str] = mapped_column(String(16), default="unknown")  # open|paused|ended|unknown
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    rate_per_1k: Mapped[float | None] = mapped_column(Float)
    min_payout: Mapped[float | None] = mapped_column(Float)
    max_payout: Mapped[float | None] = mapped_column(Float)            # per clip
    fee_pct: Mapped[float] = mapped_column(Float, default=10.0)
    budget_total: Mapped[float | None] = mapped_column(Float)
    budget_left: Mapped[float | None] = mapped_column(Float)
    platforms: Mapped[list] = mapped_column(JSON, default=list)        # tiktok, instagram, youtube, x...
    # Rates often differ per platform: [{platform, rate_per_1k, min_payout, max_payout}].
    # rate_per_1k / min_payout / max_payout above hold the lowest-paying platform's figures.
    platform_rates: Mapped[list] = mapped_column(JSON, default=list)
    source_material: Mapped[str] = mapped_column(String(16), default="unknown")  # provided|own|unknown
    tier1_min_share: Mapped[int | None] = mapped_column(Integer)       # % of viewers required in US/UK/CA/AU
    geo_requirement: Mapped[str] = mapped_column(Text, default="")
    restricted_category: Mapped[str] = mapped_column(String(16), default="none")
    requirements: Mapped[str] = mapped_column(Text, default="")        # becomes the brief of its videos
    theme_fit: Mapped[int | None] = mapped_column(Integer)             # 0..10 against the user's theme
    theme_reason: Mapped[str] = mapped_column(Text, default="")
    raw_text: Mapped[str] = mapped_column(Text, default="")            # the listing as it was read
    # "ai": Claude read the terms. "page": the scout read them from the page layout because
    # the AI was unavailable; rules, location and theme fit are then unchecked.
    read_by: Mapped[str] = mapped_column(String(8), default="ai", server_default="ai")
    decision: Mapped[str] = mapped_column(String(16), default="new", index=True)  # new|interested|joined|skipped
    notes: Mapped[str] = mapped_column(Text, default="")
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Idea(Base):
    """A video idea for the animated channel (the Studio), and how it was judged.

    Claude proposes the idea; `research` holds what YouTube showed for it and
    `judgement` Claude's reading of it. The score is computed from both on
    request, so it can always be explained. `status` and `notes` are the user's.
    """

    __tablename__ = "ideas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    theme: Mapped[str] = mapped_column(Text, default="")              # the channel theme it was made for
    title: Mapped[str] = mapped_column(Text)
    hook: Mapped[str] = mapped_column(Text, default="")               # the question that opens the video
    answer: Mapped[str] = mapped_column(Text, default="")             # the surprising answer, in one line
    beats: Mapped[list] = mapped_column(JSON, default=list)           # [{text, short}]
    angle: Mapped[str] = mapped_column(Text, default="")              # how it differs from what exists
    format: Mapped[str] = mapped_column(String(16), default="single")  # single|list
    video: Mapped[str] = mapped_column(String(8), default="long")      # long (with Shorts from it) | short (only)
    search_query: Mapped[str] = mapped_column(Text, default="")       # what a viewer would search
    origin: Mapped[str] = mapped_column(String(16), default="claude")  # claude|you
    status: Mapped[str] = mapped_column(String(16), default="new", index=True)  # inbox|new|approved|parked|skipped
    notes: Mapped[str] = mapped_column(Text, default="")
    research: Mapped[dict | None] = mapped_column(JSON)               # top YouTube results; NULL = not checked
    judgement: Mapped[dict | None] = mapped_column(JSON)              # Claude's assessment; NULL = not checked
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Script(Base):
    """The narration for one Studio video, from research to the text you will read aloud.

    `research` holds Claude's notes with numbered web sources; `sections` the
    script itself (edited by you); `claims` the fact check of every factual
    statement, with `checked_hash` recording which text it checked.
    """

    __tablename__ = "scripts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    idea_id: Mapped[int | None] = mapped_column(
        ForeignKey("ideas.id", ondelete="SET NULL", name="fk_scripts_idea_id"), index=True)
    title: Mapped[str] = mapped_column(Text)
    theme: Mapped[str] = mapped_column(Text, default="")
    format: Mapped[str] = mapped_column(String(16), default="single")      # single|list
    target_minutes: Mapped[int] = mapped_column(Integer, default=6)
    video: Mapped[str] = mapped_column(String(8), default="long")      # long | short (a Short only)
    target_seconds: Mapped[int] = mapped_column(Integer, default=50)   # for a Short
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)  # draft|approved
    research: Mapped[dict | None] = mapped_column(JSON)    # {notes, sources: [{n, url, title}], searches}
    sections: Mapped[list] = mapped_column(JSON, default=list)   # [{key, heading, text, short}]
    claims: Mapped[list | None] = mapped_column(JSON)      # [{section, claim, verdict, note, sources}]
    checked_hash: Mapped[str | None] = mapped_column(String(64))  # hash of the text the claims were checked on
    previous_sections: Mapped[list | None] = mapped_column(JSON)  # the text before the last whole-script rewrite
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    idea: Mapped["Idea | None"] = relationship()
    takes: Mapped[list["VoiceTake"]] = relationship(
        back_populates="script", cascade="all, delete-orphan", order_by="VoiceTake.id")
    storyboards: Mapped[list["Storyboard"]] = relationship(
        back_populates="script", cascade="all, delete-orphan", order_by="Storyboard.id")


class VoiceTake(Base):
    """One narration of a script: your recording, or an AI voice.

    The audio lives on disk (`path`, a loudness-normalised WAV). `alignment`
    says how the speech lines up with the script it was made from
    (`script_hash`): each section's time range, script lines that were not
    spoken, and speech that is not in the script. The per-word timeline is a
    file next to the audio.
    """

    __tablename__ = "voice_takes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    script_id: Mapped[int] = mapped_column(
        ForeignKey("scripts.id", ondelete="CASCADE", name="fk_voice_takes_script_id"), index=True)
    source: Mapped[str] = mapped_column(String(16))                 # recording|kokoro|chatterbox|elevenlabs
    voice: Mapped[str] = mapped_column(String(120), default="")     # AI voice name, or the uploaded file name
    speed: Mapped[float | None] = mapped_column(Float)
    pauses: Mapped[str | None] = mapped_column(String(16))          # AI takes: tight | natural | relaxed
    path: Mapped[str | None] = mapped_column(Text)                  # the prepared WAV, absolute
    duration: Mapped[float | None] = mapped_column(Float)
    script_hash: Mapped[str] = mapped_column(String(64))
    alignment: Mapped[dict | None] = mapped_column(JSON)
    chosen: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    script: Mapped[Script] = relationship(back_populates="takes")


class StudioAsset(Base):
    """A prop in the library, drawn once as SVG (by Claude in the house style, or by you)."""

    __tablename__ = "studio_assets"

    name: Mapped[str] = mapped_column(String(48), primary_key=True)   # snake_case, used in storyboards
    description: Mapped[str] = mapped_column(Text, default="")
    svg: Mapped[str | None] = mapped_column(Text)                      # NULL = not drawn yet
    origin: Mapped[str] = mapped_column(String(16), default="claude")  # claude|you
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StudioPlace(Base):
    """A place for the cartoon look (a study, a scriptorium...), drawn once and used by every video.

    Two drawings, made only when a video needs them: wide for long videos, tall for
    Shorts. The six places drawn by code (`cartoon.PLACES`) have no row.
    """

    __tablename__ = "studio_places"

    name: Mapped[str] = mapped_column(String(48), primary_key=True)   # snake_case, used on shot lines
    description: Mapped[str] = mapped_column(Text, default="")
    svg: Mapped[str | None] = mapped_column(Text)                      # wide; NULL = not drawn yet
    svg_tall: Mapped[str | None] = mapped_column(Text)                 # tall, for Shorts
    origin: Mapped[str] = mapped_column(String(16), default="claude")  # claude|you
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StudioMusic(Base):
    """A background track you uploaded. Use only music you have the rights to,
    for example from the YouTube Audio Library."""

    __tablename__ = "studio_music"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200))
    path: Mapped[str] = mapped_column(Text)                 # absolute
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    # Where it came from, for tracks found through the music search; empty for uploads.
    creator: Mapped[str] = mapped_column(Text, default="")
    license: Mapped[str] = mapped_column(String(16), default="")       # cc0 | pdm | by
    source_url: Mapped[str] = mapped_column(Text, default="")
    attribution: Mapped[str] = mapped_column(Text, default="")         # the credit line for the description
    genres: Mapped[list] = mapped_column(JSON, default=list)
    # Measured from the audio: tempo, how busy and how bright it is (see sound.describe_track).
    features: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Storyboard(Base):
    """Scenes for one stretch of a voice take, and the video rendered from them.

    `words` are the take's words for that stretch with times relative to
    `start`; scenes refer to them by number. A rendered video waits for your
    review (`review`) like any clip.
    """

    __tablename__ = "storyboards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    script_id: Mapped[int] = mapped_column(
        ForeignKey("scripts.id", ondelete="CASCADE", name="fk_storyboards_script_id"), index=True)
    take_id: Mapped[int | None] = mapped_column(
        ForeignKey("voice_takes.id", ondelete="SET NULL", name="fk_storyboards_take_id"))
    format: Mapped[str] = mapped_column(String(8), default="short")      # short|long
    style: Mapped[str] = mapped_column(String(8), default="ink")         # ink (beats on one page) | flat (slides)
    look: Mapped[str] = mapped_column(String(12), default="ink")         # ink style only: ink | paper | blueprint
    section_key: Mapped[str | None] = mapped_column(String(16))          # NULL = the whole script
    title: Mapped[str] = mapped_column(Text, default="")
    start: Mapped[float] = mapped_column(Float, default=0.0)            # seconds into the take's audio
    end: Mapped[float] = mapped_column(Float, default=0.0)
    words: Mapped[list] = mapped_column(JSON, default=list)
    scenes: Mapped[list] = mapped_column(JSON, default=list)
    video_path: Mapped[str | None] = mapped_column(Text)
    rendered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review: Mapped[str] = mapped_column(String(16), default="pending")   # pending|approved|rejected
    # When the verdict last changed. With rendered_at it starts the grace period (studio/retention.py).
    review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set when the render was deleted because it was not approved in time; cleared by the next render.
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The last export of the approved video: {folder, files, at, rendered_at} (studio/export.py).
    export: Mapped[dict | None] = mapped_column(JSON)
    # Sound under the voice.
    music_id: Mapped[int | None] = mapped_column(
        ForeignKey("studio_music.id", ondelete="SET NULL", name="fk_storyboards_music_id"))
    music_db: Mapped[float] = mapped_column(Float, default=-18.0)        # music level vs the voice, before ducking
    effects: Mapped[bool] = mapped_column(Boolean, default=True)         # pops and whooshes
    # The YouTube text, once written: {titles, title, description, tags, made_at, edited, problems}.
    youtube: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    script: Mapped[Script] = relationship(back_populates="storyboards")
    music: Mapped["StudioMusic | None"] = relationship()
    take: Mapped["VoiceTake | None"] = relationship()


class StudioPhoto(Base):
    """An archive picture found for a search in a storyboard (`photo "..."`), with its credit.

    The search is the key: every video that asks for it shows the same picture.
    The file is under data/studio/photos; `rank` is which search result it is,
    so "try another" can move on to the next.
    """

    __tablename__ = "studio_photos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    query: Mapped[str] = mapped_column(String(200), unique=True)
    rank: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(40), default="Wikimedia Commons")
    key: Mapped[str] = mapped_column(String(40), default="")            # the source's own id
    title: Mapped[str] = mapped_column(Text, default="")
    creator: Mapped[str] = mapped_column(Text, default="")
    license: Mapped[str] = mapped_column(String(60), default="")
    license_url: Mapped[str] = mapped_column(Text, default="")
    landing_url: Mapped[str] = mapped_column(Text, default="")
    credit: Mapped[str] = mapped_column(Text, default="")
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    path: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class StudioEvent(Base):
    """One step in making a video, by you or by the AI: the authorship log.

    YouTube asks for real human input in what it pays for. This log records who
    did what (wrote, edited, recorded, chose, reviewed), so the work can be shown
    if a channel is ever questioned. It records, it never judges.
    """

    __tablename__ = "studio_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    script_id: Mapped[int] = mapped_column(
        ForeignKey("scripts.id", ondelete="CASCADE", name="fk_studio_events_script_id"), index=True)
    storyboard_id: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(24))
    by: Mapped[str] = mapped_column(String(4))                           # you | ai
    summary: Mapped[str] = mapped_column(Text, default="")
    amount: Mapped[int] = mapped_column(Integer, default=0)              # words or lines changed, when it applies
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AiUsage(Base):
    """One AI call: what for, which model, tokens, searches and estimated cost."""

    __tablename__ = "ai_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    task: Mapped[str] = mapped_column(String(32))
    tier: Mapped[str] = mapped_column(String(16))
    provider: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(80))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    searches: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Float)              # estimate; NULL = model not priced
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    outage: Mapped[bool] = mapped_column(Boolean, default=False)      # failed because the AI was unavailable
    error: Mapped[str] = mapped_column(Text, default="")
    duration_s: Mapped[float] = mapped_column(Float, default=0.0)
    # what the call was for: "idea:3", "script:4", "storyboard:8", "video:12"; NULL before this existed
    ref: Mapped[str | None] = mapped_column(String(40), index=True)


class Moment(Base):
    __tablename__ = "moments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    start_s: Mapped[float] = mapped_column(Float)
    end_s: Mapped[float] = mapped_column(Float)
    title: Mapped[str] = mapped_column(Text, default="")
    hook: Mapped[str] = mapped_column(Text, default="")
    caption: Mapped[str] = mapped_column(Text, default="")
    score: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text, default="")

    video: Mapped[Video] = relationship(back_populates="moments")
    clip: Mapped["Clip | None"] = relationship(
        back_populates="moment", cascade="all, delete-orphan", uselist=False)


class Clip(Base):
    __tablename__ = "clips"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    moment_id: Mapped[int] = mapped_column(ForeignKey("moments.id", ondelete="CASCADE"), unique=True)
    # Absolute path at render time, so clips stay reachable if the output folder changes later.
    path: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending|approved|rejected
    seconds: Mapped[float] = mapped_column(Float)
    size_bytes: Mapped[int] = mapped_column(Integer)
    tracked_share: Mapped[float] = mapped_column(Float, default=0.0)
    captions_burned: Mapped[bool] = mapped_column(Boolean, default=True)
    subtitle_fix: Mapped[str | None] = mapped_column(String(16))
    # The shape it was rendered in: vertical (1080x1920) or horizontal (1920x1080).
    format: Mapped[str] = mapped_column(String(12), default="vertical", server_default="vertical")
    # Result of the campaign-brief check; NULL = not checked since the last render.
    compliance: Mapped[dict | None] = mapped_column(JSON)
    rendered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    video: Mapped[Video] = relationship(back_populates="clips")
    moment: Mapped[Moment] = relationship(back_populates="clip")


class Post(Base):
    """An approved Short handed to Buffer to be published on one channel.

    The video is a copy put online for Buffer (`media_key` in the bucket, see
    hosting.py): the file you approved, whatever is rendered afterwards.
    `title` is kept here so the row still reads well once its clip is gone.
    """

    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(12))                       # clip | storyboard
    clip_id: Mapped[int | None] = mapped_column(
        ForeignKey("clips.id", ondelete="SET NULL", name="fk_posts_clip_id"), index=True)
    storyboard_id: Mapped[int | None] = mapped_column(
        ForeignKey("storyboards.id", ondelete="SET NULL", name="fk_posts_storyboard_id"), index=True)
    title: Mapped[str] = mapped_column(Text, default="")
    channel_id: Mapped[str] = mapped_column(String(40))                 # Buffer's id of the channel
    service: Mapped[str] = mapped_column(String(16))                    # instagram | tiktok
    channel_name: Mapped[str] = mapped_column(Text, default="")
    text: Mapped[str] = mapped_column(Text, default="")                 # the caption posted with the video
    ai_label: Mapped[bool] = mapped_column(Boolean, default=False)      # disclosed as AI-generated
    mode: Mapped[str] = mapped_column(String(8), default="queue")       # queue | at | draft
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # queued (waiting for our worker) -> draft | scheduled -> sending -> sent, or
    # failed (never reached Buffer, or Buffer could not publish) | cancelled | removed (deleted in Buffer)
    status: Mapped[str] = mapped_column(String(12), default="queued", index=True)
    buffer_id: Mapped[str | None] = mapped_column(String(40))
    media_key: Mapped[str | None] = mapped_column(Text)                 # NULL once the hosted copy is deleted
    media_url: Mapped[str | None] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(Text)                      # the post on the network, once sent
    error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))   # last asked Buffer
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(24))        # process|select|render|check|subtitles|post...
    label: Mapped[str] = mapped_column(Text)
    # Not a foreign key: a job's history outlives the video it worked on.
    video_id: Mapped[str | None] = mapped_column(String(80), index=True)
    state: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    stages: Mapped[list] = mapped_column(JSON, default=list)
    stage: Mapped[str | None] = mapped_column(String(24))
    progress: Mapped[float | None] = mapped_column(Float)   # 0..1 within the stage; NULL = unknown
    message: Mapped[str] = mapped_column(Text, default="Waiting")
    error: Mapped[str | None] = mapped_column(Text)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    worker_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    events: Mapped[list["JobEvent"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="JobEvent.id")

    @property
    def script_id(self) -> int | None:
        """Studio script jobs carry their script in params; video jobs have none."""
        return (self.params or {}).get("script_id")

    @property
    def take_id(self) -> int | None:
        return (self.params or {}).get("take_id")

    @property
    def storyboard_id(self) -> int | None:
        return (self.params or {}).get("storyboard_id")


class JobEvent(Base):
    """One log line of a job, shown in the UI and mirrored to the process log."""

    __tablename__ = "job_events"
    __table_args__ = (Index("ix_job_events_job_id_id", "job_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    level: Mapped[str] = mapped_column(String(8), default="info")   # info|warning|error
    stage: Mapped[str | None] = mapped_column(String(24))
    message: Mapped[str] = mapped_column(Text)

    job: Mapped[Job] = relationship(back_populates="events")


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Worker(Base):
    """Heartbeat row, so the API can tell whether anything is processing the queue."""

    __tablename__ = "workers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
