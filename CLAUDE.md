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
- `captions.py`: text is drawn with Pillow because Homebrew's ffmpeg has no
  subtitle or drawtext filters. Do not switch to ffmpeg `ass`/`subtitles`.
- `subtitles.py`: burned-in captions are detected as a band of changing text,
  not by matching the transcript (many sources use summary cards).
- `campaigns.py`: listings arrive as page text; Claude extracts the terms, and
  `assess()` ranks them with plain arithmetic whose lines are shown to the user.
  Keep the score explainable: every adjustment must appear in the breakdown.
  The app never scrapes marketplaces itself; the `campaign-scout` skill does the
  reading from public pages and posts `{url, text}` to `/api/v1/campaigns`.
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
- `studio/storyboard.py`, `studio/animate.py`, `studio/art.py`: Claude plans
  scenes by word number (never timestamps) in a fixed vocabulary that the
  renderer knows; `storyboard.clean()` makes any plan or user edit safe. Props
  are SVG in `studio_assets`, checked by `art.check_prop_svg` (no text, scripts,
  styles, links or images) and rasterised with resvg. The renderer's layout
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
- `studio/compliance.py` advises before publishing and never approves. User
  steps are logged with `compliance.record` from the routes, finished jobs with
  `compliance.record_job` in the worker (`studio_events`). The similarity and
  kids scores are arithmetic with every point in their lines.
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
  shown. Found in the storyboard job's draw step.
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
- The code is in a local git repository (branch `main`, no remote yet). Never commit `.env`
  (API keys) or anything under `data/`.
- Every clip must pass human review before it is posted. Do not add auto-posting
  without that gate. The brief check advises; it never approves.
- Clip only from sources a campaign authorises.
- The UI has no login. Keep ports bound to localhost.
