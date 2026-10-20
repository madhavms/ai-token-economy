<!-- tke:delegation -->
## Subagents
- Routine changes start on a Sonnet lead. Start hard or risky changes, or a fix that failed review, on Opus,
  and hand the editing to the `implementer` subagent.
- `scoper` (or Codex `explorer`) finds code and tests on a small model; `implementer` writes code on Sonnet;
  `reviewer` checks the diff. Don't send design or root-cause decisions to the small model.
- Small, known edits (a line or two you already know): do them yourself, no subagent.
- Codex: start a subagent from its brief, not a copy of this conversation (spawn_agent with fork_turns "none").
- A follow-up fix goes to the same subagent (it still has its context), not a fresh one.
- Every brief has five lines. A subagent starts with no memory; what the brief leaves out, it re-discovers:

  Goal        one sentence: what must be true when you're done
  Where       the files and functions to start from (paths, line numbers)
  Done when   the test command that must pass, and what must not change
  Limits      don't edit outside <dir>; no new dependencies; stop and report if the plan looks wrong
  Report      at most 15 lines: files changed, tests run with results, open questions
<!-- /tke:delegation -->
