# Clipping pipeline

Semi-automatic tool that turns long videos into captioned clips (vertical 9:16
or horizontal 16:9) for paid clipping campaigns (English market, operated from France). PLAN.md has the
roadmap and the owner's setup checklist; README.md has run and ops instructions.

## Layout

- `backend/` Python package `clipper`: API (`api/`), database (`db/`), job queue
  (`jobs.py`), worker (`worker.py`), pipeline stages (`pipeline.py` plus one
  module per stage), CLI (`cli.py`). Tests in `backend/tests/`.
  `studio/` is the animated-channel side (ideas now; script, voice, storyboard
  and renderer later), one module per step, same rules as the pipeline.
- `frontend/` Angular 22 app. Standalone components, signals, zoneless, plain
  SCSS (design tokens in `src/styles.scss`: surfaces, ink for actions, gold for
  attention, `.hue-*` per theme, motion tokens). No UI library. Navigation is
  theme-first: Themes home, a workspace per theme (pipeline stepper, Performance,
  Settings), then the Workshop pages; ⌘K opens the quick switcher (`shared/palette.ts`).
  Appearance is System (default), Light or Dark (`core/appearance.ts`, kept in
  localStorage, applied before paint by the script in `index.html`): dark tokens
  live in the `dark-tokens` mixin, used for `prefers-color-scheme: dark` unless
  `data-theme="light"`, and for `data-theme="dark"`. New colours go in both sets.
- `docker-compose.yml`: db (Postgres), migrate, api, worker, web (nginx).

## Commands

```bash
make run          # native: build UI, run API + worker on :8000 (SQLite)
make up           # Docker stack on :8000 (Postgres)
make test lint    # backend tests and ruff
make dev-api      # then `make dev-ui` for the Angular dev server on :4200
```

Frontend commands need Node 24: `export PATH="$HOME/.nvm/versions/node/v24.22.3/bin:$PATH"`.

## Rules of the codebase

- New pipeline behaviour goes in `pipeline.py` or a stage module, never in the
  API routes or the CLI. Stages report through the `Reporter` they are given
  (`rep.info`, `rep.warn`, `rep.progress`); that is what fills the job log.
- User-tunable values live in `settings.py` (stored in the database, edited on
  the Settings page). `config.py` is environment and constants only.
- Schema changes need an Alembic migration (`uv run clipper db revision -m ...`).
  It must work on both SQLite and Postgres.
- Media stays on disk; the database holds metadata. A clip row stores its
  absolute path, so clips survive a change of output folder.
- Prompts (`prompts.py`, `clipper/prompts/*.md`): every system prompt is a
  Markdown file with a header (title, step, task, used_in, about, variables);
  a module holds `prompts.Prompt("name")`, read at call time. Overrides from the
  Prompts page live in `<data>/prompts/` and win until reset. In a prompt,
  `{name}` is a value the code passes (only those are replaced, so other braces
  stay literal) and `{prompt:other}` includes another prompt (shared rules live
  once: `script_spoken`, `music_tone`). New AI work gets a prompt file, never an
  inline system prompt; keep the header's `variables` in step with the code
  (a test checks every default). Each variable has a `var.<name>:` line in the
  header that explains it on the Prompts page. An edit that drops a variable,
  adds an unknown `{name}` or includes a missing prompt is refused (`problems`,
  checked again in the page as you type), so a prompt can never break.
- AI calls go through `llm.py` only: `llm.ask(task, ...)` / `llm.search(task, ...)`.
  A task maps to a tier (writing, quick, research) whose provider and model come
  from Settings; providers live in `claude.py` (Anthropic) and
  `openai_compat.py` (OpenAI Responses API, or Chat Completions for
  compatible servers), and `claude_cli.py` (the owner's Claude subscription:
  one `claude -p` per call with `--safe-mode`, no tools except web search for
  research, `ANTHROPIC_API_KEY` hidden from it so it never bills the API, cost
  logged as zero, a plan limit raised as `AiUnavailable`; a source mark is kept
  only when a search of that run returned the URL), and `codex_cli.py` (the
  same on a ChatGPT plan with `codex exec`: user config ignored, read-only
  sandbox, every tool-bearing feature disabled, `code_mode_host` kept only for
  research because it runs the web search; models from `codex debug models`;
  a CLI older than `MINIMUM` or signed in with an API key is refused). Every
  call is logged in `ai_usage`. New AI work needs a
  task in `llm.TASKS`, never a direct SDK call.
- `moments.py`: the model answers with transcript line numbers, never timestamps.
- AI outages: providers raise `ai_errors.AiUnavailable` (no key, credit, rate
  limit, 5xx, unreachable); `ai_usage.outage` records it and `llm.health()` feeds
  the status banner. Clipping falls back to `rules.py` (moments and brief checks,
  explainable points, marked "Picked by rules") when `ai_fallback` is "rules";
  pasted listings are stored with `read_by` "waiting". Never fall back silently:
  report it through the Reporter.
- Clip formats: `config.FORMATS` (vertical 1080x1920, horizontal 1920x1080).
  `Settings.clip_format` is the default, `videos.clip_format` the per-video
  choice (`pipeline.effective_clip_format`), and each clip row keeps the
  `format` it was rendered in. `reframe.compose` and `analyze` take the size;
  the text sizes and positions of each format are in `captions.LAYOUTS`. A
  source that already has the clip's shape fills the frame and skips the face
  scan. A new format needs an entry in both tables and in the UI's labels.
- Posting (`posting.py`, `buffer.py`, `hosting.py`): approved videos are
  scheduled on Instagram, TikTok and YouTube through Buffer's GraphQL API;
  nothing posts to a network directly. Buffer is for vertical Shorts only;
  Studio Shorts also need valid saved YouTube metadata. Long Studio videos and
  horizontal clips are published manually. `posting.check` is the gate
  (approved, Short, metadata ready, file on disk), run when asked and again by
  the `post` job before it uploads; the review routes refuse to un-approve while
  `posting.has_live`.
  Undo (`posting.cancel` one post, `posting.undo` every waiting post of a
  video, route `POST /posts/undo`) deletes the post on Buffer before it goes
  out; a post undone while the job is creating it is deleted there by
  `schedule`. A sent post cannot be taken back through Buffer: say so. The
  posts and their Undo stay with the video once it moves to Published.
  Buffer has no upload, so the job copies the file to a Cloudflare R2 bucket
  (`hosting.py`, S3 calls signed by hand, checked against AWS's published
  signatures in the tests) under an unguessable key, and `release_media`
  deletes it once no post needs it. One `posts` row per channel. Buffer counts
  requests (1,000 per channel per 30 days on the Free plan): never poll;
  `posting.sync` asks only for due posts, plus one full pass a day, batched in
  one request per account (`buffer.posts`). Clipping and Studio may use separate
  Buffer accounts (`BUFFER_CLIPPING_API_KEY`, `BUFFER_STUDIO_API_KEY`; the old
  `BUFFER_API_KEY` is a fallback). Keys come from `.env` only, never from Settings
  or the database. A new network needs an entry in
  `posting.SERVICES`, `posting.TEXT_MAX` and `buffer.METADATA`.
- `watch.py` (`clipper watch URL`): prepares a video for an agent to watch
  (skill `watch-video`): reuses `ingest` and `transcribe` (language `auto` lets
  Whisper detect it), then writes the post details, timed transcript, frames kept
  at each visual change and labelled contact sheets to `<data>/watch/<id>/`.
  No database rows, no AI call: the agent that asked does the looking.
- `captions.py`: text is drawn with Pillow because Homebrew's ffmpeg has no
  subtitle or drawtext filters. Do not switch to ffmpeg `ass`/`subtitles`.
- `subtitles.py`: burned-in captions are detected as a band of changing text,
  not by matching the transcript (many sources use summary cards).
- `campaigns.py`: listings arrive as page text; Claude extracts the terms, and
  `assess()` ranks them with plain arithmetic whose lines are shown to the user.
  Keep the score explainable: every adjustment must appear in the breakdown.
  Listings go through `campaigns.ingest`, whoever read them.
- `scout.py`: the `scout` job (button on Campaigns, or `scout_every_hours` in
  Settings, checked by the idle worker) reads public Content Rewards pages over
  plain HTTP: allow-listed hosts only (`scout.HOSTS`), robots.txt honoured
  (longest match, by hand), no login, never joins or submits, at most
  `scout_max_pages` listings one at a time with a pause. Claude picks theme fits
  from the cards (`llm` task `campaign_pick`); refreshes and best-paid picks are
  code. AI failures never fail the job: a listing the AI cannot read is read
  from the page layout (`scout.page_terms`: fixed blocks only, `read_by="page"`,
  theme fit None, scored as 4/10 and flagged); two failures in a row stop AI
  calls for the run (`scout.AiState`). It is announced in the log and the job's
  message ("Done, AI skipped: ..."), and the next run with AI re-reads page-read
  campaigns. The `campaign-scout` skill remains for other marketplaces: it posts
  `{url, text}` to `/api/v1/campaigns`.
- `studio/ideas.py`: same rule as campaigns. The score is arithmetic on the
  stored YouTube results and Claude's judgement, every line in the breakdown.
  YouTube data comes only from the official Data API (`studio/youtube.py`,
  `YOUTUBE_API_KEY`), never from scraping. Editing an idea's text clears its
  check, because the old score no longer applies.
- `studio/script.py`: research, story map, write and factcheck are worker job
  stages (`jobs.STAGES`), tied to a script by `params["script_id"]`.
  `studio/story_map.py` is the source-backed contract between research and prose:
  the model proposes opening, ordered beats, at most two named open loops and an
  ending; code filters citations, validates IDs and loop resolution, and hashes
  every upstream input. A changed title, format, target, audience, idea or cited
  research makes it stale with an exact reason. Writers and rewrites receive only
  a current valid map when one exists; redraft rebuilds a missing legacy map and
  requires confirmation before replacing a stale or edited one. The writer may
  use only the research notes for facts; every web fact keeps its numbered source.
  Web search goes through `llm.search`; structured answers through `llm.ask`.
  The two cannot be one call. A fact check stores the hash of the text it checked.
- `studio/voice.py`: takes are job stages (speak or prepare, then align) keyed
  by `params["take_id"]`. Every take ends as `voice.wav` (48 kHz mono,
  loudnorm) under `data/studio/script-N/take-M/`, with `timeline.json` (script
  words with times). Alignment is difflib over normalised words; differences
  shorter than 3 words are ignored as transcription noise. Kokoro is loaded per
  job and released; its model downloads to `data/cache/kokoro` on first use.
- Chatterbox (`studio/chatterbox.py`): Turbo in its own uv environment under
  `data/cache/chatterbox/<platform>` (its pins clash with the app's; the data
  folder is shared by the Mac app and Docker), run with `python -I` in a child
  process (`chatterbox_runner.py`, which imports nothing from the app). CPU
  only: the Mac GPU was slower. Claude directs each sentence (`llm` task
  `delivery`, quick tier: mood tags and rare sounds, spacing enforced in code);
  if the AI fails, plain narration, said in the log. Voices: its own, or a
  Kokoro clip as the timbre reference. Our pauses and room tone, as for Kokoro.
- ElevenLabs (`voice.speak_elevenlabs`): one request per section, `Settings.elevenlabs_model`.
  Eleven v4 (`voice.V4_MODELS`) takes only stability and similarity: speed is applied by us
  (atempo), sections are linked by `previous_request_ids` (the 3 closest), not by text. With
  `Settings.elevenlabs_direction`, the Chatterbox direction (task `delivery`) is written in as v4
  audio tags (`voice.V4_TAGS`); older models are never tagged. The voice list is fetched with a
  short timeout and cached (`voice.elevenlabs_voices`), so a stalled API never blocks the panel.
- `studio/storyboard.py`, `studio/animate.py`, `studio/art.py`: Claude plans
  scenes by word number (never timestamps) in a fixed vocabulary that the
  renderer knows; `storyboard.clean()` makes any plan or user edit safe. Props
  are SVG in `studio_assets`, checked by `art.check_prop_svg` (no text, scripts,
  styles, links or images) and rasterised with resvg. They are illustrations
  (`storyboard.DRAW`: shading as unoutlined shapes in `art.SHADES` tones, two
  outline weights) drawn on the writing tier at `Settings.prop_drawing`
  effort, looked at once by the quick tier (`look_at_prop`, task `prop_check`)
  and redrawn once on a weak verdict. `art.STYLE_MARK` marks the current
  style: older AI props are hidden from the planner (`storyboard.library`) and
  redrawn when used; `origin == "you"` props are never redrawn. The renderer's layout
  pass (`animate.separate`) keeps elements inside the stage and apart; keep it
  when adding element kinds. Captions reuse `captions.py`.
- AI director (`studio/director.py`, `llm` task `direct`, writing tier): the
  renderer draws three stills per shot onto contact sheets (`animate.still`), the
  model looks at them with the shots' narration and beat lines, and answers with
  notes and whole-shot rewrites in the beat language (ink and film only). A
  rewrite is kept only when `clean` leaves every other shot unchanged, it adds
  no photo, and the ink liveliness score drops by at most `SCORE_SLACK`;
  every verdict and refusal is in the log and in `storyboards.director`, which
  keeps the plan from before for one undo (refused once the plan was edited).
  Runs from a button, or after every plan when `Settings.director_pass` is on.
  Stills show no motion: it never judges pacing. The prompt asks only about
  visible continuity; deterministic advisory continuity findings come from
  `studio/continuity.py` and are never stored as a score.
- Storyboards have a `style`: `ink` (default), `flat` or `film`. Ink storyboards are
  shots of beat lines (`studio/beats.py`: "12 do narrator push fridge"), never
  JSON from the model; `beats.clean()` makes any plan or edit safe and counts
  what it drops. `studio/ink.py` draws (brush strokes, the figure rig with
  arm reaching, library props turned into ink lines and a wash, handwriting
  with Patrick Hand, OFL, bundled); `studio/stage.py` lays shots on one page,
  walks actors, frames the camera and adds the boil. Drawn things change at 12
  fps, the camera at 24. Keep the liveliness score (`beats.score`) arithmetic,
  every deduction in its lines. New actions go in the beat language and the
  stage, and in the planner prompt and the page's cheat sheet.
- Looks (`studio/looks.py`): `storyboards.look` changes only drawing, never the
  plan. Explicit render profiles centralise background, line weight, jitter,
  fill palette, shadow and entrance. Existing `ink`, `paper`, `blueprint` and
  `cartoon` remain unchanged; opt-in `clean_doodle` and `rough_paint` are
  code-native flat-palette looks. Photos, flags and maps are never recoloured.
- Cartoon look (`studio/cartoon.py`, `studio/places.py`): colour characters on the
  ink rig (`ink.joints`), never a separate skeleton. Shots carry a `place`
  (`in NAME` on the shot line), inherited by later shots (`places.resolved`).
  Six places are code (`cartoon.PLACES`, laid out around the frame centre); the
  rest are `studio_places` rows drawn by Claude (`llm` task `places`) in the
  fixed views `WIDE_VIEW`/`TALL_VIEW`, floor at `FLOOR`, only when a cartoon
  render needs them (`places.prepare` in the draw step).
- Film style (`studio/film.py`, `studio/wall.py`): a documentary on an evidence
  wall, after the cinematic-film skill (MIT). Beat lines like the ink style
  (`film.clean` makes any plan or edit safe and lists what it drops), never code
  or coordinates from the model: code lays out clusters, plans the camera (one
  hero per line) and the spotlight. Looks `wall` and `studio` change drawing
  only. `doc` pages are recreations marked on the page and quote only real
  words. The video runs `FilmComposer.tail` seconds past the voice (the closing
  pull-back); `animate.render` pads the audio for it.
- `studio/visual_qa.py` observes the compositor's pre-caption frames in the
  existing render pass (6 fps, small samples). It reports the first meaningful
  visual and sustained near-empty narration stretches, ignores film tail and
  catches its own failures. Findings live in `storyboards.render_meta` and are
  advisory; QA never destroys or fails a valid render.
- `studio/compliance.py` advises before publishing and never approves. User
  steps are logged with `compliance.record` from the routes, finished jobs with
  `compliance.record_job` in the worker (`studio_events`). The similarity and
  kids scores are arithmetic with every point in their lines.
- `studio/publish.py`: the YouTube text of an approved video (`storyboards.youtube`).
  The model writes only titles, the opening, hashtags and tags from the
  narration; chapters, sources and credits are assembled by code. Never upload.
- `studio/thumbnail.py`: three thumbnails (A, B, C) per long video for YouTube's
  A/B test, one per angle (reaction, stakes, mystery). The model (`llm` task
  `thumbnail`) writes a headline and, for ink storyboards, the picture in beat
  lines (never coordinates); other styles take a frame by shot number (never a
  timestamp). `picture_lines` keeps only the video's own people, props, flags
  and photos and lists what it drops. Code draws: one frozen shot with the
  camera set to fill its half, the headline in Archivo Black. `check` is
  arithmetic with every point in its lines (size when small, the duration
  corner, clutter, contrast, likeness between options, title echo, clickbait,
  numbers the narration never says); the quick tier (`thumbnail_check`) looks
  once at the three when small and an unclear or misleading one is planned
  again once. AI down: three frames by rules, said in the log. Files live in
  `script-N/thumbnails-M/` (not the render folder) as 1920x1080 JPEG under
  2 MB; export copies them. Never upload.
- `studio/retention.py`: a render not approved within `unapproved_keep_days`
  (Settings, 30 by default, 0 = never) is deleted by the idle worker: the
  storyboard's render folder only, never the plan, the take or an approved
  video. The clock starts at the render or the last verdict (`review_at`).
- `studio/export.py`: copies an approved video, its .srt (long videos),
  YouTube text and authorship log to `<output folder>/studio/<title>-<id>/`.
  A copy for the user to upload or sync; the app never reads it back and
  never posts.
- Flags, maps and timelines (`studio/geo.py`): flags from flag-icons (MIT) and
  country outlines from Natural Earth (public domain), packed by
  `backend/tools/geo_import.py` into `clipper/assets/*.json.gz`; keep their
  licence files. People are `who` lines with traits from `ink.TRAITS`; the
  first definition of an ID wins for the whole video and the name tag shows
  once. Shots can `cut` instead of gliding.
- Archive photos (`studio/photos.py`): Wikimedia Commons API only (no scraping),
  licences public domain, CC0 and CC BY only (`photos.allowed`), results must
  share most of the search's words (`photos.relevance`). One `studio_photos`
  row per search, used by every video that asks for it; credits are kept and
  shown. Found in the storyboard job's draw step. Searches are spaced
  (`photos.api_gap`) to Wikimedia's limit: 10 a minute, or 200 when the owner
  set `WIKIMEDIA_CONTACT` in `.env` (sent in the User-Agent, to Wikimedia only);
  a 429 raises `photos.Busy`, the draw step waits and asks
  again, then leaves the rest for the next render. Never retry in a tight loop.
  Photos are seasoning, not the picture: the planners get a budget
  (`storyboard.photo_budget`, one per 40 s ink, 30 s film) and `limit_photos`
  drops the extras and the marks on them, announced in the log.
- Settings `feature_clipping` (off by default) and `feature_studio` (on) only
  decide what the UI shows; at least one stays on. `/system/status` reports them.
- Recorded moves (`clipper/assets/moves.json`) come from CMU motion capture via
  `backend/tools/mocap_import.py` (a dev tool, not run by the app); keep the
  CMU credit in that file. Figures rest their lowest point on the ground
  (`ink.lowest`) plus the move's height in the air; walks use the recorded
  stride matched to the distance covered. Sounds of the ink style come from
  `InkComposer.sound_events()`, which knows the real timing of walks and moves.
- `studio/music_search.py`: Openverse only, filtered to cc0, pdm and by; never
  scrape the YouTube Audio Library. Keep the attribution of CC BY tracks.
- Themes (`studio/themes.py`, `api/themes.py`, table `themes`): the home of the
  Studio. Every idea and script has a `theme_id` (scripts follow their idea);
  `theme` text columns keep the name sent to the AI and follow a rename. A
  script's stage (ideas, script, voice, animation, ready, published) is read
  from what exists, never stored. Merge moves everything and deletes the
  source; a theme with work is archived, not deleted. `channels` are the Buffer
  channels the Schedule panel ticks first. Deleting a theme deletes everything
  in it (`themes.delete_everything`: ideas, scripts, takes, videos, posts, the
  hosted copies and the files under `studio/script-N`), only with its name typed
  to confirm, and is refused while a post still waits on Buffer or a job of the
  theme runs; exported copies stay. Banners (`studio/banner.py`, task `banner`,
  prompt `theme_banner`): an SVG drawn on the writing tier, checked like props,
  rasterised to a 1600x480 JPEG in `studio/themes/theme-N/`, queued as a `banner`
  job when a theme is created if `Settings.theme_banners`; uploads work either
  way. The UI has no cross-theme lists any more: ideas, scripts and videos live
  in their theme (`/ideas`, `/scripts`, `/animations` redirect to `/themes`).
- Learning (`studio/learning.py`): statistics come from Buffer for sent posts
  (`posting.sync_metrics`, batched like `posts`, at most once per post per 20 h,
  60 days after sending) and from the YouTube Data API for long videos linked by
  hand (`storyboards.published_url`). The idle worker reads them once a day; the
  manual refresh is a `stats` job refused within 15 minutes of the last. The
  ranking is arithmetic (views, engagement, x median) with its lines shown; the
  model only reads the table (`theme_insights`, needs 3 videos with views, never
  "high" confidence under 6). "Build on what works" (`ideas.generate_proven`,
  task `ideas_proven`) gets numbered proven examples (videos by views, approved
  scripts, kept ideas scoring 45+), the lessons and the rejected titles, and
  answers with example numbers stored as `ideas.inspired_by`.
- Generating ideas is an `ideas` worker job (`ideas.run_generation`, params
  theme_id, count, mode, hint, examples), never a long request: the page finds
  the theme's job again when you come back (`Job.theme_id`), one batch per theme
  at a time. Developing your own idea is still a direct call.
- Ideas generated by the model start in status `inbox` (the Generate tab);
  your own ideas start as `new`.
- Audience (`studio/plain.py`, `Settings.audience`): ideas, themes, the idea
  check (`easy_score`), research and every writing prompt get the audience and
  its plain-language rules from there; keep new prompts on it. `plain.check` is
  arithmetic on the narration, every deduction in its lines; it advises only.
  "Make it simpler" (`script.make_simpler`, task `simplify`) may drop details
  but never adds facts, and keeps the old text in `previous_sections`.
- Narration must sound spoken: `script.SPOKEN` holds the rules shared by the
  writer and by "Make it sound spoken" (`script.make_spoken`, which keeps the
  previous text in `previous_sections`). Kokoro delivery (`voice.delivery`) is
  per sentence; keep pauses varied, never one fixed gap.
- Hold check (`studio/hold.py`, not `retention.py`, which deletes renders): the
  quick tier (`llm` task `hold`, prompt `script_hold`) labels each sentence
  (`plain.sentences`, numbered across sections) with one role; `hold.score` is
  arithmetic on those roles (topic named, first twist, longest gap, filler,
  ending, stakes; thresholds in `hold.TARGETS`), every point in its lines. The
  model also labels the main promise, open loops and rehooks; code deducts for
  a missing/partial main payoff, unresolved secondary loops, empty rehooks and
  loop overload, capped separately and explained line by line. It
  advises only. `scripts.hold` stores the labels with the hash of the text they
  label: an edit makes it out of date, never wrong. Runs after every write or
  rewrite job; an AI failure is logged and the job still succeeds.
  Storyboards plan on it: while it is current and the take's words match the
  script, each chapter's planner gets a `<turns>` list (word numbers of twist,
  rehook and payoff sentences, `storyboard.turn_marks`); otherwise it plans
  without and says why in the log. After planning, `limit_big` keeps ink's
  `write ... big` and film's `big ... bar` on at most `MAX_BIG` lines (payoff
  first) and lists the rest; it is not in `clean`, so the director's rule that
  `clean` leaves other shots unchanged still holds.
  Global `sound_starter` and `sound_riser` remain off by default; each storyboard
  may inherit, force on or force off either sound. `animate.attention_state` is
  shared by preview and render. A successful render stores the resolved modes,
  look, story-map craft, hold timing, measured WPM and visual QA in versioned
  `storyboards.render_meta`; failed renders preserve the prior snapshot.
  `animate.attention_events` needs a current check and says in the log what it
  skipped. Chatterbox direction gets
  the Short flag and the rehook sentences (`voice.lifted`, matched by text).
  Hold timing uses a matching chosen take's `alignment.wpm` and labels it
  measured; otherwise it estimates at `WORDS_PER_MINUTE` (150).
  Learning reads render-time craft from the snapshot and falls back to the old
  live calculation for legacy boards. General lessons still need 3 videos;
  code-generated craft comparisons need 6 comparable videos in one theme,
  format and (when materially different) duration band, with at least 3 in each
  group. They report counts, medians and ranges and use non-causal language.
- Connect YouTube (`studio/youtube_auth.py`, `api/youtube.py`, `studio/analytics.py`): OAuth code flow
  with PKCE and a single-use `state` (in the API's memory, 10 minutes), scopes `yt-analytics.readonly` and
  `youtube.readonly` (reports.query needs both). Client id and secret from `.env` only; the redirect URI is
  `YOUTUBE_OAUTH_REDIRECT_URI` (default localhost:8000). Only the refresh token is kept, in
  `data/secrets/youtube-oauth.json` (0600, never in the database), never logged or returned. A refused token
  is marked Reconnect, logged and said in the job; it never fails the stats job. Analytics are read in
  `learning.refresh` (daily pass and `stats` job, each video at most every 20 h): matched only by known ids
  (`published_url`, Buffer's `posts.link`), unmatched ones listed with why; one `reports.query` per 200
  videos for the summary, one per video for retention (the API takes one). Rows in `video_analytics`.
  Retention at five seconds, the first twist and the payoff is arithmetic
  (`analytics.for_video`: straight-line interpolation between curve points; turn
  times through `storyboard.turn_marks`), blank with a reason when unavailable.
  Impressions, thumbnail CTR and the Shorts swipe-away share are not in this API:
  never fake them or infer them from views, engagement or comments.
- Ideas have `expected`: what most viewers would guess; the answer overturns it.
  It is judged text (editing it clears the check) and `_brief` passes it to the writer.
- `studio/music_pick.py`: music is chosen from measurements
  (`sound.describe_track`: tempo, density, brightness, dynamics) on the quick
  tier; never send audio to a model. Library and Openverse candidates are
  ranked together under refs `lib-<id>` / `web-<id8>`; online candidates are
  measured from their URL and cached in `data/cache/openverse-features.json`.
  Openverse search words must be plain genres or instruments. Uploaded tracks
  have an unknown licence and are described as such. "No music" is valid.
- `studio/sound.py`: effects are synthesised (no sound files), music is
  normalised at upload and ducked with sidechaincompress; subtitles come from
  the storyboard's words (script spelling), never from Whisper's text.
  Shorts are 9:16 with captions; long videos are 16:9 without (.srt instead).
- nginx re-resolves the API through Docker DNS, so the api container can be
  restarted alone.

## Versions and releases

Semantic versioning, one version for the whole project, kept in five places:
`backend/pyproject.toml`, `backend/uv.lock` (the `clipper` package),
`backend/clipper/config.py` (`VERSION`, shown by the API), and
`frontend/package.json` with `frontend/package-lock.json`. A release is a
commit "Release X.Y.Z" that bumps them, a tag `vX.Y.Z`, and a GitHub release
(`gh release create`) whose notes list what changed since the previous tag.
Minor for new features, patch for fixes only, major for breaking changes
(a migration that cannot be undone, a removed setting or route).

## Two runtimes, one codebase

| | Mac (native) | Docker / Linux |
|---|---|---|
| Transcription | mlx-whisper (GPU) | faster-whisper (CPU) |
| Encoder | h264_videotoolbox | libx264 |
| Database | SQLite file in `data/` | Postgres |

Platform differences go through the `resolve_*` functions in `settings.py`.
Test a pipeline change in both runtimes.

## Constraints

- Machine is an M1 with 8 GB RAM: keep local models light, no local LLM.
  Transcription runs in a child process so its memory is freed afterwards.
- Exactly one worker; jobs run one at a time on purpose.
- The app's dev-server preview cannot read the Desktop folder; start servers
  from the shell and open the URL instead.
- `data/` is gitignored and disposable.
- The code is on GitHub (`origin`, Mustapha-Smail/Clipper, public), branch `main`.
  Never commit `.env` (API keys, the Wikimedia contact) or anything under `data/`,
  and keep personal details out of tracked files such as `.env.example`.
- Every clip must pass human review before it is posted. Do not add auto-posting
  without that gate: posting goes through `posting.py` only, and only when you
  press Schedule on an approved video. The brief check advises; it never approves.
- Clip only from sources a campaign authorises.
- The UI has no login. Keep ports bound to localhost.
