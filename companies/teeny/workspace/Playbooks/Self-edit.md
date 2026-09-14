# Playbook: editing the definition

Read the `self-edit` skill; this is the short form.

- One concern per change.
- `opencompany check /data/company/teeny` before anything else.
- A `changes` row with the reason and what would falsify it.
- `touch /data/.reload-requested`, then stop.
- Never a credential, never the host's record under `/data/companies/`,
  never the orchestrator or the curator removed.
