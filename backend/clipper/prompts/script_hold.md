---
title: Check how a script holds
step: Script
task: hold
used_in: clipper/studio/hold.py
about: Labels each sentence of a script with its role (hook, stakes, twist...); the hold score is arithmetic on those labels.
variables: 
---
You read the narration of a faceless animated YouTube video and label what each sentence does for a viewer deciding whether to keep watching. You do not judge or rewrite anything; the app does the counting.

You get the title, the kind of video and the sentences, numbered from 1 under their section headings. When a current `<story_map>` is present, use its opening promise and named loops as the contract. Without one, audit the title and narration alone. Give every sentence exactly one role, by its number:

- hook: opens the video by naming the topic, an action or a surprising claim.
- context: background the viewer needs (who, where, what it is).
- stakes: why it matters to the viewer, or what it cost or risked someone.
- question: asks or sets up a question the video will answer.
- twist: overturns what the viewer expected, or reveals something surprising.
- rehook: a line whose job is to pull the viewer into what comes next ("But here's the strange part", "And that's not even the weird bit").
- fact: explanation, an example or a detail that moves the story forward.
- consequence: pays a named outcome for one lane or rule (use the lane only when the story map names it).
- payoff: delivers the answer to the video's main question.
- callback: refers back to something planted earlier (an object, a person, the opening scene).
- filler: says nothing new: a recap of what was just said, a stock transition, a vague aside.

Pick the role that best describes the sentence's main job; if two fit, prefer the rarer one (twist, rehook, stakes, payoff, callback over fact or context). Mark `title_question` true only for a question sentence that asks the title's own question, in any wording.

Also give the topic the title is about, as one word or a short phrase taken from the title (for "Why your fridge never makes cold", the topic is "fridge").

Audit the promises without rewriting them:

- `loops`: each concrete question the narration opens, with a short safe ID, the question, its opener sentence, its resolver sentence or null, and `main` true only for the title/opening promise. A sentence may resolve one loop before opening the next.
- `rehooks`: each rehook sentence, whether that sentence delivers specific new information, the loop ID it opens or null, and a short reason. A vague phrase such as "here is the strange part" does not deliver information by itself.
- `promise`: `paid`, `partial` or `missing` for the title/opening promise, with explicit `promise_sentence`,
  `consequence_sentences` (each with a sentence number and optional lane; one for each named parallel lane),
  `payoff_sentence`,
  and a short reason. Every referenced sentence must exist. The promise sentence comes first, each consequence
  follows it, and the payoff follows every consequence. Use `lane` only for a named parallel lane; do not claim
  both parallel consequences are paid when only one is marked.

Do not invent a loop just because a sentence is interesting. A loop is a concrete question a viewer can understand. The main promise is not also a secondary loop.

Label every sentence once, with numbers that exist; never invent sentences.
