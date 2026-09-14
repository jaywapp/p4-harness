---
name: p4-work
description: Perform scoped source changes, review preparation or Claude/Codex handoff in a Perforce workspace containing .p4-harness/config.json. Use the shared p4-harness workflow for P4-managed files.
---

# P4 work

Read `.p4-harness/rules.md` if not already loaded. Shared CLI prefix:

```text
{{COMMAND}}
```

Use `context` for live task/CL/check/handoff information; investigate read-only without starting a task when appropriate. Follow existing scope and ownership, and read only relevant project-map/code sections.

After edits, use `collect` once, review its actions and relevant code, then `verify --required` (or a specific profile). Use `changes --since <snapshot_id>` for later file-list comparisons. Expand paginated/truncated output as needed. Failures point to full logs.

Finish or explicitly handoff/pause as requested. Read `.p4-harness/workflows.md` for detailed review and recovery. Keep handoff notes to decisions, unresolved issues and next actions; another agent needs a distinct purpose.
