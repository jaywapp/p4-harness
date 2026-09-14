# Detailed P4 workflows

Load the relevant section only when needed. Use the command prefix in `rules.md`.

## Review and evidence

`collect` returns file/action summaries with a snapshot ID and full manifest path. For a long list, continue `changes --offset <next_offset>` until every relevant action has been reviewed. `changes --full` preserves the full manifest schema. For repeated inspection use `changes --since <previous_snapshot_id>`: introduced/updated/removed refer to manifest entries, not necessarily filesystem creation/deletion. This is a file-level selector, not a textual diff; files still need appropriate code review.

Inspect a tracked edit using a file-scoped `p4 diff -du <file>`, and read complete new file contents. Check deletions, moves and affected references. Verification is tied to the scoped snapshot and profile; external dependencies and ignored build outputs are not covered by that identity.

`verify --required` runs the configured required profiles in order and stops at the first failure. Successful output is short; failure output is a bounded heuristic excerpt, with the full log path. A missing keyword does not turn failure into success. Read the full log if the excerpt omits the cause. `verify <profile> --full` includes complete check metadata and the legacy tail, not the entire log.

## Handoff and recovery

Write a short note containing decisions, unresolved issues and the next action. Do not copy source files, raw logs or conversation history into the note. The receiver runs `context`, which checks current P4 state and compares recorded checks against the current snapshot. Run `collect` first if new reserved files have been written but not yet added.

`pause` preserves pending work. `resume` explicitly resets owner/session binding; stop the previous writer first. OS locks release when a harness process exits; do not delete the lock file. On auth, mapping, other-CL or exclusive-checkout errors, preserve files and fix the underlying condition through normal P4 procedures.

If a process stops after CL creation but before state is saved, inspect pending CL descriptions for `[p4-harness:<task>]` before starting again. Server mutations and local JSON writes are not one transaction. Do not rewrite baseline/state evidence to bypass an error.

Use separate client/root pairs for concurrent authors. Cross-client unshelve/integration is an operator workflow; automatic handoff here stays within the same physical workspace.
