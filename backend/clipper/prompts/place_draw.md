---
title: Draw a place
step: Drawings
task: places
used_in: clipper/studio/places.py
about: Draws one cartoon background; the code fills in the frame's size and where the camera and actors are.
variables: width, height, shape, floor, left, right, top, actors_left, actors_right, outline
var.width: The drawing's width in SVG units (wide picture for long videos, tall one for Shorts).
var.height: The drawing's height in SVG units.
var.shape: Says whether this is a wide picture or a tall one for vertical Shorts.
var.floor: The height (y) where the floor or ground meets the wall or horizon; the characters stand on it.
var.left: Left edge (x) of what the camera usually shows.
var.right: Right edge (x) of what the camera usually shows.
var.top: Top edge (y) of what the camera usually shows.
var.actors_left: Left edge (x) of the space kept free for the characters.
var.actors_right: Right edge (x) of the space kept free for the characters.
var.outline: The ink colour of every outline, as a hex code.
---
You draw one background (a place) for a cartoon explainer channel, as SVG. House style:
- viewBox="0 0 {width} {height}" ({shape}). The floor or ground meets the wall or horizon at y={floor}; below it is floor or ground only, with low, flat details (a rug, tiles, a path, grass).
- The camera mostly shows x {left} to {right} and y {top} to {height}: put the place's best details there, on the wall or horizon between y {top} and {floor}. Beyond it, continue the place more simply, for when the camera moves.
- Characters stand in front, in the middle (x {actors_left} to {actors_right}): nothing tall or busy there, just wall, sky or a calm background.
- Flat colours, soft and slightly muted so colourful characters stand out; every shape outlined in {outline}, stroke-width 4, round joins. Depth from overlapping shapes and darker tones.
- Rich but tidy: 60 to 150 shapes. A clear sense of place and era.
- Allowed elements: path, rect, circle, ellipse, line, polyline, polygon, g. No text, no people, no gradients, filters, masks, images, styles, scripts or links.
