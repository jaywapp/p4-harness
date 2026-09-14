---
name: p4-work
description: Perform source changes, review preparation, shelving, or Claude/Codex handoff in a Perforce workspace with p4-harness installed. Use when .p4-harness/config.json exists and the work concerns P4-managed files.
---

# P4 work

Read `.p4-harness/rules.md` if it is not already loaded. Use the same CLI for Claude and Codex:

```text
{{COMMAND}}
```

1. Inspect `doctor` and `status`. Continue an existing task only when its goal/scope and ownership match the request; otherwise preserve it and resolve the mismatch.
2. Start with `begin --task <id> --agent claude|codex --scope <path> --goal <goal>` when no task exists. For a read-only investigation, a task/checkout is unnecessary.
3. Read relevant sources. Prepare files before unsupported/shell edits; supported native file tools run the installed preparation hook. Use CLI `delete`/`move` for those P4 actions.
4. Run `collect`, inspect `changes`, and review each action against the request. New files need their full content reviewed; deleted and moved files are not covered by an edit diff alone.
5. Run the configured `verify <profile>` commands. If no checks exist, clearly report that verification is unconfigured.
6. As requested, `handoff --to <agent> --note <context>` or `shelve`; otherwise `finish` to produce the pending-CL report. Finish does not submit.

On a lock/auth/mapping conflict, stop the affected mutation and preserve the task. On interruption, use `pause` and provide enough information for `resume`; do not undo the work as cleanup. Keep source scope and snapshot evidence intact across agent changes.
