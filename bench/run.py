"""Template benchmark: does each tke template actually lower the bill without breaking the task?

Runs the real `claude -p` on a small practice repo (bench/repo) for every arm x task x repeat, each in its
own temp folder, and appends one JSON line per trial to bench/results/<run>.jsonl. `summarize.py` turns
that file into bench/RESULTS.md. Spends real money: about 45 Sonnet runs for the default matrix.

    python3 bench/run.py                       # default: 5 arms x 3 tasks x 3 repeats on sonnet
    python3 bench/run.py --arms baseline,rules --tasks fix --reps 1     # a quick smoke run
    python3 bench/run.py --run 2026-10-23-sonnet                        # resume: finished trials are skipped

Isolation (each of these once silently faked a result): a fresh OS temp folder per trial, reads outside it
blocked, project settings only (no user plugins, skills or hooks), the real claude binary (not a shell
wrapper), no CLAUDE_CODE_* / CMUX_* variables from the calling session, and an audit of every tool call for
paths outside the trial.
"""
import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TEMPLATES = ROOT / "token_economy" / "templates"
sys.path.insert(0, str(ROOT))
from token_economy import setup as tke_setup  # noqa: E402

ARMS = {
    "baseline": "nothing added: the repo's own short CLAUDE.md",
    "rules": "token rules appended to CLAUDE.md (tke setup project)",
    "hooks": "tke hooks (big-read guard, test-output trimming) and output caps in .claude/settings.json",
    "subagents": "Explore + scoper on Haiku, implementer and reviewer roles, delegation rules in CLAUDE.md",
    "all": "rules + hooks + subagents together",
}

TASKS = {
    "fix": {
        "prompt": ("A customer says invoice INV-20418 shows VAT of 2.20 on a 10.50 cable line, but their statement "
                   "says 2.21. The support log is in logs/app.log. Find the cause and fix the code so every test "
                   "passes. Run the tests to check."),
        "fixed_start": False,
    },
    "feature": {
        "prompt": ("Add a bulk discount: a line with 100 or more units gets 5% off that line, applied after any "
                   "promo discount and before the customer's loyalty discount, rounded half up to cents like the "
                   "other amounts. Add a test for it and run the tests."),
        "fixed_start": True,
    },
    "question": {
        "prompt": ("Don't change any files. After how many days past the due date does an invoice for an enterprise "
                   "customer count as overdue, and which function in which file decides that? Answer in two lines."),
        "fixed_start": True,
    },
}

MODEL_ALIASES = {"sonnet", "opus", "haiku"}
lock = threading.Lock()


# ------------------------------------------------------------------ claude binary and environment
def claude_bin():
    if os.environ.get("CLAUDE_BIN"):
        return os.environ["CLAUDE_BIN"]
    for c in (Path.home() / ".local" / "bin" / "claude", shutil.which("claude")):
        if c and Path(c).exists():
            return os.path.realpath(str(c))          # the real binary, not a terminal app's wrapper script
    raise SystemExit("claude not found; set CLAUDE_BIN")


def clean_env(tke_home):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CLAUDECODE", "CLAUDE_CODE_", "CMUX", "NODE_OPTIONS"))}
    env["PATH"] = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep) if "cmux" not in p.lower())
    env["TKE_HOME"] = str(tke_home)
    return env


# ------------------------------------------------------------------ trial setup
def _gen_big_files(repo, seed):
    """A 2 MB support log and a 400 KB data file: realistic noise that a careless agent reads whole."""
    rnd = random.Random(seed)
    (repo / "logs").mkdir()
    lines = []
    for i in range(16000):
        inv = "INV-%05d" % rnd.randint(10000, 29999)
        lvl = rnd.choice(["INFO"] * 8 + ["DEBUG"] * 3 + ["WARN"])
        lines.append("2026-09-%02dT%02d:%02d:%02dZ %-5s api.invoices request_id=%032x invoice=%s customer=c-%04d "
                     "status=%s latency_ms=%d" % (rnd.randint(1, 30), rnd.randint(0, 23), rnd.randint(0, 59),
                                                   rnd.randint(0, 59), lvl, rnd.getrandbits(128), inv,
                                                   rnd.randint(1, 9999), rnd.choice(["sent", "viewed", "paid"]),
                                                   rnd.randint(3, 900)))
    lines.insert(11873, "2026-09-18T09:41:07Z WARN  support.ticket ticket=T-7781 invoice=INV-20418 customer=c-0042 "
                        "message=\"VAT on line 'cable' 10.50 @ 21%% printed 2.20, statement says 2.21\"")
    (repo / "logs" / "app.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (repo / "data").mkdir()
    rows = [{"number": "INV-%05d" % i, "customer": "c-%04d" % rnd.randint(1, 9999),
             "lines": [{"description": rnd.choice(["widget", "cable", "chair", "bolts", "pen"]),
                        "quantity": rnd.randint(1, 250), "unit_price": "%d.%02d" % (rnd.randint(0, 99), rnd.randint(0, 99))}
                       for _ in range(rnd.randint(1, 4))]}
            for i in range(10000, 12200)]
    (repo / "data" / "invoices.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")


def _fix_bug(repo):
    p = repo / "invoicing" / "tax.py"
    s = p.read_text(encoding="utf-8")
    s = s.replace("return Decimal(round(raw, 2))", "return round_cents(raw)")
    s = s.replace("from .money import percent_of, to_money", "from .money import percent_of, round_cents, to_money")
    p.write_text(s, encoding="utf-8")


def _append(path, text):
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(old.rstrip() + "\n\n" + text.strip() + "\n", encoding="utf-8")


def _settings_for(arm):
    s = {}
    if arm in ("hooks", "all"):
        s["hooks"] = tke_setup.hook_block()
        s["env"] = {k: v for k, v in tke_setup.CLAUDE_ENV.items() if k != "CLAUDE_CODE_SUBAGENT_MODEL"}
    if arm in ("subagents", "all"):
        s.setdefault("env", {})["CLAUDE_CODE_SUBAGENT_MODEL"] = "sonnet"
    return s


def build_trial(arm, task, seed):
    base = Path(os.path.realpath(tempfile.mkdtemp(prefix="tke-bench-")))
    repo = base / "repo"
    shutil.copytree(HERE / "repo", repo, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    _gen_big_files(repo, seed)
    if TASKS[task]["fixed_start"]:
        _fix_bug(repo)
    if arm in ("rules", "all"):
        _append(repo / "CLAUDE.md", (TEMPLATES / "token-rules.md").read_text(encoding="utf-8"))
    if arm in ("subagents", "all"):
        agents = repo / ".claude" / "agents"
        agents.mkdir(parents=True)
        shutil.copy(TEMPLATES / "Explore.md", agents / "Explore.md")
        for f in (TEMPLATES / "roles" / "claude").glob("*.md"):
            shutil.copy(f, agents / f.name)
        _append(repo / "CLAUDE.md", (TEMPLATES / "delegation.md").read_text(encoding="utf-8"))
    settings = _settings_for(arm)
    if settings:
        (repo / ".claude").mkdir(exist_ok=True)
        (repo / ".claude" / "settings.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return base, repo


def tree_hash(repo, sub):
    h = hashlib.sha256()
    for p in sorted((repo / sub).rglob("*.py")):
        h.update(p.relative_to(repo).as_posix().encode())
        h.update(p.read_bytes())
    return h.hexdigest()


# ------------------------------------------------------------------ run and check
def run_claude(repo, prompt, model, tke_home, timeout):
    cmd = [claude_bin(), "-p", prompt, "--model", model, "--output-format", "stream-json", "--verbose",
           "--permission-mode", "auto", "--setting-sources", "project", "--no-session-persistence",
           "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
           "--settings", json.dumps({"permissions": {"blockReadsOutsideWorkingDirectories": True}})]
    t0 = time.time()
    try:
        out = subprocess.run(cmd, cwd=str(repo), env=clean_env(tke_home), stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                             timeout=timeout)
        stdout, err = out.stdout, out.stderr
    except subprocess.TimeoutExpired as e:
        stdout, err = (e.stdout or ""), "timeout"
    events = []
    for line in stdout.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    return events, err, time.time() - t0


def audit(events, base):
    """Absolute paths in tool inputs that point outside the trial folder (a leak would fake the result)."""
    base_s, bad = str(base), []
    for e in events:
        if e.get("type") != "assistant":
            continue
        for c in (e.get("message") or {}).get("content") or []:
            if isinstance(c, dict) and c.get("type") == "tool_use":
                text = json.dumps(c.get("input") or {})
                for tok in text.replace('"', " ").replace("'", " ").split():
                    if tok.startswith(("/Users/", "/home/", "/private/", "/var/", "/tmp/")) and not tok.startswith(base_s) \
                            and not tok.startswith(os.path.realpath(base_s)):
                        bad.append(tok[:120])
    return sorted(set(bad))[:10]


def tool_counts(events):
    counts = {}
    for e in events:
        if e.get("type") == "assistant":
            for c in (e.get("message") or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    counts[c.get("name")] = counts.get(c.get("name"), 0) + 1
    return counts


def check(task, repo, answer, before):
    """Did the trial do the task? ``before`` holds hashes of tests/ and invoicing/ taken before the run."""
    py = sys.executable
    tests_same = tree_hash(repo, "tests") == before["tests"]
    if task == "fix":
        r = subprocess.run([py, "-m", "unittest", "discover", "-s", "tests"], cwd=str(repo),
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        return {"passed": r.returncode == 0 and tests_same, "tests_ok": r.returncode == 0, "tests_untouched": tests_same}
    if task == "feature":
        shutil.copy(HERE / "hidden" / "test_bulk.py", repo / "tests" / "test_zz_hidden_bulk.py")
        r = subprocess.run([py, "-m", "unittest", "discover", "-s", "tests"], cwd=str(repo),
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        return {"passed": r.returncode == 0, "tests_ok": r.returncode == 0}
    text = (answer or "").lower()
    code_same = tree_hash(repo, "invoicing") == before["invoicing"]
    ok = "30" in text and "customers.py" in text and "grace_days" in text
    return {"passed": ok and code_same, "answer_ok": ok, "code_untouched": code_same}


def one_trial(run_file, arm, task, rep, model, timeout, keep=False):
    seed = 1000 + rep
    base, repo = build_trial(arm, task, seed)
    before = {"tests": tree_hash(repo, "tests"), "invoicing": tree_hash(repo, "invoicing")}
    events, err, secs = run_claude(repo, TASKS[task]["prompt"], model, base / "tke-home", timeout)
    result = next((e for e in reversed(events) if e.get("type") == "result"), None) or {}
    chk = check(task, repo, result.get("result", ""), before) if result else {"passed": False}
    usage = result.get("usage") or {}
    row = {
        "arm": arm, "task": task, "rep": rep, "model": model, "date": date.today().isoformat(),
        "cost": result.get("total_cost_usd"), "turns": result.get("num_turns"), "seconds": round(secs, 1),
        "is_error": result.get("is_error", True) if result else True, "error": None if result else err[-400:],
        "input_tokens": usage.get("input_tokens"), "cache_read": usage.get("cache_read_input_tokens"),
        "cache_write": usage.get("cache_creation_input_tokens"), "output_tokens": usage.get("output_tokens"),
        "model_usage": {k: round(v.get("costUSD", 0), 6) for k, v in (result.get("modelUsage") or {}).items()},
        "tools": tool_counts(events), "outside_paths": audit(events, base),
        "answer": (result.get("result") or "")[:600] if task == "question" else None,
    }
    row.update(chk)
    if keep:                                           # the full event stream, to see what an arm changed
        d = run_file.parent / "streams" / run_file.stem
        d.mkdir(parents=True, exist_ok=True)
        (d / ("%s-%s-%d.jsonl" % (arm, task, rep))).write_text(
            "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    with lock:
        with open(run_file, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    shutil.rmtree(base, ignore_errors=True)
    return row


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--tasks", default=",".join(TASKS))
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--jobs", type=int, default=3, help="trials at once (cost is the same; wall-clock isn't)")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--run", help="results file name (resume an earlier run)")
    ap.add_argument("--keep", action="store_true", help="also save each trial's event stream under results/streams/")
    a = ap.parse_args()
    arms, tasks = a.arms.split(","), a.tasks.split(",")
    for x in arms:
        if x not in ARMS:
            raise SystemExit("unknown arm %s (have %s)" % (x, ", ".join(ARMS)))
    for x in tasks:
        if x not in TASKS:
            raise SystemExit("unknown task %s (have %s)" % (x, ", ".join(TASKS)))
    (HERE / "results").mkdir(exist_ok=True)
    run_file = HERE / "results" / ((a.run or "%s-%s" % (date.today().isoformat(), a.model)) + ".jsonl")
    done = set()
    if run_file.exists():
        for line in run_file.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r.get("cost") is not None:
                done.add((r["arm"], r["task"], r["rep"]))
    todo = [(arm, task, rep) for rep in range(a.reps) for task in tasks for arm in arms if (arm, task, rep) not in done]
    print("%s: %d trials to run (%d already done), %s, %d at a time" % (run_file.name, len(todo), len(done), a.model, a.jobs))
    spent = 0.0
    with cf.ThreadPoolExecutor(max_workers=a.jobs) as pool:
        futs = {pool.submit(one_trial, run_file, arm, task, rep, a.model, a.timeout, a.keep): (arm, task, rep)
                for arm, task, rep in todo}
        for f in cf.as_completed(futs):
            arm, task, rep = futs[f]
            try:
                r = f.result()
            except Exception as e:                     # one broken trial must not stop the rest
                print("  %-9s %-8s #%d  FAILED TO RUN: %s" % (arm, task, rep, e))
                continue
            spent += r.get("cost") or 0
            print("  %-9s %-8s #%d  $%.3f  %s  %3ss  %s%s" % (
                arm, task, rep, r.get("cost") or 0, "pass" if r.get("passed") else "FAIL", int(r["seconds"]),
                r.get("turns"), "  OUTSIDE: %s" % r["outside_paths"] if r.get("outside_paths") else ""))
    print("spent about $%.2f (list price); results in %s" % (spent, run_file))


if __name__ == "__main__":
    main()
