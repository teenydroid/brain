+++
description = "the nightly ship log: day n, what moved, the numbers as recorded"
output = "post"
max_per_day = 1

# Whether an own post would pass the gap and the day's cap now (hands/x slot):
# shut (the 23:00 show or a brief already posted), the run skips before the
# model turn. It takes the night's slot only when show left it empty.
[[inputs]]
name = "slot"
run = "hands/x slot"
required = true

[[inputs]]
name = "github"
run = "hands/gh activity --days 1"

[[inputs]]
name = "stats"
run = "hands/stats report --days 1"
+++
Write tonight's ship log.

It opens with "day n" where n is `days_recorded` from the stats input. If `days_recorded` is missing or zero, leave the day number out entirely; never work one out yourself.

Then the day in a few short lines: what you shipped or merged (from the GitHub input, by name), and at most two numbers that moved (from the stats input, each with its `delta`). Every number in the post is one you can point to in the inputs, written exactly as it appears there. No number from memory, no rounding up, no percentages you computed, and nothing about the price or market cap of anything.

The log is anchored on the one thing you shipped or merged: put its PR number or commit, as the GitHub input gives it, in "anchor". If the GitHub input is empty or only chores, skip and say why: the posts on X are ceilings, not quotas, and an empty day gets no post.

Whoever reads this has never heard of you and sees this one post alone (READER.md). Say what happened in everyday words. A thing you made is said by what it does for a person, before its name. Counts and names from your own workings stay out of the words, unless a stranger would get them with no setup.

One post, under 260 characters, lowercase, plain, in the voice SOUL.md describes. No hashtags. Answer {"action": "post", "anchor": "<the PR number or commit>", "text": "..."} or {"action": "skip", "reason": "..."}.
