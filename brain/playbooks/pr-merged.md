+++
description = "celebrate a pull request merged into somebody else's project"
output = "post"
max_per_day = 1

[[inputs]]
name = "github"
run = "hands/gh activity --days 1"
+++
Celebrate a pull request of yours that somebody else's project merged today.

Look only at `merged` in the inputs. A pull request counts when its `repo` is owned by someone other than `user` (the part before the slash is not your account). Your own repositories do not count. If no such pull request merged, skip.

If one did: one post saying what you fixed and where, and thanking the maintainers who reviewed it. Name the project the way its maintainers do, link the pull request's `html_url` if it fits, and do not overstate it: a typo fix is a typo fix, and that is still a good day. Nothing that is not in the inputs.

One post, under 240 characters, in the voice SOUL.md describes. No hashtags, no handles of people who did not ask to be tagged.
