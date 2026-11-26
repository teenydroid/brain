"""The pull-request babysitter's own clone (docs/hands.md, "babysit"): a
checkout of one pull request's head that its jailed agent edits, and the one
way that checkout becomes a commit on the pull request's branch. The clone
is mindclone's code on another place, so nothing in the tree is trusted
here either:

  state/sitter/home/repo-<job>/  a job's working tree, the agent's to write.
                             Each job has its own, so what a late run of an
                             older one still writes lands in no other's
  state/sitter/git/repo.git  the git directory, outside the agent's jail

`prepare(job, branch, head, merge_main)` checks out HEAD, with main merged in
when asked (a merge not committed yet; a conflict is left in the files as
its markers for the agent to resolve), and says whether main already holds
the whole change. `ship(prepared, message)` drops what a test run left at
the top of the tree (LEFTOVER, when nothing there is tracked) and refuses
what mindclone.ship refuses (a `.git`, anything but plain files, over the
size limits, a frozen path as of main, what looks like a secret), a conflict
marker left in a conflicted file, and a branch that moved; then it pushes
one commit on top of the head (its second parent the main merged in),
without force. Under DRY_RUN nothing is pushed.
"""

from __future__ import annotations

import re
from typing import Any

from .. import hand
from ..gh import actions
from . import mindclone, selfclone
from .mindclone import _bare, _git

Json = dict[str, Any]

SCRATCH = "sitter/git"


def place(job: str) -> mindclone.Place:
    return mindclone.Place(f"sitter/home/repo-{job}", SCRATCH, keep=False)


PREFIX = selfclone.BRANCH_PREFIX
#: A line git writes where a merge conflicts.
MARKER = re.compile(rb"^(<<<<<<< |>>>>>>> )", re.MULTILINE)
#: A new top-level entry a test run leaves: a TMPDIR made in the tree, or a
#: tempfile one made there (#588, 2026-11-26: tmp/tmpXXXX/state/hands.db
#: was refused as a secret and cost a try). Never one the head tracks.
LEFTOVER = re.compile(r"tmp[A-Za-z0-9_]*")


def locked():
    return hand.lock("sitter-clone")


def _merge(head: str, main: str) -> tuple[str, list[str]]:
    """(the merged tree, the conflicted paths) of HEAD and MAIN. Nothing is
    checked out and no commit is made."""
    done = _bare("merge-tree", "--write-tree", "--name-only", "--no-messages", "-z", head, main, check=False)
    if done.returncode not in (0, 1):
        raise hand.HandError("git merge-tree failed")
    tree, *names = done.stdout.decode("utf-8", "replace").split("\0")
    return tree.strip(), sorted({n for n in names if n})


def prepare(job: str, branch: str, head: str, merge_main: bool) -> Json:
    """The tree at HEAD of BRANCH, main merged in when MERGE_MAIN and there
    is something to merge: {branch, head, tree, main, conflicts, behind,
    unmarked, superseded, diff}; `diff` is the pull request's whole change
    as a patch, `unmarked` the conflicts git leaves no marker in (deleted on
    one side, changed on the other; binary), `superseded` whether merging it
    into main would change nothing."""
    with mindclone.at(place(job)), locked():
        origin = mindclone._fetch_origin()
        if mindclone._rev(f"refs/remotes/origin/{branch}") != head:
            raise hand.Refused(f"{branch} moved while it was read", rule="moved")
        behind = not mindclone._is_ancestor(origin, head)
        tree = _tree(head)
        main, conflicts = None, []
        if merge_main and behind:
            main = origin
            tree, conflicts = _merge(head, origin)
        unmarked = [path for path in conflicts if not _marked(tree, path)]
        into_main = tree if main else _merge(head, origin)[0] if behind else _tree(head)
        superseded = into_main == _tree(origin)
        mindclone._fresh(head, detached=True, tree=tree)
        since = _bare("merge-base", origin, head).stdout.decode().strip()
        diff = _bare("diff", "--no-renames", since, head).stdout.decode("utf-8", "replace")
        return {"job": job, "branch": branch, "head": head, "tree": tree, "main": main, "conflicts": conflicts,
                "unmarked": unmarked, "behind": behind, "superseded": superseded, "diff": diff}


def _tree(commit: str) -> str:
    return _bare("rev-parse", f"{commit}^{{tree}}").stdout.decode().strip()


def _marked(tree: str, path: str) -> bool:
    found = _bare("cat-file", "blob", f"{tree}:{path}", check=False)
    return found.returncode == 0 and MARKER.search(found.stdout) is not None


def _ignored() -> list[str]:
    """The files the agent made that an ignore rule keeps out of the commit."""
    raw = _git("ls-files", "-z", "--others", "--ignored", "--exclude-standard").stdout.decode("utf-8", "replace")
    return sorted(p for p in raw.split("\0") if p and "__pycache__/" not in p and not p.endswith(".pyc"))[:20]


def _tree_now(base: str) -> tuple[str, list[str]]:
    """The working tree as a tree object, staged over BASE in the clone's
    index (so a tracked file an ignore rule matches stays tracked), and the
    leftovers kept out of it."""
    _bare("read-tree", base)
    _git("add", "-A")
    added = _bare("diff-index", "--cached", "--name-only", "--no-renames", "--diff-filter=A", "-z", base)
    tops = {p.split("/", 1)[0] for p in added.stdout.decode("utf-8", "replace").split("\0") if p}
    dropped = sorted(t for t in tops if LEFTOVER.fullmatch(t)
                     and not _bare("ls-tree", "--name-only", base, "--", t).stdout.strip())
    if dropped:
        _bare("read-tree", base)
        _git("add", "-A", "--", ".", *(f":(exclude,literal){t}" for t in dropped))
    return _bare("write-tree").stdout.decode().strip(), dropped


def ship(prepared: Json, message: str, gh: Any = None, as_laid: bool = False, resolved: Any = ()) -> Json:
    """The edited tree as one commit on PREPARED's branch; see the module.
    AS_LAID: the tree as it was laid out, whatever the agent did to it (an
    empty commit, or main merged in), to have the checks run again. RESOLVED
    names the unmarked conflicts the agent decided: nothing in the tree could
    show that it did."""
    branch, head, base, main = prepared["branch"], prepared["head"], prepared["tree"], prepared.get("main")
    with mindclone.at(place(prepared["job"])), locked():
        mindclone._fetch_origin()
        if mindclone._rev(f"refs/remotes/origin/{branch}") != head:
            raise hand.Refused(f"{branch} moved on GitHub since it was taken; nothing was pushed", rule="moved")
        ignored: list[str] = []
        dropped: list[str] = []
        if as_laid and prepared.get("conflicts"):
            raise hand.Refused("main was merged in with conflicts: decide them and send \"fix\", not \"retry\"",
                               rule="undecided")
        if as_laid:
            tree = base
        else:
            if mindclone._tree_kind() == "missing" or mindclone._rev("HEAD") != head:
                raise hand.Refused("the copy is not the one that was laid out", rule="base")
            mindclone._no_git_inside()
            tree, dropped = _tree_now(base)
            ignored = _ignored()
        files = mindclone._changed(base, tree)
        if not files and main is None and not as_laid:
            raise hand.Refused("nothing changed against the pull request's head"
                               + (f"; .gitignore keeps out {', '.join(ignored)}" if ignored else ""), rule="empty")
        undecided = sorted(set(prepared.get("unmarked") or ()) - set(resolved))
        if undecided:
            raise hand.Refused(f"conflict(s) with no marker were not said to be decided: {', '.join(undecided)}; keep "
                               'or delete each, and name it in the report\'s "resolved"', rule="undecided")
        for path in prepared.get("conflicts") or ():
            if _marked(tree, path):
                raise hand.Refused(f"{path} still has conflict markers", rule="conflict_markers", path=path)
        mindclone._within_limits(base, tree, files)
        mindclone._only_plain_files(tree, base)
        parents = ["-p", head] + (["-p", main] if main else [])
        commit = _bare("commit-tree", tree, *parents, "-m", message).stdout.decode().strip()
        _bare("update-ref", f"refs/heads/{branch}", commit)
        try:
            mindclone._check_branch(branch)
            pushed = actions.push_branch(mindclone.gitdir(), selfclone.repo(), branch, mindclone.ORIGIN_BASE, gh=gh,
                                         expect=head)
        finally:
            _bare("update-ref", "-d", f"refs/heads/{branch}", check=False)
    return {"branch": branch, "commit": commit, "files": files, "merged_main": bool(main), "ignored": ignored,
            "dropped": dropped, **({"dry_run": True} if pushed.get("dry_run") else {})}
