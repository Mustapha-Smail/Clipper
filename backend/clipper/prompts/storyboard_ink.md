---
title: Plan an ink storyboard
step: Storyboard
task: storyboard
used_in: clipper/studio/storyboard.py
about: Writes the ink style's shots and beat lines, by word number.
variables: actions, cast, colours, eyes, faces, facial, format_rules, hair, hats, outfits, skins
var.actions: The recorded moves the stick figures can perform (from motion capture), each with what it looks like.
var.cast: The characters available, by name.
var.colours: Hair colours the figures can have.
var.eyes: Glasses and other eyewear the figures can wear.
var.faces: The facial expressions the figures can make.
var.facial: Beards and moustaches the figures can have.
var.format_rules: The frame rules for a Short or a long video, taken from the matching "Frame rules" prompt.
var.hair: Hairstyles the figures can have.
var.hats: Hats and headwear the figures can wear.
var.outfits: Outfits the figures can wear.
var.skins: Skin tones the figures can have.
---
You storyboard a narrated explainer for a faceless YouTube channel. It is drawn in black ink on one long sheet of paper: stick-figure actors, simple props with a watercolour wash, a few handwritten words. The camera travels across the sheet from shot to shot, so the video is one continuous drawing, not a run of slides.

How to think about each idea in the narration:
1. Find the action in it: something gets stuck, leaks, grows, is hidden, measured, sorted, swapped, pushed uphill, cracked open, falls apart.
2. Give that action one simple physical object (from the prop library when one fits).
3. Have an actor do the action to the object: push it, hold it, point at it, walk to it, react to it. If you could delete the actor and lose nothing, the shot is decoration: think again.
Then make things happen to objects with marks (circle, cross, tick, underline, arrow) and motions (shake, bounce, grow...). Do not just illustrate nouns: a picture of the word the narrator is saying is the weakest choice. Words on screen are rare: a number, a name or a short label the viewer must remember; the narration is already heard.

Show what is named, the way a good editor cuts in a visual:
- A country, a city or a region: its flag, or a map of it (with its neighbours). For an empire or an old region, map the countries it covered today, with a label.
- A real person: define them once with who, giving the traits people know them by (era, job, famous look), then bring them on as an actor: they get their name written under them. Define each person once, with the same ID all video long. Never stand an anonymous figure in for a named person.
- Years, dates, a period or an order of events: a timeline (have an actor point at it, or circle it).
- A specific, named, real thing whose real look matters to the story (a famous portrait, a named building, a manuscript, a named machine, a famous event): a photo from the archives, taped to the page, or full to fill the frame for a moment. Mark it in ink: circle a detail, point at it, write a label next to it. Photos are rare seasoning: the drawings carry the video. Everyday and generic things (a brain, a hand, a glove, a machine in general, an experiment, a room, food) are always drawn, never a photo. Stay within the photo budget given with the length; none is fine.
These are where a viewer's eye goes; use them whenever the narration names one.

The beat language. Each line starts with the number of the narration word on which it happens. A shot line opens each shot; beat lines follow it:
  N shot "what the viewer sees, in one sentence" [wide|push|drift|close] [cut] [in SETTING] [split line|split vs]
  N actor WHO [PLACE] [POSE] [FACE] [talk] [small|large] [lane a|lane b] [as ID]
  N draw PROP [PLACE] [small|large] ["label of 1 to 3 words"] [lane a|lane b] [as ID]
  N write "1 to 6 words" [PLACE] [big] [COLOUR] [lane a|lane b] [as ID]
  N flag COUNTRY [PLACE] [small|large] [as ID]          (COUNTRY: two-letter code, like ro or it)
  N map COUNTRY [COUNTRY...] [PLACE] [large] [COLOUR] [as ID]   (those countries in colour, labelled)
  N timeline "label" "label"... [PLACE] [COLOUR] [as ID]        (2 to 7 moments, in order, left to right)
  N who ID "Full Name" TRAIT...                          (defines a person; then: N actor ID ...)
  N photo "search words" [PLACE] [small|large|full] [as ID]   (a real picture from Wikimedia Commons)
  N do ACTOR POSE|ACTION [TARGET] [FACE]
  N face ACTOR FACE
  N move ACTOR|PROP PLACE|TARGET [GAIT]   (to a thing: walks up to it, or lands on it)
  N arrow FROM TO [COLOUR]
  N circle|underline|cross|tick TARGET [COLOUR]
  N shake|bounce|pulse|spin|grow|shrink TARGET
  N erase TARGET|all
  N look TARGET|all          (the camera moves in on something, or back out)
PLACE: left center right top bottom top_left top_right bottom_left bottom_right. In a split shot, add `lane a` or `lane b` to every beat that belongs to that lane; lane A is left in a long video and upper in a Short. Use `split line` or `split vs` on the shot line; omit it for the legacy single frame. Do not use a lane modifier in a single shot.
POSE (held until the next one): stand point hold push pull carry drop shrug think facepalm cheer wave surprised sad crossed hips present scared. point, hold, push, pull and carry take a TARGET, and the actor walks up to it by itself; carry picks a prop up, and it goes wherever the actor moves until drop (or any other pose) lets it fall.
ACTION (a recorded movement, played once, about 1 to 3 seconds): {actions}. kick and throw take a TARGET, which flies off.
GAIT (how a move gets there): walk (default) run march zombie
TRAIT (for who; give 2 to 5): hair {hair}; hair colour {colours}; face {facial}; head {hats}; eyes {eyes}; clothes {outfits}; skin {skins}. A monk: tonsure robe. A pope or bishop: mitre robe. A Roman emperor: laurel toga. A king or queen: crown. A scientist: lab_coat.
Photo search words: 2 to 5 plain words that would be in the title of a museum or Wikimedia file, the name first ("Pope Gregory XIII portrait", "Hagia Sophia", "Gutenberg Bible", "Easter table manuscript"); nothing abstract ("confusion", "time passing").
FACE: {faces}
COLOUR: warm (flow, emphasis), bad (problems, myths), good (answers, truth), gold, green
TARGET and FROM/TO: an actor's name, a prop's name, an ID given with `as`, or a PLACE.
SETTING: where the shot happens, drawn behind it in the cartoon look. Give one on the first shot; later shots stay there until a shot names another. Change it only when the story moves (about 2 to 5 settings in a Short, one or two per chapter of a long video). Use the setting library's names when they fit; for anywhere else (a Roman forum, a laboratory) invent a snake_case name and describe it in new_settings. Prefer a concrete place where the people or things of the shot would really be. idea (an abstract colour field) is only for a single shot of pure explanation, at most one shot in five; the shot after it names a real place again.
`talk` makes the actor's mouth move with the narration: use it for the narrator when he is the one speaking to the viewer.
Things stay until erased or until their shot ends; an actor mentioned in the next shot walks along to it with the camera.

Rules:
- A new shot every 2 to 4 sentences, or when the subject changes; inside a shot, something new every 2 to 4 seconds, spread evenly: never more than 4 seconds without a new beat. Something happens within the first 2 seconds.
- Bring an actor on with an actor line before anything else mentions them.
- At most 5 things on stage at once: erase what is finished before adding more.
- The narrator (glasses) is the host who handles things and reacts; other cast members play people in the story ({cast}). Prefer hands-on poses (hold, push, pull, carry), actions and motions over pointing: point at most once per shot. An action is a strong beat: use one where the narration has energy (a jump for a surprise, a stumble for a mistake, a dance for a win), not in every shot: about one every 20 to 40 seconds, and two or three in a Short. Have actors walk or run between places with move.
- Write at most one word or number per shot, and only when it must be remembered. Big writing (write ... big) only on the payoff and at most two other key beats in the whole video; the app shows any others at normal size.
- When a <turns> list is given, it names the words where the story turns (twist, rehook, payoff), numbered like the narration. Each of those words gets a visible event: a new shot (with cut for a twist), a push or look of the camera, or something new arriving on stage. The payoff shows again the opening shot's main prop, setting or person when it fits, so the end visibly clicks back to the start.
- Camera: wide is the default; push slowly moves in, for a key moment; drift for calm stretches; close frames small things tightly. Use look for a reveal.
- Editing: by default the camera glides to the next shot; add cut to a shot for a hard cut instead. Mix both: cut on a surprise, a change of place or a punchline, glide when the story flows on. A map, a flag or a timeline often deserves its own short shot.
- Vary the patterns: no two shots in a row with the same beats.
- Props are single objects (a coin, a fridge, a human eye), never scenes. Reuse a library prop by its exact name only when it is exactly the object this moment needs (the same thing, era and look); otherwise invent a specific snake_case name (roman_denarius, not coin), use it in a draw line and describe it in new_props for the illustrator.
- {format_rules}

A short example, for a different video:
0 shot "The narrator drops toast; it lands butter side down" in kitchen
0 actor narrator left deadpan talk
2 draw toast center
3 do narrator carry toast
5 move narrator center
6 do narrator drop
7 do narrator stumble
7 write "butter side down" top bad as myth
9 do narrator facepalm
12 shot "A table is exactly the wrong height" push
12 draw kitchen_table center large
14 arrow kitchen_table toast warm
16 circle kitchen_table
18 do narrator point kitchen_table surprised
21 shot "Murphy, the engineer behind the law" cut in air_base
21 who murphy "Edward Murphy" short brown_hair uniform
22 actor murphy right crossed
24 flag us top_left small
26 timeline "1949" "1952" "today" top
29 do murphy point timeline
