---
title: Parallel story-shape rules
step: Script
task: story_map
used_in: clipper/studio/story_map.py
about: Instructions for the same-start, two-lane story shape.
variables:
---
{prompt:story_shape_common}

For `parallel`, output `parallel` metadata with one non-empty shared_start, one non-empty shared_clock and exactly two
named lanes. Each lane has a safe id, label, differing rule and a promised consequence. Beats must name their lane and
checkpoint. Use the same checkpoint ids in both lanes at least twice so the outcomes remain comparable. End with a
supported comparison or callback that pays both lane consequences. Two unrelated anecdotes that merely alternate are
not a parallel story.
