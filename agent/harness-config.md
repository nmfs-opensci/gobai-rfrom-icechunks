# Agent harness configuration on the JupyterHub

How the coding-agent harness (Claude Code) is set up on the hub this repo is
developed on. Moved out of `docs/design/pipeline-history.md`, since it is about
the tooling, not the pipeline. The paths below are real and keep their names.

Not RFROM work, but it bites if assumed. The portable config lives in the
`~/claude-config` repo, symlinked into `~/.claude` by `bootstrap.sh`. Auth and
account toggling (personal vs Bedrock) is machine-local in `~/.bashrc` /
`~/.profile`, with back-out steps in `~/claude-bedrock-toggle-notes.md` (no
credentials in it).

On **this** hub `~/.claude/settings.json` is a standalone real file, not
bootstrapped: edit it directly *and* mirror portable changes into
`~/claude-config/claude/settings.json`.

A non-destructive-git guard is enforced by deny rules plus a
`deny-destructive-git.py` hook — hard resets and force pushes are blocked.

**Project memory on this hub is not symlinked into `~/claude-config`**, so it is
unbacked, and `gridlook`'s two copies have diverged into disjoint sets. Tracked
as `eeholmes/claude-config#1`, which also warns against re-running
`bootstrap.sh` here until that is merged.
