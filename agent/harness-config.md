# Agent harness configuration on the JupyterHub

How the coding-agent harness (Claude Code) is set up on the hub this repo is
developed on. It is about the tooling, not the pipeline, and it is machine
state: re-check it rather than trusting it, because it has drifted before. The
paths below are real and keep their names.

## As checked on 2026-11-10

- **User config is linked from a repo.** `~/.claude/settings.json`,
  `~/.claude/CLAUDE.md` and `~/.claude/templates` are symlinks into
  `~/claude-config/claude/` (`eeholmes/claude-config`), put there by
  `bootstrap.sh`. Editing settings therefore edits that repo; commit the
  change there.
- **Commit and PR attribution is generic.** User settings set
  `attribution.commit` to `Co-Authored-By: Agent <agent@noreply.invalid>`,
  `attribution.pr` to `🤖 Generated with an AI coding agent`, and
  `sessionUrl` to `false`. This repo's history was rewritten to the same
  trailer on 2026-11-10.
- **No git guard is configured.** `settings.json` has no hooks and no deny
  rules, so hard resets and force pushes are not blocked by the harness. Get
  explicit approval before either.
- **This project's memory is not backed up.**
  `~/.claude/projects/-home-jovyan-gobai-rfrom-icechunks/memory/` is a plain
  local directory, not a link into `~/claude-config`, which holds memory only
  for `gridlook` and `ohw24-…`. The root cause is tracked in
  `eeholmes/claude-config#1`: bootstrap links repo → hub only, so a memory
  directory first created on a hub is never adopted.
- **No account toggle is set up here.** Nothing in `~/.bashrc` or
  `~/.profile` configures Bedrock or other harness auth.

## What earlier notes said

Notes from 2026-09 described a different state: `settings.json` as a
standalone file mirrored by hand, a `deny-destructive-git.py` hook, a
personal-vs-Bedrock toggle with back-out notes in
`~/claude-bedrock-toggle-notes.md`, and (from 2026-09-17) every memory
directory, this project's included, symlinked into `~/claude-config`. None of
that is true on this hub now. It may describe another machine, or a state
that was later reset.
