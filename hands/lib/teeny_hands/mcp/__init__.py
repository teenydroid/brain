"""hands/mcp: a stdio MCP server whose catalog is a fixed handful of hand
verbs (docs/architecture.md, section 5; docs/hands.md, "mcp").

  server  the JSON-RPC loop and the catalog

The core starts it as `[mcp_client] servers = [{name = "hands", ...}]`
(bin/lib/core_sections/automation.py) and registers each tool as
`mcp_hands_<tool>_<digest>`; an agent reaches one only when its definition
names it.
"""
