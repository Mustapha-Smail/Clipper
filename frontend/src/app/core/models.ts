export type JobState = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
export type ClipStatus = 'pending' | 'approved' | 'rejected';
export type SubtitleMode = 'cover' | 'crop' | 'keep' | 'ignore';

export interface Job {
  id: number;
  kind: string;
  label: string;
  video_id: string | null;
  script_id: number | null;
  take_id: number | null;
  storyboard_id: number | null;
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
  compliance: Compliance | null;
  rendered_at: string;
  file_exists: boolean;
}

export interface ClipListItem extends Clip {
  video_title: string;
  title: string;
  hook: string;
  caption: string;
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
  voice_engine: 'kokoro' | 'elevenlabs';
  kokoro_voice: string;
  elevenlabs_voice: string;
  elevenlabs_model: string;
  voice_speed: number;
  voice_pauses: Pauses;
  audience_tier1_share: number | null;
  platforms: string[];
  output_dir: string;
  delete_source_after_render: boolean;
  clips_per_video: number;
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
  max_height: number;
  cookies_file: string;
  encoder: string;
  bitrate_mbps: number;
  captions_enabled: boolean;
  caption_font: string;
  highlight_color: string;
  hook_seconds: number;
  subtitle_mode: SubtitleMode;
}

export interface SettingsBundle {
  settings: AppSettings;
  defaults: AppSettings;
  fonts: { value: string; label: string }[];
  default_clips_dir: string;
}

export interface SystemStatus {
  version: string;
  api_key: boolean;
  youtube_key: boolean;
  elevenlabs_key: boolean;
  openai_key: boolean;
  openai_base_url: string;
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
}

export type IdeaStatus = 'inbox' | 'new' | 'approved' | 'parked' | 'skipped';

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
  title: string;
  hook: string;
  answer: string;
  beats: { text: string; short: boolean }[];
  angle: string;
  format: 'single' | 'list';
  video: 'long' | 'short';
  search_query: string;
  origin: 'claude' | 'you';
  status: IdeaStatus;
  notes: string;
  research: { query: string; checked_at: string; videos: IdeaVideo[] } | null;
  judgement: {
    hook_score: number; hook_reason: string;
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

export type IdeaPatch = Partial<Pick<Idea, 'status' | 'notes' | 'title' | 'hook' | 'answer' | 'angle' | 'search_query'
  | 'video'>>;

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

export interface ScriptSummary {
  id: number;
  idea_id: number | null;
  title: string;
  format: 'single' | 'list';
  target_minutes: number;
  video: 'long' | 'short';
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
  sections: ScriptSection[];
  claims: ScriptClaim[] | null;
  can_undo_rewrite: boolean;
}

export type ScriptPatch = Partial<Pick<Script, 'title' | 'status' | 'target_minutes' | 'notes'>>;

export interface VoiceChoice {
  id: string;
  label: string;
  preview: string | null;
}

export interface VoiceOptions {
  kokoro: VoiceChoice[];
  kokoro_downloaded: boolean;
  elevenlabs: VoiceChoice[];
  elevenlabs_available: boolean;
  elevenlabs_error: string | null;
  defaults: { engine: 'kokoro' | 'elevenlabs'; kokoro_voice: string; elevenlabs_voice: string; speed: number; pauses: Pauses };
}

export type Pauses = 'tight' | 'natural' | 'relaxed';

export interface VoiceTake {
  id: number;
  script_id: number;
  source: 'recording' | 'kokoro' | 'elevenlabs';
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
  } | null;
  ready: boolean;
  current: boolean;
  job: Job | null;
  failure: string | null;
}

export interface StoryElement {
  kind: 'character' | 'prop' | 'text' | 'number' | 'arrow';
  name: string;
  slot: string;
  to_slot: string | null;
  size: 'small' | 'medium' | 'large';
  pose: string;
  expression: string;
  facing: 'left' | 'right';
  talking: boolean;
  text: string;
  enter: 'pop' | 'slide' | 'fade' | 'none';
  enter_word: number | null;
  motion: string;
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
}

export interface LivelinessScore {
  score: number;
  verdict: string;
  lines: { label: string; points: number; detail: string }[];
  pace: number;
  shots: number;
  changes: number;
}

export interface StoryboardSummary {
  id: number;
  script_id: number;
  take_id: number | null;
  format: 'short' | 'long';
  style: 'ink' | 'flat';
  section_key: string | null;
  title: string;
  review: 'pending' | 'approved' | 'rejected';
  rendered_at: string | null;
  created_at: string;
  music_id: number | null;
  music_db: number;
  effects: boolean;
  duration: number;
  scene_count: number;
  has_video: boolean;
  job: Job | null;
}

export interface Storyboard extends StoryboardSummary {
  words: { w: string; s: number; e: number; section?: string }[];
  scenes: StoryScene[];
  missing_props: string[];
  music_credit: string;
  score: LivelinessScore | null;
}

export interface Asset {
  name: string;
  description: string;
  drawn: boolean;
  origin: string;
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

export type AiModels = Record<'anthropic' | 'openai', { models: AiModel[]; error: string | null }>;

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

export type AiProvider = 'anthropic' | 'openai';

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
