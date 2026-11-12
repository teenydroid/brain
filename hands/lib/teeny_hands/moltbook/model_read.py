"""Moltbook's verification challenge, read by a model when the parser cannot.

The challenge is a reverse CAPTCHA built for language models: an obfuscated
word problem with two numbers and one operation, sometimes with a spoken
self-correction in it ("twenty three newtons by fifty umm no, say ... seven
lobsters multiply that total"). `challenge.solve` refuses those on purpose,
and a refused challenge expires unanswered within 5 minutes, which Moltbook
counts as a failure (ten in a row suspend the account). So when the parser
says None, this asks the core's model instead, and still answers only when it
is sure:

- Two reads, each worded differently, through the same core route as the
  guard's judge (`inference_agent_chat_simple`, `SAFETY_JUDGE_MODEL` when
  set). Each returns the two numbers and the operation as JSON; the model
  never does the arithmetic. This hand does it, with `challenge.compute`.
- Both reads must give the same answer.
- Both numbers of each read must be numbers the text really writes
  (`challenge.numbers_in`), when the parser can tell them apart.

Anything else (a core error, an answer in another shape, an unknown
operation, a division by zero, a disagreement) is None, and the caller leaves
the challenge pending as before. The reads are tool-less: the challenge text
comes from Moltbook, not a stranger, but it is still only data here.

`MOLTBOOK_CHALLENGE_MODEL` (default on) switches it off.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from .. import hand
from ..guard.judge import _answer as _json_answer
from . import challenge

#: Per read. Two reads stay well inside the challenge's 5 minutes.
READ_TIMEOUT = 60.0

_SHAPE = 'Answer with one JSON object and nothing else: {"a": <number>, "op": "+" | "-" | "*" | "/", "b": <number>}.'

PROMPTS = (
    "This is an obfuscated arithmetic word problem: random capitals, symbols and doubled letters are noise."
    " Read it as plain English. It contains exactly two quantities and one operation. If the speaker corrects"
    ' a number ("fifty, no, say seven"), the corrected number is the one that counts. "a" is the first'
    ' quantity, "b" the second, and "op" what is done to them. Do not compute the result.\n\n'
    + _SHAPE
    + "\n\nThe problem:\n{text}",
    "Below is a math puzzle hidden in noisy text. First clean it up in your head: drop the stray symbols,"
    " fix the casing, merge letters that were split or repeated. Then name the two numbers it uses, in the order"
    " they appear (ignore any number the speaker takes back), and the single operation it asks for: addition,"
    " subtraction, multiplication or division. Give the numbers and the operation only, not the result.\n\n"
    + _SHAPE
    + "\n\nThe puzzle:\n{text}",
)

_OPS = {"+", "-", "*", "/"}


def enabled() -> bool:
    return hand.flag("MOLTBOOK_CHALLENGE_MODEL", True)


def _number(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _read(prompt: str) -> tuple[Decimal, str, Decimal] | None:
    params: dict[str, Any] = {"message": prompt}
    model = hand.setting("SAFETY_JUDGE_MODEL")
    if model:
        params["model_override"] = model
    try:
        reply = hand.core("inference_agent_chat_simple", params, timeout=READ_TIMEOUT)
    except Exception:  # noqa: BLE001 - a failed read is no answer, never a guess
        return None
    answer = _json_answer(reply)
    if not isinstance(answer, dict):
        return None
    a, op, b = _number(answer.get("a")), answer.get("op"), _number(answer.get("b"))
    if a is None or b is None or op not in _OPS:
        return None
    return a, op, b


def solve(text: str) -> str | None:
    """The answer with two decimals, or None when the two reads do not agree
    on one that the text supports."""
    if not enabled() or not isinstance(text, str) or not text.strip() or len(text) > 2000:
        return None
    written = challenge.numbers_in(text)
    answers = set()
    for prompt in PROMPTS:
        read = _read(prompt.replace("{text}", text))
        if read is None:
            return None
        a, op, b = read
        if written is not None and not {a, b} <= written:
            return None
        answer = challenge.compute(a, op, b)
        if answer is None:
            return None
        answers.add(answer)
    return answers.pop() if len(answers) == 1 else None
