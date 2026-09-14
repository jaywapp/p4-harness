# Developing p4-harness

This repository is the Git source of a provider-neutral Perforce harness. Target-workspace rules live in `src/p4_harness/templates`; do not use P4 to check out files in this Git repository.

- Keep Claude Code and Codex on the same workflow core and state format. Provider differences belong in installation/hook adapters.
- Keep the core Python 3.11+ and standard-library-only. Support Windows paths and UTF-8 alongside POSIX.
- Preserve existing user changes, settings, hooks and pending changelists. Do not implement automatic submit, revert, cleanup, force resolve or broad sync.
- Use argv subprocesses, scoped paths, structured P4 output and durable task state. Never treat auth/network/mapping errors as a clean workspace.
- Do not claim a hook or regex is a security sandbox. Keep limitations and validation evidence explicit.
- Verify meaningful changes with `python -m unittest discover -s tests -v`. For P4 semantics set `P4_BIN` and `P4D_BIN` to local binaries and run the disposable-server integration suite. Never point tests at a user's server.
- Keep instructions and skill templates compact. Update Korean user documentation when behavior changes. Engine-specific workflows are outside this core.
