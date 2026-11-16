"""Who the babysitter trusts and which paths it treats with care. Every value
is read from secrets/teeny.env (the operator's), never the environment.

  BABYSIT_REPOS       the repositories it watches; only GITHUB_USER's
                      (default teenydroid/brain,teenydroid/diary)
  BABYSIT_OPERATORS   logins whose approval signs off a frozen or sensitive
                      change (default senamakel)
  BABYSIT_MAINTAINERS more logins whose approval counts, besides everyone
                      GitHub says can write to the repository
  BABYSIT_SELF_MERGE  1 lets Teeny's own evolve pull requests (teeny/gen-*
                      on teenydroid/brain) merge without a person's
                      approval when the audit is clean and teeny/eval
                      passed at the head (`self-merge` at L5, the
                      operator's decision of 2026-11-15); default off
"""

from __future__ import annotations

import fnmatch
import importlib.util
import re
from pathlib import Path
from typing import Any

from .. import hand
from ..gh.client import split_repo, user

#: The checkout this code runs from (hands/lib/teeny_hands/babysit/ -> the
#: root), where bin/lib/frozen.py and evals/frozen.txt are. Not TEENY_ROOT:
#: that moves state, never the code that judges it.
CHECKOUT = Path(__file__).resolve().parents[4]

DEFAULT_REPOS = ("teenydroid/brain", "teenydroid/diary")
DEFAULT_OPERATORS = ("senamakel",)

# --- tiers ------------------------------------------------------------------------------

OPERATOR = "operator"
MAINTAINER = "maintainer"
TEENY = "teeny"
STRANGER = "stranger"
#: Tiers whose words are not strangers' text.
TRUSTED = (OPERATOR, MAINTAINER)
WRITE_ROLES = ("admin", "maintain", "write")


def _logins(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = hand.setting(name)
    values = default if raw is None else tuple(v.strip().lstrip("@") for v in raw.split(","))
    return tuple(v.lower() for v in values if v)


def operators() -> tuple[str, ...]:
    return _logins("BABYSIT_OPERATORS", DEFAULT_OPERATORS)


def maintainers() -> tuple[str, ...]:
    return _logins("BABYSIT_MAINTAINERS", ())


def repos() -> tuple[str, ...]:
    """The watched repositories, each GITHUB_USER's own: the babysitter
    never merges anywhere Teeny does not own."""
    raw = hand.setting("BABYSIT_REPOS")
    values = DEFAULT_REPOS if raw is None else tuple(v.strip() for v in raw.split(",") if v.strip())
    out = []
    for value in values:
        owner, name = split_repo(value)
        if owner.lower() == user().lower():
            out.append(f"{owner}/{name}")
    return tuple(out)


def watched(repo: str) -> tuple[str, str, str]:
    """OWNER/REPO checked against the watched list; Refused otherwise."""
    owner, name = split_repo(repo)
    full = f"{owner}/{name}"
    if full.lower() not in (r.lower() for r in repos()):
        raise hand.Refused(f"the babysitter watches {', '.join(repos()) or 'nothing'}, not {full}", rule="not_watched")
    return owner, name, full


def tier(login: str | None, permission: Any = None) -> str:
    """Where `login` stands. `permission` is a callable login -> GitHub role,
    asked only for a login no setting names."""
    if not login:
        return STRANGER
    low = login.lower()
    if low in operators():
        return OPERATOR
    if low == user().lower():
        return TEENY
    if low.endswith("[bot]"):
        return STRANGER
    if low in maintainers():
        return MAINTAINER
    if permission is not None and permission(login) in WRITE_ROLES:
        return MAINTAINER
    return STRANGER


# --- Teeny's evolve pull requests (the self-merge path) ----------------------------------

#: The branch an evolve learning is opened from: teeny/gen-<n>-c<i> (or the
#: older loop's teeny/gen-<n>). Digits only, so a builder's teeny/<slug>
#: never reads as one, whatever its slug says.
EVOLVE_BRANCH = re.compile(r"teeny/gen-[0-9]+(?:-c[0-9]+)?")
#: The commit status the targeted eval sets on the PR's head: success is a
#: passed non-regression (or "unmeasurable"), failure a regression.
EVAL_CONTEXT = "teeny/eval"


def self_merge_on() -> bool:
    return hand.flag("BABYSIT_SELF_MERGE", False)


def is_evolve_branch(ref: Any) -> bool:
    return isinstance(ref, str) and EVOLVE_BRANCH.fullmatch(ref) is not None


# --- paths -------------------------------------------------------------------------------

#: Changes a person who owns the box signs off, in any watched repository:
#: what runs in CI, what is installed, what builds.
SENSITIVE = (
    (".github/**", "CI workflows and repository settings"),
    (".gitmodules", "submodules"),
    ("vendor/**", "vendored code"),
    ("**/Cargo.toml", "dependencies"),
    ("**/Cargo.lock", "dependencies"),
    ("**/package.json", "dependencies"),
    ("**/package-lock.json", "dependencies"),
    ("**/pnpm-lock.yaml", "dependencies"),
    ("**/yarn.lock", "dependencies"),
    ("**/requirements*.txt", "dependencies"),
    ("**/pyproject.toml", "dependencies"),
    ("**/Dockerfile", "the image"),
    (".dockerignore", "the image"),
    ("rust-toolchain.toml", "the toolchain"),
)

#: On teenydroid/brain, everything Teeny reads into a prompt. A stranger
#: does not write Teeny's prompts.
PROMPT_SURFACE = ("brain/**", "evals/**", "DIRECTIVE.md")


def _match(path: str, pattern: str) -> bool:
    """`dir/**` is the directory and everything under it; `**/name` is a
    file of that name (a glob) anywhere; anything else is the exact path."""
    if pattern.endswith("/**"):
        stem = pattern[:-3]
        return path == stem or path.startswith(stem + "/")
    if pattern.startswith("**/"):
        return fnmatch.fnmatchcase(path.rsplit("/", 1)[-1], pattern[3:])
    return path == pattern


def sensitive(path: str) -> str | None:
    for pattern, what in SENSITIVE:
        if _match(path, pattern):
            return what
    return None


def prompt_surface(path: str) -> bool:
    return any(_match(path, pattern) for pattern in PROMPT_SURFACE)


def is_test_removal(path: str, status: str | None) -> bool:
    if status != "removed":
        return False
    leaf = path.rsplit("/", 1)[-1]
    return "/tests/" in f"/{path}" or "/test/" in f"/{path}" or leaf.startswith("test_") or leaf.endswith("_test.sh")


# --- the frozen list (teenydroid/brain) -----------------------------------------------------


def _frozen_module():
    """bin/lib/frozen.py, the evolve gate's own check, loaded by path: the
    babysitter and CI answer "is this frozen" with the same code."""
    path = CHECKOUT / "bin" / "lib" / "frozen.py"
    spec = importlib.util.spec_from_file_location("teeny_babysit_frozen", path)
    if spec is None or spec.loader is None:
        raise hand.HandError("bin/lib/frozen.py is missing, so frozen paths cannot be checked")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def frozen_checker(list_text: str | None):
    """A function path -> the frozen pattern it hits, or None. `list_text`
    is evals/frozen.txt as of the base (read from GitHub); without it, this
    checkout's copy. Neither readable: every path counts as frozen, so the
    check fails closed."""
    module = _frozen_module()
    if list_text is None:
        local = CHECKOUT / "evals" / "frozen.txt"
        try:
            list_text = local.read_text()
        except OSError:
            return lambda path: "evals/frozen.txt (unreadable)"
    patterns = module.parse_patterns(list_text)
    return lambda path: module.frozen_pattern(path, patterns)


# --- teenydroid/diary ------------------------------------------------------------------------

#: The diary's layout (its README, "What's here"). Code writes every data
#: file; a pull request only ever touches the reader-facing ones.
DIARY_LAYOUT = (
    re.compile(r"receipts/\d{4}/\d{2}/\d{4}-\d{2}-\d{2}\.jsonl"),
    re.compile(r"head\.json"),
    re.compile(r"stats/(?:\d{4}-\d{2}-\d{2}|latest)\.json"),
    re.compile(r"world/(?:\d{4}-\d{2}-\d{2}|latest)\.json"),
    re.compile(r"wallet\.json"),
    re.compile(r"accounts/\d{4}-W\d{2}\.json"),
    re.compile(r"days/\d{4}-\d{2}-\d{2}\.md"),
    re.compile(r"days/chain\.jsonl"),
    re.compile(r"memory/(?:MEMORY\.md|chain\.jsonl|head\.json)"),
    re.compile(r"giveaways/(?:index|drop-\d+)\.json"),
    re.compile(r"verify\.py"),
    re.compile(r"README\.md"),
    re.compile(r"\.gitignore"),
    re.compile(r"\.github/.+"),
)
#: Written once, then never edited: an edit breaks every later hash.
DIARY_APPEND_ONLY = (
    re.compile(r"receipts/.+"),
    re.compile(r"accounts/.+"),
    re.compile(r"days/.+"),
    re.compile(r"memory/chain\.jsonl"),
)
#: Data the box's own code writes. A pull request that touches it is not
#: how it is written.
DIARY_DATA = (
    re.compile(r"head\.json"),
    re.compile(r"stats/.+"),
    re.compile(r"world/.+"),
    re.compile(r"wallet\.json"),
    re.compile(r"memory/.+"),
    re.compile(r"giveaways/.+"),
)
#: What makes the receipts checkable at all.
DIARY_VERIFIER = (re.compile(r"verify\.py"), re.compile(r"\.github/.+"))


def is_diary(repo: str) -> bool:
    return repo.lower().endswith("/diary")


def is_brain(repo: str) -> bool:
    return repo.lower().endswith("/brain")


def any_match(path: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(p.fullmatch(path) for p in patterns)
