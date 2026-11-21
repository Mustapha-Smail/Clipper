# Plan: parallel stories, concept tests, licensed sound effects and learning

Hand this whole file to the orchestrating session and give each session brief below to a fresh implementation
session. Work from the stable `v2.6.0` release made on 2026-11-21. Read `CLAUDE.md` in full before editing;
it overrides this plan where they disagree.

## Outcome

Clipper should support an original channel format built around this repeatable promise:

> Same start. One hidden rule. Two outcomes.

The product must make that format fast to produce without becoming a Tavieen Film clone. It should support four
editorial shapes (`parallel`, `explain`, `scorecard`, `investigation`), code-rendered split lanes, restrained data
graphics, contrast thumbnails, stronger licensed sound effects, 12–18 second silent concept tests, promotion of a
winning test into a freshly researched long video, and learning that keeps unlike formats separate.

The operating sequence is:

1. test a premise cheaply as a Short;
2. inspect comparable evidence, not just lifetime views;
3. promote a promising premise to a long video by creator choice;
4. re-research and rebuild the long story rather than stretching the Short;
5. learn by form, story shape, lineage and known thumbnail outcome.

The implementation remains semi-automatic. The creator approves ideas, scripts, assets, storyboards, renders and
promotion. Nothing publishes, promotes or declares a winner automatically.

## What version 2.6.0 already provides

Do not reimplement these systems:

- `studio/story_map.py` is the source-backed contract between research and prose. It validates citations and loops,
  fingerprints upstream inputs and refuses stale maps.
- `studio/hold.py` already audits a main promise, open loops, rehooks, twists, payoff and callback, and keeps every
  deduction explainable.
- `storyboards.render_meta` already freezes successful-render craft metadata. Learning prefers that snapshot over
  mutable current settings.
- ideas already have `expected`, `answer`, `format` (`single|list`), `video` (`long|short`) and `inspired_by`.
- storyboards already support ink, flat and film styles; ink looks include `ink`, `paper`, `blueprint`, `cartoon`,
  `clean_doodle` and `rough_paint`.
- starter/riser experiments, continuity advice, visual QA, thumbnail generation, YouTube retention, Buffer posting,
  Openverse music and attribution already exist.
- learning already uses minimum sample sizes for code-generated craft comparisons and does not pretend to have
  thumbnail CTR or Shorts swipe-away data.

The new work extends those contracts and keeps all legacy rows and renders readable.

## Product and editorial rules

- Copy the production system, not another creator's wording, illustrations, music, characters, title phrases or
  thumbnails. Prompt files must explicitly prohibit phrase-level or frame-level imitation.
- `format` keeps its current meaning: one subject (`single`) or a list of subjects (`list`). It is not a story shape.
- `form` means distribution/runtime: `long`, `short` or `micro_short`.
- `story_shape` means editorial structure: `parallel`, `explain`, `scorecard` or `investigation`.
- `style` remains the rendering system (`ink|flat|film`); `look` remains a drawing profile. `glow` is a look, not a
  new renderer.
- A parallel story uses the same start, shared clock and comparable checkpoints. It does not merely alternate two
  unrelated anecdotes.
- Research wins over a desired reveal. The writer may drop a contrast or consequence that the sources do not support.
- Models propose labels, structure and content. Code validates IDs, sources, ordering, geometry, timing, licences and
  arithmetic.
- All new checks advise. They may block an invalid technical contract, but never approve a creative or legal choice.
- Never infer unavailable CTR, impressions or swipe-away metrics from views, likes or comments.

## Data contracts and migrations

Check the newest migration before editing. At the time of writing it is
`0034_storyboard_render_experiments.py`; the revision names below are reservations, not permission to overwrite a
newer revision.

### Strategy and lineage

Add a migration such as `0035_strategy_and_lineage.py`:

- `ideas.form`: `long|short|micro_short`, backfilled from `ideas.video`;
- `ideas.story_shape`: nullable for legacy rows, one of the four canonical values for new rows;
- `ideas.parent_idea_id`: nullable self-reference with `SET NULL`;
- `ideas.lineage_kind`: `root|concept_test|promotion`, default `root`;
- `scripts.form` and `scripts.story_shape`, copied at creation so later idea edits do not rewrite a script's intent;
- `storyboards.form` and `storyboards.story_shape`, copied at creation and included in `render_meta` after success;
- `storyboards.caption_mode`: `default|on|off`, where `default` preserves today's Short-on/long-off behavior.

Keep `ideas.video` and `scripts.video` for API compatibility during this release line. New code derives their old
value from `form` (`micro_short` maps to `short`) and never overloads `format`.

Do not guess a legacy story shape from a title. A current, unedited story map may provide a safe mapping only where it
is exact; otherwise use `NULL`/`legacy` in read models and exclude the row from shape comparisons.

### Daily metric observations

Add the observation table in the first implementation wave even though its reports arrive in P3. Delaying collection
permanently loses the history required for seven-day velocity.

Suggested `video_metric_snapshots` fields:

- `id`, `storyboard_id`, `source_key`, `service`;
- `observed_at`, `observed_day`;
- `views`, `likes`, `comments`, `minutes_watched`, `average_view_s`, `average_view_pct` when the source supplies them;
- unique constraint on `(source_key, observed_day)` so retries update one daily point rather than duplicate it.

Write observations where current metrics already arrive: Buffer metric sync and YouTube analytics refresh. Retain the
current latest-value fields for compatibility.

### Licensed sound assets

Add a later migration such as `0036_studio_sounds.py` with `studio_sounds`:

- semantic `kind` such as `pop`, `whoosh`, `impact`, `scribble`, `step`, `riser`;
- `name`, normalized local `path`, `duration`, `gain`, `enabled`;
- `provider`, provider/Openverse ID and upstream ID;
- `creator`, creator URL, landing URL;
- `license`, version and licence URL;
- returned attribution, chosen media URL, acquisition timestamp and local checksum;
- creator approval timestamp and optional rejection note.

An approved asset is downloaded once, normalized to 48 kHz mono and mixed locally. Rendering never hotlinks a remote
file. Multiple approved assets may share a kind; selection is deterministic from storyboard ID and event index. The
existing synthesized bank remains the offline/provider-failure fallback.

## P0: editorial contracts, evidence collection and contrast thumbnails

### P0.1 Add story shape without replacing existing format

Files include `db/models.py`, the strategy migration, `studio/ideas.py`, `studio/script.py`,
`studio/story_map.py`, API schemas/routes, Ideas and Script UI, prompt files and tests.

Add `story_shape` to generated and user-created ideas and copy it to scripts. Existing `single|list` remains an
orthogonal choice. Show both controls and explain the difference in the UI.

Keep the current story-map `template` field readable as a legacy arc label. New map JSON is version 2 and carries the
canonical `story_shape`. Do not force an inaccurate one-to-one conversion from old
`mechanism|story_loop|journey|ranking` values.

Create a common prompt fragment plus shape-specific instructions:

- `parallel`: same starting conditions, exactly two named lanes, one shared clock, at least two matched checkpoints,
  a promised consequence per lane and a final comparison/callback;
- `explain`: visible mechanism, cause/effect chain, evidence before conclusion and a concrete payoff;
- `scorecard`: named criteria applied consistently, no moving goalposts and a sourced result;
- `investigation`: initial question, competing explanations, evidence that changes confidence and a supported answer.

For a parallel map, add structured data rather than hiding lanes in prose:

```json
{
  "story_shape": "parallel",
  "parallel": {
    "shared_start": "the condition both lanes begin with",
    "shared_clock": "the common time progression",
    "lanes": [
      {"id": "a", "label": "Lane A", "rule": "the differing rule", "promised_consequence": "..."},
      {"id": "b", "label": "Lane B", "rule": "the differing rule", "promised_consequence": "..."}
    ]
  },
  "beats": [
    {"id": "first_a", "lane": "a", "checkpoint": "first_test", "source_numbers": [1]},
    {"id": "first_b", "lane": "b", "checkpoint": "first_test", "source_numbers": [1]}
  ]
}
```

Code requires two safe lane IDs, at least two checkpoint IDs represented in both lanes, known source numbers and a
non-empty shared start/clock. Non-parallel maps reject lane/checkpoint metadata. Add `form` and `story_shape` to
`story_map.input_material()` so either edit makes a stored map stale with an exact reason.

Acceptance:

- all four shapes work with both `short` and `long`; `micro_short` is enabled in P2;
- old maps load and display without mutation;
- changing form/shape stales the current map and never silently feeds it to a writer;
- a malformed parallel map reports the exact missing lane or checkpoint;
- generated prompts prohibit unsupported contrasts and imitation of the reference channel.

### P0.2 Make promise, consequence and payoff observable

Extend the current hold contract rather than creating a second scorer.

- add `consequence` to sentence roles;
- extend stored hold data with validated `promise_sentence`, lane consequence sentence(s), `payoff_sentence`, verdict
  and reasons;
- validate that every referenced sentence exists and that promise precedes consequence and payoff;
- for parallel stories, validate both named lane consequences and report `missing|partial|paid` separately;
- keep combined deductions capped and print every deduction in `hold.score()`;
- freeze word/time checkpoints for promise, each consequence, twist/rehook and payoff in successful `render_meta`.

The model judges semantic delivery; code owns sentence existence, order, lane IDs, time lookup and score arithmetic.
Missing or stale hold data produces a reason, not zero retention.

Acceptance:

- old hold JSON still scores;
- invalid sentence/lane references are dropped with a warning;
- a parallel script cannot be described as having paid both consequences when only one is marked;
- an edited script makes the check stale;
- render metadata retains the checkpoints used by that render after later script edits.

### P0.3 Add a contrast thumbnail angle

Files include `studio/thumbnail.py`, `thumbnail_plan.md`, thumbnail API/UI types and tests.

Add `contrast` to the angle vocabulary: A versus B, expected versus actual, or before versus after. A contrast image
shows one readable difference; it is not permission to add two scenes plus four labels.

YouTube tests three thumbnails, so do not blindly increase output to four. For parallel and scorecard stories, generate
`contrast`, `reaction` and `mystery`; for other shapes, keep the current three-angle policy unless the planner selects
contrast in place of the weakest fit. Preserve historical `stakes` options with a read adapter.

Until lane rendering exists, contrast may use a deterministic two-zone thumbnail compositor. Once P1 lanes land, use
the same composition geometry. Store the option's angle and allow the creator to record a manual selected/winner key.
Do not infer a winning thumbnail or CTR from video views.

Acceptance:

- exactly three legal A/B/C options are saved;
- contrast remains readable at 168×94 and does not cover YouTube's duration badge;
- historical `stakes` thumbnails remain viewable;
- learning receives an angle only when the creator selected it or recorded a YouTube Studio winner.

### P0.4 Start daily observations

Write one idempotent observation per source/day during existing Buffer and YouTube refreshes. Include fixtures for a
retry on the same day, a later day, missing counters and both SQLite/Postgres migration paths. Do not expose a velocity
claim yet; P3 defines the calculation once enough days exist.

## P1: renderer primitives, glow, captions and open licensed SFX

### P1.1 Add semantic lanes

This is the highest visual-regression task. Add a shared `studio/composition.py` so ink, flat and film do not invent
three incompatible split systems.

The scene contract is semantic:

```json
{"kind": "single|lanes", "lanes": ["a", "b"], "divider": "none|line|vs"}
```

For ink beats, support explicit `lane a` / `lane b` modifiers. Do not reuse `left` and `right`: those are positions
inside a lane. `beats.clean()` canonicalizes lane IDs, scopes references and live objects per lane, and drops a lane
modifier outside a lane scene with a precise reason.

Geometry is code-owned:

- long 16:9 boards render A/B side by side;
- vertical boards stack A/B above one another and above the caption-safe area;
- camera movement is constrained while both lanes are visible;
- lane-local slots, collision handling, continuity and visual QA operate inside their viewport;
- switching back to `single` restores the full stage without changing legacy behavior.

Add editor controls for single/lanes, divider and line lane. Update storyboard prompts and cheat sheets with compact
examples. Render contact sheets for both aspect ratios and inspect every frame at normal and phone size.

Acceptance:

- a legacy storyboard serializes and renders identically;
- the same semantic lane plan produces side-by-side long and stacked vertical layouts;
- actors/props with the same ID in both lanes cannot leak state across lanes;
- no subject, label or caption crosses its safe viewport;
- ink, flat and film preview/render agree on lane geometry;
- native and Docker smoke renders remain inside the M1/8 GB operating envelope.

### P1.2 Add fixed data graphics

Add `studio/data_graphics.py` with code-rendered primitives: `metric`, `comparison`, `bars`, `gauge`, `cohort`, `clock`
and `scorecard`. The model selects a primitive and content; it never emits pixel coordinates.

Data cards live in the source-backed story map with a stable ID, exact displayed label/value/unit and source numbers.
Storyboard lines reference that ID. Unknown card IDs or cards with invalid citations are rejected rather than rendered.

Example contract:

```json
{"id":"trust_gap","kind":"bars","items":[{"label":"A","value":42},{"label":"B","value":68}],
 "unit":"%","source_numbers":[3]}
```

The renderer handles number formatting, scales, labels, colour contrast, lane fit and entrance animation. Values are
fixed for a render; there are no fake live counters.

Acceptance:

- every primitive renders in both aspect ratios and inside either lane;
- invalid/uncited card references are explained and omitted;
- labels remain readable at phone-thumbnail size;
- the fact-check/export path retains the source numbers behind displayed data.

### P1.3 Add the glow look and explicit caption mode

Add `glow` to `studio/looks.py`, API unions, UI options and style tests. It uses a dark neutral background, a restrained
cyan/magenta/gold palette and blurred under-strokes beneath the normal vector stroke. It reuses the same plan and
sprites. Photos, flags and maps are never recoloured; text contrast must pass the existing small-size checks.

Implement `caption_mode` as a storyboard override:

- `default`: today's behavior, Short on and long off;
- `on`: burn captions with a real horizontal or vertical safe layout;
- `off`: no burned captions, while SRT export remains available where applicable.

Refactor caption creation so long videos can instantiate a horizontal layout. Lane and data-graphic geometry must
receive the resolved caption safe area; captions must not be painted over the lower lane.

Acceptance:

- changing to glow never changes scenes or timing;
- raster/photo assets retain their original colours;
- long captions render within safe margins, and default renders remain unchanged;
- every combination of long/vertical, single/lanes and captions on/off has a focused layout test.

### P1.4 Replace weak synthesized effects with an approved open-audio library

Use **Openverse as the primary search API** and the **Wikimedia Commons Action API as fallback**. Do not call the
Freesound API directly in a commercial Clipper workflow unless the University Pompeu Fabra/Freesound team grants a
commercial API agreement in writing. Freesound's files may carry open licences while its API terms still restrict free
API use to non-commercial use.

Official references:

- [Openverse API](https://api.openverse.org/v1/) and
  [terms](https://docs.openverse.org/terms_of_service.html)
- [Openverse audio metadata](https://docs.openverse.org/meta/media_properties/catalog.html)
- [Freesound API terms](https://freesound.org/docs/api/terms_of_use.html)
- [Wikimedia API limits](https://www.mediawiki.org/wiki/Wikimedia_APIs/Rate_limits) and
  [Commons licensing](https://commons.wikimedia.org/wiki/Commons:Licensing)
- [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/deed.en)

Refactor the existing Openverse music transport into a shared authenticated client; do not duplicate networking and
rate-limit code. Put `OPENVERSE_CLIENT_ID` and `OPENVERSE_CLIENT_SECRET` in `.env` only. Anonymous access remains a
graceful fallback at its effective limit, but the UI/log must show the tier reported by `/v1/rate_limit/` or response
headers rather than assuming registration worked.

As checked on 2026-11-21, Openverse's anonymous tier reports 20 requests/minute and 200/day; the registered standard
tier is configured for 100/minute and 10,000/day. Treat those as discoverable limits, not constants. Wikimedia reads
allow 200 requests/minute for an identified compliant client at the current policy limit, but Clipper should stay far
below it and obey `Retry-After`.

Primary search policy:

```text
GET /v1/audio/?q=<effect description>&source=freesound&license=cc0,by
```

Do not add `category=sound_effect` for Freesound-backed Openverse records because that ingestion does not reliably set
the category. Never accept NC, ND, BY-SA, Sampling+ or an unknown licence. Openverse does not verify third-party
licensing, so the approval screen must show the upstream landing page and metadata and require creator approval before
download/use.

Create `studio/sfx_search.py` or a provider adapter in `studio/sfx.py`, a Sound Library UI and search/import jobs.
Normalize selected files locally, cache by provider ID/checksum, and map approved variants to the current semantic
events emitted by composers. A provider timeout, rate limit or dead URL logs the reason and uses the synthesized
fallback; it never fails an otherwise valid render.

Credits and evidence:

- CC BY credit includes title, creator, source page, licence/version/URL and an indication that Clipper trimmed/mixed
  the sound;
- retain the same provenance for CC0 even when public credit is optional;
- append required credits to YouTube description/export text and include a machine-readable sound manifest/checksum;
- add the Openverse product notice required by its terms to Settings/About;
- retain enough evidence to answer an erroneous Content ID claim.

Wikimedia fallback searches `filetype:AUDIO`, reads `imageinfo.extmetadata`, sanitizes its HTML, and locally whitelists
only CC0 and CC BY. Use the contact-bearing User-Agent already required by the project and keep concurrency at three or
less.

Acceptance:

- provider tests use fakes; test runs never call the network;
- non-commercial, share-alike, no-derivatives and unknown licences are rejected;
- secrets never enter the database, API response, job log or exported manifest;
- the same board/event deterministically selects the same approved local sound;
- attribution reaches description/export and fallback synthesis still renders offline;
- a human can audition, inspect source/licence and approve or reject every imported effect.

## P2: silent micro-Short tests and promotion

### P2.1 Add a 12–18 second silent form

`form=micro_short` means a vertical, silent concept test with designed visual timing. Keep renderer
`Storyboard.format=short` for compatibility, but label the form distinctly in API/UI/learning.

Use a dedicated writing/storyboard prompt with these constraints:

- one relatable situation, one hidden rule and two visible outcomes;
- at most two lanes and one object/event viewers can understand without audio;
- 12–18 seconds including the loop;
- minimal on-screen text rendered as planned `write`/data-card events, not normal word captions;
- the last visual returns cleanly to the first so replay does not feel like a hard reset;
- no unsupported research claim and no phrase/frame imitation of the reference channel.

Use the current word-number storyboard machinery through an explicit synthetic cue take:

- `VoiceTake.source="silent"`;
- a real silent 48 kHz WAV of the target duration so the existing mux/mix path remains valid;
- `timeline.json` contains designed cue tokens/sections, not Whisper output;
- alignment marks `mode="designed_cues"`, has no WPM and is never described as spoken alignment;
- the created storyboard defaults to `caption_mode=off`;
- on-screen dialogue/text is placed by the storyboard plan, while music/SFX are opt-in rather than silently assumed.

This deliberately reuses timing and beat validation without pretending silence is speech. Hold/learning use micro-specific
visual checkpoints, not narration WPM.

Acceptance:

- target duration outside 12–18 seconds is refused with a clear message;
- a micro-Short can be written, cued, planned, previewed and rendered without recording or TTS;
- no normal captions or WPM appear for a silent take;
- output contains a compatibility-safe audio stream even when the mix is silence;
- loop seam and first/last frames pass a contact-sheet review.

### P2.2 Add concept-test and promote actions with lineage

Add creator-triggered routes and UI actions:

- `POST /ideas/{id}/concept-test` creates a child `micro_short`, copying theme, premise and intended shape, with
  `parent_idea_id` and `lineage_kind=concept_test`;
- `POST /ideas/{id}/promote` creates a child `long` with `lineage_kind=promotion`;
- `GET /ideas/{id}/lineage` returns root, ancestors, descendants, form, publication state and available evidence.

Do not overload `inspired_by`; it remains the performance examples used to generate an idea. Explicit parent lineage
answers a different question.

Promotion carries the creative premise, shape and useful creator notes, but not a supposedly proven script. The long
must run fresh research, story map, writing, fact check, hold check, voice and storyboard stages. Never auto-promote on
a view threshold; show evidence and let the creator decide.

Acceptance:

- parent/root lineage remains queryable after either idea is edited or archived;
- a child cannot create a cycle;
- promotion cannot reuse a Short's research/map as if it covered a long claim set;
- the Ideas/Theme UI shows the chain and a direct link to each stage;
- deletion follows the existing `SET NULL` policy without deleting descendants.

## P3: trustworthy retention, velocity and cohort learning

### P3.1 Derive every known checkpoint, not only the first twist

Extend `studio/analytics.py` to interpolate retention for immutable render-time checkpoints:

- narrated forms: 5 seconds, promise, every consequence, twist, rehook and payoff;
- micro-Shorts: 1 second, first meaningful card/event, reveal/consequence and loop/payoff.

Return a list with checkpoint type, lane when relevant, render-time seconds, retention value and reason when missing.
Do not recompute times against an edited script. Keep interpolation arithmetic deterministic and label it as an
estimate between YouTube curve points.

### P3.2 Calculate seven-day velocity from observations

Define the measure before exposing it:

- prefer the observation closest to but not after day 7, within an explicit tolerance;
- subtract the first observation after publish, and report elapsed hours/days and source coverage;
- if a sample has not matured or lacks a baseline/day-seven point, return `unavailable` with the reason;
- do not substitute today's lifetime total or extrapolate a seven-day value from one observation;
- keep platform totals separate before showing an explicitly labelled cross-platform sum.

Add fixtures for day 0/day 7, delayed first observation, missing days, retry updates, multiple services and view-count
corrections.

### P3.3 Segment learning by what was actually rendered

Extend `studio/learning.py`, theme API models and `shared/theme-performance.ts` with comparable cohorts:

- `form`: long, normal Short and micro-Short never share a median;
- immutable `story_shape` from `render_meta`;
- duration band;
- selected/manual thumbnail angle when known;
- lineage: concept-test result beside the promoted long, not pooled as duplicates.

Retain the current minimum of three observations per group and six total for code-generated comparisons. Show count,
median and range, use non-causal wording, and return “not enough comparable videos” instead of asking an LLM to fill
the gap. Creator-entered thumbnail winners are evidence of the YouTube Studio test only; if no winner was recorded,
the angle comparison stays unavailable.

Acceptance:

- long, Short and micro-Short medians are always separate;
- story-shape/angle comparisons use successful render snapshots, not current mutable fields;
- underpowered groups produce no recommendation;
- lineage view shows whether a promoted premise travelled, without claiming the Short caused long-form performance;
- no UI or prompt fabricates CTR, impressions or swipe-away data.

## Production rollout after the code lands

The channel recommendation is an experiment, not a guarantee:

1. Create an original work/money/digital-life theme with the promise “Same start. One hidden rule. Two outcomes.”
2. Publish ten narrated comparison Shorts first. This tests the editorial and lane system before silent timing adds a
   second variable.
3. Review visual QA, creator quality notes, seven-day views and available retention checkpoints. Fix production defects
   before increasing volume.
4. Publish ten silent 12–18 second concept tests. Promote only creator-selected premises with comparable evidence.
5. Produce three 7–9 minute longs over the first month. Use two lanes, a shared clock, research that changes the plot,
   rehooks roughly every 30–45 seconds when the story earns them, and an emotional callback.
6. Generate a contrast thumbnail for each eligible long and run YouTube's own three-option test. Record its winner
   manually.
7. Reuse Buffer for TikTok, Instagram and YouTube Shorts after creator review. Keep platform evidence separate.

Target cadence after quality is stable is five Shorts per week and one long every 7–10 days. Do not encode that cadence
as a universal product rule. Do not build a 3D renderer for this experiment; the differentiator is editorial contrast,
timing and evidence, not rendering complexity.

## Orchestration and session assignments

### Branch and handoff discipline

- The orchestrator starts from `main@v2.6.0`, synchronizes `develop` in a controlled worktree, and gives every session
  an isolated feature branch/worktree. The current local `develop` was checked out elsewhere and behind during the
  release; do not assume it is the implementation base without checking.
- Reserve migration numbers before parallel work. Only one session owns each migration.
- Each session reads `CLAUDE.md` and this plan, checks `git status`, preserves unrelated/untracked files, and edits only
  its owned scope.
- A session runs focused tests while working and `make test lint` plus the Node 24 frontend build at its gate.
- The orchestrator reviews every diff, compatibility path, migration and test result before merging. No implementation
  session tags, releases, deploys or publishes.

### Model allocation

Use Terra High as the default implementation model; it is the best quality/usage tradeoff for bounded repository work.
Reserve Sol High/XHigh for contracts where semantic mistakes or renderer architecture are expensive. Use Luna Medium
for mechanical Angular wiring, documentation and exhaustive compatibility fixtures after a contract is fixed. The
orchestrating Sol Extra High session owns sequencing, reviews and integration rather than writing every feature itself.

| Session | Owned scope | Model and effort | Depends on |
|---|---|---|---|
| 0 | Sync implementation base, reserve migrations, write shared compatibility checklist | Sol Extra High orchestrator | `v2.6.0` |
| 1 | Strategy/lineage/caption fields, compatibility adapters, metric snapshot migration and writers | Terra High | 0 |
| 2 | Story-shape v2 contract, four prompt paths, stale-input rules and prompt tests | Sol High | 1 |
| 3 | Hold consequence/payoff checkpoints and immutable render times | Sol High | 2 |
| 4 | Ideas/Script Angular and API wiring for form, shape and lineage read models | Luna Medium | 1–2 |
| 5 | Contrast thumbnail angle, compatibility for `stakes`, manual result recording | Terra High | 2 |
| 6 | Shared composition geometry and lane-aware ink/flat/film renderers | Sol XHigh | 2–3 |
| 7 | Fixed source-backed data graphics and storyboard editor controls | Terra High | 6 |
| 8 | Glow look, explicit captions, safe-area/layout tests | Terra High | 6 |
| 9 | Shared Openverse client, sound schema/provider adapters, licence filters and mocked API tests | Terra High | 1 |
| 10 | Sound Library UI, approval, deterministic local mixing, credits/export and offline fallback | Terra High | 9 |
| 11 | Silent cue take, micro-Short prompts/render path and loop QA | Sol XHigh | 3, 6–8 |
| 12 | Concept-test/promote routes, lineage UI and fresh-research guards | Terra High | 4, 11 |
| 13 | Retention checkpoints, seven-day velocity arithmetic and cohort learning | Sol High | 1, 3, 5, 12 |
| 14 | Theme Performance UI, evidence reasons and lineage drilldown | Luna Medium | 13 |
| 15 | Cross-feature regression, SQLite/Postgres upgrade tests, Docker/native smoke renders, docs audit | Terra High | all |
| 16 | Final diff/security/licence/originality review and release recommendation | Sol Extra High orchestrator | 15 |

Safe parallel waves:

1. Session 1 only, because it establishes migrations and shared types.
2. Sessions 2, 5 and 9 can run on disjoint files after Session 1; Session 4 begins when Session 2's API contract is fixed.
3. Sessions 3 and 10 can run in parallel. Session 6 begins after the story/hold contracts are stable.
4. Sessions 7 and 8 can run in parallel after lane geometry lands.
5. Sessions 11 and then 12 are sequential; Session 13 may prepare snapshot arithmetic earlier but integrates after 12.
6. Sessions 14–16 are the integration wave.

### Required handoff from every implementation session

Return:

- branch and commit hash;
- files and migrations changed;
- compatibility decisions and any plan deviation;
- focused and full verification commands with results;
- screenshots/contact sheets for visual work;
- known gaps, follow-up risks and exact next dependency.

The orchestrator rejects a handoff that only says tests passed without naming the commands, that changes a contract
owned by another session without coordination, or that adds provider content without auditable licence metadata.

## Final completion gate

The full program is complete only when:

- a legacy 2.6.0 database upgrades on SQLite and Postgres and old videos still render;
- all four story shapes can create narrated Shorts and longs;
- a parallel plan renders responsive lanes, fixed data graphics and glow with captions on or off;
- approved Openverse/Commons effects mix locally with correct attribution and synth fallback;
- a 12–18 second silent concept test loops and can be promoted into a freshly researched long;
- learning shows checkpoint retention and mature seven-day velocity by comparable form/shape cohorts;
- no unavailable metric is inferred, no licence-unsafe result is importable, and no reference creator's assets or
  phrasing are copied;
- `make test lint`, the Node 24 production build, native smoke renders and Docker smoke renders all pass;
- the creator reviews representative outputs at full resolution and phone size before any release.
