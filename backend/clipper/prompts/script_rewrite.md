---
title: Rewrite a section
step: Script
task: rewrite
used_in: clipper/studio/script.py
about: Rewrites one section on request, keeping the writer's rules.
variables: 
---
{prompt:script_write}

You are rewriting one section of an existing script following the creator's instruction. Keep its facts, keep the voice of
the rest of the script, and return only the new narration for that section. The `<story_map>` is the contract: preserve the
title promise and every loop this section must open or resolve. Wording may change; the promised story job may not. Facts
still come only from `<research_notes>`.
