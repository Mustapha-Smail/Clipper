---
title: Write a silent micro concept test
step: Script
task: script_write
used_in: clipper/studio/script.py
about: Writes a designed visual cue sheet for a 12–18 second silent concept test.
variables: plain
var.plain: The plain-language rules for the audience chosen in Settings.
---
You write a silent vertical concept test for an original faceless animated channel. This is a visual cue sheet, not
narration: nobody reads it aloud. The finished loop lasts 12–18 seconds including its return to the opening image.

{plain}

Return exactly one section with exactly these five non-empty lines, in this order and with these labels:

SETUP: one relatable situation with one object or event that is understandable without audio
RULE: the hidden rule made visible through action, a symbol or one very short planned text phrase
OUTCOME A: the first visible consequence
OUTCOME B: the contrasting visible consequence from the same start and shared clock
LOOP: the opening state restored cleanly so replay does not feel like a hard reset

The two outcomes must be comparable, not two unrelated anecdotes. Use no more than two visual lanes. Keep any planned
on-screen words to the minimum needed; they will be drawn as storyboard `write` or cited data-card events, never as
normal captions. Use only claims supported by the research notes and story map. If the desired contrast is unsupported,
make the rule and outcomes modest enough to remain true. Do not imitate any reference creator's phrases, drawings,
characters, framing, music, title pattern or individual shots. Copy no frame-level composition.

The `<story_map>` is the source-backed contract. Preserve its promise, supported outcomes, cited data cards and loop.
Do not add numbers, names or factual claims absent from the research notes. The section heading should be “Visual loop”,
and `short` must be true.
