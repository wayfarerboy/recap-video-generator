---
name: orchestrate
description: Runs a Sandcastle-style autonomous issue orchestration loop against this repo's GitHub issues. Use when the user says "run orchestrate", "process issues", "work on issues", "start the agent loop", or asks to autonomously implement ready-for-agent issues.
---

# Orchestrate

Three-phase loop (up to 10 iterations) that autonomously works through `ready-for-agent` GitHub issues:

1. **Plan** — validate the working branch, pull, fetch issues, build a dependency graph, create agent branches
2. **Implement & Review** — up to 4 parallel issue-handlers per iteration (TDD implementation then two-axis review against the repo's coding standards and the issue body as the spec)
3. **Merge** — merge completed branches, run tests, update issue labels, force-with-lease push the working branch

Treat your current git branch as the working branch. Agent branches are created from it and merged back into it. If you're not on `dev`, warn once at loop start but continue. Read `orchestrate.reviewLabel` (default: none) from `.pi/settings.json` if the `orchestrate` key exists. Merging the working branch into `main` is always a manual step the user performs themselves.

## Loop start — Validate the working branch

Before entering the Plan step, run these checks. Abort with an explanation if any fail:

1. **Clean working tree** — `git status --porcelain` must be empty. Dirty tree → abort. "Working tree is not clean. Commit or stash changes before running /orchestrate."
2. **Not on an agent branch** — `git branch --show-current` must not start with `agent/`. On an agent branch → abort. "You're on an agent branch (agent/…). Check out your working branch first."
3. **Upstream tracking** — `git rev-parse --abbrev-ref --symbolic-full-name @{u}` must succeed. No upstream → abort. "This branch has no upstream tracking set. Run `git push -u origin {branch}` first."

After passing all checks, warn if not on `dev` — once per invocation:

```
⚠ You're on `{branch}`, not `dev`. Proceeding with `{branch}` as the working branch.
```

### Create isolated worktree

**Critical: the orchestrator must NEVER switch branches or modify files in the main working tree.** All working-branch operations (pull, merge, push, tests) happen in a dedicated worktree created here:

```bash
WT=$(mktemp -d)
git worktree add --force "$WT" {working-branch}
```

Store `$WT` for the entire loop. From this point forward, every git operation that touches the working branch uses `git -C "$WT"`; every file read/write/edit inside the working tree uses `$WT/`-prefixed paths; every test run via `cd "$WT" && python3 -m pytest tests/`. The main working tree (your cwd) is read-only from here on.

When the loop ends (all iterations complete or abort):

```bash
git worktree remove "$WT"
```

## Each iteration

### Step 1 — Fetch and plan

Pull the working branch first (inside the worktree — never in the main tree):

```bash
git -C "$WT" pull
```

If `dev` doesn't exist locally (the working branch name literal `dev`), abort: "You're not on the dev branch and the `dev` branch doesn't exist locally. Create it first (run setup or `git checkout -b dev`)."

Then fetch issues:

```bash
gh issue list --state open --label ready-for-agent --limit 100 --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'
```

Parse the JSON yourself. Build a dependency graph — issue B is blocked by A if:
- B requires code or infrastructure that A introduces
- B and A modify overlapping files likely to cause merge conflicts
- B's requirements depend on an API shape that A will establish

Keep only unblocked issues (up to 4). If all are blocked, take the single highest-priority candidate.

For each kept issue, delete any pre-existing branch with the same name, then create it off the working branch — namespaced to prevent collisions across working branches:

```bash
git -C "$WT" branch --list "agent/{working-branch}/issue-{number}" && git -C "$WT" branch -D "agent/{working-branch}/issue-{number}"
git -C "$WT" branch "agent/{working-branch}/issue-{number}" {working-branch}
```

If the issue list is empty, stop and report.

### Step 2 — Implement & Review

Each handler implements the issue using TDD, runs tests, then does a two-axis review — Standards (against this repo's documented coding standards) and Spec (against the issue body, which is the spec the ticket came from). After review, it commits.

**CRITICAL: Always use parallel mode with `worktree: true`, even for a single issue.** Never launch an issue-handler as a single agent — single-agent mode runs in the main working tree and will pollute it. Parallel mode with `worktree: true` isolates every handler in its own temporary worktree:

```json
{
  "tasks": [
    {
      "agent": "issue-handler",
      "task": "Fix issue #N: <title>\n\nBranch: agent/{working-branch}/issue-N\nBase: {working-branch}\n\n<body>"
    }
  ],
  "worktree": true
}
```

Each handler runs in a temporary git worktree (isolated filesystem), checks out its `agent/{working-branch}/issue-N` branch, and works without affecting the main working tree or other handlers.

### Step 3 — Merge

All merge operations happen inside the worktree (`$WT`). The main working tree is never touched.

For each issue-handler that committed its branch:

1. `git -C "$WT" checkout {working-branch}` (usually a no-op — it's already on this branch)
2. `git -C "$WT" merge "agent/{working-branch}/issue-{number}" --no-edit`
3. Resolve any conflicts — read `$WT/path/to/file` (use the `read` tool with the `$WT` prefix), edit files in the worktree with the `edit`/`write` tools, then `git -C "$WT" add ...` and `git -C "$WT" commit ...`
4. Remove any stray scratch/summary files the handler left behind: `rm -f "$WT/issue-{number}-result.md"` (check for other common names too)
5. Run `cd "$WT" && python3 -m pytest tests/` — fix failures inside the worktree before continuing
6. `gh issue edit {number} --remove-label "ready-for-agent"`
7. If `orchestrate.reviewLabel` is configured: ensure the label exists (`gh label list`, then `gh label create "{reviewLabel}"` if missing), then `gh issue edit {number} --add-label "{reviewLabel}"`

After all issues in the iteration are processed, push once with force-with-lease from the worktree:

```bash
git -C "$WT" push --force-with-lease origin {working-branch}
```

## Loop logic

- Max **10 iterations**, counting from 1
- After each iteration: if the issue list is empty, stop and report total iterations run
- If a handler fails, log it and continue — do not abort the iteration
- Report after each iteration: issues planned / committed / merged
