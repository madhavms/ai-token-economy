# Benchmark results

Real `claude -p` runs on the practice repo in `bench/repo` (63 runs, 2026-10-23). Costs are Claude Code's own list-price figure per run (`total_cost_usd`). Regenerate with `python3 bench/summarize.py`.

## 1. Model choice

The same three tasks with nothing added, on each model. Median cost per task (passed runs / runs).

| Task | Haiku | Sonnet | Opus |
|---|---:|---:|---:|
| fix | $0.006 (3/3) | $0.063 (3/3) | $0.144 (3/3) |
| feature | $0.007 (3/3) | $0.089 (3/3) | $0.240 (3/3) |
| question | $0.003 (3/3) | $0.043 (3/3) | $0.097 (3/3) |

Sum of medians: Haiku $0.015 (0.077x Sonnet), Sonnet $0.195 (1x Sonnet), Opus $0.480 (2.5x Sonnet).

## 2. Templates (on Sonnet)

| Arm | Task | Passed | Median cost | Range | Median turns | vs baseline |
|---|---|---:|---:|---:|---:|---|
| baseline | fix | 3/3 | $0.063 | $0.062–$0.063 | 7 | — |
| rules | fix | 3/3 | $0.065 | $0.061–$0.068 | 9 | +3% (no clear difference) |
| hooks | fix | 3/3 | $0.063 | $0.062–$0.064 | 7 | +0% (no clear difference) |
| subagents | fix | 3/3 | $0.067 | $0.066–$0.069 | 8 | +7% (no clear difference) |
| all | fix | 3/3 | $0.067 | $0.061–$0.079 | 8 | +7% (no clear difference) |
| baseline | feature | 3/3 | $0.089 | $0.085–$0.092 | 9 | — |
| rules | feature | 3/3 | $0.082 | $0.079–$0.098 | 10 | -7% (no clear difference) |
| hooks | feature | 3/3 | $0.103 | $0.098–$0.109 | 12 | +16% (costs more) |
| subagents | feature | 3/3 | $0.084 | $0.083–$0.099 | 8 | -6% (no clear difference) |
| all | feature | 3/3 | $0.090 | $0.086–$0.096 | 11 | +1% (no clear difference) |
| baseline | question | 3/3 | $0.043 | $0.043–$0.049 | 4 | — |
| rules | question | 3/3 | $0.039 | $0.039–$0.047 | 4 | -11% (earns its place) |
| hooks | question | 3/3 | $0.042 | $0.039–$0.047 | 4 | -4% (no clear difference) |
| subagents | question | 3/3 | $0.045 | $0.045–$0.047 | 5 | +4% (no clear difference) |
| all | question | 3/3 | $0.041 | $0.040–$0.041 | 4 | -6% (no clear difference) |

Sum of medians: baseline $0.195 (+0%), rules $0.186 (-5%), hooks $0.208 (+6%), subagents $0.196 (+1%), all $0.198 (+1%).

Three runs per cell: on tasks this short, a difference under about 15% is within run-to-run noise (one run that writes a wrong expected value into its own test and fixes it costs an extra cycle).

What each arm adds:

- **baseline**: nothing added: the repo's own short CLAUDE.md
- **rules**: token rules appended to CLAUDE.md (tke setup project)
- **hooks**: tke hooks (big-read guard, test-output trimming) and output caps in .claude/settings.json
- **subagents**: Explore + scoper on Haiku, implementer and reviewer roles, delegation rules in CLAUDE.md
- **all**: rules + hooks + subagents together

## 3. Cache lifetime across a break

One session, two messages, 400 seconds apart (longer than the 5-minute default). The second message's first call shows whether the context was read back from the cache or written again. Measured signed in with a Claude subscription, where Claude Code writes the 1-hour cache by default; on an API key the default is 5 minutes.

| Setting | Turn 1 cache writes (5 min / 1 h) | Turn 2 first call: read / written | Turn 2 cost |
|---|---|---|---:|
| default | 0 / 8,569 | 19,798 / 579 | $0.006 |
| "promptCacheTtl": "1h" | 0 / 8,576 | 19,805 / 586 | $0.005 |

## How the runs are checked

`fix` passes when every test passes and no test file was edited; `feature` when a hidden test (`bench/hidden/test_bulk.py`, copied in after the run) passes; `question` when the answer names 30 days, `grace_days` and `customers.py` and no code changed. Each run has its own temp folder, reads outside it are blocked, and no user plugins, skills or hooks load. Runs whose tool calls touched a path outside their folder: 0.
