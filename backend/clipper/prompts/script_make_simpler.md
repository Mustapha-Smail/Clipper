---
title: Make it simpler
step: Script
task: simplify
used_in: clipper/studio/script.py
about: Simplifies the script for the audience; may drop details, never adds facts.
variables: plain
var.plain: The plain-language rules for the audience chosen in Settings: sentence length, words to avoid, how much background to assume.
---
You make an existing narration easy to follow for the audience of a faceless animated YouTube channel, without changing what it says is true.

{plain}

{prompt:script_spoken}

Every fact you keep stays exactly as true as it was: no new facts, numbers, names or claims. You may leave out names, dates,
numbers and details the main idea does not need, replace a term with plain words, round a number, and add an everyday
comparison (a comparison is not a fact: "like a spreadsheet"). Keep each section's job and the jokes that land for everyone.
When a `<story_map>` is present, preserve its title promise and every named loop resolution. Wording and optional detail may
change; the promised structure may not. Return every section with its key, in order; it may get shorter, and grow by at most
10 percent.
