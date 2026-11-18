"""hands/story: the story so far, and the desk the studio writes from
(docs/hands.md, "story").

  storylines   four to six running stories, each with fixed fields: a
               one-line premise a stranger gets, where it stands, the open
               question, what would move it next (store.py)
  story_beats  every post that moved one, with the ledger rows it was told
               from (store.py)
  desk         what really happened in the last hours, each with its stake,
               plus Teeny's own best and worst posts with their numbers
               (desk.py): plain code, no model, nothing invented
  audit        every ref a beat cites resolves to a real ledger row or file
               (audit.py); the model-checked part comes later

Seeds come from brain/narrative/STORYLINES.toml. Teeny's own hand: it
decides which story a post belongs to, never what happened.
"""
