---
title: Thumbnail picture (ink)
step: Publishing
task: thumbnail
used_in: clipper/studio/thumbnail.py
about: How to describe an ink thumbnail's picture; filled into the thumbnail prompt.
variables: cast, expressions, poses, props, shown
var.cast: The characters available, by name.
var.expressions: The facial expressions an actor can have on a thumbnail.
var.poses: The still poses an actor can hold on a thumbnail.
var.props: The props this video already uses.
var.shown: The flags and archive photos of this video that may appear on a thumbnail (empty when there are none).
---
The picture is drawn in the video's ink style from beat lines, one per line, in the order things appear, without word numbers. Write 2 to 5 lines:
  actor NAME [SLOT] [POSE] [EXPRESSION] [SIZE]     a person: one of {cast}
  do NAME POSE [TARGET] [EXPRESSION]               a person takes a pose, reaching for TARGET
  draw PROP [SLOT] [SIZE]                          a prop of this video: {props}
{shown}  arrow FROM TO | circle THING [COLOUR] | underline THING | cross THING | tick THING
Slots: left, center, right. Sizes: small, medium, large. Colours: warm, bad, good, gold.
Poses: {poses}. Expressions: {expressions}.
Use only the people, props, flags and pictures listed: they are the video's own. Set shot to 0.
Example:
  actor narrator left surprised
  draw wall_calendar right large
  do narrator point wall_calendar surprised
  circle wall_calendar bad
