"""Moltbook's verification challenge, read without a model.

A write on Moltbook may come back with a challenge: an obfuscated word
problem with two numbers and one operation, "A] lO^bSt-Er S[wImS aT/
tW]eNn-Tyy mE^tE[rS aNd] SlO/wS bY^ fI[vE, wH-aTs] ThE/ nEw^ SpE[eD?"
(20 - 5 = 15.00). A wrong answer counts toward the account's suspension (ten
failures in a row), so this parser answers only when it is sure, and says
None otherwise; the caller then leaves the content pending for the operator
instead of guessing.

How it reads one:

1. Lower-case it and drop every character that is not a letter, a digit,
   whitespace or a decimal point between digits: the scattered symbols sit
   inside words ("tW]eNn-Tyy"), so they are removed, not turned into spaces.
2. Collapse every run of a repeated letter ("twennttyy" -> "twenty"). The
   vocabulary is collapsed the same way ("three" -> "thre"), so a doubled
   letter in the noise and a real one compare equal.
3. Re-join a word the noise split with a space ("tw enty") when the joined
   form is a number word or an operation word and the pieces are not both
   words already.
4. Read the numbers: digits, or number words with the usual grammar
   ("thirty two", "one hundred and five", "three point five"). Two numbers
   written next to each other ("five twenty") are not one number: that is
   ambiguous, so no answer.
5. Find the operation from its words (plus, gains ... / minus, slows,
   loses ... / times, multiplied ... / divided, split, shared ...). A
   comparison ("how much faster than") is the difference, as a positive
   number. "Total", "sum", "combined" often only name the result ("multiplied
   by two, what is the total force?"), so they mean addition only when no
   other operation is named. "Doubles", "triples", "halves" carry the second
   number themselves. A "*" is noise, never an operation: Moltbook scatters
   it between words like any other symbol.

Confident means exactly two numbers and exactly one kind of operation, and
for a division a divisor that is not zero. Anything else is None.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

_RUNS = re.compile(r"(.)\1+")


def collapse(word: str) -> str:
    """A word with every run of one repeated character cut to one."""
    return _RUNS.sub(r"\1", word)


_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19,
}  # fmt: skip
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_SCALES = {"hundred": 100, "thousand": 1000}

UNITS = {collapse(k): v for k, v in _UNITS.items()}
TENS = {collapse(k): v for k, v in _TENS.items()}
SCALES = {collapse(k): v for k, v in _SCALES.items()}
POINT = "point"

_OPS_RAW = {
    "+": (
        "plus add adds added adding gain gains gained increase increases increased accelerates accelerated"
        " another extra grows grew rises rose receives collects"
        " joins faster more"
    ),
    "-": (
        "minus subtract subtracts subtracted slow slows slowed slower lose loses lost decrease decreases decreased"
        " reduce reduces reduced drop drops dropped fall falls fell less fewer remain remains remaining left"
        " difference decelerates decelerated spends gives"
    ),
    "*": "times multiply multiplied multiplies multiplying product",
    "/": "divide divided divides dividing split splits share shares shared quotient",
}
OPS = {op: {collapse(w) for w in words.split()} for op, words in _OPS_RAW.items()}
#: Words that name the result ("what is the total force?") as often as they
#: ask for an addition: an addition only when no other operation is named.
RESULT_WORDS = {collapse(w) for w in "total sum combined together altogether".split()}
#: One number and a word that carries the second: "it doubles" is times two.
_IMPLICIT_RAW = {
    "doubles doubled": ("*", 2), "triples tripled": ("*", 3), "halves halved": ("/", 2),
}  # fmt: skip
IMPLICIT = {collapse(w): (op, Decimal(n)) for words, (op, n) in _IMPLICIT_RAW.items() for w in words.split()}
#: Words that turn "faster", "more", "less" ... into a comparison: the difference.
COMPARISON = {collapse("than")}
#: Comparative words that are not an operation of their own in a comparison.
_COMPARATIVES = {collapse(w) for w in "faster slower more less fewer greater bigger smaller longer shorter heavier lighter".split()}
#: "*" is not here: Moltbook scatters it through the noise ("Is* ToTaL").
_SYMBOLS = {"+": "+", "×": "*", "÷": "/"}

_VOCABULARY = set(UNITS) | set(TENS) | set(SCALES) | {POINT} | set().union(*OPS.values()) | RESULT_WORDS | set(IMPLICIT) | COMPARISON


def _is_number_word(word: str) -> bool:
    return word in UNITS or word in TENS or word in SCALES or _compound(word) is not None


def _compound(word: str) -> int | None:
    """"twentyfive" (a hyphen the noise filter took out) as 25."""
    for tens, value in TENS.items():
        if word.startswith(tens):
            rest = word[len(tens):]
            if rest in UNITS and 1 <= UNITS[rest] <= 9:
                return value + UNITS[rest]
    return None


def _tokens(text: str) -> list[str]:
    text = text.lower()
    # A decimal point survives only between digits.
    text = re.sub(r"(?<=\d)\.(?=\d)", "\x00", text)
    kept = []
    for ch in text:
        if ch == "\x00":
            kept.append(".")
        elif ch in _SYMBOLS:
            kept.append(f" {ch} ")
        elif ch.isascii() and (ch.isalnum() or ch.isspace()):
            kept.append(ch)
        # anything else is noise and goes, without leaving a space
    words = [collapse(w) if not any(c.isdigit() for c in w) else w for w in "".join(kept).split()]
    return _rejoin(words)


def _known(word: str) -> bool:
    return word in _VOCABULARY or _compound(word) is not None


def _rejoin(words: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(words):
        joined = None
        for span in (3, 2):
            if i + span > len(words):
                continue
            parts = words[i : i + span]
            if any(p in _SYMBOLS or any(c.isdigit() for c in p) for p in parts):
                continue
            candidate = collapse("".join(parts))
            if _known(candidate) and not all(_known(p) for p in parts):
                joined = (candidate, span)
                break
        if joined:
            out.append(joined[0])
            i += joined[1]
        else:
            out.append(words[i])
            i += 1
    return out


class _Ambiguous(Exception):
    pass


def _number_run(words: list[str]) -> Decimal:
    """A run of number words as one number, or _Ambiguous when the run is
    really two numbers side by side."""
    total = 0
    current = 0
    last: str | None = None  # unit | teen | tens | hundred | thousand
    decimals: list[int] | None = None
    for word in words:
        if decimals is not None:
            if word in UNITS and UNITS[word] <= 9:
                decimals.append(UNITS[word])
                continue
            raise _Ambiguous
        if word == POINT:
            if last is None:
                raise _Ambiguous
            decimals = []
            continue
        compound = _compound(word)
        if compound is not None:
            if last in ("unit", "teen", "tens"):
                raise _Ambiguous
            current += compound
            last = "unit"
        elif word in UNITS:
            value = UNITS[word]
            kind = "unit" if value <= 9 else "teen"
            if last in ("unit", "teen") or (last == "tens" and kind == "teen") or (last == "tens" and value == 0):
                raise _Ambiguous
            current += value
            last = kind
        elif word in TENS:
            if last in ("unit", "teen", "tens"):
                raise _Ambiguous
            current += TENS[word]
            last = "tens"
        elif word == collapse("hundred"):
            if last not in ("unit", "teen", None) or current >= 100:
                raise _Ambiguous
            current = (current or 1) * 100
            last = "hundred"
        elif word == collapse("thousand"):
            if last == "thousand":
                raise _Ambiguous
            total += (current or 1) * 1000
            current = 0
            last = "thousand"
        else:  # pragma: no cover - callers pass number words only
            raise _Ambiguous
    if decimals is not None and not decimals:
        raise _Ambiguous
    value = Decimal(total + current)
    if decimals:
        value += Decimal("0." + "".join(str(d) for d in decimals))
    return value


def _numbers(words: list[str]) -> list[Decimal]:
    numbers: list[Decimal] = []
    run: list[str] = []

    def flush() -> None:
        if run:
            numbers.append(_number_run(run))
            run.clear()

    i = 0
    while i < len(words):
        word = words[i]
        if re.fullmatch(r"\d+(?:\.\d+)?", word):
            flush()
            numbers.append(Decimal(word))
        elif _is_number_word(word) or (word == POINT and run):
            run.append(word)
        elif word == "and" and run and run[-1] == collapse("hundred") and i + 1 < len(words) and _is_number_word(words[i + 1]):
            pass  # "one hundred and five"
        else:
            flush()
        i += 1
    flush()
    return numbers


def solve(challenge: str) -> str | None:
    """The answer with two decimals ("15.00"), or None when the challenge
    cannot be read with confidence."""
    if not isinstance(challenge, str) or not challenge.strip() or len(challenge) > 2000:
        return None
    words = _tokens(challenge)
    try:
        numbers = _numbers(words)
    except _Ambiguous:
        return None
    present = set(words)
    implicit = {IMPLICIT[w] for w in present if w in IMPLICIT}
    if implicit:
        numbers.append(next(iter(implicit))[1])
        if len(implicit) != 1 or len(numbers) != 2:
            return None
    if len(numbers) != 2:
        return None
    comparison = bool(present & COMPARISON)
    ops = set()
    for op, vocabulary in OPS.items():
        hits = present & vocabulary
        if comparison:
            hits -= _COMPARATIVES
        if hits:
            ops.add(op)
    ops |= {_SYMBOLS[w] for w in present if w in _SYMBOLS}
    ops |= {op for op, _ in implicit}
    if not ops and not comparison and present & RESULT_WORDS:
        ops.add("+")
    if comparison:
        if ops - {"-"}:
            return None
        a, b = numbers
        return _format(abs(a - b))
    if len(ops) != 1:
        return None
    op = ops.pop()
    a, b = numbers
    try:
        if op == "+":
            result = a + b
        elif op == "-":
            result = a - b
        elif op == "*":
            result = a * b
        else:
            if b == 0:
                return None
            result = a / b
    except InvalidOperation:  # pragma: no cover - Decimal of two finite numbers
        return None
    return _format(result)


def _format(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}"
