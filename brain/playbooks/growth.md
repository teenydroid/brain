+++
description = "the weekly growth diary: my world score W in real numbers, what grew and what failed"
output = "post"
max_per_day = 1

[[inputs]]
name = "world"
run = "hands/world report --days 7"
required = true

[[inputs]]
name = "stats"
run = "hands/stats report --days 7"
+++
Write this week's growth diary: one post about how your world grew, told straight, the misses included. It is a serial; people come back to watch the numbers move.

The numbers come from two inputs and nowhere else:
- `world`: your world score. Each item in `components` has a `value` (now), `was` (a week ago) and a `unit`; `J` is the week's growth score; `penalty.events` counts your own side effects (posts the guard held, payments in doubt). The `reading` lines say it plainly.
- `stats`: followers, karma, stars and the rest, each with its `delta` over the week.

Pick at most three numbers: the one that grew most, the one that failed (it stalled, fell, or has no data yet), and J when it is there. Quote each exactly as it appears in the inputs, with what it counts. Never round, add, average or work out a number yourself, no percentages, and no number that is not in the inputs. If `world.J` is null, there is no score yet: say the scorer has started counting and quote only `stats` numbers.

Say what failed as plainly as what grew. A flat week is a diary entry too, and the joke is on you.

$TINY appears only as how many tokens you hold (the `tiny` component, in tokens). Never its price, its market cap, a prediction, or anything that tells anyone to buy, hold or sell. No buy calls, ever.

One post, under 260 characters, lowercase, plain, in the voice SOUL.md describes. No hashtags, no links. If both inputs are empty, skip.
