# Clipping pipeline

Semi-automatic tool that turns long videos into captioned vertical clips for
paid clipping campaigns (English market, operated from France). PLAN.md has the
roadmap and the owner's setup checklist; README.md has run and ops instructions.

## Layout

- `backend/` Python package `clipper`: API (`api/`), database (`db/`), job queue
  (`jobs.py`), worker (`worker.py`), pipeline stages (`pipeline.py` plus one
  module per stage), CLI (`cli.py`). Tests in `backend/tests/`.
  `studio/` is the animated-channel side (ideas now; script, voice, storyboard
  and renderer later), one module per step, same rules as the pipeline.
- `frontend/` Angular 22 app. Standalone components, signals, zoneless, plain
  SCSS (design tokens in `src/styles.scss`). No UI library.
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
- AI calls go through `llm.py` only: `llm.ask(task, ...)` / `llm.search(task, ...)`.
  A task maps to a tier (writing, quick, research) whose provider and model come
  from Settings; providers live in `claude.py` (Anthropic) and
  `openai_compat.py` (OpenAI Responses API, or Chat Completions for
  compatible servers). Every call is logged in `ai_usage`. New AI work needs a
  task in `llm.TASKS`, never a direct SDK call.
- `moments.py`: the model answers with transcript line numbers, never timestamps.
- AI outages: providers raise `ai_errors.AiUnavailable` (no key, credit, rate
  limit, 5xx, unreachable); `ai_usage.outage` records it and `llm.health()` feeds
  the status banner. Clipping falls back to `rules.py` (moments and brief checks,
  explainable points, marked "Picked by rules") when `ai_fallback` is "rules";
  pasted listings are stored with `read_by` "waiting". Never fall back silently:
  report it through the Reporter.
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
- `studio/script.py`: research, write and factcheck are worker job stages
  (`jobs.STAGES`), tied to a script by `params["script_id"]`. The writer may use
  only the research notes; every web fact keeps its numbered source. Web search
  goes through `claude.search` (basic `web_search_20250305`, which returns
  citations); structured answers through `claude.ask`. The two cannot be one
  call. A fact check stores the hash of the text it checked.
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
- Storyboards have a `style`: `ink` (default) or `flat`. Ink storyboards are
  shots of beat lines (`studio/beats.py`: "12 do narrator push fridge"), never
  JSON from the model; `beats.clean()` makes any plan or edit safe and counts
  what it drops. `studio/ink.py` draws (brush strokes, the figure rig with
  arm reaching, library props turned into ink lines and a wash, handwriting
  with Patrick Hand, OFL, bundled); `studio/stage.py` lays shots on one page,
  walks actors, frames the camera and adds the boil. Drawn things change at 12
  fps, the camera at 24. Keep the liveliness score (`beats.score`) arithmetic,
  every deduction in its lines. New actions go in the beat language and the
  stage, and in the planner prompt and the page's cheat sheet.
- Looks (`studio/looks.py`): `storyboards.look` is ink, paper or blueprint and
  changes only drawing (backgrounds, sprites dressed per sprite and cached,
  pop-on, shadow), never the plan. Photos, flags and maps are never recoloured.
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
- `studio/compliance.py` advises before publishing and never approves. User
  steps are logged with `compliance.record` from the routes, finished jobs with
  `compliance.record_job` in the worker (`studio_events`). The similarity and
  kids scores are arithmetic with every point in their lines.
- `studio/publish.py`: the YouTube text of an approved video (`storyboards.youtube`).
  The model writes only titles, the opening, hashtags and tags from the
  narration; chapters, sources and credits are assembled by code. Never upload.
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
  without that gate. The brief check advises; it never approves.
- Clip only from sources a campaign authorises.
- The UI has no login. Keep ports bound to localhost.
