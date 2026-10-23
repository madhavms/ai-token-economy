"""Does the 1-hour prompt cache pay for itself across a coffee break?

One session, two messages, a pause between them longer than the default 5-minute cache. Arm "default"
leaves the cache lifetime alone; arm "1h" sets "promptCacheTtl": "1h" in the project settings (what
`tke setup claude` does). The interesting number is the second message's first call: a warm cache reads the
context back at a tenth of the price, a cold one writes all of it again. The usage also shows which cache
the session actually used (ephemeral_5m vs ephemeral_1h), which depends on how you pay.

    python3 bench/cache_ttl.py --gap 400 --reps 2      # about 7 minutes of wall-clock, a few cents per run
"""
import argparse
import concurrent.futures as cf
import json
import os
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run as bench  # noqa: E402

FIRST = ("Read invoicing/invoice.py, invoicing/discounts.py, invoicing/tax.py and invoicing/money.py, then explain in "
         "ten lines how an invoice total is computed, in order.")
SECOND = "Which line applies the loyalty discount, and before or after VAT? Two lines."


def send(proc, text):
    proc.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n")
    proc.stdin.flush()


def read_turn(proc):
    """Events up to and including the next result."""
    events = []
    for line in proc.stdout:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        events.append(e)
        if e.get("type") == "result":
            break
    return events


def calls(events):
    out = []
    for e in events:
        if e.get("type") == "assistant" and not e.get("parent_tool_use_id"):
            u = e["message"].get("usage") or {}
            cc = u.get("cache_creation") or {}
            out.append({"read": u.get("cache_read_input_tokens", 0), "write": u.get("cache_creation_input_tokens", 0),
                        "w5": cc.get("ephemeral_5m_input_tokens", 0), "w1h": cc.get("ephemeral_1h_input_tokens", 0)})
    dedup, seen = [], set()
    for c in out:                                  # one reply can span several events with the same usage
        k = tuple(sorted(c.items()))
        if k not in seen:
            seen.add(k)
            dedup.append(c)
    return dedup


def one(arm, rep, gap, model):
    base, repo = bench.build_trial("baseline", "question", 2000 + rep)
    if arm == "1h":
        (repo / ".claude").mkdir(exist_ok=True)
        (repo / ".claude" / "settings.json").write_text(json.dumps({"promptCacheTtl": "1h"}), encoding="utf-8")
    cmd = [bench.claude_bin(), "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
           "--model", model, "--permission-mode", "auto", "--setting-sources", "project", "--no-session-persistence",
           "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
           "--settings", json.dumps({"permissions": {"blockReadsOutsideWorkingDirectories": True}})]
    proc = subprocess.Popen(cmd, cwd=str(repo), env=bench.clean_env(base / "tke-home"), stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True, bufsize=1)
    send(proc, FIRST)
    t1 = read_turn(proc)
    r1 = next(e for e in reversed(t1) if e.get("type") == "result")
    time.sleep(gap)
    send(proc, SECOND)
    t2 = read_turn(proc)
    r2 = next(e for e in reversed(t2) if e.get("type") == "result")
    proc.stdin.close()
    proc.wait(timeout=60)
    c1, c2 = calls(t1), calls(t2)
    row = {"arm": arm, "rep": rep, "gap_s": gap, "model": model, "date": date.today().isoformat(),
           "turn1_cost": r1.get("total_cost_usd"), "cumulative_cost": r2.get("total_cost_usd"),
           "turn2_cost": (r2.get("total_cost_usd") or 0) - (r1.get("total_cost_usd") or 0),
           "turn1_ttl_buckets": {"w5": sum(c["w5"] for c in c1), "w1h": sum(c["w1h"] for c in c1)},
           "turn2_first_call": c2[0] if c2 else None, "turn2_calls": len(c2)}
    with open(HERE / "results" / "cache-ttl.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gap", type=int, default=400, help="seconds between the two messages (default 400 > 5 min)")
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--model", default="sonnet")
    a = ap.parse_args()
    (HERE / "results").mkdir(exist_ok=True)
    jobs = [(arm, rep) for rep in range(a.reps) for arm in ("default", "1h")]
    with cf.ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        for r in pool.map(lambda j: one(j[0], j[1], a.gap, a.model), jobs):
            f = r["turn2_first_call"] or {}
            print("%-7s #%d  turn 1 $%.3f (cache writes 5m %d / 1h %d)  turn 2 $%.3f  first call: read %d, wrote %d" % (
                r["arm"], r["rep"], r["turn1_cost"] or 0, r["turn1_ttl_buckets"]["w5"], r["turn1_ttl_buckets"]["w1h"],
                r["turn2_cost"], f.get("read", 0), f.get("write", 0)))


if __name__ == "__main__":
    main()
