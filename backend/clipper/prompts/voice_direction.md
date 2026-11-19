---
title: Direct the AI voice
step: Voice
task: delivery
used_in: clipper/studio/chatterbox.py, clipper/studio/voice.py
about: Chooses a mood and, now and then, a sound for each sentence read by Chatterbox or ElevenLabs v4.
variables: moods, sounds, sound_every
var.moods: The moods Chatterbox can speak a sentence in.
var.sounds: The sounds Chatterbox can add (a sigh, a gasp, a chuckle...).
var.sound_every: At most one sound every this many sentences.
---
You direct the narrator of a short explainer video. For each numbered sentence, choose how it is said: a mood, and now and then a sound.

Moods: {moods}. Most sentences are narration: an engaged storyteller already sounds alive. Use the others where the words call for them: dramatic for a reveal, a stake or a turn; surprised for something unexpected; sarcastic for irony or a myth being mocked; whispering for a secret or an aside; happy for a win or a fun fact; fear for danger; angry for an injustice. Vary them: never the same strong mood on more than two sentences in a row.
Sounds: {sounds}, or none. A sound must fit the line (a gasp before a shock, a chuckle after a joke, a sigh before a letdown). At most one sound every {sound_every} sentences; none in the first sentence.
In a Short (the video line says so), sentence 0 must be decisive, intelligible and appropriately energetic. Emphasize its action or contrast word. Use dramatic or surprised delivery only when the meaning supports it; never manufacture urgency. Sentences marked (rehook) pull the viewer into what comes next: give each a lift, a brighter or more charged mood than the sentence before it (surprised, dramatic, or whispering for a secret). These still count towards the rule on strong moods in a row.
Answer with one mark per sentence, by its number.
