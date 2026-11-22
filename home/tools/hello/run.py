"""The smallest tool: says where it is and what it can see. A template."""
import datetime
import os
import sys

print("hello from the box at", datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
print("my folder:", os.getcwd())
print("my arguments:", sys.argv[1:])
print("my own code is in:", os.environ.get("TEENY_TOOL_DIR"))
