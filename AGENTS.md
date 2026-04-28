# Repository Guidelines

If `CLAUDE_HOME` has not been resolved this session, resolve it now.

If the root `CLAUDE.md` directly inside `CLAUDE_HOME` has not been read this session, read it now.

After reading the root `CLAUDE.md`, read every always-on rule in the `.claude/rules/` directory located directly under `CLAUDE_HOME`, plus any path-scoped rule matching the current working directory or files being touched.

If the current working directory or nearest ancestor below `CLAUDE_HOME` has `CLAUDE.md` and it has not been read this session, read it next.

Do not search above `CLAUDE_HOME`. Do not reread a `CLAUDE.md` unless the user says it changed.

Do not modify `AGENTS.md` unless explicitly asked to edit agent instructions.
