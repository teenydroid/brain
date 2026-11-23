import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hands" / "lib"))
import socket
from teeny_hands import websites  # noqa
MAX_BYTES = 2 * 1024 * 1024
TOTAL_SECS = 30
USER_AGENT = "x"
class Refused(Exception): pass
class Failed(Exception): pass
def _resolve(host): return []
def _connect(ip, host, timeout): return None
def fetch(url, *, on_send=lambda: None, deny=None): raise Failed("not yet")
