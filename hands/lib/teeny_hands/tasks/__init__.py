"""hands/tasks: the browser operator's queue (docs/hands.md, "tasks").

  queue  the `browser_tasks` table: add, claim, finish, list
  cli    the verbs

The operator agent (brain/agents/operator.toml) claims a task and reports it
through `hands/mcp`; playbooks and the operator (the person) add tasks with
`hands/tasks add`. Money never moves here: a payment checkpoint ends a task
as needs_human, and only hands/wallet pays.
"""
