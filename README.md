# ai-token-economy

**Make a monthly AI coding budget last the month.** `tke` reads the logs that Claude Code and Codex already write on your machine, tells you where the tokens went and when the budget will run out, and applies the settings and hooks that stop the biggest leaks.

It runs offline, has no dependencies (Python 3.8+ standard library), and **never calls a model, so it spends zero tokens.**

Real output from the author's machine:

```
$ python3 tke.py report
Claude  $421.05 of $500.00 this month   ########################....
        $42.11 a workday on average, $22.73 lasts the month  (1.9x the pace that lasts)
        at this rate it runs out on Fri 16 Oct

Where the Claude money went (month, 4277 calls)
  re-reading the prompt (cache reads)               $195.58   46%
  new tokens written / uncached input               $134.44   32%
  output and thinking                                $91.03   22%
    of which: rebuilds after breaks, avoidable       $28.20    7%
    calls with a prompt over 150k tokens            $322.57   77%
  -> Cache rebuilds after breaks: set "promptCacheTtl": "1h" ...
  -> Big contexts: /clear between tasks, one fresh session per phase ...

Most expensive sessions (month)
    $171.36  claude 1763 calls  peak  965k  ...  big context, 14 cache rebuilds, long session
```

The ideas behind it, with sources and diagrams: **[AI Token Economy](https://madhavms.github.io/knowledge-graph/ai-engineering/ai-token-economy.html)**. In short: a coding agent re-sends the whole conversation on every call, so the bill is set by how big that conversation gets, whether it is read from the prompt cache, and which model reads it.

## Quick start

```bash
git clone https://github.com/madhavms/ai-token-economy.git
cd ai-token-economy
python3 tke.py init --claude 500 --codex 13000     # your monthly budgets
python3 tke.py report                              # where it went, when it runs out
python3 tke.py setup                               # preview the recommended settings (changes nothing)
python3 tke.py setup --apply                       # apply them, with backups
```

On Windows use `py tke.py ...`. To get a `tke` command instead, `pip install .` from the clone.

## The control centre

```bash
python3 tke.py ui
```

Opens a local dashboard at http://127.0.0.1:8790 (the next free port if that one is taken). It reads the same logs, binds to your machine only, and never calls a model.

- **Overview:** each budget with a "where you should be by today" marker, today against your daily allowance, the run-out date, spend per day against the allowance, where the money went with the fix for each leak, and the costliest sessions.
- **Sessions:** click a session to see every call: how big the prompt was, where it crossed 150k tokens, where the cache went cold after a break, and the running cost. It then says in plain words what would have saved money, for example: *"The prompt passed 150k tokens at call 10. Every call after that re-read it: $142 (79% of this session)."*
- **Setup health:** checks your Claude Code and Codex settings (cache lifetime, default model and effort, subagent model, Explore agent, hooks, compaction backstop, Codex model and profiles), explains each gap, and applies the fix with one click. Backups are made and your own values are kept unless you tick the box.
- **Plan a task:** the routing questions and a cost estimate for a session before you start it.
- **Budget:** edit your monthly budgets in place.

Changes made from the page need a per-launch token that only the page receives, and requests from any host other than localhost are refused.

## Commands

| Command | What it does |
|---|---|
| `tke init --claude 500 --codex 13000` | Save your monthly budgets (dollars of Claude credit, Codex credits). |
| `tke report [today\|week\|month\|7d]` | Pace against budget and a run-out date, spend per day, where the money went (cache reads, writes, output, rebuilds after breaks, big contexts, frontier models, subagents), the costliest sessions with what went wrong in each, and the fix. `--json` for scripts. |
| `tke status` | One line per tool: today and this month against budget. |
| `tke setup [claude] [codex] [project]` | Apply the recommended settings. Dry run unless `--apply`. |
| `tke route` | Three yes/no questions, then the lane and the exact command: `claude --model ...` or `codex --profile ...`. |
| `tke estimate --model claude-opus-5-5 --calls 110 --start 32 --end 290 --breaks 5` | Price a session before you run it. |
| `tke bench <source-dir> gpt-oss:20b` | Time a local Ollama model reading an agent-sized prompt, GPU on and off, to see whether local is usable on this machine. |
| `tke ui` | The local control centre (see above). |
| `tke uninstall --apply` | Remove tke's hooks and status line from Claude Code. |

## What `setup` changes

Nothing until you pass `--apply`. Every edited file is backed up next to itself (`*.tke-backup-<time>`). Values you already set are kept unless you pass `--force`.

**Claude Code** (`~/.claude/settings.json`)

| Setting | Why |
|---|---|
| `"promptCacheTtl": "1h"` | On an API key or usage credits the cache lasts 5 minutes; every break longer than that re-writes the whole prompt at full price. |
| `"model": "sonnet"`, `"effortLevel": "medium"` | A budget default for building from a plan. Switch with `/model` at the start of a session for hard work. |
| `CLAUDE_CODE_SUBAGENT_MODEL=sonnet` | Subagents don't inherit Opus or Fable when you switch up. |
| `CLAUDE_CODE_AUTO_COMPACT_WINDOW=200000` | Compact at 200k instead of near 1M, as a backstop. |
| `BASH_MAX_OUTPUT_LENGTH`, `CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS` = 15000 | Smaller caps on one command's output and one file read. |
| `~/.claude/agents/Explore.md` | Runs the Explore subagent on Haiku 5.5 at low effort: searches cost cents, not dollars. |
| Status line | Model, session cost, today against your daily budget, month against budget. |
| Hooks | See below. |

**Hooks** (they run on your machine and cost no tokens; all fail open)

- **Big-file guard:** blocks a whole-file read or `cat` of a file over 60 KB and tells Claude to grep, then read a range.
- **Sleep guard:** blocks `sleep 120`-style polling, which re-sends the whole context on every wake-up.
- **Test-output trim:** pipes `pytest`, `npm test`, `go test`, `cargo test` and similar through `trim.py`, which keeps the failures and the summary.
- **Cold-cache warning:** if a session over 100k tokens has been idle longer than its cache lifetime, it blocks your next prompt once, shows what continuing will cost, and suggests `/clear`. Send again to continue.
- **Handoff notes:** after each turn it saves Claude's last reply and `git status` for the folder, and loads that note on the next start or `/clear`, so starting fresh doesn't lose the thread.

Thresholds are environment variables: `TKE_READ_MAX_BYTES`, `TKE_SLEEP_MAX_SEC`, `TKE_IDLE_CTX`, `TKE_NUDGE_CTX`, `TKE_TRIM_TESTS=0` to turn trimming off.

**Codex** (`~/.codex/config.toml`)

- `model = "gpt-6.1-sol"`, `model_reasoning_effort = "medium"`, `model_verbosity = "low"`, `tool_output_token_limit = 12000`, added only where missing.
- Profiles `luna` (chores) and `astra` (the frontier, on purpose): `codex --profile luna`.

**A project** (`tke setup project --dir path/to/repo`)

- Appends short token rules to `CLAUDE.md` and `AGENTS.md`, and adds a `SPEC.md` template: files, out of scope, the command that proves it works.

## How the numbers are computed

- **Claude Code:** `~/.claude/projects/**/*.jsonl`, including subagents. A reply that spans several lines is counted once, by message id and request id. Cost = input + 5-minute writes + 1-hour writes + cache reads + output, each at its own price.
- **Codex:** `~/.codex/sessions/**/*.jsonl`. Usage is a running total per session; tke takes the differences. Input includes cached input; credits use the Codex rate card, which has no write charge.
- **Rebuilds:** a call that writes at least 60% of a prompt over 20k tokens after more than the cache lifetime since the previous call. The avoidable part is what that write cost above reading the same tokens from cache.
- **Prices** are list prices as of 2026-10-13 in `token_economy/prices.py`. If your company pays contracted rates or prices change, put a `prices.json` in the config folder (`~/.config/token-economy/`, or `%APPDATA%\token-economy` on Windows):

```json
{"claude": {"claude-opus-5-5": {"inp": 4, "w5": 5, "w1h": 8, "read": 0.2, "out": 20}},
 "codex":  {"gpt-6.1-sol": {"inp": 50, "cached": 2.5, "out": 250}}}
```

The figures are estimates from local logs. On a subscription they are the API-equivalent value of your usage, not your bill. Usage from other machines or from claude.ai isn't on this machine and isn't counted.

## The habits the tool can't apply for you

1. One task, one session: `/clear` between unrelated tasks. After two failed corrections, `/clear` and write a better first prompt.
2. Split big tasks: a spec, then a plan in its own session (written to `PLAN.md`), then a fresh session to build, then a fresh one to review only the diff.
3. Don't switch models mid-session; each model has its own cache.
4. Use the frontier models (Fable, GPT-6 Astra) on purpose, for one hard problem.
5. Local models (Ollama) are an overflow tier: on a laptop CPU they take minutes to read an agent's prompt. Run `tke bench` to see your machine. Keep company code off cloud-hosted and free model tiers.

## Development

```bash
python3 -m unittest discover -s tests -v
```

MIT licensed. Not affiliated with Anthropic or OpenAI.
