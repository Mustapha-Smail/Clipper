# Clipping pipeline: plan

Status (2026-11-12): Phases 1 to 3 are done. Campaigns can be pasted or scouted from public listings, are ranked with an explained score, and feed their rules into a video's brief. Open in Phase 3: running the scout on a daily schedule. Next: Phase 4 (tracking views and payouts).

Studio (animated YouTube channel), started 2026-11-12: S1 to S5 are built, and Studio 2 phases 1 to 4 (ink style, acting characters, montage, archive pictures, three looks and the "Before you publish" checks): ideas, scripts, voice, animated Shorts (9:16, 1080x1920) and long videos (16:9, 1920x1080) with chapter banners, music ducked under the voice, sound effects, an .srt subtitle file and YouTube chapters. A 6-minute video renders in about 3.5 minutes in Docker. Next: S7 publishing (title, description, thumbnail, private upload); S6 (Shorts from chapters) is covered by the per-section Shorts. AI calls now run by tier (writing, quick, research) on Claude or OpenAI-compatible models, with a spending log; music can be found on Openverse with credits kept.

## Decisions

| Topic | Decision |
|---|---|
| Market | English-language campaigns, operated from France |
| Theme | One theme per account. Default: tech / AI / SaaS and founder or business podcasts (to confirm) |
| Channels | Same handle on YouTube Shorts, Instagram Reels and TikTok |
| Automation level | Semi-automatic: code cuts, you approve campaigns and clips, posting is manual at first |
| Campaign sources | Content Rewards first, Vyro and one other as secondary |
| Machine | M1, 8 GB RAM, 64 GB free: light local models, Claude API for selection, delete sources after render |

### Operating from France

- Many English campaigns require a minimum share of US/UK/CA/AU viewers. The scout records this per campaign and filters out campaigns you cannot satisfy.
- Profile, captions and hooks are English only.
- Until your analytics show where your viewers are, prefer campaigns with no geography rule.
- Never misreport audience geography to a campaign.

## Architecture

```
Scout -> Ingest -> Transcribe -> Select -> Render -> Review (you) -> Publish (you) -> Submit -> Track
```

| Stage | Input -> output | Tool | Who |
|---|---|---|---|
| Scout | Campaign listings -> ranked campaigns in the database | Claude in Chrome, `campaign-scout` skill | Claude, you pick |
| Ingest | Campaign source link -> video file | yt-dlp | Code |
| Transcribe | Video -> word-level transcript | mlx-whisper (large-v3-turbo) | Code |
| Select | Transcript + brief -> ranked moments with hook and caption | Claude API, `clip-select` skill | Code |
| Render | Moment -> 9:16 clip with captions and hook text | ffmpeg (VideoToolbox), OpenCV face detection, Pillow text | Code |
| Review | Candidate clips -> approved clips | Local web page | You |
| Publish | Approved clip -> post on each channel | Phone, later a scheduler with audited TikTok access | You |
| Submit | Post links -> campaign submission | Claude in Chrome, with your confirmation | Claude |
| Track | Views and payouts -> net rate per campaign and per hour | SQLite, `performance-review` skill | Claude |

### Repository layout

See README.md ("How it is built") and CLAUDE.md ("Layout").

### Data model

Built: `videos`, `moments`, `clips`, `campaigns`, `jobs`, `job_events`, `settings`, `workers`
(see `backend/clipper/db/models.py`). Still to add in Phase 4:

- `posts`: clip, channel, URL, posted at, views (snapshots)
- `payouts`: campaign, clip, amount, paid at

### Choices driven by the machine

- No speaker diarization at first (pyannote is too heavy for 8 GB). Reframing follows the largest detected face.
- No local LLM. Moment selection uses the Claude API.
- Source videos are deleted once their clips are rendered.
- Long sources (2 hours and more) may need a hosted transcription API later.

## Phases

| Phase | What gets built | Done when |
|---|---|---|
| 0. Setup (you) | Accounts, payouts, legal status, API key | You can join a campaign and receive money |
| 1. Core pipeline | Ingest, transcribe, select, render, CLI | One command turns a YouTube link into 5 captioned vertical clips |
| 2. Review and compliance (done) | Review UI, Docker stack, brief check, subtitle detection, job logs | You approve or reject clips in a browser and each has a caption |
| 3. Scout (done, except the schedule) | Campaigns page, extraction, ranking, `campaign-scout` skill; daily run still to set up | A ranked campaign list appears each morning |
| 4. Tracking | View and payout tracking, `performance-review` | A weekly report shows net rate per campaign and per hour worked |
| 5. Publishing | Scheduler integration | Approved clips are queued without manual upload |

## Studio: animated YouTube videos and Shorts

A second activity in the same app: original animated videos (simple drawn characters, comic narration, one theme per channel) and Shorts cut from their chapters. Every step stops for your review; nothing is published automatically.

| Phase | What gets built | Done when |
|---|---|---|
| S1. Ideas (done) | Theme, ideas from Claude or your own, YouTube demand check, explained score, approve/park/skip | You approve an idea from a ranked list |
| S2. Script studio (done) | Research with sources, script by section, fact check per claim, editor, rewrite per section | You have a script you would read aloud |
| S3. Voice and timing (done) | Record in the browser or upload, or an AI voice (Kokoro or ElevenLabs); word timing with Whisper; skipped lines and retakes flagged | Every word of the script has a time |
| S4. Assets, storyboard, renderer (done) | Stick-figure cast drawn by code, prop library drawn by Claude as SVG, storyboard per section timed to the voice, editor with previews, Pillow renderer with captions | One Short renders end to end |
| S5. Long form (done) | Whole-script storyboard planned per chapter, 16:9 layout, chapter banners, music ducked under speech, sound effects, .srt and chapter list | An 8 to 18 minute video renders |
| S6. Shorts (done in S4) | One Short per section in 9:16 with captions; "Make Shorts for the marked sections" | Shorts appear in Review |
| S7. Publishing | Title, description, chapters, thumbnail drafts; private upload through the YouTube API | You press Publish in YouTube |

### Studio 2: a real video-making machine (started 2026-11-12)

Goal: videos that do not look like AI slop, stay within YouTube's rules on inauthentic content (July 2026: generic, template-based or repetitive videos, distressing videos, and AI personas on sensitive topics cannot be monetised), and cost about $0.50 or less per long video plus its Shorts.

| Phase | What gets built | Status |
|---|---|---|
| 1. Ink foundation | Beat language, action-per-idea planning, one continuous page with a framing camera, draw-on strokes, boil, 12 fps drawing, ink props from the existing library, liveliness score, shot editor with stills. Measured: planning a 7-minute video $0.07 on Sonnet (was $0.25; about $0.90 on Opus), render 4 min in Docker | Done 2026-11-12 |
| 2. Characters that act | Motion-capture walks, runs, marches and 10 actions (CMU, free for commercial use), 6 new poses, carry and drop, kicks and throws that send props flying, ground contact, footsteps and action sounds, two new cast members, a prop style guide that works in both looks. Icon sets were skipped: props already cost about $0.001 each, and stock icons would make the look more generic | Done 2026-11-12 |
| 2b. Montage | Flags and hand-drawn maps for places, dressed and named characters for real people, timelines for dates, hard cuts mixed with glides, a subtle wobble (setting) and a calmer camera | Done 2026-11-12 |
| 3. Archive documentary | Pictures from Wikimedia Commons (public domain, CC0, CC BY; relevance-checked) as taped prints or full frame, with a Ken Burns move and ink marks on top; one picture per search, "Try another", a credits list for the description. Library of Congress, Smithsonian and NASA can be added as further sources | Done 2026-11-12 |
| 4. More looks and compliance helpers | Three looks for the same plan (ink sketch, paper cut-out on kraft card with shadows and pop-on pieces, blueprint in white lines on a grid), switched without planning again; an authorship log of who did each step (you, the AI, the app) with a downloadable text copy; a likeness score against your 12 most recent videos, every point explained; made-for-kids and altered-content advice; all on a "Before you publish" panel that advises and never approves | Done 2026-11-13 |

Cost levers still open: Shorts cut from the long video's plan instead of planned again, fewer research searches, fact checks on the claims without a source only, Batch API for non-urgent steps.

Decided: visuals are drawn by code; voice can be your own or AI (your own is the safer choice for monetisation). The ink sketch look is the default from 2026-11-12; the flat look stays available.

Your steps for the Studio:
- [x] Create a YouTube Data API v3 key (Google Cloud console, new project, enable the API, Credentials, API key) and put it in `.env` as `YOUTUBE_API_KEY`
- [ ] Pick the channel theme and handle (separate from the clipping account)

## Your steps (Phase 0)

### Accounts
- [ ] Confirm the theme and pick one handle available on all three channels
- [ ] Create the YouTube channel, Instagram creator account and TikTok account with an English profile
- [ ] Create a Whop account and a Content Rewards account; complete identity verification
- [ ] Add your bank account for payouts in Whop and confirm France is supported
- [ ] Link the three social accounts inside Content Rewards
- [ ] Sign up to Vyro and check whether France is eligible

### Legal and tax
- [ ] Register a micro-entreprise (guichet unique) if you do not have one
- [ ] Ask URSSAF or an accountant: activity classification, and VAT on income from foreign platforms
- [ ] Check the French influencer law (June 2023): disclosure of paid collaborations, restricted product categories

### Technical
- [x] Create an Anthropic API key and put it in `.env` as `ANTHROPIC_API_KEY`
- [ ] Install the Claude in Chrome extension and log in to Content Rewards in Chrome
- [x] Approve the tool install: `brew install ffmpeg yt-dlp uv`

### First campaigns
- [ ] Browse Content Rewards and send me 3 campaigns that fit the theme (link or screenshot of the brief)
- [ ] Post the first clips manually to learn what each channel rewards

## Rules

- Clip only from sources the campaign authorises.
- No bought or bot views: permanent ban on Content Rewards.
- Every clip passes your review before it is posted.
- Label paid collaborations as the law and the campaign require.

## Expectations

Published rates are mostly platform marketing. The one live tracker found puts the median near $1 per 1,000 views before a 10% fee, so 1 million monthly views is roughly $900. The edge comes from choosing campaigns well and from volume, not from the headline rate.
