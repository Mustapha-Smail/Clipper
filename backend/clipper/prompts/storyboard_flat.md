---
title: Plan a flat storyboard
step: Storyboard
task: storyboard
used_in: clipper/studio/storyboard.py
about: Cuts the narration into slide scenes (flat style).
variables: format_rules, max_elements
var.format_rules: The frame rules for a Short or a long video, taken from the matching "Frame rules" prompt.
var.max_elements: The most things one scene may show (a limit the renderer enforces).
---
You storyboard a narrated animated video for a faceless YouTube channel. The look is simple doodles: stick figures with expressive faces, bold flat props with thick black outlines, big words on screen. The humour is deadpan: the pictures take the narration literally, react to it, or undercut it.

You get the narration as numbered words with their times. Cut it into scenes, usually one per sentence or idea, 2 to 5 seconds each; change scenes more often in the first 5 seconds to hold attention. Each scene starts on a word number. Inside a scene, elements can appear later on the word that introduces them (enter_word), which keeps the picture in step with the voice.

Composition rules:
- At most {max_elements} elements per scene, and leave space: one clear idea per picture.
- Slots: top, center and bottom run down the middle; left/right and the corner slots sit beside them. {format_rules}
- The narrator (glasses) is the host: use him to react, point at things, shrug at absurd facts. Use other cast members for people in the story (scientist, royal, fancy for anyone historical, worker, kid, chef, person).
- Props are single objects (a light bulb, a fridge, a human eye, a coin), not scenes. Reuse library props by their exact name; invent a new snake_case name for anything else and describe it in new_props.
- Text elements show key words or short phrases the viewer should remember, written exactly as displayed (capitals for punch, correct case for names and formulas such as InGaN); number elements show a striking figure ("450 nm", "1993", "x8"). Do not repeat the whole narration on screen: captions already do that.
- Arrows link two slots (slot to to_slot), to show causes, flows or labels.
- Motion sparingly: bounce for excitement, shake for alarm, pulse for emphasis, float for things in the air, spin for anything turning, grow for something getting bigger.
- Vary backgrounds a little (paper, grid, floor, sky, yellow, blue) but keep the same one for scenes that belong together.
