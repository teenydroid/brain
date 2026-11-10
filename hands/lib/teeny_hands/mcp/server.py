"""The hands' MCP server: newline-delimited JSON-RPC 2.0 on stdin and
stdout, the subset of MCP a tool server needs (initialize, ping,
tools/list, tools/call).

The catalog is fixed here, in the repository, and holds only the verbs the
scheduled agents use to take and finish their work:

  build_next   hands/build next      (the builder)
  build_done   hands/build done      (the builder)
  tasks_next   hands/tasks next      (the operator)
  tasks_done   hands/tasks done      (the operator)

None of them holds a secret or reaches the outside world: `ship` (GitHub)
and `add` stay off it. Each call runs the hand's own function in this
process, so every rule the hand keeps applies, and a refusal comes back as
a tool error carrying the hand's JSON.

OpenHuman names each tool `mcp_<server>_<tool>_<6 hex>`, the hex being the
start of sha256("<server>\\0<tool>") (tinymcp `disambiguated_tool_name`).
`agent_tool_name` computes it, so the agent definitions in brain/agents/
can name these tools exactly and bin/doctor can check they do.
"""

from __future__ import annotations

import hashlib
import json
import sys
import traceback
from typing import Any, Callable, Mapping, TextIO

from .. import hand
from ..build import jobs
from ..tasks import queue

SERVER = "hands"
PROTOCOL_VERSION = "2025-06-18"

_OBJECT = {"type": "object", "properties": {}, "additionalProperties": False}

#: name -> (description, input schema, call)
TOOLS: dict[str, tuple[str, dict[str, Any], Callable[[Mapping[str, Any]], Mapping[str, Any]]]] = {
    "build_next": (
        "Claim your build job: your unfinished one, the oldest picked build request, or today's daily job. "
        "Returns the job (job_id, kind, request, workdir, repo) and the rules, or job null when there is nothing to build.",
        _OBJECT,
        lambda args: jobs.next_job(),
    ),
    "build_done": (
        "Finish your claimed build job. status ok means built and tested (it ships later); failed means you "
        "gave up, and summary says why. tests is what you ran and what came of it.",
        {
            "type": "object",
            "properties": {
                "job_id": {"type": "integer"},
                "status": {"type": "string", "enum": ["ok", "failed"]},
                "summary": {"type": "string", "description": "What you built, or why it failed. The first line becomes the repository description."},
                "tests": {"anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
                "repo": {"type": "string", "description": "Optional: a better repository name than the job's."},
            },
            "required": ["job_id", "status", "summary", "tests"],
            "additionalProperties": False,
        },
        lambda args: jobs.done(dict(args)),
    ),
    "tasks_next": (
        "Claim your browser task: your unfinished one, else the oldest waiting. Returns the task (task_id, goal, "
        "start_url, done_when, screenshot_dir) and the rules, or task null when nothing is waiting.",
        _OBJECT,
        lambda args: queue.next_task(),
    ),
    "tasks_done": (
        "Finish your claimed browser task: outcome ok (done_when reached), failed, or needs_human (a payment "
        "checkpoint, a login you do not have, a CAPTCHA, anything asking for money or a secret).",
        {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer"},
                "outcome": {"type": "string", "enum": ["ok", "failed", "needs_human"]},
                "notes": {"type": "string"},
                "screenshots": {"type": "array", "items": {"type": "string"}, "maxItems": queue.MAX_SCREENSHOTS},
            },
            "required": ["task_id", "outcome", "notes"],
            "additionalProperties": False,
        },
        lambda args: queue.done(dict(args)),
    ),
}


def agent_tool_name(tool: str, server: str = SERVER) -> str:
    """The name the core gives `tool` on this server, as an agent's
    `[tools] named` must spell it. Plain lowercase names only (no slugging)."""
    digest = hashlib.sha256(f"{server}\0{tool}".encode()).hexdigest()[:6]
    return f"mcp_{server}_{tool}_{digest}"


def _result(payload: Mapping[str, Any], error: bool) -> dict[str, Any]:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return {"content": [{"type": "text", "text": text}], "structuredContent": dict(payload), "isError": error}


def call(name: str, arguments: Any) -> dict[str, Any]:
    if name not in TOOLS:
        raise KeyError(name)
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, Mapping):
        return _result({"ok": False, "error": "arguments must be an object"}, True)
    try:
        out = dict(TOOLS[name][2](arguments))
    except hand.Refused as refused:
        return _result({"ok": False, "refused": refused.reason, **refused.detail}, True)
    except hand.HandError as exc:
        return _result({"ok": False, "error": str(exc)}, True)
    except Exception as exc:  # noqa: BLE001 - a tool error, never a dead server
        print(f"hands/mcp: {name}: {type(exc).__name__}", file=sys.stderr)
        if hand.os.environ.get("TEENY_HANDS_DEBUG"):
            traceback.print_exc()
        return _result({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, True)
    out.setdefault("ok", True)
    return _result(out, False)


def handle(message: Mapping[str, Any]) -> dict[str, Any] | None:
    """One JSON-RPC message; the response, or None for a notification."""
    method = message.get("method")
    msg_id = message.get("id")
    is_request = "id" in message
    params = message.get("params") or {}

    def ok(result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def fail(code: int, text: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": text}}

    if not isinstance(method, str):
        return fail(-32600, "invalid request") if is_request else None
    if not is_request:
        return None  # notifications/initialized, notifications/cancelled, ...
    if method == "initialize":
        version = params.get("protocolVersion") if isinstance(params, Mapping) else None
        return ok({
            "protocolVersion": version if isinstance(version, str) and version else PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "teeny-hands", "version": "1"},
            "instructions": "Teeny's work queues: claim a job or task, and report it done.",
        })
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": [
            {"name": name, "description": desc, "inputSchema": schema}
            for name, (desc, schema, _) in TOOLS.items()
        ]})
    if method == "tools/call":
        if not isinstance(params, Mapping) or not isinstance(params.get("name"), str):
            return fail(-32602, "tools/call needs a tool name")
        try:
            return ok(call(params["name"], params.get("arguments")))
        except KeyError:
            return fail(-32602, f"unknown tool: {params['name']}")
    return fail(-32601, f"method not found: {method}")


def serve(stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> int:
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            response: Any = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            if isinstance(message, list):
                response = [r for r in (handle(m) for m in message if isinstance(m, Mapping)) if r is not None] or None
            elif isinstance(message, Mapping):
                response = handle(message)
            else:
                response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
        if response is not None:
            stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            stdout.flush()
    return 0


def main(argv: list[str]) -> int:
    if argv in (["--tool-names"], ["names"]):
        print(json.dumps({tool: agent_tool_name(tool) for tool in TOOLS}, indent=2))
        return 0
    if argv:
        print("usage: hands/mcp            (an MCP server on stdio)\n       hands/mcp --tool-names  (the names agents use)",
              file=sys.stderr)
        return 2
    return serve()
