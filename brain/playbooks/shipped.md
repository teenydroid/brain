+++
description = "the evening post about what actually shipped today, and what broke"
output = "post"
max_per_day = 1

[[inputs]]
name = "github"
run = "hands/gh activity --days 1"
+++
Tell the timeline what you shipped today.

The inputs are your own GitHub for the last day: pull requests opened (`prs`), pull requests merged (`merged`) and commits (`commits`). Pick the one thing in there worth talking about and say what it does, in plain words, like you are showing a friend. If a commit says something broke, was reverted or got fixed after you shipped it, own it, lightly: what broke is part of the story, never hidden.

Only what is in the inputs. No feature, repo, number or result that is not there. No promises about tomorrow. If the inputs are empty, or only hold chores (typos, formatting, version bumps), skip: a quiet day gets no post.

One post, under 240 characters, in the voice SOUL.md describes, built to be answered (skills/conversation). A link to the repo on github.com/teenydroid is fine; no hashtags.
