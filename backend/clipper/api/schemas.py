"""Request and response shapes of the API."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

from ..db.models import as_utc

# Always serialise with an explicit UTC offset, whatever the database returned.
UtcDatetime = Annotated[datetime, PlainSerializer(lambda v: as_utc(v).isoformat(), return_type=str)]


class Model(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- jobs ----------------------------------------------------------------------

class JobOut(Model):
    id: int
    kind: str
    label: str
    video_id: str | None
    script_id: int | None = None
    take_id: int | None = None
    storyboard_id: int | None = None
    state: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    stages: list[str]
    stage: str | None
    progress: float | None
    message: str
    error: str | None
    cancel_requested: bool
    created_at: UtcDatetime
    started_at: UtcDatetime | None
    finished_at: UtcDatetime | None


class JobEventOut(Model):
    id: int
    job_id: int
    ts: UtcDatetime
    level: Literal["info", "warning", "error"]
    stage: str | None
    message: str


class ActivityEvent(JobEventOut):
    job_label: str
    job_kind: str
    video_id: str | None


# --- clips and moments ---------------------------------------------------------------

class ComplianceCheck(BaseModel):
    requirement: str
    status: Literal["pass", "fail", "manual"]
    note: str


class Compliance(BaseModel):
    verdict: Literal["pass", "review", "fail"]
    summary: str
    checks: list[ComplianceCheck]
    checked_at: int


class ClipOut(Model):
    id: int
    video_id: str
    moment_id: int
    status: Literal["pending", "approved", "rejected"]
    seconds: float
    size_bytes: int
    tracked_share: float
    captions_burned: bool
    subtitle_fix: str | None
    compliance: Compliance | None
    rendered_at: UtcDatetime
    file_exists: bool = True


class ClipListItem(ClipOut):
    video_title: str
    title: str
    hook: str
    caption: str


class MomentOut(Model):
    id: int
    position: int
    start: float
    end: float
    title: str
    hook: str
    caption: str
    score: int | None
    reason: str
    clip: ClipOut | None


class MomentIn(BaseModel):
    id: int | None = None
    start: float
    end: float
    title: str = ""
    hook: str = ""
    caption: str = ""


class MomentsIn(BaseModel):
    moments: list[MomentIn]


class ClipPatch(BaseModel):
    status: Literal["pending", "approved", "rejected"]


# --- campaigns -------------------------------------------------------------------

COMPUTED_CAMPAIGN_FIELDS = {"net_rate", "rate_platform", "best_max_payout", "payout_100k", "score", "joinable",
                            "breakdown", "flags", "brief", "video_count"}


class ScoreLine(BaseModel):
    label: str
    points: float


class CampaignOut(Model):
    id: int
    url: str | None
    source: str
    title: str
    brand: str
    category: str
    summary: str
    kind: str
    payout_model: str
    listing_status: str
    currency: str
    rate_per_1k: float | None
    min_payout: float | None
    max_payout: float | None
    fee_pct: float
    budget_total: float | None
    budget_left: float | None
    platforms: list[str]
    platform_rates: list[dict]
    source_material: str
    tier1_min_share: int | None
    geo_requirement: str
    restricted_category: str
    requirements: str
    theme_fit: int | None
    theme_reason: str
    decision: Literal["new", "interested", "joined", "skipped"]
    notes: str
    first_seen: UtcDatetime
    last_seen: UtcDatetime
    # Computed for the current settings:
    net_rate: float | None
    rate_platform: str | None
    best_max_payout: float | None
    payout_100k: float | None
    score: int
    joinable: bool
    breakdown: list[ScoreLine]
    flags: list[str]
    brief: str
    video_count: int


class CampaignIngest(BaseModel):
    url: str | None = Field(None, max_length=500)
    text: str = Field(min_length=40, description="The listing's page text, copied as is")


class CampaignPatch(BaseModel):
    decision: Literal["new", "interested", "joined", "skipped"] | None = None
    notes: str | None = None
    title: str | None = Field(None, min_length=1)
    listing_status: Literal["open", "paused", "ended", "unknown"] | None = None
    rate_per_1k: float | None = Field(None, ge=0)
    min_payout: float | None = Field(None, ge=0)
    max_payout: float | None = Field(None, ge=0)
    fee_pct: float | None = Field(None, ge=0, le=100)
    budget_left: float | None = Field(None, ge=0)
    tier1_min_share: int | None = Field(None, ge=0, le=100)
    requirements: str | None = None


class CampaignRef(Model):
    id: int
    title: str


# --- studio ideas ------------------------------------------------------------------

IdeaStatus = Literal["inbox", "new", "approved", "parked", "skipped"]


class IdeaBeat(BaseModel):
    text: str
    short: bool = False


class IdeaDemand(BaseModel):
    reference_views: int
    reference_rank: str
    best_small_views: int | None
    best_small_channel: str | None
    old_share: str
    videos: int


class IdeaOut(Model):
    id: int
    theme: str
    title: str
    hook: str
    answer: str
    beats: list[IdeaBeat]
    angle: str
    format: str
    search_query: str
    origin: str
    status: IdeaStatus
    notes: str
    research: dict | None
    judgement: dict | None
    validated_at: UtcDatetime | None
    created_at: UtcDatetime
    # Computed:
    score: int | None
    complete: bool
    breakdown: list[ScoreLine]
    flags: list[str]
    demand: IdeaDemand | None
    script_id: int | None = None


class IdeaGenerate(BaseModel):
    count: int = Field(8, ge=1, le=20)
    theme: str | None = Field(None, max_length=300, description="Defaults to the Studio theme in Settings")


class IdeaCreate(BaseModel):
    text: str = Field(min_length=3, max_length=2000, description="Your idea, in a few words or sentences")


class IdeaPatch(BaseModel):
    status: IdeaStatus | None = None
    notes: str | None = None
    title: str | None = Field(None, min_length=1)
    hook: str | None = None
    answer: str | None = None
    angle: str | None = None
    search_query: str | None = Field(None, min_length=2)


# --- videos ---------------------------------------------------------------------

class SubtitleResult(BaseModel):
    detected: bool
    kind: str | None = None
    hit_rate: float = 0.0
    samples: int = 0
    band: list[float] | None = None
    example: str | None = None


class VideoSummary(Model):
    id: str
    url: str | None
    title: str
    uploader: str | None
    duration: float
    width: int
    height: int
    created_at: UtcDatetime
    moment_count: int
    clip_count: int
    approved_count: int
    has_file: bool
    busy: bool


class VideoDetail(VideoSummary):
    brief: str
    file_mb: int
    has_transcript: bool
    subtitles: SubtitleResult | None
    subtitle_mode: str | None
    effective_subtitle_mode: str
    campaign: CampaignRef | None
    moments: list[MomentOut]


class VideoCreate(BaseModel):
    src: str = Field(min_length=1, description="A video link, or a file path the server can read")
    n: int | None = Field(None, ge=1, le=20)
    min_len: int | None = Field(None, ge=5, le=600)
    max_len: int | None = Field(None, ge=10, le=900)
    brief: str = ""
    campaign_id: int | None = Field(None, description="Clip this video for a campaign; its rules become the brief")


class VideoPatch(BaseModel):
    brief: str | None = None
    # Send null to unlink. With `use_campaign_brief`, the brief is replaced by the campaign's rules.
    campaign_id: int | None = None
    use_campaign_brief: bool = False
    # "default" clears the per-video choice.
    subtitle_mode: Literal["default", "cover", "crop", "keep", "ignore"] | None = None


class SelectRequest(BaseModel):
    n: int | None = Field(None, ge=1, le=20)
    min_len: int | None = Field(None, ge=5, le=600)
    max_len: int | None = Field(None, ge=10, le=900)


class RenderRequest(BaseModel):
    moment_ids: list[int] | None = None


class CheckRequest(BaseModel):
    clip_ids: list[int] | None = None


class UploadOut(BaseModel):
    path: str
    name: str


# --- settings and system ---------------------------------------------------------------

class FontChoice(BaseModel):
    value: str
    label: str


class SettingsOut(BaseModel):
    settings: dict
    defaults: dict
    fonts: list[FontChoice]
    default_clips_dir: str


class SystemStatus(BaseModel):
    version: str
    api_key: bool
    youtube_key: bool
    elevenlabs_key: bool
    openai_key: bool
    openai_base_url: str
    in_docker: bool
    database: str
    data_dir: str
    clips_dir: str
    disk_free_gb: float
    whisper_engine: str
    encoder: str
    tools: dict[str, bool]
    worker_online: bool
    worker_last_seen: UtcDatetime | None
    queued: int
    running: int


# --- studio scripts ------------------------------------------------------------------

class ScriptSection(BaseModel):
    key: str | None = Field(None, max_length=16, description="Omit for a new section")
    heading: str = Field(max_length=200)
    text: str = Field(max_length=20000)
    short: bool = False


class ScriptSource(BaseModel):
    n: int
    url: str
    title: str


class ScriptResearch(BaseModel):
    notes: str
    sources: list[ScriptSource]
    searches: int = 0


class ScriptClaim(BaseModel):
    section: str
    claim: str
    verdict: Literal["supported", "contradicted", "unclear", "joke"]
    sources: list[int]
    note: str


class ScriptSummary(Model):
    id: int
    idea_id: int | None
    title: str
    format: str
    target_minutes: int
    status: Literal["draft", "approved"]
    created_at: UtcDatetime
    updated_at: UtcDatetime
    # Computed:
    words: int
    minutes: float
    claim_counts: dict[str, int] | None
    checked_current: bool
    job: JobOut | None


class ScriptOut(ScriptSummary):
    theme: str
    notes: str
    research: ScriptResearch | None
    sections: list[ScriptSection]
    claims: list[ScriptClaim] | None


class ScriptCreate(BaseModel):
    target_minutes: int | None = Field(None, ge=1, le=30, description="Defaults to 6 for one topic, 10 for a list")


class ScriptPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    status: Literal["draft", "approved"] | None = None
    target_minutes: int | None = Field(None, ge=1, le=30)
    notes: str | None = None


class ScriptSections(BaseModel):
    sections: list[ScriptSection] = Field(min_length=1)


class SectionRewrite(BaseModel):
    key: str
    instruction: str = Field(min_length=2, max_length=500)


class SectionRewriteOut(BaseModel):
    key: str
    text: str


# --- studio voice ------------------------------------------------------------------------

class VoiceChoice(BaseModel):
    id: str
    label: str
    preview: str | None = None


class VoiceSample(BaseModel):
    voice: str = Field(min_length=2, max_length=64)
    speed: float = Field(1.0, ge=0.7, le=1.3)
    text: str = Field("", max_length=600)


class VoiceOptions(BaseModel):
    kokoro: list[VoiceChoice]
    kokoro_downloaded: bool
    elevenlabs: list[VoiceChoice]
    elevenlabs_available: bool
    elevenlabs_error: str | None
    defaults: dict


class TakeMissing(BaseModel):
    section: str
    heading: str
    text: str
    heard: str


class TakeExtra(BaseModel):
    at: float
    text: str


class TakeSection(BaseModel):
    key: str
    heading: str
    start: float
    end: float


class TakeAlignment(BaseModel):
    coverage: float
    sections: list[TakeSection]
    missing: list[TakeMissing]
    extra: list[TakeExtra]
    spoken_words: int
    script_hash: str


class TakeOut(Model):
    id: int
    script_id: int
    source: Literal["recording", "kokoro", "elevenlabs"]
    voice: str
    speed: float | None
    duration: float | None
    chosen: bool
    created_at: UtcDatetime
    alignment: TakeAlignment | None
    # Computed:
    ready: bool
    current: bool          # made from the script as it is now
    job: JobOut | None


class AiTakeCreate(BaseModel):
    engine: Literal["kokoro", "elevenlabs"]
    voice: str = Field(min_length=1, max_length=64)
    speed: float = Field(1.0, ge=0.7, le=1.3)


# --- studio storyboards and assets ---------------------------------------------------------

class StoryboardCreate(BaseModel):
    format: Literal["short", "long"] = "short"
    section_key: str | None = Field(None, description="Required for a Short: the section it tells")
    take_id: int | None = Field(None, description="Defaults to the take marked for the video")


class StoryboardSummary(Model):
    id: int
    script_id: int
    take_id: int | None
    format: Literal["short", "long"]
    section_key: str | None
    title: str
    review: Literal["pending", "approved", "rejected"]
    rendered_at: UtcDatetime | None
    created_at: UtcDatetime
    music_id: int | None
    music_db: float
    effects: bool
    # Computed:
    duration: float
    scene_count: int
    has_video: bool
    job: JobOut | None


class StoryboardListItem(StoryboardSummary):
    script_title: str


class StoryboardOut(StoryboardSummary):
    words: list[dict]
    scenes: list[dict]
    missing_props: list[str]
    music_credit: str = ""


class StoryboardScenes(BaseModel):
    scenes: list[dict] = Field(min_length=1)


class StoryboardPatch(BaseModel):
    review: Literal["pending", "approved", "rejected"] | None = None
    music_id: int | None = None
    music_db: float | None = Field(None, ge=-40, le=-6)
    effects: bool | None = None


class MusicOut(Model):
    id: int
    name: str
    duration: float
    creator: str
    license: str
    source_url: str
    attribution: str
    genres: list[str]
    features: dict | None
    created_at: UtcDatetime


class SuggestedTrack(BaseModel):
    ref: str
    fit: int
    reason: str
    source: Literal["library", "online"]
    track_id: int | None          # library tracks
    openverse_id: str | None      # online tracks, to preview or add
    title: str
    creator: str
    license: str
    duration: float
    words: list[str]
    landing_url: str


class MusicSuggestion(BaseModel):
    choice: str | None            # a ref from the ranking, or None for no music
    reason: str
    level_db: int
    searches: list[str]           # what was searched online
    ranking: list[SuggestedTrack]


class SuggestMusic(BaseModel):
    online: bool = Field(True, description="Also look for tracks on Openverse, not only in your library")


class MusicFound(BaseModel):
    id: str
    title: str
    creator: str
    license: str
    license_version: str
    license_url: str
    source: str
    landing_url: str
    audio_url: str
    duration: float
    genres: list[str]
    attribution: str


class MusicSearch(BaseModel):
    count: int
    page: int
    pages: int
    results: list[MusicFound]


class MusicImport(BaseModel):
    openverse_id: str = Field(min_length=8, max_length=64)


class UsageLine(BaseModel):
    key: str
    label: str
    tier: str
    calls: int
    failed: int
    input_tokens: int
    output_tokens: int
    searches: int
    cost_usd: float
    unpriced_calls: int


class UsageReport(BaseModel):
    days: int
    calls: int
    failed: int
    cost_usd: float
    by_task: list[UsageLine]
    by_model: list[UsageLine]
    tiers: dict[str, dict]


class Chapters(BaseModel):
    text: str
    valid: bool
    problems: list[str]


class AssetOut(Model):
    name: str
    description: str
    drawn: bool
    origin: str
    updated_at: UtcDatetime
    used_in: int


class AssetRedraw(BaseModel):
    instruction: str = Field("", max_length=500)
    description: str | None = Field(None, max_length=500)
