"""Measure the numbers on the basics page with the real Claude Code CLI.

Runs `claude -p` (your logged-in session, or an API key if that's how you use it) against a copy of
the toy repo in a temp folder and writes measured.json. Every number on the page comes from that file.
It spends a few cents: the toy fix runs on Sonnet 5.5 and the scoper subagent on Haiku 5.5.

    python3 docs/basics/measure.py            # needs `claude` on PATH, or set CLAUDE_BIN
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
from token_economy import analyze, config, logs, prices  # noqa: E402

CLAUDE = os.environ.get("CLAUDE_BIN") or shutil.which("claude") or str(Path.home() / ".local/bin/claude")
MODEL = "sonnet"
MINIMAL = ["--setting-sources", "project", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
           "--disable-slash-commands"]

ENGLISH = ("Every call to a language model sends the whole conversation again, because the model keeps no "
           "memory between calls. The program around the model, called the harness, stores the conversation "
           "and replays it: the instructions, the list of tools, your messages, every file it has read and "
           "every command it has run. The bill is set by how much text that replay contains, how often it "
           "happens, and how much of it can be read back from the prompt cache instead of being processed again.")
CODE = (HERE / "toy" / "shop" / "cart.py").read_text(encoding="utf-8") * 3


def run(cwd, prompt, extra=(), model=MODEL, persist=False):
    sid = str(uuid.uuid4())
    cmd = [CLAUDE, "-p", prompt, "--model", model, "--output-format", "stream-json", "--verbose",
           "--permission-mode", "auto"] + list(extra)
    cmd += ["--session-id", sid] if persist else ["--no-session-persistence"]
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CMUX"))}
    out = subprocess.run(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, universal_newlines=True, timeout=900)
    first, result = None, None
    for line in out.stdout.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") == "assistant" and first is None and not d.get("parent_tool_use_id"):
            first = d["message"]["usage"]
        if d.get("type") == "result":
            result = d
    if result is None:
        raise SystemExit("claude failed: %s" % out.stderr[-800:])
    return sid, first, result


def prompt_tokens(u):
    return (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0))


def transcript(sid):
    root = config.claude_home() / "projects"
    for _ in range(20):
        hits = list(root.glob("*/%s.jsonl" % sid))
        if hits:
            return hits[0]
        time.sleep(0.5)
    raise SystemExit("no transcript for %s" % sid)


def toy_copy(with_claude_md=True, with_agent=False):
    d = Path(tempfile.mkdtemp(prefix="tke-basics-"))
    shutil.copytree(HERE / "toy", d / "repo", ignore=shutil.ignore_patterns("__pycache__"))
    repo = d / "repo"
    if not with_claude_md:
        (repo / "CLAUDE.md").unlink()
    if not with_agent:
        shutil.rmtree(repo / ".claude")
    return repo


def main():
    out = {"date": date.today().isoformat(), "prices_date": prices.PRICES_DATE}
    out["claude_version"] = subprocess.run([CLAUDE, "--version"], stdout=subprocess.PIPE, universal_newlines=True).stdout.strip()
    hello = "Reply with the single word OK."

    # 1. startup context: an empty folder, minimal setup, then the same with the project's CLAUDE.md
    bare = toy_copy(with_claude_md=False)
    _, u0, r0 = run(bare, hello, MINIMAL)
    _, u0b, _ = run(bare, hello, MINIMAL)                     # straight away: the prefix is in the cache
    withmd = toy_copy(with_claude_md=True)
    _, u1, _ = run(withmd, hello, MINIMAL)
    _, u_full, r_full = run(bare, hello)                       # this machine's normal setup
    md = (HERE / "toy" / "CLAUDE.md").read_text(encoding="utf-8")
    out["startup_minimal"] = prompt_tokens(u0)
    out["startup_full"] = prompt_tokens(u_full)
    out["hello_first"] = u0
    out["hello_second"] = u0b
    out["claude_md_tokens"] = prompt_tokens(u1) - prompt_tokens(u0)
    out["claude_md_chars"] = len(md)
    out["claude_md_lines"] = md.count("\n")
    out["hello_cost_minimal"] = r0.get("total_cost_usd")

    # 2. tokens per character: the same prompt with an English paragraph, then with Python code
    _, ue, _ = run(bare, hello + "\n\n" + ENGLISH, MINIMAL)
    _, uc, _ = run(bare, hello + "\n\n" + CODE, MINIMAL)
    out["english_chars"], out["english_tokens"] = len(ENGLISH) + 2, prompt_tokens(ue) - prompt_tokens(u0)
    out["code_chars"], out["code_tokens"] = len(CODE) + 2, prompt_tokens(uc) - prompt_tokens(u0)
    out["english_words"] = len(ENGLISH.split())

    # 3. the toy task, call by call
    task = ("The test in tests/test_cart.py fails. Find out why and fix shop/cart.py so all tests pass. "
            "Run the tests to check.")
    repo = toy_copy(with_claude_md=True)
    sid, _, rt = run(repo, task, MINIMAL, persist=True)
    s, rows = analyze.analyze_file(transcript(sid), "claude")
    out["toy"] = _toy(s, rows, rt)
    out["toy"]["passes"] = subprocess.run([sys.executable, "-m", "unittest", "-q"], cwd=repo,
                                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT).returncode == 0

    # 4. the same task with a scoper subagent on Haiku
    repo = toy_copy(with_claude_md=True, with_agent=True)
    sid, _, rs = run(repo, "First use the scoper subagent to find the cause, then fix it yourself. " + task, MINIMAL, persist=True)
    path = transcript(sid)
    s2, rows2 = analyze.analyze_file(path, "claude")
    out["toy_sub"] = _toy(s2, rows2, rs)
    subs = sorted((path.with_suffix("") / "subagents").glob("agent-*.jsonl"))
    out["toy_sub"]["sub_calls"] = [{"prompt": c.prompt, "read": c.read, "new": c.w5 + c.w1h + c.uncached, "out": c.out,
                                    "cost": c.cost, "model": c.model}
                                   for f in subs for c in sorted(logs.claude_file_calls(f, None, None, True), key=lambda c: c.ts)]
    out["toy_sub"]["passes"] = subprocess.run([sys.executable, "-m", "unittest", "-q"], cwd=repo,
                                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT).returncode == 0
    (HERE / "measured.json").write_text(json.dumps(out, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if not isinstance(v, dict)}, indent=2))
    print("toy:", out["toy"]["cost"], out["toy"]["calls"], "· with subagent:", out["toy_sub"]["cost"], out["toy_sub"]["calls"])


def _toy(s, rows, result):
    return {
        "cost": s["cost"], "lead_cost": s["lead_cost"], "sub_cost": s["sub_cost"], "calls": s["calls"],
        "reported_cost": result.get("total_cost_usd"), "peak": s["peak"], "startup_tokens": s["startup_tokens"],
        "categories": s["categories"], "models": s["all_models"],
        "rows": [{"i": r["i"], "prompt": r["prompt"], "read": r["read"], "new": r["new"], "out": r["out"],
                  "cost": r["cost"], "c_read": r["c_read"], "c_write": r["c_write"], "c_out": r["c_out"],
                  "w1h": r["w1h"], "tools": r["tools"], "model": r["model"]} for r in rows],
        "items": [{k: i[k] for k in ("cat", "label", "tokens", "carried", "cost")} for i in s["top_items"][:12]],
        "subagents": s["subagents"],
    }


if __name__ == "__main__":
    main()
