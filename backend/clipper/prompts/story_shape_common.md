---
title: Common story-shape rules
step: Script
task: story_map
used_in: clipper/studio/story_map.py
about: Rules shared by every canonical story shape.
variables:
---
The research wins over the desired shape. Use only supplied notes and numbered sources; never manufacture certainty,
an opposing outcome, a dramatic reveal or a contrast that the evidence does not support. A shape is a planning aid,
not permission to make a claim sound stronger than it is.

Build an original production system, not a copy of a reference creator. Do not imitate wording, title phrases,
characters, illustrations, music, thumbnail composition, shot order or frame-level style.

`format` remains the orthogonal single-or-list choice. `form` remains the long, short or micro_short runtime choice.
`story_shape` is exactly one of parallel, explain, scorecard and investigation.

Only `parallel` may use the top-level `parallel` object or beat fields `lane` and `checkpoint`. For explain, scorecard
and investigation, omit that object and leave every beat's lane and checkpoint absent/null even when the response
schema offers them.

Before answering, trace the loop IDs in beat order: each `resolves` ID must already be open, each `opens` ID becomes
open after that beat, and the active set must be empty after the final beat. Do not rely on the ending payoff to close
a loop implicitly; the resolving beat must name the loop ID in `resolves`.
