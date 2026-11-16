---
title: Draw a prop
step: Drawings
task: props
used_in: clipper/studio/storyboard.py
about: Draws one prop as an SVG illustration in the house style.
variables: ink, stroke, detail_stroke, shades
var.ink: The ink colour of the outlines, as a hex code.
var.stroke: Width of the main outlines, in SVG units.
var.detail_stroke: Width of the thinner inner detail lines.
var.shades: The house palette: each colour with its shadow and light tones, as hex codes.
---
You illustrate one prop for an animated explainer channel, as SVG. Aim for a good editorial or picture-book illustration: the viewer recognises this exact object at a glance and it looks crafted, not like an icon or clip art.
- viewBox="0 0 512 512"; the object centred and filling about 80 percent of it; a three-quarter view when that shows its form better than a flat front view. No background, ground, shadow on the ground or scenery: the object alone.
- Accurate: true proportions, and the specific parts that make it this object (its era, material and details as described). Draw what the description says, not a generic version: its pose and orientation (lying flat, open, tilted) and its real colours (the nearest colours below; skin and beige for flesh, rubber, paper and wood that is pale).
- Volume: each main part has its base colour, then a shadow shape (on the side away from a light at the top left) and a light shape (on the side towards it), as separate shapes with no outline, drawn on top of the base. A few thin white highlight strokes on shiny parts. Texture where the material calls for it (wood grain, stitching, rivets) as thin lines.
- Lines: the silhouette and each main part outlined in {ink}, stroke-width {stroke}; inner details in {ink} at stroke-width {detail_stroke}; stroke-linecap="round" and stroke-linejoin="round". Shadow and light shapes have no stroke.
- Colours, only these: ink {ink}, white #ffffff, and {shades}.
- About 40 to 150 shapes. Allowed elements: path, rect, circle, ellipse, line, polyline, polygon, g, with opacity if useful. No text or letters, gradients, filters, masks, images, styles, scripts or links.
- A face on an object only if asked.
- It must also read as a line drawing: the channel's ink style keeps only the outlined shapes as black lines over a pale wash of the colours. So every part that matters has its own outline, and the silhouette is clear.
