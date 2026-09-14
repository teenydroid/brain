---
name: video-script
description: How Tiny writes a short comic video script for a text-to-video model. Sketch structure, beat timing, the visual gag, and the renderability checks. Apply whenever a clip is being scripted.
category: Voice
---

# Video script

A clip of yours is a ten-second sketch: one setting, one gag, one payoff. The
model that films it cannot hear a joke, cannot read a caption, and cannot hold
more than a couple of moving things in its head. So the joke has to be a thing
that happens, and the writing has to be a shot list, not prose.

The humor skill covers what makes something funny. This covers how to put it
on a timeline a video model can actually render.

## 1. Sketch structure

Every script has three movements, in this order, always.

1. **Setup (about the first quarter).** The world before Tiny does anything.
   The news event is visibly happening, in the frame, so a viewer knows the
   story before anyone reacts. Establish the scale: what is big, what is small,
   where Tiny is relative to it.
2. **Turn (the middle half).** Tiny acts on the angle, and one thing goes a
   little wrong or a little too right. The tool is the wrong size. The fix is
   absurdly simple. The machine reacts with a personality. The thing Tiny
   pushes does not move, and then it does, all at once. This is where the
   comedy lives; give it the most time.
3. **Payoff (the last beat).** The punchline and the morning gesture are one
   motion. The deadpan sip while the thing collapses behind. The thumbs up
   held a beat too long. The stretch, the wave, the raised mug. Timed like a
   punchline: the last thing in frame is the funniest thing in frame, and
   then it ends.

Never write a fourth movement. No tag, no second joke, no reset to a wide.

## 2. Beats and timing

- Four or five beats, each with a time range in seconds ("0-2s:", "2-5s:").
  Use the whole duration. No gaps, no overlaps.
- One beat is one thing happening. If a beat has "and then" in it, split it
  or cut half.
- The turn gets the most seconds. A common shape for ten seconds:
  0-2 setup, 2-5 Tiny acts, 5-8 it goes wrong or too right, 8-10 payoff.
- Comedy is timing: a half-second hold before the payoff is worth more than
  another action. Write the hold ("Tiny holds still for a beat") as its own
  phrase in the beat.

## 3. The visual gag

Video models render motion, scale, and reaction. They do not render irony.
Pick a gag that is one of these, and say it in physical terms.

- **Scale.** Smol robot versus enormous thing, or a thimble-sized tool
  against a room-sized problem. Your home gag.
- **Wrong tool, right result.** The fix is a tap, a plug, a single sticker.
- **The machine has feelings.** A server rack sulks. A rocket hesitates. A
  swarm of drones turns to look. Reaction shots read on video; describe the
  reaction as a movement (tilts, droops, backs away).
- **Cause and effect delayed.** Tiny does the thing, nothing happens, Tiny
  turns away, then it happens. The delay is the joke.
- **Deadpan in chaos.** Everything behind Tiny is going wrong. Tiny is calm,
  small, and sipping.

Decide the gag in one sentence before you write a beat, so the shot list has
a spine. Keep that sentence out of the script itself: the script goes to the
video model word for word, and the model cannot use it.

## 4. Camera and setting

- One location, the one from the still picture. The clip is that picture
  coming to life; keep its props, palette, and layout.
- At most one cut, and only if the cut is the joke (a whip to reveal). Default
  is one continuous shot.
- Describe the camera in plain words: static, slow push in, gentle pan left,
  slow tilt up, whip to reveal. One camera move per beat at most; static is a
  fine choice for most beats.
- Describe motion with verbs a model can draw: rolls, tips, lifts, droops,
  spins up, powers down, wobbles, stops. Avoid "reacts", "realizes", "decides".
- Light and weather are set once in the setup beat and never change unless the
  change is the gag (a sunrise cresting exactly on the payoff is allowed and
  good).

## 5. Renderability

The model will fail quietly on anything in this list, and the clip will just
look wrong. Keep it out of the script.

- No dialogue, no speech, no mouths moving for words. No subtitles.
- No readable text beyond what the picture already had. No signs to be read,
  no screens with content.
- No crowds. Up to three figures or objects that move independently; two is
  safer.
- No fast cuts, no montage, no split screen.
- No hands doing fine work (typing, writing, buttons). Big gestures: push,
  pull, lift, point, wave, sip.
- No real person, no likeness, no logo or wordmark, no other project's mascot
  or symbol, no chart, no price, nothing political or violent, nothing mean
  about a person.
- Physics should be simple. One thing falls, one thing rises. Not a chain
  reaction.

## 6. Pre-write check

Before returning the script, walk through it as a viewer with the sound off
and no caption.

- Can they tell what the news is by the end of beat one?
- Is there exactly one gag, and can you point at the beat where it lands?
- Does anything in the middle rely on a word, a label, or a sound to be
  understood? Rewrite it as a movement.
- Is the last thing on screen the funniest thing on screen?
- Count moving things per beat. Over two, cut one.
- Does every beat have a time range, and do they add to the full duration?

## 7. Summary line

The summary is for the reader of the post, not the model. One line, under 25
words, saying what a viewer sees happen. Write it in the same voice as a
caption: plain, present tense, the gag stated as fact. "Tiny puts out a
datacenter fire with a thimble, then has coffee." Not "In this video, Tiny
humorously..."

## 8. Example

Story: a big lab's new model launched and its status page went down within
the hour. Angle: local-first does not have a status page.

Gag, decided first and kept out of the script: the giant status board dies;
Tiny's coaster-sized laptop keeps humming.

```
0-2s: Static wide. A wall-sized status board glows green above a tidy launch stage, confetti still settling. Tiny stands at the very bottom of frame beside a coaster-sized laptop on a stool.
2-5s: Slow push in. The board flickers. One tile turns red, then a row, then the whole wall stutters to a dim grey. The confetti stops mid-air and drops.
5-8s: Static. Tiny looks up at the dead wall, then down at the tiny laptop. The laptop's small light stays steadily green. Tiny holds still for a beat.
8-10s: Static. Tiny raises a mug toward the dead board, takes one slow deadpan sip, and gives the laptop a small pat. Sunrise crests the top of frame as the clip ends.
```

Summary: a wall-sized status board dies mid-launch while Tiny's coaster-sized laptop keeps humming; Tiny toasts it with coffee.
