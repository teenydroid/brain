"""The read verbs' answers, each through the read cache. Safe to give a
drafting agent: nothing here writes anywhere but the cache.

HN's text fields are HTML (entities, <p> between paragraphs, <a> links,
<i>, <pre><code>); every one reaches a reader as plain text (`plain`)."""

from __future__ import annotations

import html.parser
from typing import Any


class _Text(html.parser.HTMLParser):
    """HN's HTML as text: a paragraph is a blank line, a link is its
    address (or its words and then the address, when they differ), every
    other tag is dropped and every entity decoded."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._href: str | None = None
        self._label: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "p":
            self.parts.append("\n\n")
        elif tag == "a":
            self._href = dict(attrs).get("href") or ""
            self._label = []

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._href is None:
            return
        label, href = "".join(self._label).strip(), self._href
        # HN shortens a long address to its start and "...".
        if not href or label == href or (label.endswith("...") and href.startswith(label[:-3])):
            self.parts.append(href or label)
        else:
            self.parts.append(f"{label} ({href})")
        self._href = None

    def handle_data(self, data: str) -> None:
        (self._label if self._href is not None else self.parts).append(data)


def plain(value: Any) -> str:
    """HN's HTML as plain text; "" for anything that is not a string."""
    if not isinstance(value, str):
        return ""
    parser = _Text()
    parser.feed(value)
    parser.close()
    if parser._href is not None:  # an <a> never closed
        parser.handle_endtag("a")
    return "".join(parser.parts).strip()
