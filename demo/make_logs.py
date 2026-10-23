"""Write a made-up month of Claude Code and Codex logs for demos, screenshots and the README recordings.

Nothing here comes from a real machine: projects, people, prompts and file names are invented, and the
token counts are simulated call by call (the context grows with every tool result, cache reads and
writes follow it, a long break re-writes the whole prompt). Dates end today, so "this month" has data.

    python3 demo/make_logs.py /tmp/tke-demo          # then:
    CLAUDE_CONFIG_DIR=/tmp/tke-demo/claude CODEX_HOME=/tmp/tke-demo/codex TKE_HOME=/tmp/tke-demo/tke python3 tke.py report
"""
import json
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECTS = {
    "shop-api": ["Fix the VAT rounding on invoices", "Add bulk discounts to the cart", "Speed up the orders query",
                 "Rename customer_id to account_id", "Flaky test in test_payments"],
    "web-dashboard": ["Dark mode for the settings page", "Chart tooltips cut off on mobile", "Upgrade the date picker"],
    "data-pipeline": ["Backfill September events", "Why did the nightly load double count?", "Move the S3 paths to config"],
    "mobile-app": ["Crash on the login screen", "Offline queue for orders"],
}
STARTUP = 21000            # system prompt + tool definitions on a lean setup
TOOLS = [("Read", 0.30), ("Bash", 0.25), ("Grep", 0.20), ("Edit", 0.20), ("Glob", 0.05)]


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (t.microsecond // 1000)


class Chain:
    """One model-call chain (a lead session or a subagent) with consistent cache accounting."""

    def __init__(self, rnd, sid, model, cwd, side=False, ttl_min=5):
        self.rnd, self.sid, self.model, self.cwd, self.side = rnd, sid, model, cwd, side
        self.rows, self.ctx, self.cached, self.last, self.ttl = [], 0, 0, None, timedelta(minutes=ttl_min)
        self.speed = None

    def user(self, t, text):
        self.rows.append({"type": "user", "timestamp": iso(t), "sessionId": self.sid, "cwd": self.cwd,
                          "isSidechain": self.side, "message": {"role": "user", "content": text}})
        self.ctx += len(text) // 4 + 10

    def call(self, t, blocks, out):
        warm = self.last is not None and t - self.last <= self.ttl and self.cached
        read = self.cached if warm else 0
        write = self.ctx - read
        mid, req = "msg_" + uuid.UUID(int=self.rnd.getrandbits(128)).hex[:24], "req_" + uuid.UUID(int=self.rnd.getrandbits(128)).hex[:24]
        usage = {"input_tokens": self.rnd.randint(1, 6), "cache_read_input_tokens": read,
                 "cache_creation_input_tokens": write,
                 "cache_creation": {"ephemeral_5m_input_tokens": write if self.ttl.seconds <= 300 else 0,
                                    "ephemeral_1h_input_tokens": write if self.ttl.seconds > 300 else 0},
                 "output_tokens": out, "service_tier": "standard"}
        if self.speed:
            usage["speed"] = self.speed
        self.rows.append({"type": "assistant", "timestamp": iso(t), "sessionId": self.sid, "cwd": self.cwd,
                          "requestId": req, "isSidechain": self.side,
                          "message": {"id": mid, "model": self.model, "role": "assistant", "type": "message",
                                      "content": blocks, "usage": usage}})
        self.ctx += out
        self.cached, self.last = self.ctx, t

    def result(self, t, tool_id, chars):
        self.rows.append({"type": "user", "timestamp": iso(t), "sessionId": self.sid, "cwd": self.cwd,
                          "isSidechain": self.side, "message": {"role": "user", "content": [
                              {"type": "tool_result", "tool_use_id": tool_id, "content": "x" * chars}]}})
        self.ctx += chars // 4


def tool_block(rnd, name, project, big=False):
    tid = "toolu_" + uuid.UUID(int=rnd.getrandbits(128)).hex[:22]
    files = ["src/%s/%s.py" % (project.split("-")[0], w) for w in ("models", "views", "service", "tax", "orders", "utils")]
    if name == "Read":
        inp, chars = {"file_path": "/home/dev/work/%s/%s" % (project, rnd.choice(files))}, rnd.randint(3000, 30000)
        if big:
            inp, chars = {"file_path": "/home/dev/work/%s/logs/app.log" % project}, rnd.randint(300000, 480000)
    elif name == "Bash":
        cmd = rnd.choice(["pytest -q", "pytest tests/ -x", "git diff", "npm test", "ls -la src"])
        inp, chars = {"command": cmd}, rnd.randint(800, 20000) if "test" in cmd else rnd.randint(200, 3000)
    elif name == "Grep":
        inp, chars = {"pattern": rnd.choice(["round", "discount", "account_id", "def load"])}, rnd.randint(300, 4000)
    elif name == "Edit":
        inp, chars = {"file_path": "/home/dev/work/%s/%s" % (project, rnd.choice(files)), "old_string": "a", "new_string": "b"}, 200
    else:
        inp, chars = {"pattern": "**/*.py"}, rnd.randint(300, 2000)
    return tid, {"type": "tool_use", "id": tid, "name": name, "input": inp}, chars


def pick_tool(rnd):
    r, acc = rnd.random(), 0
    for name, w in TOOLS:
        acc += w
        if r <= acc:
            return name
    return "Read"


def session(rnd, root, project, title, start, model, kind):
    sid = str(uuid.UUID(int=rnd.getrandbits(128)))
    cwd = "/home/dev/work/%s" % project
    lead = Chain(rnd, sid, model, cwd, ttl_min=60 if kind == "1h" else 5)
    lead.rows.append({"type": "ai-title", "sessionId": sid, "aiTitle": title})
    lead.ctx = STARTUP
    t = start
    lead.user(t, title + ". " + rnd.choice(["Run the tests when done.", "Keep the change small.", "Explain the cause first."]))
    subs, turns = [], rnd.randint(2, 4) if kind != "long" else 7
    for turn in range(turns):
        if turn:
            gap = rnd.choice([2, 3, 4, 8]) if kind not in ("break", "1h") else rnd.choice([3, 40, 90])
            t += timedelta(minutes=gap)
            lead.user(t, rnd.choice(["Now the edge cases.", "Also update the docs.", "The CI run failed, have a look.",
                                     "Good. Next: the same for refunds."]))
            if kind == "switch" and turn == 2:
                lead.model, lead.speed = "claude-opus-5-5", "fast"
        for _ in range(rnd.randint(5, 14)):
            t += timedelta(seconds=rnd.randint(4, 40))
            if kind in ("subagent", "long") and rnd.random() < 0.12 and len(subs) < 3:
                tid = "toolu_" + uuid.UUID(int=rnd.getrandbits(128)).hex[:22]
                brief = rnd.choice(["find where %s is computed" % w for w in ("tax", "the discount", "the total")])
                good = rnd.random() < 0.5
                prompt = (("Goal: %s.\nWhere: start in src/.\nDone when: you can name file and line.\n"
                           "Limits: read only.\nReport: 10 lines max.") % brief) if good else brief
                lead.call(t, [{"type": "tool_use", "id": tid, "name": "Agent", "input": {
                    "subagent_type": "scoper", "description": brief, "prompt": prompt}}], rnd.randint(150, 400))
                subs.append((tid, brief, t))
                lead.result(t + timedelta(seconds=50), tid, rnd.randint(600, 2500))
                t += timedelta(seconds=55)
                continue
            name = pick_tool(rnd)
            big = kind == "bigread" and name == "Read" and not any(
                "app.log" in json.dumps(r) for r in lead.rows)
            tid, block, chars = tool_block(rnd, name, project, big)
            lead.call(t, [{"type": "text", "text": "Looking."}, block], rnd.randint(80, 900))
            lead.result(t + timedelta(seconds=2), tid, chars)
        t += timedelta(seconds=20)
        lead.call(t, [{"type": "text", "text": "Done: tests pass."}], rnd.randint(150, 700))
    path = root / ("-home-dev-work-" + project) / (sid + ".jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in lead.rows) + "\n", encoding="utf-8")
    for i, (tid, brief, t0) in enumerate(subs):
        sub = Chain(rnd, sid, "claude-haiku-5-5", cwd, side=True)
        sub.ctx = 4500
        sub.user(t0, brief)
        tt = t0
        for _ in range(rnd.randint(3, 8)):
            tt += timedelta(seconds=4)
            stid, block, chars = tool_block(rnd, rnd.choice(["Grep", "Read", "Glob"]), project)
            sub.call(tt, [block], rnd.randint(60, 300))
            sub.result(tt, stid, chars)
        sub.call(tt + timedelta(seconds=3), [{"type": "text", "text": "Found it in src/ at line 42."}], 200)
        d = path.with_suffix("") / "subagents"
        d.mkdir(parents=True, exist_ok=True)
        name = "agent-%s" % uuid.UUID(int=rnd.getrandbits(128)).hex[:16]
        (d / (name + ".jsonl")).write_text("\n".join(json.dumps(r) for r in sub.rows) + "\n", encoding="utf-8")
        (d / (name + ".meta.json")).write_text(json.dumps({"agentType": "scoper", "description": brief, "toolUseId": tid}),
                                               encoding="utf-8")
    return t


def codex_session(rnd, root, project, title, start, model):
    sid = str(uuid.UUID(int=rnd.getrandbits(128)))
    day = root / "sessions" / start.strftime("%Y/%m/%d")
    day.mkdir(parents=True, exist_ok=True)
    rows = [{"timestamp": iso(start), "type": "session_meta", "payload": {"id": sid, "cwd": "/home/dev/work/" + project,
                                                                          "source": "cli", "originator": "codex_cli_rs"}},
            {"timestamp": iso(start), "type": "turn_context", "payload": {"model": model, "cwd": "/home/dev/work/" + project}},
            {"timestamp": iso(start), "type": "response_item", "payload": {"type": "message", "role": "user",
                                                                           "content": [{"type": "input_text", "text": title}]}}]
    tot, ctx, t = [0, 0, 0], 14000, start
    for _ in range(rnd.randint(8, 30)):
        t += timedelta(seconds=rnd.randint(5, 60))
        ctx += rnd.randint(500, 9000)
        cached = int(ctx * rnd.uniform(0.75, 0.95))
        out = rnd.randint(100, 1200)
        tot = [tot[0] + ctx, tot[1] + cached, tot[2] + out]
        rows.append({"timestamp": iso(t), "type": "event_msg", "payload": {"type": "token_count", "info": {
            "total_token_usage": {"input_tokens": tot[0], "cached_input_tokens": tot[1], "output_tokens": tot[2]},
            "last_token_usage": {"input_tokens": ctx, "cached_input_tokens": cached, "output_tokens": out}}}})
    (day / ("rollout-%s-%s.jsonl" % (start.strftime("%Y-%m-%dT%H-%M-%S"), sid))).write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def main(out_dir, seed=7):
    rnd = random.Random(seed)
    out = Path(out_dir)
    claude_root = out / "claude" / "projects"
    now = datetime.now(timezone.utc).replace(microsecond=0)
    month_start = now.replace(day=1, hour=8, minute=0, second=0)
    days = max(1, (now - month_start).days)
    kinds = ["plain", "plain", "subagent", "bigread", "break", "long", "switch", "1h", "plain", "subagent"]
    n = 0
    for d in range(days + 1):
        day = month_start + timedelta(days=d)
        if day.weekday() >= 5 or day > now:
            continue
        for k in range(rnd.randint(1, 3)):
            project = rnd.choice(list(PROJECTS))
            start = day.replace(hour=9 + 2 * k, minute=rnd.randint(0, 50))
            if start > now - timedelta(minutes=10):
                continue
            model = rnd.choice(["claude-sonnet-5-5"] * 3 + ["claude-opus-5-5"])
            session(rnd, claude_root, project, rnd.choice(PROJECTS[project]), start, model, kinds[n % len(kinds)])
            n += 1
            if rnd.random() < 0.45:
                codex_session(rnd, out / "codex", project, rnd.choice(PROJECTS[project]),
                              start + timedelta(hours=1), rnd.choice(["gpt-6.1-sol", "gpt-6.1-sol", "gpt-6-luna"]))
    (out / "tke").mkdir(parents=True, exist_ok=True)
    (out / "tke" / "config.json").write_text(json.dumps({"claude_budget_usd": 30, "codex_budget_credits": 150}),
                                             encoding="utf-8")
    (out / "claude" / "settings.json").write_text(json.dumps({"model": "sonnet"}), encoding="utf-8")
    print("wrote %d Claude sessions under %s" % (n, out))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "demo-data")
