# Perforce workspace contract

This workspace uses Perforce. The harness source repository uses Git, but this installed contract applies only to the P4 workspace.

The shared command prefix is:

```text
{{COMMAND}}
```

- Read the current task with `status`; verify the actual client/root with `doctor`.
- Before source changes, `begin --task <id> --agent claude|codex --scope <path> --goal <goal>`. Choose the smallest useful file/directory scope. An existing dirty scope is refused; preserve it rather than clearing it.
- One workspace has one active editing task and one owner session. For parallel writers use separate P4 clients AND physical roots. Pending CLs alone do not isolate files.
- `prepare <file> ...` before editing through a shell or an unsupported tool. Native Claude Edit/Write and Codex apply_patch receive this preparation automatically through installed hooks.
- Never clear read-only attributes to replace checkout. An existing file opened in another CL belongs to that work. New paths must be reserved before writing.
- Use `delete <file>` and `move <source> <destination>` for P4 actions. A delete intentionally refuses existing local edits. Do not work around it with revert.
- `collect` adds only new files reserved by this task; it never performs a blanket reconcile mutation. Inspect `changes` and its manifest, including add/delete/move actions and actual file contents.
- Use `verify <profile>` for checks configured in `.p4-harness/config.json`. A changed file, changed base revision or changed check profile invalidates earlier evidence. No configured checks means `not_configured`, not a test pass.
- `handoff --to codex|claude --note <next step>` transfers the active task and CL. The next owner starts from the recorded goal, scope, snapshot and verification evidence, then reads the relevant code.
- For interruption, `pause --note <reason>` preserves changes. `resume --agent claude|codex` is an explicit recovery/ownership action; it does not sync or discard files.
- `shelve` is an explicit checkpoint action; it does not submit. `finish` creates a report and releases the active task only when configured required checks match the current snapshot. It leaves the pending files for the normal submission process.
- Raw submit/sync/revert/clean/force-resolve and server administration are outside the automatic coding workflow. The operator's normal P4 process owns these actions.
- Continue within the user's authorized task scope without asking for permission for every file. Ask only when intent/scope or an actual access control requires it.
- Report task ID, CL, snapshot ID, changed behavior, performed checks, unverified items and next action. Keep complete logs on disk and read relevant excerpts.

Hooks protect supported tool calls and catch common accidental P4 commands. They are not an OS sandbox or a comprehensive shell/MCP parser. Preserve normal client permissions and organizational P4 access controls. Do not edit harness/agent configuration as part of an ordinary source task.
