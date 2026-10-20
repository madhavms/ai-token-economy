"""Run: python -m unittest discover -s tests -v   (stdlib only, Python 3.8+)"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from token_economy import agents, estimate, hooks, logs, prices, report, route, setup, trim  # noqa: E402


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def claude_row(ts, mid, req, read, w5=0, w1h=0, out=100, model="claude-opus-5-5", sid="s1", side=False):
    return {"type": "assistant", "timestamp": ts, "sessionId": sid, "requestId": req, "isSidechain": side,
            "message": {"id": mid, "model": model, "usage": {
                "input_tokens": 2, "cache_read_input_tokens": read, "cache_creation_input_tokens": w5 + w1h,
                "cache_creation": {"ephemeral_5m_input_tokens": w5, "ephemeral_1h_input_tokens": w1h},
                "output_tokens": out}}}


class Prices(unittest.TestCase):
    def test_longest_prefix_and_date_suffix(self):
        self.assertEqual(prices.claude_price("claude-sonnet-4-5-20250929"), prices.CLAUDE["claude-sonnet-4-5"])
        self.assertEqual(prices.claude_price("claude-opus-4-20250514"), prices.CLAUDE["claude-opus-4"])
        self.assertEqual(prices.claude_price("claude-opus-4-8"), prices.CLAUDE["claude-opus-4-8"])
        self.assertIsNone(prices.claude_price("gpt-6-astra"))

    def test_haiku_long_prompt_rates(self):
        small = prices.claude_cost("claude-haiku-5-5", read=100000)
        big = prices.claude_cost("claude-haiku-5-5", read=100001)
        self.assertGreater(big, small * 4.9)

    def test_codex_credits(self):
        self.assertAlmostEqual(prices.codex_credits("gpt-6.1-sol", 1e6, 1e6, 1e6), 50 + 2.5 + 250)


class ClaudeLogs(unittest.TestCase):
    def test_dedupe_subagents_and_rebuilds(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            rows = [
                claude_row("2026-10-01T10:00:00Z", "m1", "r1", read=0, w5=30000),
                claude_row("2026-10-01T10:00:00Z", "m1", "r1", read=0, w5=30000),   # same reply, second block
                claude_row("2026-10-01T10:01:00Z", "m2", "r2", read=30000, w5=5000),
                claude_row("2026-10-01T10:20:00Z", "m3", "r3", read=0, w5=36000),   # 19 min later: rebuild
            ]
            write_jsonl(home / "projects" / "-repo" / "s1.jsonl", rows)
            write_jsonl(home / "projects" / "-repo" / "s1" / "subagents" / "agent-a.jsonl",
                        [claude_row("2026-10-01T10:02:00Z", "m9", "r9", read=0, w5=12000,
                                    model="claude-haiku-5-5", side=True)])
            calls = logs.claude_calls(home=home)
            self.assertEqual(len(calls), 4)
            main = sorted([c for c in calls if not c.subagent], key=lambda c: c.ts)
            self.assertEqual([c.rebuild for c in main], [False, False, True])
            self.assertGreater(main[2].rebuild_waste, 0)
            self.assertEqual(sum(c.subagent for c in calls), 1)
            first = prices.claude_cost("claude-opus-5-5", 2, 30000, 0, 0, 100)
            self.assertAlmostEqual(main[0].cost, first)

    def test_one_hour_cache_is_not_a_rebuild_at_20_minutes(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            write_jsonl(home / "projects" / "-repo" / "s1.jsonl", [
                claude_row("2026-10-01T10:00:00Z", "m1", "r1", read=0, w1h=30000),
                claude_row("2026-10-01T10:20:00Z", "m2", "r2", read=30000, w1h=1000)])
            self.assertFalse(any(c.rebuild for c in logs.claude_calls(home=home)))


class CodexLogs(unittest.TestCase):
    def test_running_totals_become_deltas(self):
        def tc(ts, inp, cached, out):
            u = {"input_tokens": inp, "cached_input_tokens": cached, "output_tokens": out}
            return {"timestamp": ts, "type": "event_msg",
                    "payload": {"type": "token_count", "info": {"total_token_usage": u, "last_token_usage": u}}}
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            write_jsonl(home / "sessions" / "2026" / "10" / "01" / "rollout-x.jsonl", [
                {"timestamp": "2026-10-01T10:00:00Z", "type": "turn_context", "payload": {"model": "gpt-6.1-sol"}},
                tc("2026-10-01T10:00:05Z", 25000, 16000, 200),
                tc("2026-10-01T10:00:06Z", 25000, 16000, 200),          # repeated event
                tc("2026-10-01T10:00:20Z", 52000, 42000, 500)])
            calls = logs.codex_calls(home=home)
            self.assertEqual(len(calls), 2)
            self.assertEqual((calls[1].prompt, calls[1].read, calls[1].uncached, calls[1].out), (27000, 26000, 1000, 300))
            self.assertAlmostEqual(calls[0].cost, (9000 * 50 + 16000 * 2.5 + 200 * 250) / 1e6)


class Estimate(unittest.TestCase):
    def test_matches_the_published_toy_task(self):
        total, parts, unit = estimate.estimate("claude-opus-5-5", 110, 32000, 290000, 900, 5, "5m")
        self.assertEqual(unit, "$")
        self.assertAlmostEqual(total, 10.75, places=2)
        self.assertAlmostEqual(parts["rebuild"], 4.05, places=2)
        credits, _, unit = estimate.estimate("gpt-6-astra", 110, 32000, 290000, 900, 5)
        self.assertEqual(unit, "credits")
        self.assertAlmostEqual(credits, 812, delta=1)

    def test_one_hour_cache_wins_from_the_second_break(self):
        e = lambda b, ttl: estimate.estimate("claude-opus-5-5", 110, 32000, 290000, 900, b, ttl)[0]
        self.assertLess(e(1, "5m"), e(0, "1h"))
        self.assertGreater(e(2, "5m"), e(0, "1h"))


class Pace(unittest.TestCase):
    def test_double_pace_runs_out_mid_month(self):
        now = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)   # Wednesday, 10th workday
        p = report.pace(spent=450, budget=500, now=now, workdays=22)
        self.assertAlmostEqual(p["per_workday"], 500 / 22)
        self.assertGreater(p["ratio"], 1.9)
        self.assertEqual(str(p["runout"]), "2026-10-16")   # $50 left at $45 a day: gone during Friday


class Trim(unittest.TestCase):
    def test_short_output_untouched_long_output_keeps_failures(self):
        self.assertEqual(trim.trim(["ok"] * 10), ["ok"] * 10)
        lines = ["test_%d PASSED" % i for i in range(500)]
        lines[250] = "test_250 FAILED: AssertionError: 2 != 3"
        lines.append("=== 1 failed, 499 passed ===")
        out = trim.trim(lines)
        self.assertLess(len(out), 60)
        self.assertTrue(any("FAILED" in l for l in out))
        self.assertEqual(out[-1], "=== 1 failed, 499 passed ===")


def run_hook(event, payload, env=None):
    e = dict(os.environ, **(env or {}))
    r = subprocess.run([sys.executable, str(ROOT / "tke.py"), "hook", event], input=json.dumps(payload),
                       capture_output=True, text=True, env=e, timeout=30)
    return json.loads(r.stdout) if r.stdout.strip() else None


class Hooks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {"TKE_HOME": self.tmp.name}

    def tearDown(self):
        self.tmp.cleanup()

    def test_big_read_denied_ranged_read_allowed(self):
        big = Path(self.tmp.name) / "big.log"
        big.write_text("x" * 100000)
        out = run_hook("pre_tool", {"tool_name": "Read", "tool_input": {"file_path": str(big)}}, self.env)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        out = run_hook("pre_tool", {"tool_name": "Read", "tool_input": {"file_path": str(big), "limit": 200}}, self.env)
        self.assertIsNone(out)

    def test_test_commands_are_trimmed_and_sleep_is_denied(self):
        out = run_hook("pre_tool", {"tool_name": "Bash", "tool_input": {"command": "pytest -q"}}, self.env)
        cmd = out["hookSpecificOutput"]["updatedInput"]["command"]
        self.assertIn("trim.py", cmd)
        self.assertIn("pipefail", cmd)
        out = run_hook("pre_tool", {"tool_name": "Bash", "tool_input": {"command": "sleep 120"}}, self.env)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIsNone(run_hook("pre_tool", {"tool_name": "Bash", "tool_input": {"command": "ls"}}, self.env))

    def test_cold_big_session_blocks_once(self):
        t = Path(self.tmp.name) / "t.jsonl"
        write_jsonl(t, [claude_row("2026-01-01T10:00:00Z", "m1", "r1", read=150000, w5=1000)])
        ev = {"transcript_path": str(t), "session_id": "abc"}
        first = run_hook("prompt", ev, self.env)
        self.assertEqual(first["decision"], "block")
        self.assertIn("$0.76", first["reason"])          # 151k tokens re-written at $5 per million on Opus 5.5
        self.assertNotEqual((run_hook("prompt", ev, self.env) or {}).get("decision"), "block")

    def test_bad_input_fails_open(self):
        r = subprocess.run([sys.executable, str(ROOT / "tke.py"), "hook", "pre_tool"], input="not json",
                           capture_output=True, text=True, env=dict(os.environ, **self.env))
        self.assertEqual((r.returncode, r.stdout), (0, ""))


class Setup(unittest.TestCase):
    def test_keeps_user_values_is_idempotent_and_uninstalls(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["CLAUDE_CONFIG_DIR"] = d
            try:
                Path(d, "settings.json").write_text(json.dumps({"model": "opus", "hooks": {"Stop": [
                    {"hooks": [{"type": "command", "command": "my-own-hook"}]}]}}))
                with redirect_stdout(io.StringIO()):
                    for _ in range(2):
                        setup.run(*setup.plan_claude(), apply=True)
                s = json.loads(Path(d, "settings.json").read_text())
                self.assertEqual(s["model"], "opus")
                self.assertEqual(s["promptCacheTtl"], "1h")
                self.assertEqual(len(s["hooks"]["Stop"]), 2)            # theirs + ours, not duplicated
                self.assertTrue(Path(d, "agents", "Explore.md").exists())
                with redirect_stdout(io.StringIO()):
                    setup.run(*setup.unplan_claude(), apply=True)
                s = json.loads(Path(d, "settings.json").read_text())
                self.assertEqual(s["hooks"]["Stop"][0]["hooks"][0]["command"], "my-own-hook")
                self.assertNotIn("statusLine", s)
            finally:
                del os.environ["CLAUDE_CONFIG_DIR"]

    def test_codex_toml_keys_added_on_top(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["CODEX_HOME"] = d
            try:
                Path(d, "config.toml").write_text('model = "gpt-5.5"\n\n[profiles.x]\nmodel = "y"\n')
                with redirect_stdout(io.StringIO()):
                    setup.run(*setup.plan_codex(), apply=True)
                text = Path(d, "config.toml").read_text()
                keys = setup._toml_top_keys(text)
                self.assertEqual(keys["model"], '"gpt-5.5"')            # kept
                self.assertEqual(keys["model_reasoning_effort"], '"medium"')
                self.assertLess(text.index("model_verbosity"), text.index("[profiles.x]"))
                self.assertTrue(Path(d, "luna.config.toml").exists())
            finally:
                del os.environ["CODEX_HOME"]


def spawn_row(ts, tid, atype, prompt, desc="task"):
    return {"type": "assistant", "timestamp": ts, "sessionId": "s1", "requestId": "rq" + tid,
            "message": {"id": "sp" + tid, "model": "claude-opus-5-5", "usage": {"input_tokens": 1, "output_tokens": 10},
                        "content": [{"type": "tool_use", "id": tid, "name": "Agent",
                                     "input": {"subagent_type": atype, "prompt": prompt, "description": desc}}]}}


class Agents(unittest.TestCase):
    GOOD = ("Goal: total() must multiply price by qty.\nWhere: shop/cart.py:3\nDone when: pytest -q tests/test_cart.py passes.\n"
            "Limits: don't edit outside shop/.\nReport in at most 15 lines: files changed, test results.")

    def test_frontmatter_and_agent_checks(self):
        meta, body = agents.frontmatter("---\nname: x\ndescription: Use for search\nmodel: haiku\ntools:\n  - Read\n  - Grep\n---\nbody")
        self.assertEqual((meta["model"], meta["tools"], body), ("haiku", ["Read", "Grep"], "body"))
        p = Path("a.md")
        r = agents.check_claude_agent(p, {"name": "finder", "description": "Use to find code", "model": "opus",
                                          "tools": "Read, Grep"}, "Report in at most 10 lines.", None)
        self.assertTrue(any("top rate" in f["text"] for f in r["findings"]))
        r = agents.check_claude_agent(p, {"name": "impl", "description": "Use to write code", "model": "haiku"}, "do it", None)
        texts = " ".join(f["text"] for f in r["findings"])
        for bit in ("writes code on Haiku", "maxTurns", "report format", "every tool"):
            self.assertIn(bit, texts)
        for t in (setup.TEMPLATES / "roles" / "claude").glob("*.md"):
            meta, body = agents.frontmatter(t.read_text())
            warn = [f for f in agents.check_claude_agent(t, meta, body, None)["findings"] if f["level"] != "info"]
            self.assertEqual(warn, [], t.name)

    def test_brief_scoring(self):
        self.assertEqual(agents.score_brief(self.GOOD, "implementer")[0], [])
        missing, _ = agents.score_brief("Fix the cart bug please", "general-purpose")
        self.assertIn("done-when check", missing)
        self.assertTrue(any(m.startswith("very short") for m in missing))
        # a review brief isn't asked for a test command
        missing, _ = agents.score_brief("Review shop/cart.py. Do not edit files. When done, report: findings with file:line.",
                                        "general-purpose", "Review the cart change")
        self.assertEqual(missing, [])
        with tempfile.TemporaryDirectory() as d:
            ref = Path(d, "brief.md")
            ref.write_text("Done when pytest -q passes. Don't touch other files. Reply in under 15 lines.")
            self.assertEqual(agents.score_brief("Implement task 3 in shop/cart.py.\nRead your brief first: %s" % ref)[0], [])
            gone = agents.score_brief("Implement task 3 in shop/cart.py.\nRead your brief first: %s/missing.md" % d)[0]
            self.assertEqual(gone, [agents.UNSEEN])

    def test_usage_split_and_findings(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            proj = home / "projects" / "-repo"
            write_jsonl(proj / "s1.jsonl", [
                spawn_row("2026-10-01T10:00:00Z", "t1", "general-purpose", "look into the cart"),
                spawn_row("2026-10-01T10:00:01Z", "t2", "implementer", self.GOOD),
                claude_row("2026-10-01T10:00:02Z", "m1", "r1", read=0, w5=20000)])
            sub = proj / "s1" / "subagents"
            write_jsonl(sub / "agent-a.jsonl", [claude_row("2026-10-01T10:01:00Z", "m2", "r2", read=0, w5=50000, side=True)])
            (sub / "agent-a.meta.json").write_text(json.dumps({"agentType": "general-purpose", "toolUseId": "t1"}))
            write_jsonl(sub / "agent-b.jsonl", [claude_row("2026-10-01T10:02:00Z", "m3", "r3", read=0, w5=200000,
                                                           model="claude-haiku-5-5", side=True)])
            (sub / "agent-b.meta.json").write_text(json.dumps({"agentType": "implementer", "toolUseId": "t2"}))
            sessions, runs = agents.usage(home=home)
            self.assertEqual((len(sessions), len(runs)), (1, 2))
            by = {r["type"]: r for r in runs}
            self.assertTrue(by["general-purpose"]["brief_missing"])
            self.assertEqual(by["implementer"]["brief_missing"], [])
            self.assertEqual(by["implementer"]["haiku_long"], 1)
            summ = agents.summarize(sessions, runs)
            self.assertAlmostEqual(summ["lead"] + summ["sub"], sum(c.cost for c in logs.claude_calls(home=home)))
            text = " ".join(t for _, t in agents.findings(sessions, runs, summ))
            self.assertIn("general-purpose subagent runs on Opus", text)
            self.assertIn("over 100K", text)

    def test_force_flag_codex_v2_and_codex_split(self):
        with tempfile.TemporaryDirectory() as d:
            home, cx, cp = Path(d, "claude"), Path(d, "codex"), Path(d, "copilot")
            home.mkdir()
            (home / "settings.json").write_text(json.dumps({"env": {"CLAUDE_CODE_SUBAGENT_MODEL": "haiku",
                                                                    "CLAUDE_CODE_SUBAGENT_MODEL_FORCE": "1"}}))
            (cx / "agents").mkdir(parents=True)
            (cx / "config.toml").write_text('model = "gpt-6.1-sol"\n')
            (cx / "agents" / "e.toml").write_text('name = "explorer"\nmodel = "gpt-6-luna"\n'
                                                   'developer_instructions = """Report in at most 10 lines."""\n')
            chk = agents.setup_check(None, home, cx, cp)
            levels = {g["level"] for g in chk["general"]}
            self.assertIn("bad", levels)
            self.assertTrue(any("multi_agent_v2" in g["text"] for g in chk["general"]))
            day = cx / "sessions" / "2026" / "10" / "01"
            tc = lambda ts, n: {"timestamp": ts, "type": "event_msg", "payload": {"type": "token_count", "info": {
                "total_token_usage": {"input_tokens": n, "cached_input_tokens": 0, "output_tokens": 10}}}}
            write_jsonl(day / "rollout-a-P.jsonl", [
                {"type": "session_meta", "payload": {"id": "P", "source": "exec"}},
                {"type": "turn_context", "payload": {"model": "gpt-6.1-sol"}}, tc("2026-10-01T10:00:00Z", 1000)])
            write_jsonl(day / "rollout-b-C.jsonl", [
                {"type": "session_meta", "payload": {"id": "C", "source": {"subagent": {"thread_spawn": {
                    "parent_thread_id": "P", "agent_path": "/root/explorer"}}}}},
                {"type": "turn_context", "payload": {"model": "gpt-6-luna"}}, tc("2026-10-01T10:00:05Z", 4000)])
            u = agents.codex_usage(home=cx)
            self.assertEqual([t["type"] for t in u["types"]], ["explorer"])
            self.assertEqual(u["types"][0]["models"], ["gpt-6-luna"])
            self.assertGreater(u["lead"], 0)

    def test_roles_setup_writes_three_tools_once(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "CLAUDE.md").write_text("# Mine\n")
            with redirect_stdout(io.StringIO()):
                setup.run(*setup.plan_roles(d), apply=True)
                files, _ = setup.plan_roles(d)
            self.assertEqual(files, [])                                   # second run changes nothing
            for rel in (".claude/agents/scoper.md", ".codex/agents/explorer.toml", ".github/agents/implementer.agent.md"):
                self.assertTrue(Path(d, rel).exists(), rel)
            text = Path(d, "CLAUDE.md").read_text()
            self.assertTrue(text.startswith("# Mine") and "tke:delegation" in text)
            chk = agents.setup_check(d, Path(d, "nohome"), Path(d, "nocodex"), Path(d, "nocopilot"))
            warn = [f for a in chk["agents"] for f in a["findings"] if f["level"] != "info"]
            self.assertEqual((len(chk["agents"]), warn), (9, []))
            self.assertIn("name: subagent-review", setup.review_skill())


class Route(unittest.TestCase):
    def test_first_yes_wins(self):
        self.assertEqual(route.decide({"check": True, "hard": True}), "fast")
        self.assertEqual(route.decide({"check": False, "decided": True}), "workhorse")
        self.assertEqual(route.decide({"hard": True}), "frontier")
        self.assertEqual(route.decide({}), "strong")


class FreshMachine(unittest.TestCase):
    """Every command on a machine with no logs and no settings, as a new clone sees it."""

    def test_every_command_runs_on_empty_homes(self):
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, HOME=d, USERPROFILE=d, APPDATA=d, TKE_HOME=os.path.join(d, "tke"),
                       CLAUDE_CONFIG_DIR=os.path.join(d, ".claude"), CODEX_HOME=os.path.join(d, ".codex"))
            for args in (["init", "--claude", "500", "--codex", "13000"], ["report"], ["report", "week", "--json"],
                         ["status"], ["statusline"], ["setup"], ["setup", "claude", "codex", "project", "--dir", d],
                         ["route", "--check"], ["estimate"], ["uninstall"], ["agents"], ["agents", "7d", "--dir", d, "--brief"],
                         ["agents", "--json"], ["agents", "--prompt"], ["setup", "roles", "--dir", d]):
                r = subprocess.run([sys.executable, str(ROOT / "tke.py")] + args, env=env, cwd=d, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
                self.assertEqual(r.returncode, 0, "tke %s: %s" % (" ".join(args), r.stderr))
            self.assertFalse(os.path.exists(os.path.join(d, ".claude", "settings.json")), "dry run wrote a file")
            r = subprocess.run([sys.executable, str(ROOT / "tke.py"), "setup", "nope"], env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
            self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()


class UI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import ThreadingHTTPServer
        from token_economy import ui
        cls.ui = ui
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ.update(CLAUDE_CONFIG_DIR=cls.tmp.name, CODEX_HOME=cls.tmp.name, TKE_HOME=cls.tmp.name)
        write_jsonl(Path(cls.tmp.name) / "projects" / "-repo" / "s1.jsonl",
                    [claude_row(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "m1", "r1", read=0, w5=30000)])
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), ui.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        for k in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "TKE_HOME"):
            os.environ.pop(k, None)
        cls.tmp.cleanup()

    def req(self, path, data=None, token=None, host=None):
        import urllib.request, urllib.error
        r = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path),
                                   data=json.dumps(data).encode() if data is not None else None)
        if token:
            r.add_header("X-TKE-Token", token)
        if host:
            r.add_header("Host", host)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_page_summary_and_session(self):
        code, body = self.req("/")
        self.assertEqual(code, 200)
        self.assertIn(self.ui.TOKEN.encode(), body)
        code, body = self.req("/api/summary?refresh=1")
        s = json.loads(body)
        self.assertEqual(s["tools"]["claude"]["leaks"]["calls"], 1)
        sid = s["tools"]["claude"]["sessions"][0]["session"]
        d = json.loads(self.req("/api/session?tool=claude&id=" + sid)[1])
        self.assertEqual(len(d["calls"]), 1)

    def test_writes_need_the_token_and_a_local_host(self):
        self.assertEqual(self.req("/api/budget", {"claude_budget_usd": 1})[0], 403)
        self.assertEqual(self.req("/api/summary", host="evil.example")[0], 403)
        code, body = self.req("/api/budget", {"claude_budget_usd": 321, "codex_budget_credits": 999, "workdays": 20},
                              token=self.ui.TOKEN)
        self.assertEqual((code, json.loads(body)["config"]["claude_budget_usd"]), (200, 321))

    def test_estimate_and_route(self):
        e = json.loads(self.req("/api/estimate?model=claude-opus-5-5&calls=110&start=32&end=290&out=900&breaks=5")[1])
        self.assertAlmostEqual(e["total"], 10.75, places=2)
        self.assertEqual(json.loads(self.req("/api/route?hard=1")[1])["lane"], "frontier")
