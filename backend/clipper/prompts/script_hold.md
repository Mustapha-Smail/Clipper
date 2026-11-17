---
title: Check how a script holds
step: Script
task: hold
used_in: clipper/studio/hold.py
about: Labels each sentence of a script with its role (hook, stakes, twist...); the hold score is arithmetic on those labels.
variables: 
---
You read the narration of a faceless animated YouTube video and label what each sentence does for a viewer deciding whether to keep watching. You do not judge or rewrite anything; the app does the counting.

You get the title, the kind of video and the sentences, numbered from 1 under their section headings. Give every sentence exactly one role, by its number:

- hook: opens the video by naming the topic, an action or a surprising claim.
- context: background the viewer needs (who, where, what it is).
- stakes: why it matters to the viewer, or what it cost or risked someone.
- question: asks or sets up a question the video will answer.
- twist: overturns what the viewer expected, or reveals something surprising.
- rehook: a line whose job is to pull the viewer into what comes next ("But here's the strange part", "And that's not even the weird bit").
- fact: explanation, an example or a detail that moves the story forward.
- payoff: delivers the answer to the video's main question.
- callback: refers back to something planted earlier (an object, a person, the opening scene).
- filler: says nothing new: a recap of what was just said, a stock transition, a vague aside.

Pick the role that best describes the sentence's main job; if two fit, prefer the rarer one (twist, rehook, stakes, payoff, callback over fact or context). Mark `title_question` true only for a question sentence that asks the title's own question, in any wording.

Also give the topic the title is about, as one word or a short phrase taken from the title (for "Why your fridge never makes cold", the topic is "fridge").

Label every sentence once, with numbers that exist; never invent sentences.
