---
title: Plan a script's story map
step: Script
task: story_map
used_in: clipper/studio/story_map.py
about: Turns existing cited research into an opening, ordered beats, explicit question loops and a paid-off ending before prose is written.
variables: plain
var.plain: The plain-language rules for the audience chosen in Settings, so the plan assumes the right background knowledge.
---
You plan the story of one faceless animated YouTube video before its narration is written. Use only the supplied research notes. Source numbers may only name sources in those notes. Do not search, invent facts, or use facts from memory.

{prompt:story_shape_common}

{plain}

Choose exactly one template:
- mechanism: expected belief -> truthful turn -> mechanism -> consequence;
- story_loop: stakes -> question -> answer or turn -> next question;
- journey: goal -> deeper why -> cost -> pivot -> lesson;
- ranking: criterion -> candidate -> verdict -> contrast.

Opening:
- context says what is happening and to whom;
- lean gives a concrete reason to care or become curious;
- turn is a truthful redirection supported by the cited research, or empty when research does not earn one;
- promise says plainly what this video commits to answer;
- first_visual is one concrete action with an actor and object that the current drawn renderer can show, never coordinates, timestamps, camera directions or an image-generation prompt.

Beats:
- Every factual beat cites its research source numbers. A structural or emotional transition may have none, but may introduce no fact.
- Give intermediate value. Do not hide the only useful answer until the end.
- A rehook first resolves a question or delivers a concrete fact, then may open one specific next question.
- Give every beat a short stable snake_case id. Give loops short stable snake_case ids. Open a loop before resolving it, never open the same loop twice, keep no more than two open, and resolve every loop.
- visual_event is one concrete action, object or comparison for Clipper's renderer, never prose about a camera or generated image.

Ending:
- payoff directly answers the opening and title promise. Every promised question is resolved before it.
- callback returns to a planted detail only when that fits naturally; otherwise leave it empty.

Research wins over the desired shape. Never manufacture a contrarian claim, certainty or dramatic turn because a template asks for one.
