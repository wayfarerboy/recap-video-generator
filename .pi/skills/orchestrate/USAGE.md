# Orchestrate — Usage Guide

Runs the autonomous issue loop: plan → implement+review → merge, repeated until the `ready-for-agent` queue is empty (or 10 iterations).

## Installation

**1. Install pi-subagents** (once per machine, not per project):

```bash
pi install npm:pi-subagents
```

**2. That's it.** Pi automatically discovers `.pi/skills/`, `.pi/agents/`, and `.pi/settings.json` when you open a session in this directory. No manual registration needed.

To verify the skill is loaded, ask Pi in a session:

```
what skills do you have?
```

You should see `orchestrate` in the list.

## Prerequisites

1. **Run `/setup-matt-pocock-skills`** in this repo first — this configures the GitHub issue tracker and wires up the agent skill config
2. **Tests pass** on your working branch before you start — workers will fail if the baseline is already broken:
   ```bash
   python3 -m pytest tests/
   ```

## Triggering the skill

In a Pi session, say any of:

```
run orchestrate
process my ready-for-agent issues
start the agent loop
work on issues autonomously
```

Pi picks up the skill automatically and begins the first iteration.

## What happens

### Iteration start

The main Pi session runs plan and merge directly (no scout/merger subagents) and only delegates the implement step. You'll see three phases per iteration:

**Phase 1 — Plan**
Pi validates the working branch (clean tree? upstream tracking set? not on an agent branch?), warns if you're not on `dev`, pulls, runs `gh issue list --label ready-for-agent`, and builds a dependency graph itself. For each unblocked issue it creates a branch (`agent/{working-branch}/issue-42`) off the working branch. It prints which issues it selected and their assigned branches. If the queue is empty it stops here.

**Phase 2 — Implement + Review**
Up to 4 `issue-handler` subagents run in parallel. Each one:
- Fetches the full issue with comments
- Checks out the branch already created for it
- Implements using TDD (red → green → refactor)
- Runs `python3 -m pytest tests/` before committing
- Commits with a `Fixed #N: ...` message
- Self-reviews its own diff and commits refinements if needed

**Phase 3 — Merge**
Pi merges every branch where commits were made into the working branch, resolves any conflicts, runs tests after each merge, removes the `ready-for-agent` label from each merged issue (and applies the configured review label, if any), then force-with-lease pushes the working branch once at the end of the iteration.

Pi never closes issues and never touches `main`. Issues close automatically, later, when you manually merge the working branch into `main` — GitHub recognizes the `Fixed #N` keyword in the commit history at that point.

### Iteration end

Pi reports a summary:
```
Iteration 1/10: 3 issues planned → 3 committed → 3 merged
```

Then starts the next iteration unless the queue is empty.

## Configuring concurrency and model

Edit `.pi/settings.json`:

```json
{
  "parallel": {
    "concurrency": 4
  },
  "subagents": {
    "agentOverrides": {
      "issue-handler": { "model": "deepseek-v4-pro" }
    }
  }
}
```

Change `concurrency` to run more or fewer issue-handlers in parallel. Change `model` to use a faster/cheaper model for the implement step.

## Configuring a review label

If you want issues to get a label for human sign-off after merging, add an `orchestrate` block to `.pi/settings.json`:

```json
{
  "orchestrate": {
    "reviewLabel": "needs-client-signoff"
  }
}
```

- `reviewLabel` — optional. If set, this label is applied to each issue (alongside removing `ready-for-agent`) when its branch is merged, so a human reviewer can find work that's ready for sign-off. Created on GitHub automatically the first time it's used if it doesn't already exist.

## Stopping early

Tell Pi at any point:
```
stop after this iteration
```
Pi will finish the current iteration cleanly and not start another.

## Troubleshooting

**Plan step returns 0 issues**
No open issues are labelled `ready-for-agent`. Apply the label in GitHub and re-trigger.

**An issue-handler fails mid-iteration**
The failed issue is logged and skipped. Other issues in that iteration continue. The next iteration will re-plan and may pick the failed issue up again if it's now unblocked.

**Merge conflicts Pi can't resolve**
Pi will leave a comment on the issue and move on. Resolve the conflict manually on the branch, then re-trigger orchestrate — the next plan step will see the issue is still labelled `ready-for-agent` and re-queue it.

**Tests fail before you start**
Fix the baseline first on your working branch. Workers run `python3 -m pytest tests/` before committing — if those are already broken they'll abort and report it.

**An issue never closes even though its branch merged**
This is expected — orchestrate never closes issues. They close automatically once you merge the working branch into `main` yourself, because the merged commits contain `Fixed #N`.
