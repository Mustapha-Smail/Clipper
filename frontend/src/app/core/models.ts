export type JobState = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
export type ClipStatus = 'pending' | 'approved' | 'rejected';
export type SubtitleMode = 'cover' | 'crop' | 'keep' | 'ignore';
/** The shape of a clip: vertical is 9:16 (1080×1920), horizontal is 16:9 (1920×1080). */
export type ClipFormat = 'vertical' | 'horizontal';

export interface Job {
  id: number;
  kind: string;
  label: string;
  video_id: string | null;
  script_id: number | null;
  take_id: number | null;
  storyboard_id: number | null;
  /** Theme jobs (ideas, banner). */
  theme_id?: number | null;
  state: JobState;
  stages: string[];
  stage: string | null;
  progress: number | null;
  message: string;
  error: string | null;
  cancel_requested: boolean;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface JobEvent {
  id: number;
  job_id: number;
  ts: string;
  level: 'info' | 'warning' | 'error';
  stage: string | null;
  message: string;
}

export interface ActivityEvent extends JobEvent {
  job_label: string;
  job_kind: string;
  video_id: string | null;
}

export interface ComplianceCheck {
  requirement: string;
  status: 'pass' | 'fail' | 'manual';
  note: string;
}

export interface Compliance {
  verdict: 'pass' | 'review' | 'fail';
  summary: string;
  checks: ComplianceCheck[];
  checked_at: number;
}

export interface Clip {
  id: number;
  video_id: string;
  moment_id: number;
  status: ClipStatus;
  seconds: number;
  size_bytes: number;
  tracked_share: number;
  captions_burned: boolean;
  subtitle_fix: string | null;
  format: ClipFormat;
  compliance: Compliance | null;
  rendered_at: string;
  file_exists: boolean;
}

export interface ClipListItem extends Clip {
  video_title: string;
  title: string;
  hook: string;
  caption: string;
  /** The moment's score, 1 to 10. */
  score: number | null;
}

export interface Moment {
  id: number;
  position: number;
  start: number;
  end: number;
  title: string;
  hook: string;
  caption: string;
  score: number | null;
  reason: string;
  clip: Clip | null;
}

export interface SubtitleResult {
  detected: boolean;
  kind: 'verbatim' | 'summary' | null;
  hit_rate: number;
  samples: number;
  band: [number, number] | null;
  example: string | null;
}

export interface VideoSummary {
  id: string;
  url: string | null;
  title: string;
  uploader: string | null;
  duration: number;
  width: number;
  height: number;
  created_at: string;
  moment_count: number;
  clip_count: number;
  approved_count: number;
  has_file: boolean;
  busy: boolean;
  /** Its latest job failed and it has no clip yet: it can be deleted from the list. */
  failed?: boolean;
  error?: string;
  failed_job_id?: number | null;
}

export type CampaignDecision = 'new' | 'interested' | 'joined' | 'skipped';

export interface Campaign {
  id: number;
  url: string | null;
  source: string;
  title: string;
  brand: string;
  category: string;
  summary: string;
  kind: string;
  payout_model: string;
  listing_status: 'open' | 'paused' | 'ended' | 'unknown';
  currency: string;
  rate_per_1k: number | null;
  min_payout: number | null;
  max_payout: number | null;
  fee_pct: number;
  budget_total: number | null;
  budget_left: number | null;
  platforms: string[];
  platform_rates: { platform: string; rate_per_1k: number; min_payout: number | null; max_payout: number | null }[];
  source_material: string;
  tier1_min_share: number | null;
  geo_requirement: string;
  restricted_category: string;
  requirements: string;
  theme_fit: number | null;
  theme_reason: string;
  decision: CampaignDecision;
  /** ai, page (read by the scout without AI) or waiting (for the AI to read it). */
  read_by?: 'ai' | 'page' | 'waiting';
  notes: string;
  first_seen: string;
  last_seen: string;
  net_rate: number | null;
  rate_platform: string | null;
  best_max_payout: number | null;
  payout_100k: number | null;
  score: number;
  joinable: boolean;
  breakdown: { label: string; points: number }[];
  flags: string[];
  brief: string;
  video_count: number;
}

export type CampaignPatch = Partial<Pick<Campaign,
  'decision' | 'notes' | 'title' | 'listing_status' | 'rate_per_1k' | 'max_payout' | 'budget_left' | 'requirements'>>;

export interface VideoDetail extends VideoSummary {
  campaign: { id: number; title: string } | null;
  brief: string;
  file_mb: number;
  has_transcript: boolean;
  subtitles: SubtitleResult | null;
  subtitle_mode: SubtitleMode | null;
  effective_subtitle_mode: SubtitleMode;
  clip_format: ClipFormat | null;
  effective_clip_format: ClipFormat;
  moments: Moment[];
}

export interface MomentInput {
  id: number | null;
  start: number;
  end: number;
  title: string;
  hook: string;
  caption: string;
}

export interface AppSettings {
  theme: string;
  studio_theme: string;
  theme_banners: boolean;
  /** Who the videos are for: ideas, research and scripts are written for them. */
  audience: Audience;
  voice_engine: VoiceEngine;
  kokoro_voice: string;
  chatterbox_voice: string;
  elevenlabs_voice: string;
  elevenlabs_model: string;
  elevenlabs_direction: boolean;
  voice_speed: number;
  voice_pauses: Pauses;
  audience_tier1_share: number | null;
  platforms: string[];
  /** Hours between automatic campaign scouts; 0 = only by hand. */
  scout_every_hours: 0 | 6 | 12 | 24 | 72 | 168;
  scout_max_pages: number;
  output_dir: string;
  delete_source_after_render: boolean;
  /** Days an unapproved Studio render is kept before it is deleted; 0 = forever. */
  unapproved_keep_days: number;
  prop_drawing: 'careful' | 'quick';
  director_pass: boolean;
  /** Optional effects: an attention sound at 0 s, and risers into the first twist and the payoff. */
  sound_starter: boolean;
  sound_riser: boolean;
  clips_per_video: number;
  ai_fallback: 'rules' | 'fail';
  min_len: number;
  max_len: number;
  ai_writing_provider: AiProvider;
  ai_writing_model: string;
  ai_quick_provider: AiProvider;
  ai_quick_model: string;
  ai_research_provider: AiProvider;
  ai_research_model: string;
  auto_compliance: boolean;
  language: string;
  whisper_engine: string;
  whisper_model: string;
  voice_align_model: string;
  ink_wobble: 'off' | 'subtle' | 'strong';
  feature_clipping: boolean;
  feature_studio: boolean;
  max_height: number;
  cookies_file: string;
  encoder: string;
  bitrate_mbps: number;
  captions_enabled: boolean;
  caption_font: string;
  highlight_color: string;
  hook_seconds: number;
  subtitle_mode: SubtitleMode;
  clip_format: ClipFormat;
}

export interface SettingsBundle {
  settings: AppSettings;
  defaults: AppSettings;
  fonts: { value: string; label: string }[];
  default_clips_dir: string;
}

/** Whether this feature can schedule through its Buffer account and the public bucket. */
export interface PostingStatus {
  ready: boolean;
  buffer_key: boolean;
  hosting: boolean;
  missing: string[];
  services: string[];
}

/** One of your channels in Buffer. */
export interface BufferChannel {
  id: string;
  service: string;
  name: string;
  type: string;
  timezone: string;
  weekly_slots: number;
  /** Buffer's posting times per weekday ("mon"...), in the channel's timezone. */
  schedule: { day: string; times: string[] }[];
  disconnected: boolean;
  locked: boolean;
  queue_paused: boolean;
}

export type PostMode = 'queue' | 'at' | 'draft';
export type PostStatus = 'queued' | 'draft' | 'scheduled' | 'sending' | 'sent' | 'failed' | 'cancelled' | 'removed';

/** An approved video handed to Buffer for one channel. */
/** What "Undo all" did: posts taken back, those Buffer refused, and those already out (left alone). */
export interface UndoResult {
  undone: Post[];
  failed: string[];
  sent: Post[];
}

export interface Post {
  id: number;
  kind: 'clip' | 'storyboard';
  clip_id: number | null;
  storyboard_id: number | null;
  title: string;
  channel_id: string;
  service: string;
  channel_name: string;
  text: string;
  ai_label: boolean;
  mode: PostMode;
  due_at: string | null;
  status: PostStatus;
  link: string | null;
  error: string | null;
  sent_at: string | null;
  checked_at: string | null;
  created_at: string;
  hosted: boolean;
}

export interface NewPost {
  kind: 'clip' | 'storyboard';
  ref_id: number;
  channel_ids: string[];
  text: string;
  youtube_text: string;
  title: string;
  mode: PostMode;
  due_at: string | null;
  ai_label: boolean;
}

export interface SystemStatus {
  features?: { clipping: boolean; studio: boolean };
  posting?: PostingStatus;
  /** The latest AI call failed because the AI is unavailable (no credit, no key, down). */
  ai?: { ok: boolean; message: string; since: string | null };
  version: string;
  api_key: boolean;
  youtube_key: boolean;
  elevenlabs_key: boolean;
  openai_key: boolean;
  openai_base_url: string;
  /** The Claude CLI is installed where the app runs. */
  claude_cli: boolean;
  /** CLAUDE_CODE_OAUTH_TOKEN is set: what a server needs to use a Claude plan through the CLI. */
  claude_cli_token: boolean;
  /** The Codex CLI is installed where the app runs. */
  codex_cli: boolean;
  /** The providers the three AI tiers use now. */
  ai_providers: AiProvider[];
  in_docker: boolean;
  database: string;
  data_dir: string;
  clips_dir: string;
  disk_free_gb: number;
  whisper_engine: string;
  encoder: string;
  tools: Record<string, boolean>;
  worker_online: boolean;
  worker_last_seen: string | null;
  queued: number;
  running: number;
}

export interface NewVideo {
  src: string;
  n?: number;
  min_len?: number;
  max_len?: number;
  brief?: string;
  campaign_id?: number | null;
  clip_format?: ClipFormat;
}

export type IdeaStatus = 'inbox' | 'new' | 'approved' | 'parked' | 'skipped';
export type IdeaForm = 'long' | 'short' | 'micro_short';
export type StoryShape = 'parallel' | 'explain' | 'scorecard' | 'investigation';
export type CaptionMode = 'default' | 'on' | 'off';

export interface IdeaVideo {
  id: string;
  title: string;
  channel: string;
  subscribers: number | null;
  views: number;
  published: string;
  duration_s: number;
}

export interface Idea {
  id: number;
  theme: string;
  theme_id: number | null;
  /** Generated from a proven theme: what it builds on. */
  inspired_by: { kind: 'video' | 'script' | 'idea'; id: number; title: string; evidence: string }[] | null;
  title: string;
  hook: string;
  /** What most viewers would guess before watching; the answer overturns it. */
  expected: string;
  answer: string;
  beats: { text: string; short: boolean }[];
  angle: string;
  format: 'single' | 'list';
  video: 'long' | 'short';
  form: IdeaForm;
  story_shape: StoryShape | null;
  parent_idea_id: number | null;
  lineage_kind: 'root' | 'concept_test' | 'promotion';
  search_query: string;
  origin: 'claude' | 'you';
  status: IdeaStatus;
  notes: string;
  research: { query: string; checked_at: string; videos: IdeaVideo[] } | null;
  judgement: {
    hook_score: number; hook_reason: string;
    /** Explainable 0-2 hook dimensions; absent on checks saved before v1. */
    context_score?: number | null; lean_score?: number | null; turn_score?: number | null;
    promise_score?: number | null; first_visual_score?: number | null;
    easy_score?: number | null; easy_reason?: string;
    drawable_score: number; drawable_reason: string;
    facts_score: number; facts_reason: string;
    ad_friendly: boolean; ad_reason: string;
    angle_taken: boolean; angle_reason: string;
    big_recent_same: string;
  } | null;
  validated_at: string | null;
  created_at: string;
  score: number | null;
  complete: boolean;
  breakdown: { label: string; points: number }[];
  flags: string[];
  demand: {
    reference_views: number; reference_rank: string;
    best_small_views: number | null; best_small_channel: string | null;
    old_share: string; videos: number;
  } | null;
  /** After a check: what length the results point to, and why. */
  length: { video: 'long' | 'short'; minutes: number | null; seconds: number | null; reason: string } | null;
  script_id: number | null;
}

export interface LineageIdea {
  id: number;
  title: string;
  form: IdeaForm;
  story_shape: StoryShape | null;
  parent_idea_id: number | null;
  lineage_kind: 'root' | 'concept_test' | 'promotion';
  status: IdeaStatus;
  publication_state: 'idea' | 'in_progress' | 'approved' | 'published';
  script_id: number | null;
  storyboard_ids: number[];
  metric_evidence: Record<string, unknown>[];
}

export interface IdeaLineage {
  idea: LineageIdea;
  root: LineageIdea;
  ancestors: LineageIdea[];
  descendants: LineageIdea[];
}

export type IdeaPatch = Partial<Pick<Idea, 'status' | 'notes' | 'title' | 'hook' | 'expected' | 'answer' | 'angle' | 'search_query'
  | 'video' | 'form' | 'story_shape'>>;

export type ClaimVerdict = 'supported' | 'contradicted' | 'unclear' | 'joke';

export interface ScriptSection {
  key: string | null;
  heading: string;
  text: string;
  short: boolean;
}

export interface ScriptClaim {
  section: string;
  claim: string;
  verdict: ClaimVerdict;
  sources: number[];
  note: string;
}

export type StoryTemplate = 'mechanism' | 'story_loop' | 'journey' | 'ranking';
export type StoryPurpose = 'question' | 'evidence' | 'answer' | 'twist' | 'payoff' | 'callback';

export interface StoryMapOpening {
  context: string;
  lean: string;
  turn: string;
  promise: string;
  first_visual: string;
  source_numbers: number[];
}

export interface StoryMapBeat {
  id: string;
  purpose: StoryPurpose;
  summary: string;
  source_numbers: number[];
  opens: string[];
  resolves: string[];
  visual_event: string;
  lane?: string | null;
  checkpoint?: string | null;
}

export interface StoryMapEnding {
  payoff: string;
  callback: string;
  source_numbers: number[];
}

export type StoryDataKind = 'metric' | 'comparison' | 'bars' | 'gauge' | 'cohort' | 'clock' | 'scorecard';

export interface StoryDataCard {
  id: string;
  kind: StoryDataKind;
  label: string;
  value: number | null;
  unit: string;
  items: { label: string; value: number }[];
  max_value: number | null;
  source_numbers: number[];
}

export interface StoryMapEdit {
  template: StoryTemplate;
  story_shape?: StoryShape | null;
  parallel?: {
    shared_start: string;
    shared_clock: string;
    lanes: { id: string; label: string; rule: string; promised_consequence: string }[];
  } | null;
  opening: StoryMapOpening;
  beats: StoryMapBeat[];
  ending: StoryMapEnding;
  data_cards?: StoryDataCard[] | null;
}

/** A source-backed plan, not an approval or quality score. */
export interface StoryMap extends StoryMapEdit {
  version: number;
  input_hash: string;
  made_at: string;
  by: 'ai' | 'you';
  current: boolean;
  stale_reasons: string[];
  problems: string[];
}

export interface ScriptSummary {
  id: number;
  idea_id: number | null;
  theme_id: number | null;
  title: string;
  format: 'single' | 'list';
  target_minutes: number;
  video: 'long' | 'short';
  form: IdeaForm;
  story_shape: StoryShape | null;
  target_seconds: number;
  status: 'draft' | 'approved';
  created_at: string;
  updated_at: string;
  words: number;
  minutes: number;
  claim_counts: Record<ClaimVerdict, number> | null;
  checked_current: boolean;
  job: Job | null;
}

export interface Script extends ScriptSummary {
  theme: string;
  notes: string;
  research: { notes: string; sources: { n: number; url: string; title: string }[]; searches: number } | null;
  story_map: StoryMap | null;
  sections: ScriptSection[];
  claims: ScriptClaim[] | null;
  can_undo_rewrite: boolean;
  /** How easy the narration is to follow for the audience: arithmetic, every deduction explained. Advice only. */
  plain: PlainCheck | null;
  /** How the narration holds attention: roles labelled by the AI, arithmetic on them. Advice only. */
  hold: HoldCheck | null;
}

export type ScriptPatch = Partial<Pick<Script, 'title' | 'status' | 'target_minutes' | 'notes' | 'story_shape'>>;

export interface VoiceChoice {
  id: string;
  label: string;
  preview: string | null;
}

export interface VoiceOptions {
  kokoro: VoiceChoice[];
  kokoro_downloaded: boolean;
  chatterbox: VoiceChoice[];
  /** Installed; otherwise the first take installs it (about 2.5 GB). */
  chatterbox_ready: boolean;
  elevenlabs: VoiceChoice[];
  elevenlabs_available: boolean;
  elevenlabs_error: string | null;
  defaults: {
    engine: VoiceEngine; kokoro_voice: string; chatterbox_voice: string; elevenlabs_voice: string;
    speed: number; pauses: Pauses;
  };
}

export type VoiceEngine = 'kokoro' | 'chatterbox' | 'elevenlabs';

export type Pauses = 'tight' | 'natural' | 'relaxed';

export interface VoiceTake {
  id: number;
  script_id: number;
  source: 'recording' | VoiceEngine;
  voice: string;
  speed: number | null;
  pauses: Pauses | null;
  duration: number | null;
  chosen: boolean;
  created_at: string;
  alignment: {
    coverage: number;
    sections: { key: string; heading: string; start: number; end: number }[];
    missing: { section: string; heading: string; text: string; heard: string }[];
    extra: { at: number; text: string }[];
    spoken_words: number;
    script_hash: string;
    /** Measured pace: script words a minute, pauses included. Missing on takes timed before it was measured. */
    wpm?: number | null;
  } | null;
  ready: boolean;
  current: boolean;
  job: Job | null;
  failure: string | null;
}

export interface StoryElement {
  kind: 'character' | 'prop' | 'text' | 'number' | 'arrow' | 'data';
  name: string;
  slot: string;
  to_slot: string | null;
  size: 'small' | 'medium' | 'large';
  pose: string;
  expression: string;
  facing: 'left' | 'right';
  talking: boolean;
  text: string;
  data_card?: string;
  enter: 'pop' | 'slide' | 'fade' | 'none';
  enter_word: number | null;
  motion: string;
  lane?: 'a' | 'b' | null;
}

export interface StoryComposition {
  kind: 'single' | 'split';
  lanes: ('a' | 'b')[];
  divider: 'none' | 'line' | 'vs';
}

/**
 * A scene of a flat storyboard, or a shot of an ink one. Ink shots have `lines`
 * (the beat language: "12 do narrator push fridge") and no elements or background.
 */
export interface StoryScene {
  start_word: number;
  note: string;
  background: string;
  camera: string;
  elements: StoryElement[];
  lines?: string[];
  page?: boolean;
  cut?: boolean;
  /** Ink shots: where it happens, drawn behind it in the cartoon look; empty = where the shot before was. */
  place?: string;
  composition?: StoryComposition;
}

/** A place for the cartoon look. Six are drawn by code; the others by Claude when a video needs them. */
export interface Place {
  name: string;
  description: string;
  built_in: boolean;
  wide: boolean;
  tall: boolean;
  origin: string;
  updated_at: string | null;
  used_in: number;
  /** Drawn by code now: one of the six you have not redrawn or deleted. */
  by_code: boolean;
}

export interface LivelinessScore {
  score: number;
  verdict: string;
  lines: { label: string; points: number; detail: string }[];
  pace: number;
  shots: number;
  changes: number;
}

/** One shot the AI director had something to say about. */
export interface DirectorNote {
  shot: number;
  start_word: number;
  problems: string[];
  before: string;
  after: string;
  /** changed: the rewrite was kept; refused: it failed a safety check (see why); noted: no rewrite proposed. */
  outcome: 'changed' | 'refused' | 'noted';
  why: string;
  dropped: string[];
}

export interface DirectorReport {
  at: string;
  shots: number;
  sheets: number;
  looked: number;
  changed: number;
  refused: number;
  lines: number;
  score_before: number | null;
  score_after: number | null;
  notes: DirectorNote[];
  skipped: string;
  undone_at?: string;
}

export interface StoryboardSummary {
  id: number;
  script_id: number;
  take_id: number | null;
  format: 'short' | 'long';
  form: IdeaForm;
  story_shape: StoryShape | null;
  caption_mode: CaptionMode;
  style: StoryStyle;
  look: Look;
  section_key: string | null;
  title: string;
  review: 'pending' | 'approved' | 'rejected';
  rendered_at: string | null;
  /** When the verdict last changed. */
  review_at?: string | null;
  /** Long video marked as uploaded or scheduled manually. */
  published_at: string | null;
  /** When the render was deleted for not being approved in time (the plan is kept). */
  expired_at: string | null;
  created_at: string;
  music_id: number | null;
  music_db: number;
  effects: boolean;
  starter_mode: AttentionMode;
  riser_mode: AttentionMode;
  duration: number;
  scene_count: number;
  has_video: boolean;
  /** Saved YouTube title and description exist and pass YouTube's limits. */
  publish_ready: boolean;
  job: Job | null;
  /** When the render is deleted if it is still not approved; null = never. */
  expires_at: string | null;
}

/** The last copy of an approved video in the output folder. */
export interface ExportInfo {
  folder: string;
  files: string[];
  at: string;
  size_bytes: number;
  /** The video was rendered again since. */
  stale: boolean;
}

export interface Storyboard extends StoryboardSummary {
  words: { w: string; s: number; e: number; section?: string }[];
  scenes: StoryScene[];
  missing_props: string[];
  music_credit: string;
  score: LivelinessScore | null;
  photos: ArchivePhoto[];
  photo_searches_missing: string[];
  credits: string;
  youtube: YoutubeText | null;
  /** The AI director's last pass over the stills, or null. */
  director: DirectorReport | null;
  /** The plan is still the one the director left, so Undo can put the old one back. */
  director_undoable: boolean;
  export: ExportInfo | null;
  /** Long videos: three thumbnails for YouTube's A/B test, or null until made. */
  thumbnails: Thumbnails | null;
  render_meta: RenderMeta | null;
  sound_starter_default: boolean;
  sound_riser_default: boolean;
  continuity: string[];
  data_cards: StoryDataCard[];
}

export type AttentionMode = 'default' | 'on' | 'off';

export interface VisualQa {
  sample_hz: number;
  samples: number;
  first_visual_event_s: number | null;
  empty_runs: { start_s: number; end_s: number; duration_s: number }[];
  warnings: string[];
  problem: string | null;
}

export interface RenderMeta {
  version: number;
  at: string;
  look: string;
  style: string;
  story_template: StoryTemplate | null;
  data_cards?: StoryDataCard[];
  hook_shape: string | null;
  starter: boolean;
  riser: boolean;
  effects: boolean;
  wpm: number | null;
  hold_hash: string | null;
  hold_score: number | null;
  first_twist_s: number | null;
  longest_gap_s: number | null;
  first_visual_event_s: number | null;
  visual_qa: VisualQa;
}

export type ThumbnailAngle = 'reaction' | 'contrast' | 'stakes' | 'mystery';
export type ThumbnailAccent = 'gold' | 'warm' | 'bad' | 'good';

/** One line of a thumbnail's check: what was measured and the points it cost. */
export interface ThumbnailCheckLine { text: string; points: number; }

/** One option of the A/B test: a headline over a picture drawn from the video. */
export interface ThumbnailOption {
  key: 'A' | 'B' | 'C';
  angle: ThumbnailAngle;
  /** At most four words; a word between *stars* is coloured. */
  headline: string;
  accent: ThumbnailAccent;
  side: 'left' | 'right';
  /** Which title option it goes with (1-based), and that title's text when it was chosen. */
  title: number;
  title_text?: string;
  why: string;
  by: 'ai' | 'rules' | 'you';
  /** drawn: one shot in the ink style; frame: a moment of the video. */
  mode: 'drawn' | 'frame';
  at: number | null;
  /** Drawn mode: the picture's beat lines, as you edit them. */
  beats: string;
  /** Lines of the AI's plan left out because the video does not have them. */
  dropped: string[];
  bytes: number;
  version: string;
  edited?: boolean;
  check: { score: number; lines: ThumbnailCheckLine[]; problems: string[] };
  /** The quick tier's look at it when small; redrawn once when it was unclear or misleading. */
  look?: { clear: boolean; honest: boolean; note: string; redrawn?: boolean };
}

export interface Thumbnails {
  options: ThumbnailOption[];
  made_at: string;
  by: 'ai' | 'rules';
  /** Creator choice, distinct from an observed A/B winner. */
  selected_key?: 'A' | 'B' | 'C' | null;
  winner_key?: 'A' | 'B' | 'C' | null;
}

export interface ThumbnailEdit {
  headline?: string;
  accent?: ThumbnailAccent;
  side?: 'left' | 'right';
  title?: number;
  mode?: 'drawn' | 'frame';
  at?: number;
  beats?: string;
}

/** The YouTube title, description and tags of an approved video. Copied into YouTube Studio by you. */
export interface YoutubeText {
  titles: string[];
  title: string;
  description: string;
  tags: string[];
  made_at: string | null;
  edited: boolean;
  problems: string[];
}

export type Audience = 'everyone' | 'curious' | 'enthusiasts';

export const AUDIENCES: { value: Audience; label: string; hint: string }[] = [
  { value: 'everyone', label: 'Everyone', hint: 'a curious 12-year-old: no background, everyday words, few names and numbers' },
  { value: 'curious', label: 'Curious adults', hint: 'a general education, no special knowledge of the subject' },
  { value: 'enthusiasts', label: 'Enthusiasts', hint: 'people who already know the basic terms of the subject' },
];

export interface PlainCheck {
  score: number;
  verdict: 'plain' | 'a bit dense' | 'hard to follow';
  audience: string;
  lines: { points: number; label: string; detail: string }[];
  stats: { grade: number; sentence_words: number; long_words: number; numbers_per_minute: number;
           names_per_minute: number; minutes: number };
  names: string[];
  hardest: string[];
}

export type HoldRole = 'hook' | 'context' | 'stakes' | 'question' | 'twist' | 'rehook' | 'fact' | 'payoff'
  | 'callback' | 'filler';

/** The hold check. `current` is false once the text changed since the labels were made: then only
 * `previous_score` is known and the check should run again. */
export interface HoldCheck {
  current: boolean;
  checked_at: string | null;
  score: number | null;
  previous_score?: number | null;
  verdict?: 'holds well' | 'some slack' | 'likely to lose people';
  topic?: string;
  lines?: { points: number; label: string; detail: string }[];
  stats?: { seconds: number; first_twist: number | null; longest_gap: number | null; topic_at: number | null;
            timing: 'estimated' | 'measured'; wpm: number };
  promise?: { verdict: 'paid' | 'partial' | 'missing'; answer_sentence: number | null; reason: string };
  loops?: { id: string; question: string; opener_sentence: number; resolver_sentence: number | null; main: boolean }[];
  rehooks?: { sentence: number; delivers_new_information: boolean; opens_loop: string | null; reason: string }[];
  sentences?: { n: number; section: string; text: string; role: HoldRole | null; at: number;
                title_question: boolean }[];
}

/** How an ink storyboard is drawn. Changing it needs a render, not a new plan. */
export type InkLook = 'ink' | 'paper' | 'blueprint' | 'cartoon' | 'clean_doodle' | 'rough_paint';
/** How a film storyboard (the documentary wall) is drawn. */
export type FilmLook = 'wall' | 'studio';
export type Look = InkLook | FilmLook;
/** ink: beats on one page; film: a documentary on an evidence wall; flat: the first style, slides. */
export type StoryStyle = 'ink' | 'flat' | 'film';

export const INK_LOOKS: { value: InkLook; label: string; hint: string }[] = [
  { value: 'ink', label: 'Ink sketch', hint: 'brush lines drawn on cream paper' },
  { value: 'paper', label: 'Paper cut-out', hint: 'colour pieces on kraft card, popping on with a shadow' },
  { value: 'blueprint', label: 'Blueprint', hint: 'white lines on a blue grid' },
  { value: 'cartoon', label: 'Cartoon', hint: 'colour characters in drawn places, one per setting' },
  { value: 'clean_doodle', label: 'Clean doodle', hint: 'thin steady lines and a small flat palette on near-white' },
  { value: 'rough_paint', label: 'Rough paint', hint: 'heavy uneven outlines, hand jitter and sparse colour' },
];

export const FILM_LOOKS: { value: FilmLook; label: string; hint: string }[] = [
  { value: 'wall', label: 'Evidence wall', hint: 'archive photos, cards and stamps pinned on dark cork, red string between them' },
  { value: 'studio', label: 'Studio board', hint: 'the same documentary on a light board: prints laid down, dark type' },
];

export const ALL_LOOKS = [...INK_LOOKS, ...FILM_LOOKS];

export interface PublishLine {
  points?: number;
  text: string;
  needs?: boolean | null;
}

/** Advice before uploading. It never approves anything: every decision stays yours. */
export interface PublishCheck {
  checklist: { done: boolean | null; text: string }[];
  authorship: {
    lines: { part: string; text: string; yours: boolean }[];
    yours: number;
    parts: number;
    advice: string[];
    events: { at: string; kind: string; by: 'you' | 'ai' | 'app'; summary: string; amount: number }[];
  };
  similarity: {
    score: number;
    verdict: 'first' | 'different' | 'similar' | 'close';
    lines: PublishLine[];
    note: string;
    compared: number;
    nearest: { id: number; title: string; script_id: number } | null;
    variety?: string;
  };
  kids: { score: number; verdict: 'kids' | 'unsure' | 'general'; lines: PublishLine[]; advice: string; note: string };
  disclosure: { verdict: 'none' | 'check' | 'label'; advice: string; lines: PublishLine[] };
}

/** A picture from Wikimedia Commons used by an ink storyboard, with its credit. */
export interface ArchivePhoto {
  id: number;
  query: string;
  title: string;
  creator: string;
  license: string;
  landing_url: string;
  credit: string;
  rank: number;
}

export interface Asset {
  name: string;
  description: string;
  drawn: boolean;
  origin: string;
  created_at: string;
  updated_at: string;
  used_in: number;
}

export interface MusicTrack {
  id: number;
  name: string;
  duration: number;
  creator: string;
  license: string;
  source_url: string;
  attribution: string;
  genres: string[];
  features: { bpm: number | null; words: string[] } | null;
  created_at: string;
}

export interface SuggestedTrack {
  ref: string;
  fit: number;
  reason: string;
  source: 'library' | 'online';
  track_id: number | null;
  openverse_id: string | null;
  title: string;
  creator: string;
  license: string;
  duration: number;
  words: string[];
  landing_url: string;
}

export interface MusicSuggestion {
  choice: string | null;
  reason: string;
  level_db: number;
  searches: string[];
  ranking: SuggestedTrack[];
}

export interface AiModel {
  id: string;
  label: string;
  price: [number, number] | null;
  live: boolean;
}

export type AiModels = Record<AiProvider, { models: AiModel[]; error: string | null }>;

export interface MusicFound {
  id: string;
  title: string;
  creator: string;
  license: string;
  license_version: string;
  license_url: string;
  source: string;
  landing_url: string;
  audio_url: string;
  duration: number;
  genres: string[];
  attribution: string;
}

export type SoundKind = 'pop' | 'whoosh' | 'impact' | 'scribble' | 'step' | 'riser' | 'character' | 'arrow' | 'bounce' | 'glide';

/** A creator-reviewed, locally cached CC0/CC BY production sound. */
export interface StudioSound {
  id: number;
  kind: SoundKind;
  name: string;
  cached: boolean;
  duration: number;
  gain: number;
  enabled: boolean;
  provider: 'openverse' | 'wikimedia';
  provider_id: string;
  upstream_id: string;
  creator: string;
  creator_url: string;
  landing_url: string;
  license: 'cc0' | 'by';
  license_version: string;
  license_url: string;
  attribution: string;
  checksum: string;
  acquired_at: string | null;
  approved_at: string | null;
  rejected_at: string | null;
  rejection_note: string;
}

export interface SoundCandidate {
  provider: 'openverse' | 'wikimedia';
  provider_id: string;
  upstream_id: string;
  upstream_source: string;
  title: string;
  creator: string;
  creator_url: string;
  landing_url: string;
  media_url: string;
  license: 'cc0' | 'by';
  license_version: string;
  license_url: string;
  attribution: string;
  duration: number;
  filetype: string;
}

export interface SoundSearch {
  count: number;
  page: number;
  pages: number;
  results: SoundCandidate[];
  rate_limit: Record<string, unknown>;
}

export interface StoryboardSound {
  id: number;
  storyboard_id: number;
  sound_id: number;
  rule: string;
  at: number;
  gain: number;
  created_at: string;
  sound: StudioSound;
}

/** 'claude_cli' is Claude on the owner's subscription (Claude Code CLI); 'codex_cli' OpenAI on their ChatGPT plan (Codex CLI). */
export type AiProvider = 'anthropic' | 'openai' | 'claude_cli' | 'codex_cli';

export interface UsageLine {
  key: string;
  label: string;
  tier: string;
  calls: number;
  failed: number;
  input_tokens: number;
  output_tokens: number;
  searches: number;
  cost_usd: number;
  unpriced_calls: number;
}

export interface UsageReport {
  days: number;
  calls: number;
  failed: number;
  cost_usd: number;
  by_task: UsageLine[];
  by_model: UsageLine[];
  tiers: Record<string, { provider: AiProvider; model: string; tasks: string[] }>;
}

export interface StoryboardListItem extends StoryboardSummary {
  script_title: string;
}

export interface Chapters {
  text: string;
  valid: boolean;
  problems: string[];
}

export interface CostLine {
  step: string;
  task: string;
  label: string;
  calls: number;
  cost: number;
}

export interface CostReport {
  lines: CostLine[];
  total: number;
  note: string;
}

/** Which video (or script, or clipped video) a cost report is about. */
export interface CostOf {
  script?: number;
  storyboard?: number;
  video?: string;
}


// --- themes (v2) -------------------------------------------------------------------

export type ThemeColor = 'amber' | 'coral' | 'violet' | 'teal' | 'sky' | 'lime' | 'rose' | 'indigo';
export const THEME_COLORS: ThemeColor[] = ['amber', 'coral', 'violet', 'teal', 'sky', 'lime', 'rose', 'indigo'];
export type ThemeStage = 'ideas' | 'script' | 'voice' | 'animation' | 'ready' | 'published';
export const THEME_STAGES: { value: ThemeStage; label: string; hint: string }[] = [
  { value: 'ideas', label: 'Ideas', hint: 'Sort, check and approve ideas' },
  { value: 'script', label: 'Script', hint: 'Research, write and approve the narration' },
  { value: 'voice', label: 'Voice', hint: 'Record or generate the voice' },
  { value: 'animation', label: 'Animation', hint: 'Plan, render and review the video' },
  { value: 'ready', label: 'Ready', hint: 'Approved: schedule or publish' },
  { value: 'published', label: 'Published', hint: 'Out, with its numbers' },
];

export interface Theme {
  id: number;
  name: string;
  description: string;
  color: ThemeColor;
  status: 'active' | 'paused' | 'archived';
  channels: string[];
  notes: string;
  insights: ThemeInsights | null;
  banner: { made_at: string; by: 'ai' | 'you'; note: string } | null;
  banner_job: Job | null;
  created_at: string;
  updated_at: string;
  counts: Record<ThemeStage, number>;
  inbox: number;
  performance: {
    published: number; with_views: number; total_views: number; median_views: number | null;
    best_title: string | null; best_views: number | null;
  };
  next: { stage: ThemeStage; text: string };
}

export interface ThemeInsights {
  summary: string;
  works: string[];
  avoid: string[];
  try_next: string[];
  confidence: 'low' | 'medium' | 'high';
  made_at: string;
  based_on: number;
  median_views: number | null;
}

export interface ThemeBoardItem {
  kind: 'script';
  id: number;
  title: string;
  stage: ThemeStage;
  video: 'long' | 'short';
  status: 'draft' | 'approved';
  updated_at: string;
  idea_id: number | null;
  words: number;
  voiced: boolean;
  take: { source: string; voice: string; duration: number | null } | null;
  storyboards: { id: number; title: string; format: 'short' | 'long'; review: string; has_video: boolean;
    published: boolean; post: string | null }[];
  job: Job | null;
}

export interface ProvenExample {
  key: string;
  n: number;
  kind: 'video' | 'script' | 'idea';
  id: number;
  title: string;
  evidence: string;
}

export interface ThemeBoard {
  theme: Theme;
  items: ThemeBoardItem[];
  ideas: Idea[];
  proven: ProvenExample[];
}

export interface ThemeContents {
  ideas: number;
  scripts: number;
  takes: number;
  videos: number;
  blockers: string[];
}

export interface ThemeVideo {
  storyboard_id: number;
  script_id: number;
  idea_id: number | null;
  title: string;
  hook: string;
  format: 'short' | 'long';
  form: 'long' | 'short' | 'micro_short' | null;
  story_shape: 'parallel' | 'explain' | 'scorecard' | 'investigation' | null;
  thumbnail_angle: 'reaction' | 'contrast' | 'mystery' | null;
  thumbnail_evidence: 'creator_selected' | 'manual_winner' | null;
  parent_idea_id: number | null;
  root_idea_id: number | null;
  lineage_kind: 'root' | 'concept_test' | 'promotion' | null;
  style: string;
  look: Look;
  seconds: number | null;
  published_at: string | null;
  published_url: string | null;
  views: number | null;
  likes: number | null;
  comments: number | null;
  shares: number | null;
  saves: number | null;
  engagement: number | null;
  vs_median: number | null;
  cohort_median_views: number | null;
  seven_day_velocity: SevenDayVelocity;
  /** How it was built (hold check of its script, the take's pace); null when not measured. */
  hold_score: number | null;
  first_twist: number | null;
  longest_gap: number | null;
  opening: string | null;
  wpm: number | null;
  story_template: StoryTemplate | null;
  hook_shape: string | null;
  starter: boolean | null;
  riser: boolean | null;
  first_visual_event: number | null;
  /** YouTube Analytics once connected: average % viewed and % retained at measured landmarks. */
  avg_viewed: number | null;
  kept_at_5s: number | null;
  kept_at_twist: number | null;
  kept_at_payoff: number | null;
  retention: VideoRetention | null;
  lines: string[];
  networks: { service: string; name: string; link: string | null; views: number | null; status: string }[];
}

export interface SevenDayVelocitySource {
  source_key: string;
  service: string;
  name: string;
  status: 'available' | 'unavailable';
  reason?: string;
  baseline_at?: string;
  baseline_views?: number;
  endpoint_at?: string;
  endpoint_views?: number;
  elapsed_hours?: number;
  elapsed_days?: number;
  views_gained?: number;
  views_per_day?: number;
  corrected?: boolean;
}

export interface SevenDayVelocity {
  status: 'available' | 'unavailable';
  reason: string | null;
  coverage: string;
  sources: SevenDayVelocitySource[];
  cross_platform: { label: string; source_count: number; views_gained: number; views_per_day: number } | null;
}

export interface VideoRetention {
  youtube_id: string;
  source: 'linked' | 'buffer';
  views: number | null;
  minutes_watched: number | null;
  average_view_s: number | null;
  average_view_pct: number | null;
  duration_s: number | null;
  /** [fraction of the video, audience watch ratio] from YouTube */
  curve: [number, number][];
  /** Editorial landmarks frozen by the successful render, then measured against YouTube's curve. */
  checkpoints: RetentionCheckpoint[];
  twist_at: number | null;
  payoff_at: number | null;
  lines: string[];
  /** why a column is blank, by column name */
  why: Record<string, string>;
  note: string | null;
  fetched_at: string | null;
}

export interface RetentionCheckpoint {
  kind: string;
  label: string;
  lane: string | null;
  time_s: number | null;
  retention_pct: number | null;
  position: number | null;
  reason: string | null;
  evidence: string | null;
  sentence: number | null;
  word_number: number | null;
  how: string | null;
}

export interface YoutubeStatus {
  configured: boolean;
  connected: boolean;
  needs_reconnect: boolean;
  channel_id: string | null;
  channel_title: string | null;
  connected_at: string | null;
  problem: string | null;
  problem_at: string | null;
  redirect_uri: string;
  testing_note: string;
  last_fetch: string | null;
  videos_with_numbers: number;
  revoked?: boolean | null;
}

export interface ThemeLearning {
  videos: ThemeVideo[];
  median_views: number | null;
  cohort_medians: { form: string; median_views: number; with_views: number }[];
  with_views: number;
  total_views: number;
  insights: ThemeInsights | null;
  can_learn: boolean;
  proven: { n: number; kind: string; id: number; title: string; evidence: string }[];
  stats_sources: { buffer: boolean; youtube: boolean; analytics: boolean };
  experiments: {
    cohorts: Record<string, { value: string; videos: number; with_views: number; median_views: number | null;
      views_range: [number, number] | null; with_mature_velocity: number; median_views_per_day: number | null;
      velocity_range: [number, number] | null }[]>;
    comparisons: string[];
    lineages: { root_idea_id: number; videos: { storyboard_id: number; idea_id: number | null;
      parent_idea_id: number | null; lineage_kind: string | null; form: string | null; title: string;
      views: number | null; seven_day_velocity: SevenDayVelocity }[] }[];
    note: string;
  };
  unmatched: { storyboard_id: number; title: string; why: string }[];
}

export interface ThemePatch {
  name?: string;
  description?: string;
  color?: ThemeColor;
  status?: Theme['status'];
  channels?: string[];
  notes?: string;
}

export interface PromptInfo {
  name: string;
  title: string;
  step: string;
  task: string;
  tier: string;
  used_in: string;
  about: string;
  variables: string[];
  variable_help: Record<string, string>;
  includes: string[];
  overridden: boolean;
  text: string;
  default: string;
  problems: string[];
}
