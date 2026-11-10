# Agent harness configuration on the JupyterHub

How the coding-agent harness (Claude Code) is set up on the hub this repo is
developed on. It is about the tooling, not the pipeline. The source of truth is
the `~/claude-config` repo (`eeholmes/claude-config`); this file only records
what matters here. The paths below are real and keep their names.

## How it is wired

- `bootstrap.sh` in `~/claude-config` links `~/.claude/CLAUDE.md`,
  `templates/`, `hooks/` and `settings.json` into that repo, adds the account
  launchers (`claude-personal`, `claude-bedrock`, `claude-cloudbank`,
  `claude-vertex`) to `~/.local/bin`, and links each project's memory
  directory, this one's included, into `claude/memory/`. Editing settings or
  memory therefore edits that repo; commit there, on a branch with a PR.
- **Destructive git is guarded.** `permissions.deny` rules plus the
  `PreToolUse(Bash)` hook `~/.claude/hooks/deny-destructive-git.py` block
  `git reset --hard` and `git push --force`/`-f`. `--force-with-lease` is
  allowed.
- **Attribution is generic**: `Co-Authored-By: Agent <agent@noreply.invalid>`
  on commits, `🤖 Generated with an AI coding agent` on PRs, no session URL.
  This repo's history was rewritten to the same trailer on 2026-11-10.

## Keep the checkout current

A `SessionStart` hook warns when `~/claude-config` is behind. Heed it. On
2026-11-10 the hub's checkout was 143 commits stale: the guard hook and memory
links were missing, so none of the above held until a pull and a re-run of
`bootstrap.sh`. After pulling, re-run `bootstrap.sh`, since the pull can move
files the `~/.claude` links point at. Until it runs, the guard hook's missing
script makes every Bash call fail, so run it yourself with `!` from the prompt.
