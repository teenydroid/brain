# Fonts for words drawn on pictures

`hands/meme` draws a caption onto a picture with code, so the words are
exactly the words written (a picture model misspells them). These are the
two typefaces it draws with. Both are under the SIL Open Font License 1.1,
whose full text sits beside each file; the OFL allows bundling and
redistributing the fonts with software, and they are used here unmodified,
under their own names.

| File | Used for | Licence | Source |
| --- | --- | --- | --- |
| `Anton-Regular.ttf` | `impact`: the classic meme caption, white with a black outline | OFL 1.1, `Anton-OFL.txt` (Copyright 2020 The Anton Project Authors) | [google/fonts `ofl/anton`](https://github.com/google/fonts/tree/main/ofl/anton), last changed in e0a8124c |
| `AtkinsonHyperlegible-Bold.ttf` | `label`, `subtitle` and the panel captions of a comic | OFL 1.1, `AtkinsonHyperlegible-OFL.txt` (Copyright 2020 Braille Institute of America, Inc.) | [google/fonts `ofl/atkinsonhyperlegible`](https://github.com/google/fonts/tree/main/ofl/atkinsonhyperlegible), last changed in 1b22086d |

Fetched from google/fonts at 7af0d90b on 2026-11-18. sha256:

```
a4ba3a92350ebb031da0cb47630ac49eb265082ca1bc0450442f4a83ab947cab  Anton-Regular.ttf
5a3b0c8cc8ca545155150b4512a1fa248298df121c50d6557e651e61fbdab92f  AtkinsonHyperlegible-Bold.ttf
```

Neither font has emoji or most non-Latin scripts. `hands/meme` refuses a
caption with a character its font cannot draw, rather than print a box.
