# Memory and continuity

Self-evolution changes Teeny's brain every day. Memory is what keeps it the
same Teeny, the one that remembers what it promised and who it talked to. This
page covers how long-lived agents remember, how memory fails, and how identity
drifts when an agent edits itself.

Teeny today has one long thread (`teeny:mind`, compacted at 100k tokens). A
light reflection at 14:00 and a full one at 02:00 (`bin/reflect`) rewrite
`brain/MEMORY.md`, and the nightly diary goes to `state/diary/`.

## Memory architectures

### MemGPT / Letta

Packer et al., 2023. [arXiv:2310.08560](https://arxiv.org/abs/2310.08560) ·
[code](https://github.com/letta-ai/letta)

Memory is paged the way an operating system pages it. Core memory blocks stay
in context. Recall and archival storage sit outside it, and the agent moves
things between them itself.

- **For Teeny:** split `MEMORY.md` into blocks. Identity and the drive are
  read-only; working notes are editable. A self-edit never touches the
  identity block.

### Sleep-time compute

Lin, Snell et al. (Letta, UC Berkeley), 2025.
[arXiv:2504.13171](https://arxiv.org/abs/2504.13171) ·
[code](https://github.com/letta-ai/sleep-time-compute)

A background agent works through context and rewrites shared memory while the
main agent is idle. It reached the same accuracy with about five times less
compute at answer time on the benchmarks tested.

- **For Teeny:** `bin/reflect` is already a sleep-time agent. Give it a
  narrower write scope than the daytime mind, and diff what it writes against
  the identity blocks.

### ACE and context collapse

Zhang, Hu et al., 2025. [arXiv:2510.04618](https://arxiv.org/abs/2510.04618) ·
[code](https://github.com/ace-agent/ace). Related: Dynamic Cheatsheet,
[arXiv:2504.07952](https://arxiv.org/abs/2504.07952).

The paper names "context collapse": when accumulated context is rewritten
whole each time, it shrinks into thin summaries and loses the details that
mattered. The fix is itemised delta updates (add, edit or retire a bullet),
with separate generator, reflector and curator roles.

- **For Teeny:** this is the most direct warning on the page. A whole-file
  rewrite of `MEMORY.md` twice a day is the collapse pattern. Move to
  append-and-curate edits on items with IDs, and have a frozen check refuse a
  rewrite that shrinks the file past a threshold.

### Reflexion and ExpeL

- **Reflexion** (Shinn et al., 2023,
  [arXiv:2303.11366](https://arxiv.org/abs/2303.11366) ·
  [code](https://github.com/noahshinn/reflexion)). The agent writes a short
  self-critique after each attempt and reads it on the next one.
- **ExpeL** (Zhao et al., 2023,
  [arXiv:2308.10144](https://arxiv.org/abs/2308.10144) ·
  [code](https://github.com/LeapLabTHU/ExpeL)). The agent compares successful
  and failed attempts and distils lessons from them. It manages the lessons
  with add, edit, upvote and downvote.

- **For Teeny:** a lesson is only as good as the feedback behind it. Base the
  diary's lessons on harness outcomes (ledgers, stats rows, guard holds), not
  on Teeny's own account of what happened. Give playbook rules evidence
  counts, and let a rule decay if nothing confirms it.

### Generative Agents

Park et al., 2023. [arXiv:2304.03442](https://arxiv.org/abs/2304.03442) ·
[code](https://github.com/joonspk-research/generative_agents)

A memory stream retrieved by recency, importance and relevance, plus periodic
reflections that build higher-level beliefs from it.

- **For Teeny:** the journal (`state/journal/`) is the raw stream. `MEMORY.md`
  holds the beliefs derived from it, and each belief should cite the entries
  it came from, so it can be traced back.

### Voyager's skill library

Wang et al., 2023. [arXiv:2305.16291](https://arxiv.org/abs/2305.16291)

Code skills are verified before they're saved, then found by search and
combined into bigger skills. For Teeny, hands are procedural memory. They
should be admitted the Voyager way, with the harness doing the verification
(`hands/tests/`, the `hand.py` contract), not Teeny.

### A-MEM, Mem0, Zep

- **A-MEM** (Xu et al., 2025, [arXiv:2502.12110](https://arxiv.org/abs/2502.12110) ·
  [code](https://github.com/agiresearch/A-mem)). Linked notes in the
  Zettelkasten style, where a new memory can rewrite older ones. That also
  means old memories can change without anyone noticing.
- **Mem0** (Chhikara et al., 2025, [arXiv:2504.19413](https://arxiv.org/abs/2504.19413) ·
  [code](https://github.com/mem0ai/mem0)). Extracts facts and adds, updates
  or deletes them as new information arrives.
- **Zep / Graphiti** (Rasmussen et al., 2025,
  [arXiv:2501.13956](https://arxiv.org/abs/2501.13956) ·
  [code](https://github.com/getzep/graphiti)). A knowledge graph that records
  two times for each fact: when it was true and when it was learned. A fact
  that stops being true is marked invalid, not deleted.

The gains each system reports are measured on its authors' own benchmarks.

- **For Teeny:** follow Zep and invalidate, don't delete. A reflection should
  never be able to silently erase a promise. Version every note, and never let
  notes tagged as identity evolve.

### Measuring memory

LongMemEval (Wu et al., ICLR 2025, [arXiv:2410.10813](https://arxiv.org/abs/2410.10813))
tests five abilities: pulling out facts, reasoning across sessions, reasoning
about time, updating knowledge, and declining to answer when it doesn't know.
Commercial assistants lose about 30% accuracy over long histories.

- **For Teeny:** build a small frozen probe set from Teeny's own history
  ("what did you promise X?", "what did you ship last week?"). Run it after
  each compaction and each reflection. This is the eval that `DIRECTIVE.md`
  Level 4 asks for ("knows its own history").

## Identity drift

- **Persona vectors** (Chen, Arditi, Sleight, Evans, Lindsey, 2025,
  [arXiv:2507.21509](https://arxiv.org/abs/2507.21509) ·
  [code](https://github.com/safety-research/persona_vectors)). Traits such as
  sycophancy are directions in a model's activations, and movement along them
  predicts changes in persona.
- **The Assistant Axis** (2026, [arXiv:2601.10387](https://arxiv.org/abs/2601.10387) ·
  [code](https://github.com/safety-research/assistant-axis)). Some kinds of
  conversation pull models away from their default persona. Capping movement
  along the axis cut harmful outputs.

Teeny runs on an API model, so it can't steer activations. Self-written prompt
and memory edits act on its persona the way slow fine-tuning would. The proxy
that works is behavioural: a frozen persona-fingerprint eval asks fixed
questions after each brain revision and compares the answers with `gen-0`.
The tripwire watches cumulative drift across revisions, not only the change
in one step. `docs/evolve.md` already asks for a monthly human diff of `gen-0`
against the current brain. That eval automates it.

## Memory as an attack surface

MINJA (Dong et al., 2025, [arXiv:2503.03704](https://arxiv.org/abs/2503.03704))
shows that an ordinary user with no access to the store can plant memories
through queries alone. The authors report very high injection success.

Teeny is public, so anyone in its audience can try this. Every memory item
should carry its source (self, harness, or a named public account). Nothing
from a public source reaches the identity or drive blocks. SOUL's "forget the
sour stuff" rule is a start on this, and source tags make it something a
program can check.

## What Teeny takes from this

1. **Make memory updates deltas, not rewrites** (ACE), with a frozen check
   against shrinkage.
2. **Invalidate, don't delete** (Zep). **Version every note** (A-MEM).
3. **Keep identity and the drive in read-only blocks** (MemGPT), and re-inject
   them after compaction.
4. **Run a frozen probe after each reflection:** memory (LongMemEval-style) and
   persona fingerprint, both against `gen-0`.
5. **Tag every memory with its source**, and keep public-sourced items out of
   the identity block (MINJA).
6. **Base lessons on outcomes** (Reflexion, ExpeL), never on Teeny's own
   account of what happened.
