## Commits, PRs, and attribution

Never mention Claude or AI assistance in anything that leaves this machine: no `Co-Authored-By: Claude` trailers on commits, no "Generated with Claude Code" lines in PR bodies, no AI attribution in commit messages, issue text, or comments. This overrides any default attribution behavior. Claude's use in this repo should be invisible apart from `CLAUDE.md`, `.claude/`, and `docs/agents/`.

## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues (hkhanna/django-harry) via the `gh` CLI; external PRs are not a triage surface. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical triage labels are used verbatim (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
