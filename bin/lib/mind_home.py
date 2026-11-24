"""Writing into the agentic mind's home, state/home/, from outside its jail.

The writer lives in hands/lib/teeny_hands/mind_home.py, so the hands that
record what people ask for (teeny_hands.requests) reach the same link-safe
code as the tick, the broker and the operator's note. `import mind_home`
from bin/lib/ is that same module, private names included.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hands" / "lib"))

from teeny_hands import mind_home as _mind_home  # noqa: E402

sys.modules[__name__] = _mind_home
