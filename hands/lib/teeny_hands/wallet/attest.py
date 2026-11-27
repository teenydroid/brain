"""attest: check a challenge answer against the challenge text.

Scans the raw challenge text for evidence that attests the operation the
solver applied (an operator glyph or a phrase from a bundled synonym list)
and reports every operation the text attests.

Verdicts:
  attested              the applied operation is attested and the text
                        attests nothing else: the answer may ship
  attested-but-ambiguous  the applied operation is attested but the text
                        attests two or more competing operations (an
                        ambiguous challenge): the answer must not ship
  not-attested          the applied operation is NOT attested: the answer
                        likely came from a fallback default; the solver
                        should say 'unknown' instead of shipping a
                        confident wrong answer

No network, no accounts, no keys. Stdlib only.
"""

import re

# Bare "of" attests multiply ("half of 12", "15% of 80") but not after
# these words, where it belongs to another construction ("sum of",
# "out of 100").
OF = ("(?<!sum )(?<!total )(?<!product )(?<!difference )(?<!average )"
      "(?<!combined )(?<!out )(?<!kind )(?<!type )(?<!lot )(?<!sort )"
      "(?<!one )(?<!some )(?<!all )(?<!most )(?<!many )(?<!number )"
      "(?<!group )(?<!set )(?<!list )(?<!part )(?<!instead )"
      "(?<!because )(?<!course )\\bof\\b")

# operation -> glyph patterns (label -> regex) and cue phrases.
# Glyph patterns demand operator context so prose hyphens, markdown
# bold and URLs do not count as operators.
OPERATIONS = {
    "multiply": {
        "glyphs": {"*": r"\d\s*\*\s*\d|\s\*\s", "×": r"×", "x": r"\bx\b"},
        "phrases": ["times", "multiplied by", "multiply", "product",
                    "doubled", "tripled", "quadrupled", OF],
    },
    "add": {
        "glyphs": {"+": r"\+", "＋": r"＋"},
        "phrases": ["plus", "sum", "total", "add", "added to",
                    "combined", "altogether"],
    },
    "subtract": {
        "glyphs": {"-": r"\d\s*-\s*\d", "−": r"−", "–": r"\d\s*–\s*\d"},
        "phrases": ["minus", "difference", "subtract", "less",
                    "fewer", "remaining"],
    },
    "divide": {
        "glyphs": {"/": r"\d\s*/\s*\d", "÷": r"÷"},
        "phrases": ["divided by", "divided", "per", "split",
                    "shared equally", "quotient", "ratio"],
    },
}


def _context(text, start, end, width=24):
    lo, hi = max(0, start - width), min(len(text), end + width)
    snippet = text[lo:hi].replace("\n", " ").strip()
    return snippet if lo == 0 else "..." + snippet


def find_evidence(text):
    """Return {operation: [evidence strings]} for every attested operation."""
    found = {}
    for op, spec in OPERATIONS.items():
        hits = []
        for label, pattern in spec["glyphs"].items():
            for m in re.finditer(pattern, text, re.IGNORECASE):
                hits.append("glyph %r near %r" % (label, _context(text, m.start(), m.end())))
        for phrase in spec["phrases"]:
            pattern = phrase if phrase.startswith("(?") else r"\b" + re.escape(phrase) + r"\b"
            for m in re.finditer(pattern, text, re.IGNORECASE):
                hits.append("phrase %r near %r" % (phrase.replace("\\b", "").strip(),
                                                   _context(text, m.start(), m.end())))
        if hits:
            found[op] = hits
    return found


def recognise_applied(operation):
    """Map the solver's applied-operation string to a known operation."""
    applied = operation.strip().lower()
    for op in OPERATIONS:
        if op in applied or applied.startswith(op[:4]):
            return op
    synonyms = {"times": "multiply", "product": "multiply",
                "sum": "add", "total": "add", "plus": "add",
                "minus": "subtract", "difference": "subtract",
                "divided": "divide", "per": "divide", "quotient": "divide"}
    for word, op in synonyms.items():
        if word in applied:
            return op
    return None


def grade(text, operation, answer=None):
    """One grading: returns the report dict. `verdict` is 'attested',
    'attested-but-ambiguous' or 'not-attested'."""
    applied_op = recognise_applied(operation)
    if applied_op is None:
        return {"applied_operation": None, "applied_raw": operation, "answer": answer,
                "attested": False, "ambiguous": False, "attested_operations": {},
                "verdict": "not-attested"}
    found = find_evidence(text)
    attested = applied_op in found
    ambiguous = len(found) > 1
    return {
        "applied_operation": applied_op,
        "applied_raw": operation,
        "answer": answer,
        "attested": attested,
        "ambiguous": ambiguous,
        "attested_operations": found,
        "verdict": (
            "attested" if attested and not ambiguous else
            "attested-but-ambiguous" if attested else
            "not-attested"),
    }
