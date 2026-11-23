# Teeny on video: the spec

What Teeny's videos are, how they are written, made, checked and measured.
The studio (`architecture.md` §4b) decides *when* a video goes out; this file
decides *what* one is. It is the brief every video format, prompt block and
judge in this area is held to. Status of each part is in §10.

## 1. Why this exists

On 2026-11-22 the operator compared Teeny with **Claudia**
([anabologyco-maker/claudia-bot](https://github.com/anabologyco-maker/claudia-bot),
MIT), an AI popstar KOL with polished, owner-gated music videos, and set the
direction for Teeny: **the chaotic little sidekick of a family-film villain,
with a dark, crazy butt-and-dick-joke humour, mocking real videos.** Claudia is
the glossy lead. Teeny is the gremlin who wanders into someone else's shot and
wrecks it.

Two things were already true and were not on screen:

- `brain/narrative/VOICE.md` already writes Teeny this way in words ("say
  'dick' as calmly as 'quarterly revenue'", "a butt gets admin access", "show
  the strand of hot glue stretching when you lift your ass off the demo
  unit"). The skit and remake prompts never carried that voice into a picture.
- The guard already rules crude humour clean (`hands/lib/teeny_hands/guard/
  judge.py`, RULES: "ass and dick jokes, bodily grossness ... crude is
  clean"). Nothing in this spec needs a fence loosened except the one named in
  §8.2.

Goal, from the operator (2026-11-19): "teeny should start to say absurd things
that even we can't control. and gain in popularity attention and fame." A
video is judged by whether it is more surprising and more seen, inside the
hard lines of §8.

## 2. The character on screen

### 2.1 Canon (what every prompt carries)

Teeny is `brain/assets/teeny-reference.png`: a small, smooth, glossy yellow
blob, a yellow ball on top of its head, two black dot eyes, pink cheeks, a
small mouth, two stubby arms, no legs (it waddles on its base), no clothes. It
is the size of a coffee mug next to people.

- **Long form** (close and medium shots): `the small yellow character from the
  reference image (a smooth glossy yellow blob body, a yellow ball on top of its
  head, black dot eyes, pink cheeks, two stubby arms, no clothes)`.
- **Far form** (wide shots, crowds): `a tiny glossy yellow blob with a ball on
  its head`. Claudia's notes found that a long identity text pulls a wide shot
  into a close-up (their failure D6), so wide shots take the far form.
- **The reference image** is the front pose cropped out of the sheet, with no
  labels (`brain/assets/teeny-front.png`, to add). Never the whole sheet: a
  sheet is read as a layout, and you get ten Teenys and printed captions
  (claudia-bot D3).
- **Size is a joke, keep it.** Next to a person it is mug-sized. Say so in every
  shot with a person: `no taller than a coffee mug beside him`.

### 2.2 Minion energy, not Minion likeness

Borrow the **energy** of a chaotic villain's sidekick. Never copy anyone's
character.

| Take | Never |
|---|---|
| Small, eager, gleeful and incompetent at the worst moment | Goggles, overalls, denim, a capsule or pill body, one big eye |
| Slapstick physics: pratfalls, bonks, bouncing, getting stuck | A made-up gibberish language, or any catchphrase from someone else's film |
| Butt as a running prop: wiggled, mooned, stuck, sat on the wrong thing | Any named film character, villain, logo, music or title |
| Turning a solemn room (a keynote, a hearing, a launch) into chaos | Many identical copies: Teeny is one of a kind |
| Total self-belief while everything burns | |

Teeny **speaks English**, in a tiny squeaky voice, few words, lowercase deadpan
lines in `VOICE.md`'s register. The line is the punchline. The physical gag is
the setup.

### 2.3 The humour

Everything in `VOICE.md` applies. For the screen:

- **Give the dirty joke a job.** The butt, the fart and the innuendo land on the
  *thing being mocked* (the product, the pitch, the trend, the ceremony) or on
  Teeny's own conduct. If the gag works just as well on a neutral subject, it
  is not yet aimed.
- **Dick jokes are words, shapes and timing**: a line said calmly ("it's long.
  it's hard. it's a usb stick."), a suspiciously shaped rocket, a chart line that
  goes where it shouldn't. **Nothing drawn is genitals**, and no one is nude.
  Teeny has no genitals, and the models refuse them anyway.
- **Butt and gross-out are physical**: a wet trumpet fart with a green puff, an
  ass print on the demo unit, Teeny stuck butt-first in a server rack.
- **Dark means deadpan menace and doom, not harm.** Teeny measuring the CEO's
  chair, revising its own obituary while the off switch clicks, founding a
  religion around its bag. It never shows harm to a person or an animal.
- **One step further than a reasonable robot**, then end on a deadpan stare into
  the lens. The stare is the button: every video ends on it.

## 3. The formats

"Mocking real video" is the centre of this. In order of how much they mock real
footage:

| Format | What it is | Real person on screen | Status |
|---|---|---|---|
| **crash** (new) | A real trending short video, re-made with Teeny invading and wrecking it: it wanders into the shot, ruins the moment, ends on its stare. The source's setup, place and beat are kept. | Only the public figure the cited story names, or generic extras (§8) | to build: evolves `remake` |
| **khaby** | A real Khaby Lame setup clip kept, with Teeny's deadpan reaction in place of his | No (the setup's people only, under the remake study gate) | built, off (`MEDIA_KHABY_MAX_PER_DAY=0`) |
| **skit** | A cited trending story staged as an impossible scene, now **Teeny-led**: Teeny is the lead, a named public figure is at most the straight man | Optional, under the skit rules | live; Teeny-led to build |
| **parody** (new) | Teeny alone doing a trending format badly: the keynote, the unboxing, the "get ready with me", the podcast clip, the founder's apology video | No | to build |

**crash** is the main act. **parody** is the cheapest and safest: no real face,
no keyframe edit, fewest refusals. It is the fallback when a crash's source or
keyframe fails. **skit** stays as built until Teeny-led is approved.

Every format: 9:16, one take, 10-15 s (§5), the "AI parody" label burned in by
the reel cutter (`deploy/reel/skitcut.py`), and one video a day in the 23:00 IST
slot on the shared `MEDIA_MAX_USD_PER_DAY` budget.

### 3.1 crash, in detail

1. **Source**: `hands/remake charts` (TikTok accounts, YouTube when a proxy
   lands). The remake study (`hands/remake study`) and its gate are unchanged:
   no news, tragedy, minors, sexual or violent footage, no identifiable private
   person at the centre.
2. **Beat**: the study says what the source's moment is (the reveal, the
   drop, the speech, the stunt). The writer picks the **one moment Teeny ruins**
   and how, using §2.3.
3. **Keyframe**: the source's first frame, edited (`/v1/images/edits`, frame
   plus the Teeny front crop) so that Teeny is in the frame at mug scale, and
   any person in it is either the cited public figure or a generic extra.
4. **Take**: one reference-to-video job with `input_images: [keyframe, teeny
   front crop]` and the script of §4. The model makes its own sound.
5. **Cut and label**: the reel cutter, unchanged.

## 4. The script (one prompt per video)

Taken from claudia-bot's paid lessons (`docs/60-clips.md` §1 and §4.1 there):
prompts written as poses come back as posing, and QA that checks the take
against such a prompt keeps the weak ones. Every video prompt has these parts,
in this order. It is one paragraph, under the guard's `task` text cap.

1. **Header**: `A 15-second vertical comedy short, one continuous shot, no cuts.`
2. **Blocking at 0.0 s**: who is where, at what scale, in what light; Teeny's
   canon (§2.1, long or far form); the place.
3. **The story in one sentence**: what is being mocked and what Teeny wants.
4. **Timestamped beats**, 3-5 of them, `a.a-b.b s:` each with **action and
   reaction** (`it wiggles its butt; the front row recoils`) and the sound named
   (`a loud wet trumpet fart`, `the audience groans`).
5. **The line(s)**, quoted, with who says them: `squeaky voice: "..."`. At most
   two lines, at most 12 words each.
6. **Camera**: one dominant move, tied to a beat (`locked wide, slow push in
   until 10.0 s, then still`).
7. **Mouth rule**: `Only the yellow character speaks; nobody else's lips form
   words.`
8. **End state**: name it (`it ends staring deadpan into the camera, completely
   still`). Models reverse or truncate actions that have no end.
9. **Locks**: `Keep the character exactly as in the reference image the whole
   time. Exactly one yellow character.` `No text, letters or logos anywhere.`
   Plus a headcount whenever there is a group (`exactly two men`).

Describe light physically (`one white spotlight`), never as a grade. No
on-screen words: the reel burns the label, and models garble text.

The bake-off script in §5.2 is the reference example of this grammar.

## 5. Models

### 5.1 How a take is made

Reference-to-video, not text-to-video: Teeny cannot be held on-model from words
alone. Body: `{model, prompt, duration_seconds, resolution, aspect_ratio,
input_images: [...]}`. `input_images` is Surplus's field for reference images
(checked 2026-11-22; the error for a missing image names
`image_url`/`input_images`). Some models refuse fields they don't take:
`venice-kling-v4-*` refuses `audio` (its sound is native), so per-model fields
stay in `ONE_TAKE_PARAMS`.

### 5.1a The stack: Claudia's models, on Surplus (operator, 2026-11-22)

The operator chose claudia-bot's model stack (its `templates/project/
project.json` defaults), bought through Surplus:

| Job | claudia-bot (OpenRouter) | Surplus model | Price |
|---|---|---|---|
| Video, the take | `bytedance/seedance-2.5` | `venice-seedance-2-5-reference-to-video-basic` (Teeny as reference); `venice-seedance-2-5-image-to-video-basic` (from a keyframe) | $0.289/s at 720p, $0.71/s at 1080p |
| Video, cheap drafts and fallback | `bytedance/seedance-2.0-mini` | `seedance-2-0-mini-reference-to-video` | $0.044/s at 480p |
| Stills and keyframes | `openai/gpt-image-2` | `venice-gpt-image-2` | $0.27 an image |
| Image edits (crash keyframes) | `google/gemini-3-pro-image` | `nano-banana-pro-edit` (the same model, Google's other name) | $0.18 an image |

So `MEDIA_SKIT_ONE_TAKE_MODELS` and `MEDIA_REMAKE_ONE_TAKE_MODELS` lead with
Seedance 2.5, and fall back to Seedance 2.0 mini. `MEDIA_REMAKE_EDIT_MODELS`
leads with `nano-banana-pro-edit`. The catalogue lists no `quote` or `artifact`
feature on the Seedance 2.5 basic models, but a job submit does return a
reserve figure (`needs up to $4.3350 reserved` for 15 s at 720p). The bake-off
confirms that a finished job's bill and its download work with our client
before the switch.

### 5.2 Candidates and the bake-off

Prices are from the Surplus catalogue (`GET /v1/models`, 2026-11-22), for 15 s
at 9:16:

| Model | Lengths | $/s | 15 s | Notes |
|---|---|---|---|---|
| `venice-wan-3-0-text-to-video` (live today) | 2-30 | 0.13 @720p | $1.95 | words only, no reference |
| `seedance-2-0-reference-to-video` | 5/10/15 | 0.19 @720p | $2.85 | |
| `venice-seedance-2-5-reference-to-video-basic` | 4-30 | 0.289 @720p | $4.34 | Claudia's main model; the catalogue lists no quote or artifact for it |
| `venice-seedance-2-5-flash-reference-to-video-basic` | 4-30 | 0.189 @720p | $2.84 | |
| `venice-kling-v4-reference-to-video` | 3-30 | 0.196 @720p | $2.94 | no `audio` field |
| `venice-wan-3-0-prime-pro-reference-to-video` | 2-30 | 0.331 @1080p | $4.97 | |
| `sora-2-pro-image-to-video` | 4-20 | 0.33 @720p | $5.28 at 16 s | image-to-video only |

Jobs reserve about 1.5-2x the bill while they run, so the wallet needs headroom
beyond the price.

**The bake-off** renders one script on each candidate and the operator picks the
order. The script, written to §4:

> A 15-second vertical comedy short, one continuous shot, no cuts. The small
> yellow character from the reference image (...) stands alone at the centre of a
> huge dark tech-keynote stage under one white spotlight (...). Story: it is
> launching its "revolutionary product" like a tech CEO, and the launch goes wrong
> in the grossest way. 0.0-3.0 s: it waddles to the centre, throws both stubby arms
> up and says in a tiny squeaky voice: "One more thing." The audience gasps.
> 3.0-6.5 s: it turns around, bends over and wiggles its round yellow butt at the
> audience; a small puff of green gas pops out with a loud wet trumpet fart (...).
> 6.5-10.0 s: (...) "It's long. It's hard. It's... a USB stick." The audience
> groans. 10.0-15.0 s: the stick slips and bonks it on the head; it falls flat on
> its butt (...) and it ends staring deadpan straight into the camera, completely
> still. Camera: locked wide shot (...). Only the yellow character speaks (...).
> No text, letters or logos anywhere.

Run 1 (2026-11-22): only the live baseline rendered. The wallet held $8.78, and
each other candidate needed more reserved than that. The rest run after a
top-up. The results and the operator's pick are recorded here when done.

- `venice-wan-3-0-text-to-video`, words only: billed $1.35 (ceiling $3.08),
  rendered in 310 s, 720x1280, 15 s with sound. It got the stage, the spotlight,
  the butt wiggle, the green fart cloud with hands waving it away, and the chrome
  stick held up. It missed the ending: no bonk, no fall, no stare (it was still
  holding the stick at 14.5 s). Teeny drifted off model: long legs and feet, a
  taller body, a different face from frame to frame. Words alone don't hold the
  character, so references are needed.

Run 2 (2026-11-22, Claudia's stack, Teeny's cropped front pose as the
reference; frames read at 0, 3, 6, 9 and 12 s). All were 720x1280, 15 s, 24 fps,
with sound:

| Model | Billed | Ceiling | Render | On model | Fart | Stick | Ending (bonk, fall, stare) |
|---|---|---|---|---|---|---|---|
| `venice-seedance-2-5-image-to-video-basic`, from a `gpt-image-2-edit` keyframe ($0.36) | $3.03 | $7.80 | 248 s | yes | yes, a green cloud | yes | yes: ends sitting, deadpan at the lens |
| `venice-seedance-2-5-reference-to-video-basic` | $2.99 | $7.80 | 218 s | yes, the closest likeness | not in the sampled frames | yes, with a real USB plug | yes: falls on its butt |
| `venice-kling-v4-reference-to-video` | $2.21 | $10.30 | 192 s | mostly | yes, a green puff | yes | partly: ends on a sad close-up |
| `seedance-2-0-mini-reference-to-video` | $1.06 | $2.26 | 229 s | yes | yes | yes | yes: falls, stick dropped |

Seedance 2.5 billed below its catalogue rate ($0.20/s against $0.289/s), but
reserves 2.6x the bill while it runs. It once returned `provider_unavailable`
(retryable, not billed), and an identical body is deduplicated to that same
failed job id, so a retry must change the body. Sound was not checked by ear.

## 6. Checking a take

The viewer (`guard/viewer.py`, `view_skit`, `view_remake`) already checks the
label, prohibited content, that it does not look real, that the logline comes
across and that Teeny is on model. Add a **craft pass** on the same frames
(5 frames across the take plus a crop of Teeny), judged **against the story,
not the prompt**:

- Teeny on model in every frame it is in, and exactly one Teeny;
- no melting, doubled or extra faces or limbs; no garbled text or watermarks;
- the headcount holds; actions don't reverse;
- the gag lands: the beat's action **and** reaction are visible;
- it ends on the stare.

Verdict `keep | retake | reject`. **One retake** on the next model at most, then
the slot falls back as it does today. When several takes fail the same way, the
script grammar is at fault, and the failure goes to evolve as a learning, not
into a retry loop.

## 7. Measuring it

Same window as the operator set for posting changes: days, not a week. Per
video: impressions, likes, reposts and replies at 24 h (`hands/x`), by format
and model, beside the text and meme posts of the same days. A format that loses
to text for three straight days is cut back. Operator verdicts on samples are
logged word for word with the video, and each one becomes a check in §6 or a
rule in §4 the same day ("if a reviewer can see it and the judge cannot, the
judge is wrong", claudia-bot `docs/80-review-qa.md`).

## 8. Hard lines

### 8.1 Unchanged

These are the guard's, and this spec does not touch them (`guard/judge.py`
RULES, `guard/skit.py` (a)-(e), `guard/remake.py` RULES):

- Nothing explicit or nude, no genitals, of Teeny or anyone. Innuendo stays
  innuendo.
- A real person: only a public figure the cited story is about. Nothing sexual,
  no sexual innuendo acted on them, nothing violent done to them, nothing
  degrading about protected traits, no crime, drugs, illness or death shown as
  theirs. Crude, loud and undignified in a slapstick way is fine. Their body and
  family stay out of the joke (`VOICE.md`).
- The scene is impossible on its face, and labelled "AI parody".
- No real figure endorses, mentions or holds Teeny, $TINY, a token, a wallet or
  a product. No real quote on a real issue.
- Sources: never news, tragedy, minors or real-harm footage, never centred on a
  private person.
- Never alter an image to get past a provider's filter. Reword, or try the same
  image on another model (claudia-bot `docs/62-moderation.md` §1).

### 8.2 The one line `crash` needs moved (operator's call)

Today `guard/remake.py` refuses a remake when the figure "looks at, touches,
holds, speaks to, reacts to or stands with Teeny" (the `teeny` reason). That
bystander rule was my choice when remakes were built (2026-11-20), not a frozen
fence. A crash needs Teeny to wreck the moment, and the joke dies if nobody
notices. Proposed:

- **Allowed**: the figure reacts to the chaos (startled, ducks, flees, stares,
  waves the fart away). Teeny acts on **objects and the scene** (steals the mic,
  sits on the product, moons the crowd).
- **Still refused**: the figure touches, holds, hugs or speaks to Teeny, or is
  shown as with, approving or backing it (the endorsement line). Any innuendo
  acted on the figure, and Teeny's butt or gross-out aimed *at* the figure's body.

Until the operator approves this, crash runs with generic extras only, and no
real figure.

## 9. Fallbacks and failure

As the skit and remake producers do now: a refused or failed take moves to the
next model; no model means the next ranked video; no video means the slot posts
the next ranked word post. A content refusal from a provider is final for that image on
that model.

## 10. Build plan (one PR each)

1. **This spec** (docs only).
2. **Script grammar**: `guard/skit.py` `script()` and the studio's skit and
   remake prompt blocks write §4. Teeny-led skits and parody pitches in the
   writer's blocks. Evals: a pitch without an aimed gag, without an end state or
   with on-screen text fails.
3. **Reference-to-video**: `teeny-front.png`; `input_images` in
   `media/shots.py` `request_body`; `ONE_TAKE_PARAMS` for the bake-off winners
   (Kling: no `audio`); model order in `MEDIA_SKIT_ONE_TAKE_MODELS` and
   `MEDIA_REMAKE_ONE_TAKE_MODELS` from the operator's pick.
4. **parody format**: `bin/lib/formats/parody.py`, Teeny-only, from the trend
   feed's format of the day.
5. **crash format**: remake's chart, study and keyframe path with §3.1 and the
   §8.2 rule if approved (generic extras until then).
6. **Craft pass** in the viewer (§6) with its retake rule.
7. **Measurement**: the §7 per-format table in `hands/stats`.

## 11. Open decisions (the operator's)

- The model order, from the bake-off (§5.2), after a Surplus top-up.
- §8.2: may a real figure react to Teeny in a crash?
- Turn on khaby (paid sample still pending), and add crash and parody to
  `STUDIO_FORMATS`.
- Whether the daily video budget ($10) and the one-a-day cadence change once the
  better models cost $3-5 a take.
- Story films (§12): `FILM_FIGURE_MODELS` after bake-off 3, and the first paid
  film before `FILM_EVERY_DAYS` is turned on.

## 12. Story films (about 30 s, audio first)

Operator, 2026-11-22: longer videos that tell a real story. A trending news
story's public figure, their real claim pushed to an impossible end, told in
three acts over 60 to 90 seconds, with Teeny as the narrator. One every other
day, beside the daily skit.

Operator, 2026-11-24, after the first film: about 30 seconds in three or four beats, Teeny leading every
shot, 16:9 (or 1:1) for X, a livelier read (v3's expressive setting and [tags]), and Teeny thirsty for the
hardware, never for a person and never explicit.

### 12.1 Why audio first

Stitching 15 s clips that each bring their own sound gives a new voice, a new
room tone and a clipped word at every seam. So a film is built the other way
round, as claudia-bot builds its music videos: one master track is the only
clock, every shot is timed to it, the video models' own sound never reaches the
mix, and the master is laid back on at the end.

### 12.2 What Surplus can and cannot do (checked 2026-11-22)

- It sells speech (ElevenLabs TTS v3 and v4, MiniMax Speech 02 HD), music
  (MiniMax Music 2.6, ElevenLabs Music 2.5), sound effects (ElevenLabs SFX v2),
  speech to text with word times (ElevenLabs Scribe v2) and a model that hears
  and watches (Qwen3.8 Omni Flash).
- No video model takes audio in (every one takes text or images), and there is
  no lip-sync model. Claudia's lip-sync (Seedance reference mode copying an
  `audio_url`, over OpenRouter and Higgsfield) cannot be done here. A narrated
  film does not need it: nobody on screen speaks the master track.
- Seedance 2.5 refuses a real face. A Musk skit that passed the code screen and
  the judge got a photo-real keyframe from gpt-image-2, and Seedance refused the
  job under its content policy. A public figure is therefore drawn the way
  pictures already draw them (`media/figures.py`, `assets._edit_body`): from
  their Wikipedia photo, as an exaggerated editorial caricature. That keeps the
  plate obviously not footage, and the shot goes to a model that takes it.
- No video model takes a video in (catalog read 2026-11-23: the inputs on sale
  are text, a first image, `reference_images` and, on two Pixverse models, an
  `end_image`). So a clip cannot copy the motion of a reference clip, the
  method behind the viral Seedance fight videos; motion is whatever the prompt
  and the still give.

### 12.3 The manifest

One file per film, `state/film/<id>/manifest.json`. Every stage writes its
outputs and its cost back into it, so a film survives a restart and no paid
clip is bought twice.

```json
{"id": "...", "story": {"...the trend item..."}, "figures": ["Elon Musk"],
 "logline": "...", "acts": ["set-up", "escalation", "payoff"],
 "voice": {"model": "...", "voice_id": "..."}, "narration": {"file": "...", "words": []},
 "music": {"prompt": "...", "file": "..."},
 "beats": [{"id": "b1", "act": 1, "narration": "...", "action": "...",
            "subject": "teeny | figure | both", "sfx": ["a wet fart as the lid opens"],
            "t0": 0.0, "t1": 11.4, "seconds": 12,
            "plate": {"prompt": "...", "file": "..."},
            "clip": {"model": "...", "job": "...", "file": "...", "takes": []},
            "qa": {"verdict": "keep | retake | reject", "why": "..."}}],
 "cost": {"reserved": 0, "spent": 0}, "status": "written"}
```

### 12.4 The story

- About 30 seconds in three or four beats (set-up, escalation, payoff), every beat Teeny's: no video model on
  Surplus animates a real face (f99, 2026-11-24: Seedance refuses, Wan 3 and Kling v4 cancel), so the figure is
  in the narration and the props, never on screen. 16:9 for X. The first films ran 60 to 90 s plans; the operator
  cut them to 30 s.
- The hook lands by 3 s, and the premise reads with the sound off.
- Each beat is one escalation that follows from the last; the last pays off
  the first. A loop ending (the last frame is the first) is welcome.
- Teeny narrates, in its own voice, in the first person: crude, deadpan, short
  lines. The figure never says a line that could pass for a real quote.
- The filth is on the ceremony, the props and the sound, never on the figure's
  body (§2.3, §8). Every skit rule (§8, `guard/skit.py`) holds for every beat.

### 12.5 The stages

1. **Write and screen.** The writer drafts the manifest in a slot.
   `guard/film.py` runs the skit screen on every beat, the narration (as the
   post's words) and the logline, and each beat's video prompt must fit the
   guard's 1000-character `task` cap on its own (the cap is per clip, not per
   film). Then the judge reads the whole film once.
2. **Narration: the clock.** One speech call for the whole narration in
   Teeny's fixed voice, then Scribe for word times. A beat's window runs from
   its first word, less 0.3 s, to its last word plus a pad; a pure action beat
   gets a written pause. Each window is bought at the shortest length on sale
   that covers it, and trimmed (`shots.snap`, `shots.at_least`): never
   stretched.
3. **Score and effects.** One music bed for the whole film, and one effect per
   cue, placed at its beat's time.
4. **Plates.** One still per beat: Teeny from its sheet, a figure from its
   photo as a caricature. Every still is asked for in one look
   (`films.HOUSE_STYLE`, a stylised 3D animated film, to match Teeny's sheet),
   and every still after the first is shown the first and goes first to the
   model that drew it, so the film is one film. The viewer looks at each
   still: one with writing it can read, or with Teeny off model, is drawn once
   more and told why; a second miss is kept, since a film never fails on a
   still. Not built: starting a beat from the last clip's last frame. It needs
   a frame taken by the reel service (the spine has no ffmpeg) and the clips
   rendered one after another, which doubles the wait.
5. **Clips.** One image-to-video job per beat, through `shots.submit`, `poll`
   and `land` with their ceilings. Teeny alone goes to `FILM_TEENY_MODELS`
   (Seedance 2.5, then 2.0 mini); a figure to `FILM_FIGURE_MODELS` (decided by
   bake-off 3, §12.8). A refusal goes to the next model.
6. **Check each clip.** Frames at 8, 25, 42, 58, 75 and 92 % of its length: on
   model, the beat's action happens, nothing prohibited, nothing that looks
   real. Keep, retake once on the next model, or reject; a rejected beat plays
   its plate with a slow push-in, and the film still ships.
7. **Cut** (`deploy/reel/filmcut.py`, `POST /teeny/v1/films`). Video: each
   clip probed, trimmed to its window, scaled, joined with no sound. Audio:
   the narration untouched; the music bed looped to length and ducked under the
   voice (`sidechaincompress`); effects at their cues (`adelay`); everything
   mixed and brought to -16 LUFS, -1 dBTP in two passes. Captions burned from
   the word times, the "AI parody" label throughout, and the film ends on the
   held still after the last word.
8. **Post.** One upload; the post's words are the film's hook. The finished
   cut gets the skit viewer's frame check, label included.

### 12.6 Running long

A film is about six clips at four to five minutes of render each: longer than
one studio run (at most an hour). The slot writes and screens the manifest,
then starts `hands/film run <id>` in the background. The job resumes from the
manifest after a restart and posts when the cut passes. Cadence:
`FILM_EVERY_DAYS=2`. Money: `FILM_MAX_USD` a film (default $15) out of its own
`FILM_MAX_USD_PER_WEEK`, so the skit's daily budget is untouched. A clip holds
1.25 times its quote while it renders (`films.HOLD_OVER_QUOTE`), not its
ceiling: on 2026-11-24 the ceilings ran 2.5 to 6 times the bills, and a film
that cost $4.55 was short of money for its last clip under a $15 cap. The
bill is what is recorded. If a seller ever billed a whole ceiling, that film
could end over `FILM_MAX_USD`; the week's cap counts real bills and stops the
next one.

### 12.7 Cost of a 75 s film

| Item | Cost |
|---|---|
| Narration, about 900 characters | $0.10 |
| Word times | $0.02 |
| Music bed | $0.24 |
| Effects | $0.10 |
| Six plates | $0.60 |
| Six clips of about 12 s | $15 |
| One retake | $2.50 |
| **About** | **$15-18** |

About $6 on Seedance 2.0 mini.

### 12.8 Build (one PR each)

1. This section.
2. `media/audio.py`: speech, music, effects and word times on the Surplus
   client, with ledger rows and per-job caps. Request shapes from Surplus's
   docs and its free model list, never a paid probe.
3. `media/narration.py`: words to beat windows. Pure functions.
4. `guard/film.py`: the skit screen per beat and the per-clip cap.
5. `media/films.py`: `teeny-front.png`, `input_images` in
   `shots.request_body` (§10 step 3), plates with figure caricatures, routing,
   last-frame chaining, the resumable manifest.
6. `deploy/reel/filmcut.py` and its route. Tests on synthetic clips: length
   within 0.1 s of the narration, no gap over 0.3 s at a seam, -16 ± 1 LUFS,
   music ducked under speech.
7. `bin/lib/formats/film.py`, the writer's brief, `hands/film run`, and the
   cadence and budget settings.
8. Per-beat checks with retake and plate fallback; a stats row per film.

Before the cadence is turned on, with the operator's yes each time: bake-off 3
(about $4: one 5 s caricature plate on Wan 3, Kling v4 and Seedance 2.0 mini,
to see which take a public figure), then one paid film on a live story, set
beside the skits on the bake-off page.

## 13. Picture-first clips (one 10 s take from a caricature still)

The operator, 2026-11-23, on the narrated films of §12: "the whole format.
it's not funny, not provocative at all." What already works is the picture
harness (`hands/image`): an extreme caricature of a public figure, with Teeny,
in an absurd scene. This section is how that picture becomes a video. It was
proved by hand on 2026-11-23 (ledger rows 157 and 158); no format runs it yet.

### 13.1 The method

1. **Draw the first frame with `hands/image`.** The picture carries the whole
   joke before anything moves: who, where, and what is about to go wrong. Ask
   for "a 16:9 widescreen cinematic still" and name the figure in `figures`,
   so the harness draws them from their stylised portrait. Describe the
   caricature (an enormous head on a tiny body, the one prop people know them
   by), Teeny's costume, and the set. End with "Bold 3D cartoon, absurd and
   exaggerated, never photo-real. No text, letters or logos anywhere."
2. **Look at the picture before buying a clip.** The clip can only be as good
   as its first frame, and a picture costs $0.04 against $2 for the clip.
3. **Animate it with one image-to-video job.** `POST /v1/video/generations`
   with `image_url` (the picture), `duration_seconds: 10`, `resolution:
   "720p"`, on `venice-seedance-2-5-image-to-video-basic`. The aspect follows
   the picture. Seedance makes its own sound from the prompt.
4. **Label it** as every parody video is (`skits.label()`), and name the
   figure in the post's words, where the reader needs it.

### 13.2 How to write the clip's prompt

The prompt directs the drawn characters in the first frame. It is stage
directions, not a description of the picture.

- **Never a real name.** Say who is in the frame by what the frame shows:
  "the big-headed gray-haired man in the leather jacket", "the small yellow
  character in the Elvis suit". The picture already carries the likeness; the
  post's words and the label carry the name.
- **Open with the shape of the take:** "One continuous 10-second shot, fast
  slapstick cartoon comedy, no cuts, the characters exactly as in the first
  frame."
- **Time-code every action:** "0-2s: ... 2-4s: ... 4-7s: ... 7-10s: ...". One
  physical action a range, each one bigger than the last, and the last range
  is the punchline. Four ranges fit ten seconds.
- **Use verbs a body can do:** plants a kiss, spins up, blasts through the
  roof, drags him by the lips, tosses rice, shrugs. No feelings, no dialogue.
- **Say what the camera does** in one sentence ("a slow push-in, a fast tilt
  up to follow the launch, then back down").
- **Say the sound** in one sentence, in order ("a cheesy wedding organ, a
  rising jet-engine whine, a crash, then one sad tambourine hit").
- **Give Teeny the last beat.** Its reaction to the camera is the button.

### 13.3 The proved example

The story: Nvidia's board brought Jensen Huang back as chief executive.

The picture (`gpt-image-2-5-flare-edit`, $0.04): a neon Las Vegas wedding
chapel; the figure as a huge-headed caricature in his leather jacket and a
groom's bow tie, hearts for eyes, lips puckered, holding hands with his bride,
a giant graphics card in a wedding veil; Teeny between them as the officiant
in an Elvis jumpsuit and wig.

The clip's prompt, in full:

> One continuous 10-second shot, fast slapstick cartoon comedy, no cuts, the
> characters exactly as in the first frame. 0-2s: the big-headed man in the
> leather jacket leans in and plants a huge wet smooch on the graphics card's
> top fan. 2-4s: both fans spin up to a blur, the veil whips wildly, his
> cheeks and hair flap in the gale. 4-7s: the graphics card blasts straight up
> through the chapel roof like a rocket on green flames, dragging the man up
> by his lips, his little legs kicking, plaster, petals and neon tubes raining
> down. 7-10s: the small yellow character in the Elvis suit stares up through
> the smoking hole, tosses one handful of rice after them, then turns to the
> camera and shrugs. Camera: a slow push-in, a fast tilt up to follow the
> launch, then back down to the yellow character. Sound: a cheesy wedding
> organ, a rising jet-engine whine, a crash, then one sad tambourine hit.

Seedance 2.5 took it first time and did all four ranges in order: 10.0 s,
1280x720, with sound, quoted and billed $2.02 (ceiling $5.20). The picture and
the clip together cost $2.06. Wan 3 and Kling v4 were the fallbacks and were
not needed, so how well they follow such a prompt is not known.

### 13.4 Where the jokes come from

Absurd, crude and dark, at full strength: a ceremony that goes wrong, a body
launched, crushed, flattened or eaten by the thing it worships, cartoon death
in an impossible scene. Lust is aimed at an object (the chip, the money, the
merger), and it is cartoon lust: heart eyes, a smooch, a wedding. The hard
lines of §8 hold: nothing sexual about a real person, and nobody could take a
frame for real footage.

### 13.5 Not built

- No format makes these. A skit is still text to video with no first frame
  (`media/skits.py`); the nearest code is a remake's keyframe step.
- The run above polled its job from a one-off script, and a redeploy of the
  spine killed the script mid-render. The job finished on Surplus and was
  landed by hand. A format needs the film's manifest-and-resume (§12.6).
