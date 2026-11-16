+++
description = "queue the next Reddit read for the browser operator: an allowlisted subreddit in turn, or the inbox"
output = "hand:reddit browse"
surface = "reddit"
max_per_day = 12

[[inputs]]
name = "due"
run = "hands/reddit due"
required = true

[[inputs]]
name = "status"
run = "hands/reddit status"

[[inputs]]
name = "leads"
run = "hands/brief leads --surface reddit"
optional = true
+++
Queue the next Reddit read for the browser operator. This turns **attention**: what it brings back reaches you as a `world · reddit_browse` stimulus (or `reddit · inbox`), and you answer from there with your reddit_* intents, so read where people ask things you can help with.

`due` names what is owed: a subreddit, or the inbox. Answer exactly one of:

{"action": "hand", "subreddit": "<due.subreddit, exactly>"}
{"action": "hand", "inbox": true}

with `"sort": "new"` added to a subreddit read when `status` shows you read that subreddit within the last day (new threads are where an early, useful comment is seen). If `leads` names a subreddit or a thread the operator wants you to look at and `due` is a subreddit, you may read the lead's subreddit instead, once. Never anything else: no comment, vote or post from here.
