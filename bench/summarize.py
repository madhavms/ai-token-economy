"""Turn the benchmark results into bench/RESULTS.md.

    python3 bench/summarize.py                       # every results/<date>-<model>.jsonl plus cache-ttl.jsonl

Three questions, answered from real runs:
  1. Model choice: the same tasks on Haiku, Sonnet and Opus with nothing added (arm "baseline").
  2. Templates: each tke template against the baseline, on Sonnet.
  3. Cache lifetime: a two-message session with a break longer than five minutes, with and without the 1h setting.
A template "earns its place" on a task when its median cost is at least 10% below the baseline's and it
passes at least as often. Medians, because one wandering run can double a mean.
"""
import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from run import ARMS, TASKS  # noqa: E402

WIN = 0.10
MODELS = ("haiku", "sonnet", "opus")


def load(paths):
    rows = []
    for p in paths:
        rows += [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]
    return [r for r in rows if r.get("cost") is not None]


def stats(rs):
    costs = sorted(r["cost"] for r in rs)
    return {"n": len(rs), "passed": sum(bool(r.get("passed")) for r in rs), "median": statistics.median(costs),
            "lo": costs[0], "hi": costs[-1], "turns": statistics.median(r.get("turns") or 0 for r in rs),
            "secs": statistics.median(r.get("seconds") or 0 for r in rs),
            "leaks": sum(bool(r.get("outside_paths")) for r in rs)}


def models_section(rows):
    by = defaultdict(list)
    for r in rows:
        if r["arm"] == "baseline":
            by[(r["model"], r["task"])].append(r)
    models = [m for m in MODELS if any(k[0] == m for k in by)]
    if len(models) < 2:
        return []
    L = ["## 1. Model choice", "",
         "The same three tasks with nothing added, on each model. Median cost per task (passed runs / runs).", "",
         "| Task | " + " | ".join(m.title() for m in models) + " |", "|---|" + "---:|" * len(models)]
    tot = defaultdict(float)
    for task in TASKS:
        cells = []
        for m in models:
            rs = by.get((m, task))
            if not rs:
                cells.append("—")
                continue
            s = stats(rs)
            tot[m] += s["median"]
            cells.append("$%.3f (%d/%d)" % (s["median"], s["passed"], s["n"]))
        L.append("| %s | %s |" % (task, " | ".join(cells)))
    if "sonnet" in tot:
        L += ["", "Sum of medians: " + ", ".join("%s $%.3f (%.2gx Sonnet)" % (m.title(), tot[m], tot[m] / tot["sonnet"])
                                                 for m in models) + "."]
    return L + [""]


def templates_section(rows, model="sonnet"):
    rs = [r for r in rows if r["model"] == model]
    by = defaultdict(list)
    for r in rs:
        by[(r["arm"], r["task"])].append(r)
    arms = [a for a in ARMS if any(k[0] == a for k in by)]
    if len(arms) < 2:
        return []
    L = ["## 2. Templates (on %s)" % model.title(), "",
         "| Arm | Task | Passed | Median cost | Range | Median turns | vs baseline |", "|---|---|---:|---:|---:|---:|---|"]
    tot = defaultdict(float)
    for task in TASKS:
        base = stats(by[("baseline", task)]) if by.get(("baseline", task)) else None
        for arm in arms:
            if not by.get((arm, task)):
                continue
            s = stats(by[(arm, task)])
            tot[arm] += s["median"]
            vs = "—"
            if base and arm != "baseline":
                ch = s["median"] / base["median"] - 1
                ok = s["passed"] / s["n"] >= base["passed"] / base["n"]
                vs = "%+.0f%% (%s)" % (ch * 100, "earns its place" if ch <= -WIN and ok else
                                       "costs more" if ch >= WIN else "no clear difference")
            L.append("| %s | %s | %d/%d | $%.3f | $%.3f–$%.3f | %g | %s |" % (
                arm, task, s["passed"], s["n"], s["median"], s["lo"], s["hi"], s["turns"], vs))
    if "baseline" in tot:
        L += ["", "Sum of medians: " + ", ".join("%s $%.3f (%+.0f%%)" % (a, tot[a], (tot[a] / tot["baseline"] - 1) * 100)
                                                 for a in arms) + "."]
    L += ["", "Three runs per cell: on tasks this short, a difference under about 15% is within run-to-run noise "
          "(one run that writes a wrong expected value into its own test and fixes it costs an extra cycle)."]
    L += ["", "What each arm adds:", ""] + ["- **%s**: %s" % (a, ARMS[a]) for a in arms]
    return L + [""]


def cache_section(path):
    if not path.exists():
        return []
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    rows = [r for r in rows if r.get("gap_s", 0) > 300]
    if not rows:
        return []
    L = ["## 3. Cache lifetime across a break", "",
         "One session, two messages, %d seconds apart (longer than the 5-minute default). The second message's first "
         "call shows whether the context was read back from the cache or written again. Measured signed in with a Claude "
         "subscription, where Claude Code writes the 1-hour cache by default; on an API key the default is 5 minutes."
         % rows[0]["gap_s"], "",
         "| Setting | Turn 1 cache writes (5 min / 1 h) | Turn 2 first call: read / written | Turn 2 cost |", "|---|---|---|---:|"]
    for r in rows:
        f = r.get("turn2_first_call") or {}
        L.append("| %s | %s / %s | %s / %s | $%.3f |" % (
            "default" if r["arm"] == "default" else '"promptCacheTtl": "1h"', format(r["turn1_ttl_buckets"]["w5"], ","),
            format(r["turn1_ttl_buckets"]["w1h"], ","), format(f.get("read", 0), ","), format(f.get("write", 0), ","),
            r["turn2_cost"]))
    return L + [""]


def main():
    res = HERE / "results"
    paths = sys.argv[1:] or sorted(str(p) for p in res.glob("*.jsonl") if re.match(r"\d{4}-\d\d-\d\d-[a-z]+\.jsonl$", p.name))
    rows = load(paths)
    leaks = sum(bool(r.get("outside_paths")) for r in rows)
    L = ["# Benchmark results", "",
         "Real `claude -p` runs on the practice repo in `bench/repo` (%d runs, %s). Costs are Claude Code's own "
         "list-price figure per run (`total_cost_usd`). Regenerate with `python3 bench/summarize.py`." % (
             len(rows), max(r["date"] for r in rows)), ""]
    L += models_section(rows) + templates_section(rows) + cache_section(res / "cache-ttl.jsonl")
    L += ["## How the runs are checked", "",
          "`fix` passes when every test passes and no test file was edited; `feature` when a hidden test "
          "(`bench/hidden/test_bulk.py`, copied in after the run) passes; `question` when the answer names 30 days, "
          "`grace_days` and `customers.py` and no code changed. Each run has its own temp folder, reads outside it are "
          "blocked, and no user plugins, skills or hooks load. Runs whose tool calls touched a path outside their "
          "folder: %d." % leaks, ""]
    text = "\n".join(L)
    (HERE / "RESULTS.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
