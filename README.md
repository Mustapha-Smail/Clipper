# Clipper

Turns a long video into captioned vertical clips. Paste a link or pick a file,
Claude chooses the moments, and you review, edit and approve each clip in the
browser before anything is posted.

## How it is built

```
            browser
               │
        ┌──────▼──────┐
        │  web (nginx) │  Angular app; proxies /api
        └──────┬──────┘
        ┌──────▼──────┐        ┌────────────┐
        │  api         │◄──────►│  database  │  videos, moments, clips,
        │  (FastAPI)   │        │            │  jobs, job logs, settings
        └─────────────┘        └─────▲──────┘
        ┌─────────────┐              │
        │  worker      │◄────────────┘   takes jobs off the queue, one at a time
        └──────┬──────┘
               ▼
        data folder: downloads, transcripts, rendered clips
```

| Part | Where | Stack |
|---|---|---|
| Web UI | `frontend/` | Angular 22, no UI library |
| API | `backend/clipper/api/` | FastAPI, versioned under `/api/v1`, docs at `/api/docs` |
| Worker and pipeline | `backend/clipper/` | Python: yt-dlp, Whisper, Claude, OpenCV, ffmpeg |
| Database | Postgres in Docker, SQLite natively | SQLAlchemy, Alembic migrations |

## Run it in Docker

```bash
docker compose up -d --build
```

Open http://localhost:8000. Needs `ANTHROPIC_API_KEY` in `.env` (see `.env.example`).

This starts five containers: `db` (Postgres), `migrate` (applies migrations and
exits), `api`, `worker` and `web`. Everything runs on CPU.

| What | In the containers | On the host (change in `.env`) |
|---|---|---|
| Downloads, transcripts, model caches | `/data` | `CLIPPER_DATA`, default `./data` |
| Finished clips, default | `/data/clips` | inside the data folder |
| Finished clips, separate disk | `/output` | `CLIPPER_OUTPUT` |
| Local videos to clip | `/videos` (read-only) | `CLIPPER_VIDEOS`, default `./videos` |
| Database | `pgdata` volume | managed by Docker |

To send clips to another disk: set `CLIPPER_OUTPUT=/path/on/host` in `.env`,
restart, then set **Output folder** to `/output` on the Settings page.

## Run it natively on a Mac (faster)

Uses the Mac's GPU for transcription and its hardware video encoder, and a
SQLite file instead of Postgres.

```bash
make install     # once
make run         # builds the UI, then serves everything on http://localhost:8000
```

Needs `brew install ffmpeg uv` and Node 24 (`nvm use` inside `frontend/`).

The native app and the Docker stack share the data folder but not the database,
so each has its own list of videos. A video already downloaded by one is not
downloaded again by the other.

## Campaigns

The Campaigns page lists clipping campaigns ranked for you: net rate after the
platform fee on the best platform you post on, what a 100K-view clip would pay
given the per-clip cap, budget left, fit with your theme, and warnings (location
rules, restricted products, closed listings). Open "Rules, score and notes" on
any campaign to see exactly how its score was reached.

Three ways to add campaigns:

- **Look for campaigns:** the button on the Campaigns page queues the scout job.
  The worker reads the public discover page of Content Rewards, then up to 15
  listings (Settings, "Campaign scout"): the campaigns you marked interested or
  joined, to refresh budget and status; new ones that Claude judges a fit for
  your theme from their cards; and a few of the best paid, for comparison.
  Skipped campaigns are never read again. Plain page reads with no account,
  robots.txt honoured, one page at a time. Settings can run it on a schedule
  (every 6 hours to once a week); a scheduled run waits behind other jobs. From a
  terminal: `uv run clipper scout`. Costs a few cents of the quick tier per run.
  When the AI is unavailable (no credit, no key, an outage) the run still
  succeeds and the listings still show up: it chooses by rate, reads each page's
  fixed blocks itself (rates per platform, caps, budget, status, any rules shown
  before joining), and ranks them with the same arithmetic. Theme fit is then
  unknown and counts as 4/10; rules and location are flagged as not checked.
  The Campaigns page says so, and the next run with AI reads them again.
- **Paste:** on the Campaigns page, "Add a campaign", paste the listing's page
  text and its link. Claude extracts the terms.
- **Scout skill:** ask Claude Code to "run the campaign scout". It reads public
  listings in its browser and loads them (see `.claude/skills/campaign-scout/`).
  Useful for other marketplaces; the job above covers Content Rewards.

Mark a campaign as joined, then "Clip a video for it": its rules become the
video's brief, which drives both moment selection and the pre-post check.

Set your theme, platforms and audience split under Settings, "Your channel".

## Features

Settings, Features turns each half of the app on or off: the **Studio**
(animated videos, on by default) and **Clipping** (clips for paid campaigns,
off by default). A feature that is off leaves the menu and its settings are
hidden; nothing is deleted. The menu collapses to a rail of icons on a desktop
(the arrow next to the logo) and becomes a drawer behind the menu button on a
phone.

## Studio ideas

The Ideas page (under Studio) is the first step of the animated channel. No theme in mind? The magic wand next to the theme field suggests five,
with a reason and an example title each, different from the themes you already
have (type a word first, such as "money", to steer it). One quick-tier call,
a small fraction of a cent. Write
the channel's theme and ask for ideas, or add your own and let Claude develop
it into a title, hook, beats and a YouTube search. "Check it" then:

1. searches YouTube for the topic (official YouTube Data API v3, about 102 of
   the 10,000 free daily quota units per check), and
2. asks Claude to judge the hook, how drawable it is, how settled the facts
   are, whether the angle is already taken and whether ads would be limited.

The score out of 100 adds demand (views of the 3rd most-viewed long video),
room for a small channel (best views from a channel under 100K subscribers),
age of the top videos, a free angle, the hook, drawability, facts and Shorts
potential, minus penalties for limited ads or a big channel's recent video on
the same angle. "Beats, competition and score" shows every line and the
YouTube results it used. Without `YOUTUBE_API_KEY` in `.env`, only Claude's
part is scored (out of 40) and the card says so.

A checked idea also suggests a length, from the same results and shown with
its reason: when the best Short has more than twice the views of the best long
video, a Short of about 50 seconds; otherwise the middle length of the five
most-viewed long videos, kept between 4 and 15 minutes (8 at least for a list).
Without YouTube data it falls back on the format. An approved idea is either a
**long video** (Shorts can still be cut from its sections) or **Shorts only**:
its script is then written as one section of 15 to 90 seconds, and its Videos
tab offers only the Short. The length field next to "Write the script" starts
from the suggestion.

## Studio scripts

On an approved idea, "Write the script" starts a job with three steps:

1. **Research:** Claude searches the web (up to 10 searches) and writes notes
   grouped by beat, every fact followed by its numbered source, plus the myths
   to avoid.
2. **Write:** Claude writes the narration from those notes only, in sections
   (cold open, body, outro), aiming at the target length at 150 words a minute.
3. **Fact check:** every factual claim is checked against the notes; claims the
   notes do not cover are searched on the web. Each claim is marked supported,
   contradicted, unclear or joke, with links to its sources.

On the script page you edit each section, reorder or add sections, ask Claude
to rewrite one section ("shorter", "simpler words") and keep or discard its
version, then save. Editing marks the fact check out of date until you run it
again. "Download as text" gives the script to read from. A full run takes about
four minutes.

Search uses Claude's basic web search tool on purpose: the newer one returned
answers without citations, which the fact check depends on.

## Studio voice

Below the script, the Voice panel makes takes of the script as it is saved.
Every take is timed word by word with Whisper "small" (Settings, Timing
model): it only has to find words the script already has, and the large model
can run Docker out of memory. A take whose timing failed says why and offers
"Time it again".

- **Record yourself:** the browser records your microphone while the script is
  shown as a teleprompter. Browser voice processing is off; the server evens
  out the loudness.
- **Upload a recording:** any audio file, or a video with sound.
- **AI voice:** Kokoro runs locally for free (22 English voices; the model,
  205 MB, downloads once into `data/cache/kokoro`), or ElevenLabs with
  `ELEVENLABS_API_KEY` in `.env` (your account's voices appear in the list).

**Sounding human.** Scripts are written for the ear (contractions, questions to
the viewer, varied rhythm, signposts like "So." and "Here's the thing:", beats
marked with "…", a new paragraph where the narrator breathes). For a script
written before that, "Make it sound spoken" rewrites the whole text in one call,
keeping every fact; "Switch versions" swaps back. Kokoro reads one sentence at a
time: short punchy lines a touch slower with room to land, long ones a little
quicker, longer pauses after questions, ellipses and paragraphs, quiet room tone
instead of digital silence. "Pauses" (tight, natural, relaxed) sets how much
room it leaves; natural pauses make a video about 15 to 20 percent longer than
tight ones. Kokoro still has no breaths or emotion: your own recording, or
ElevenLabs, sounds more human.

To compare AI voices before making a take, open "Compare voices": each Kokoro
voice reads the start of your script (free, cached after the first play); each
ElevenLabs voice plays ElevenLabs' own preview clip (no credits used).

Every take becomes a 48 kHz mono WAV normalised to about -16 LUFS, then
Whisper times it and the words are matched to the script: each section's time
range, passages not spoken as written, and speech that is not in the script
(retakes, asides). The word timeline is kept for the storyboard. Mark one take
"Use for the video". Editing the script marks takes as out of date; "Time it
against the current script" re-times a take without re-recording.

Timing on this Mac: a 6-minute script takes about 2 minutes to speak with
Kokoro in Docker and about 3 minutes to time with Whisper on the CPU (faster
natively, where Whisper uses the GPU).

## Studio videos (animated Shorts)

Once a take is marked "Use for the video", the Videos panel on the script page
makes a Short from any section, or the long video. Pick the look first:

- **Ink sketch** (the default): the whole video is one hand-drawn page that the
  camera travels across. Stick-figure actors act on things (point at them,
  hold, push or pull them, walking up to them by themselves), props draw
  themselves on in ink with a watercolour wash, words are handwritten, and
  circles, crosses, ticks and arrows are drawn over what matters. Lines wobble
  like hand-drawn animation and drawings move 12 times a second; the camera
  keeps the action framed and glides from shot to shot.
- **Flat colour** (the first style): scene by scene, stickers in fixed slots.

### The ink style

Claude plans an ink storyboard in a short beat language, one line per thing
that happens, each starting with the narration word it happens on:

```
0 shot "The narrator meets a fridge" push
0 actor narrator left deadpan talk
1 draw fridge right large "fridge"
2 do narrator push fridge worried
3 write "0 cold" top big bad as zero
4 cross zero
```

The planner is told to turn each idea into an action done to an object, not a
picture of the word being said. The storyboard page shows each shot with a
still, its words numbered, and its beats; edit the beats as text (a cheat sheet
is under "How to write beats"). Lines the app cannot use are refused with the
reason. A **liveliness score** (out of 100) is plain arithmetic on the saved
plan, with every deduction listed: stretches over 4 seconds with nothing new,
shots where nothing acts, idle characters, too much writing, repeated patterns,
a slow average pace. It advises; you decide.

**Acting.** Actors hold poses (point, hold, push, pull, carry, drop, shrug,
think, facepalm, cheer, wave, surprised, sad, crossed arms, hands on hips,
"ta-da", scared) and play recorded movements once: jump, kick, throw, pick up,
stumble, laugh, dance, cartwheel, jumping jacks, signal. Those movements, and
walking, running, marching and a zombie shuffle, come from the CMU Graphics Lab
Motion Capture Database (free for commercial use), turned into the figure's
angles by `backend/tools/mocap_import.py` and bundled as
`clipper/assets/moves.json`. Walks are matched to the ground covered, so feet
do not slide; the lowest point of the body rests on the ground (hands, in a
cartwheel). An actor carrying a prop takes it along until they drop it, and it
falls; a kicked or thrown prop flies off. Footsteps, landings, swishes,
creaks and slides are synthesised to match. The cast: narrator, person,
scientist, worker, royal, fancy, kid, chef, woman, elder.

**Showing what is named.** The planner cuts in a visual whenever the narration
names something:
- a country, city or region: its flag (flag-icons, MIT) or a hand-drawn map
  (Natural Earth outlines, public domain) with the countries named in colour
  and labelled, their neighbours in outline;
- a real person: defined once with `who` and the traits people know them by
  (hair, beard, headwear such as a mitre, laurel or crown, glasses, clothes
  such as a robe, toga or suit, skin tone), then on stage with their name
  written under them the first time;
- dates and periods: a timeline that draws itself, label by label.

`backend/tools/geo_import.py` packs the flags and outlines into
`clipper/assets/` (flags.json.gz, countries.json.gz).

**Pictures from the archives.** For a real thing that exists in paintings,
engravings or old photographs (a portrait, a building, a manuscript), the
planner writes `photo "Pope Gregory XIII portrait"`. When the storyboard is
planned or rendered, the app searches Wikimedia Commons through its public API
and keeps only pictures that are public domain, CC0 or CC BY (share-alike,
non-commercial and no-derivatives licences are left out, as for music), large
enough, and whose title or description has most of the search words. A picture
appears as an aged print taped to the page, or fills the frame (`full`); a
picture far from the frame's shape is shown whole over a blurred copy of itself.
It moves slowly (a Ken Burns move), and ink marks can be drawn over it. Each
search is resolved once and reused; the storyboard page lists the pictures
with their licence and source, "Try another" moves to the next result, and the
For YouTube box gathers every credit line (pictures and music) to paste into
the description.

**Editing.** Shots glide from one to the next across the page, or cut hard
(`cut` on the shot line); the planner mixes both. The camera reframes at most
every 3 seconds unless something would appear out of view. The lines' hand-drawn
wobble is subtle by default (Settings, Ink style: off, subtle or strong).

**Looks.** One plan, three ways to draw it, chosen when you start a video or
later on the storyboard page (render again to see it; no new plan, no AI cost):
ink sketch (the default), paper cut-out (props in their own colours, every
piece cut from card with a pale edge and a shadow, popping on instead of being
drawn, no wobble) and blueprint (white lines on a blue grid). Archive photos,
flags and maps are pasted as they are in every look.

**Cartoon.** A fourth look: the same characters drawn in colour (filled
shapes with an outline, on the same skeleton, so every pose, walk and action
works), standing in a drawn place. Each shot line can say where it happens
(`12 shot "A monk at work" push in scriptorium`); a shot without one stays where
the one before was. Six places are drawn by code (study, church, town, hills,
night, idea); for anywhere else the planner invents a name and Claude draws it
the first time a cartoon video needs it, wide for long videos or tall for
Shorts (about 4 cents each, kept in the Library under Places and reused by
every later video). Storyboards planned before settings existed get them from
a quick call when they are first drawn as cartoons, so switching the look never
needs a new plan. Words, labels and timelines sit on cards and maps on a board,
so they read over a busy place. The place moves a little slower than the
camera (parallax) and dissolves into the next when a shot glides.

**The storyboard page** has four tabs: Video (the render, your review, the
look), Scenes (the shots, the liveliness score, archive pictures), Sound
(music, level, effects) and Publish.

**YouTube title and description.** On the Publish tab of an approved video,
"Write the title and description" asks the writing model for three title
options, an opening of two or three paragraphs, hashtags and tags, using only
the script's own facts (about one cent). The app adds the rest as it is:
chapters (long videos), the research sources the checked claims rely on, and
the credit lines for pictures and music. You edit it and copy each part into
YouTube Studio; counters show YouTube's limits (100, 5000 and 500 characters).

**Export.** The Publish tab of an approved video has an Export button. It
copies the video into the output folder (Settings, Storage) as
`studio/<title>-<id>/`, with its subtitles (.srt, long videos only: Shorts have
theirs burned in), `youtube.txt` (title, description and tags, once written)
and `authorship.txt`. Exporting again replaces those files; the page says when
the copy is of an earlier render. In Docker the output folder is `/output`
(`CLIPPER_OUTPUT`), so point it at a synced or shared folder to get finished
videos out of the machine. Nothing is posted.

**Grace period.** A rendered video that is not approved within 30 days is
deleted to save space: the video file and its effects track, nothing else. The
days count from the render, or from your last verdict if that is later, so
taking an approval back gives a full period again. The storyboard, the voice,
the sound choices and the YouTube text stay, and rendering again costs no AI.
Approved videos and exported copies are never deleted. The video's page and
the Animations list show the date; Settings, Storage, "Delete unapproved videos
after" changes the days (0 keeps everything). The worker checks once an hour
while idle.

**Before you publish.** Once a video is rendered, its page shows a checklist
and four pieces of advice, all plain rules with every point shown. Nothing here
approves or posts anything.

- *Your part*: the authorship log records who did each step: you (edits to the
  script and scenes, takes chosen, looks, music, picture swaps, review), the AI
  (research, drafts, rewrites, plans, suggestions) or the app (renders). Keep
  the text copy with the video; it is your record if the channel is reviewed
  for inauthentic content.
- *Like your recent videos?*: a 0 to 100 score against your 12 most recent
  videos from other scripts: same look, same music, same AI voice, shared
  props, title words and opening words. YouTube does not pay for uploads that
  look mass-produced.
- *Made for kids?*: reading grade (Flesch-Kincaid) and topic words. You answer
  this in YouTube Studio for every upload.
- *Altered or synthetic content*: drawn animation needs no label; a cloned
  real voice does.

Props come from the same library as the flat style: each is turned into ink
lines and a wash by code, so no new drawing is needed. New props are drawn
with a style guide that makes them work both as flat stickers and as ink line
drawings. The pen scribbles when
something draws itself on, the camera whooshes between shots, an eraser rubs
things out.

Planning is cheap: a beat is one short line instead of a block of JSON, the
planner works at medium effort with a beat budget (one beat every 2.2 seconds
or so), and the instructions shared by a long video's chapters are cached
between calls. Measured on the 7-minute LED video: $0.07 to plan on Sonnet 5.5
(the flat style: $0.25 on Sonnet, about $0.90 on Opus); a one-minute Short
costs about $0.04.

### The flat style

1. **Plan:** Claude reads the section's words with their times and cuts them
   into scenes (usually one per sentence). Each scene has a background, a camera
   move and up to four elements placed in named slots: cast members (pose,
   expression, talking or not), props, big text, numbers and arrows. Elements
   can appear on the word that introduces them.
2. **Draw:** any prop the library lacks is drawn by Claude as SVG in the house
   style (thick black outlines, a fixed palette, no text), checked, and kept for
   every later video. The Props page shows the library; redraw any prop with an
   instruction.
3. **Edit:** the storyboard page shows a preview frame per scene; every
   element is editable, scenes can be split or removed.
4. **Render:** every frame is drawn with Pillow (pop-ins, motion, camera,
   mouths that move with the voice, blinking), with the same word-by-word
   captions as clips, encoded with the voice at 1080x1920, 24 fps. A one-minute
   Short takes under a minute to render in Docker.
5. **Review:** approve or reject the video; nothing is posted for you.

The cast (narrator, person, scientist, worker, royal, fancy, kid, chef) is
drawn by code in `studio/art.py`; props are rendered with resvg.

## Studio long videos, sound and publishing files

- **Formats:** a Short is vertical 9:16 (1080x1920) and tells one section,
  with burned-in captions. The long video is horizontal 16:9 (1920x1080) and
  tells the whole script, without burned-in captions (upload the .srt instead).
- **Long storyboards** are planned one chapter (script section) at a time, in
  parallel, then joined; each chapter opens on its own scene (in ink, a new row
  of the page) and a "Chapter N" banner appears at the top left.
- **Music:** under Library, Music, either upload a track (for example from the
  YouTube Audio Library, which has no API: download it in YouTube Studio) or
  use "Find free music online", which searches Openverse (Jamendo, Freesound,
  ccMixter...) for CC0, public-domain and CC BY tracks only. A CC BY track keeps
  its credit line, which appears in the storyboard's For YouTube box. Each track is normalised to -16 LUFS on
  upload. On a storyboard, pick a track and a level (default -18 dB below the
  voice); it loops, fades in and out, and drops about 7 dB more while the voice
  speaks.
- **Sound effects:** synthesised in code and matched to what appears: bloops and
  plops for characters and props, plucks and clicks for words, a small bell for
  numbers, a boing for things that bounce or grow, a zip for arrows, whooshes for
  slides. Pitch and length vary, the same sound never plays twice in a row, and
  each storyboard has its own sequence. On by default; switch off per storyboard.
- **Suggest music:** ranks your library and tracks found online together. A
  first small call on the quick tier turns the video's tone into plain search
  words (Openverse matches titles and tags, not moods), the app searches
  Openverse (CC0, public domain, CC BY only), measures each candidate from 40
  seconds read straight from its source (tempo, density, brightness, dynamics;
  cached; no audio goes to a model), and a second small call ranks everything.
  It may answer "no music". Online picks can be heard with the voice before
  adding them; "Add and use" imports one with its credit line. Untick "Search
  online too" to rank only the library. About 10 to 20 seconds, a fraction of a
  cent. "Hear the mix" plays 25 seconds of voice, effects and a track together
  without rendering anything.
- **Scenes** are grouped by chapter (long videos) or by 15 seconds (Shorts);
  groups fold away.
- **For YouTube:** download the subtitles (.srt, the script's exact words with
  the take's timing) and copy the chapter list for the description. The app
  warns when YouTube's chapter rules (3 or more, 10 s each, from 0:00) are not
  met.

## AI models and spending

Every AI call names a task, and each task belongs to a tier (Settings, AI
models):

| Tier | Tasks | Default |
|---|---|---|
| Writing | moments, ideas, scripts, section rewrites, fact-check claims, storyboards | Claude Opus 5.5 |
| Quick tasks | idea checks, campaign terms, choosing campaigns to read, brief checks, reading fact-check results, drawing props | Claude Haiku 5.5 |
| Research | script research and claim look-ups, with web search and citations | Claude Sonnet 5.5 |

Models are picked from a list: the known current ones first with their prices,
then every model your key can use ("Other…" accepts any model id, for example
on a local Ollama). Each tier can use Claude or OpenAI (`OPENAI_API_KEY`), or any OpenAI-compatible
server (`OPENAI_BASE_URL`: OpenRouter, a local Ollama...). Compatible servers
have no web search, so Research needs Claude or OpenAI itself.

Quick tasks default to Claude Haiku 5.5, about a fortieth of Opus's price. If a
quick task's results look weak (prop drawings first), move that tier to Sonnet
5.5. Check the spending table under Settings,
which lists every call by task with tokens, web searches and an estimated cost
(list prices; unknown models show "+?"). The app already avoids repeat calls:
props are drawn once and reused, fact checks search the web only for claims the
research does not cover, ideas are checked only when you ask.

**Cost per video.** Every AI call is tagged with what it was for (an idea, a
script, a storyboard or a clipped video). The script page shows what its idea,
the script and all its videos cost so far; a storyboard page shows the cost of
that video from idea to render, step by step (planning again adds to it;
rendering is free); a clipped video's page shows what its moments and brief
checks cost. Calls made before tagging existed were matched to the jobs that
were running at the time; idea checks from back then cannot be traced.

## Seeing what is happening

- **Activity page:** what is running now (with its stages and log, and Cancel),
  then recent jobs one line each (filter to failed ones; "Log" opens a job's
  full log), and the combined log, folded until you open it.
- **Container logs:** `docker compose logs -f worker` (or `api`). One JSON
  object per line, with `job_id`, `video_id` and `request_id` fields.
- **Health:** `GET /healthz` checks the API and the database.
  `GET /api/v1/system/status` adds worker heartbeat, queue depth and disk space.

Set `CLIPPER_LOG_LEVEL=DEBUG` to also log the UI's polling requests.

## Moving to a VM

1. Copy the project folder and your `.env`. Set a real `POSTGRES_PASSWORD`.
2. `docker compose up -d --build`.
3. Reach the UI through an SSH tunnel: `ssh -L 8000:localhost:8000 user@vm`.
   The UI has no login, so the port is bound to localhost on purpose.
4. YouTube often blocks downloads from cloud servers. If links fail, export a
   `cookies.txt` from your browser, put it in the data folder and set
   **Cookies file** to `/data/cookies.txt` on the Settings page.
5. If the VM's user is not uid 1000, add `user: "<uid>:<gid>"` to the backend
   block in `docker-compose.yml` so the containers can write to the data folder.

A VM without a GPU transcribes on CPU. Pick the **Small** or **Medium** speech
model on the Settings page if the large one is too slow.

## Development

```bash
make dev-api     # API + worker on :8000
make dev-ui      # Angular dev server on :4200, proxying /api to :8000
make test lint   # backend tests and lint
```

Database changes: edit `backend/clipper/db/models.py`, then
`cd backend && uv run clipper db revision -m "what changed"` and review the new
file in `backend/clipper/db/migrations/versions/`. Migrations are applied by
`clipper ui` on start and by the `migrate` container in Docker.

Without the UI: `cd backend && uv run clipper run <url-or-file> --n 5`.

When downloads stop working, update yt-dlp:
`cd backend && uv lock --upgrade-package yt-dlp && uv sync`, then rebuild.
