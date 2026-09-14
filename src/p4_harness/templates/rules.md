# Perforce workspace contract

Shared CLI prefix for Claude Code and Codex:

```text
{{COMMAND}}
```

- Existing task: `context`. New task: `doctor`, then `begin --task <id> --agent claude|codex --scope <path> --goal <goal>`. Preserve existing changes; select a clean, narrow scope.
- One physical workspace has one editing task/session. Parallel writers require separate P4 clients AND physical roots. Use another agent only for a distinct review or handoff.
- Supported native edit hooks prepare files automatically. Before shell/unsupported edits, `prepare <file> ...`; reserve new paths before writing. Never clear read-only flags or take files from another CL.
- Use `delete`/`move` for those P4 actions. No automatic submit, sync, revert, clean, force resolve or server administration. Ordinary source tasks must not edit harness/agent settings.
- `collect` adds only reserved new files and returns a compact manifest view. Review all actions; follow pagination or use `--full` if truncated. `changes --since <snapshot_id>` selects changed file entries; read relevant code/diffs too.
- Run `verify <profile>` or `verify --required`. Required checks must match current files and configuration before `finish`. Unconfigured checks are not a pass. Failures retain original logs; read omitted details when needed.
- `handoff --to <agent> --note <decisions, remaining work, next action>` transfers the same CL. `pause --note ...` / `resume --agent ...` preserve work. `shelve` checkpoints explicitly. `finish` reports pending work for the existing submission process.
- Keep full logs/evidence on disk. Prefer compact output and changed files; avoid repeating inventories or conversation transcripts. Stored summaries never replace fresh authorization checks.
- Read relevant `.p4-harness/project-map.md` sections for navigation when populated, and `.p4-harness/workflows.md` for detailed review/recovery when needed. Avoid re-reading rules already in context.
- Continue within the user's authorized scope. Report task/CL, changed behavior, checks, unverified items and next action. Hooks cover supported calls/common mistakes; normal CLI permissions and P4 access controls remain necessary.
