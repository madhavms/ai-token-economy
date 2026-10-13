"""tke: the AI token economy toolkit. Runs offline on your machine; it never calls a model."""
import argparse
import json
import sys
import time
from datetime import timedelta

from . import __version__, config, estimate, logs, prices, report, route, setup

BAR = 28


def money(x, unit="$"):
    return ("$%.2f" % x) if unit == "$" else ("%s cr" % format(int(round(x)), ","))


def since_for(period, now):
    if period == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "week":
        return (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    if period.endswith("d") and period[:-1].isdigit():
        return now - timedelta(days=int(period[:-1]))
    return report.month_start(now)


def bar(frac):
    n = max(0, min(BAR, int(round(frac * BAR))))
    return "#" * n + "." * (BAR - n)


# ---------------------------------------------------------------- commands
def cmd_init(a):
    cfg = config.load()
    for key, val in (("claude_budget_usd", a.claude), ("codex_budget_credits", a.codex), ("workdays", a.workdays)):
        if val is not None:
            cfg[key] = val
    print("saved %s" % config.save(cfg))
    print("budget: $%s Claude and %s Codex credits a month, over %s workdays"
          % (cfg["claude_budget_usd"], format(cfg["codex_budget_credits"], ","), cfg["workdays"]))


def _pace_lines(name, unit, p):
    lines = ["%s  %s of %s this month   %s" % (name.ljust(6), money(p["spent"], unit), money(p["budget"], unit),
                                              bar(p["spent"] / p["budget"] if p["budget"] else 0))]
    status = "on pace" if p["ratio"] <= 1.05 else "%.1fx the pace that lasts" % p["ratio"]
    lines.append("        %s a workday on average, %s lasts the month  (%s)"
                 % (money(p["daily_avg"], unit), money(p["per_workday"], unit), status))
    if p["runout"]:
        lines.append("        at this rate it runs out on %s" % p["runout"].strftime("%a %d %b"))
    return lines


def cmd_report(a):
    cfg = config.load()
    now = report.local_now()
    since = since_for(a.period, now)
    month = report.month_start(now)
    calls = logs.all_calls(min(since, month))
    month_calls = [c for c in calls if c.ts >= month]
    period_calls = [c for c in calls if c.ts >= since]
    claude_m = sum(c.cost or 0 for c in month_calls if c.tool == "claude")
    codex_m = sum(c.cost or 0 for c in month_calls if c.tool == "codex")
    pc = report.pace(claude_m, cfg["claude_budget_usd"], now, cfg.get("workdays"))
    px = report.pace(codex_m, cfg["codex_budget_credits"], now, cfg.get("workdays"))
    if a.json:
        print(json.dumps({"claude_pace": pc, "codex_pace": px,
                          "claude_leaks": report.leaks([c for c in period_calls if c.tool == "claude"], cfg["big_context"]),
                          "codex_leaks": report.leaks([c for c in period_calls if c.tool == "codex"], cfg["big_context"])},
                         default=str, indent=2))
        return
    print("AI token economy · %s · prices as of %s · API-equivalent list prices\n" % (now.strftime("%a %d %b %Y"), prices.PRICES_DATE))
    print("\n".join(_pace_lines("Claude", "$", pc) + [""] + _pace_lines("Codex", "cr", px)))

    print("\nLast 7 days")
    for d, cl, cx in report.daily(calls, 7, now):
        print("  %s   Claude %8s   Codex %10s" % (d.strftime("%a %d %b"), money(cl), money(cx, "cr")))

    for tool, unit in (("claude", "$"), ("codex", "cr")):
        tc = [c for c in period_calls if c.tool == tool]
        if not tc:
            continue
        lk = report.leaks(tc, cfg["big_context"])
        t = lk["total"] or 1
        print("\nWhere the %s money went (%s, %d calls)" % ("Claude" if tool == "claude" else "Codex", a.period, lk["calls"]))
        rows = [("re-reading the prompt (cache reads)", lk["reads"]),
                ("new tokens written / uncached input", lk["writes"]),
                ("output and thinking", lk["output"])]
        if tool == "claude":
            rows.append(("  of which: rebuilds after breaks, avoidable", lk["rebuild_waste"]))
        rows += [("  calls with a prompt over %dk tokens" % (cfg["big_context"] // 1000), lk["fat_context"]),
                 ("  frontier models (Fable, Astra)", lk["frontier"]),
                 ("  subagents", lk["subagents"])]
        for label, v in rows:
            print("  %-46s %10s  %3.0f%%" % (label, money(v, unit), 100 * v / t))
        if lk["unpriced_models"]:
            print("  no price for: %s (add them to prices.json)" % ", ".join(lk["unpriced_models"]))
        for tip in report.tips_for(lk):
            print("  -> " + tip)

    top = report.sessions(period_calls, cfg["big_context"], a.top)
    if top:
        print("\nMost expensive sessions (%s)" % a.period)
        for s in top:
            unit = "$" if s["tool"] == "claude" else "cr"
            where = s["project"] or s["tool"]
            print("  %9s  %-6s %4d calls  peak %4dk  %s  %s" % (money(s["cost"], unit), s["tool"], s["calls"],
                  s["peak"] // 1000, (where[-28:]).ljust(28), ", ".join(s["tags"]) or "-"))
    print("\nNumbers are estimates from local logs at list price; your bill may use contracted rates.")


def cmd_status(a):
    cfg = config.load()
    now = report.local_now()
    calls = logs.all_calls(report.month_start(now))
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    for tool, budget, unit in (("claude", cfg["claude_budget_usd"], "$"), ("codex", cfg["codex_budget_credits"], "cr")):
        spent = sum(c.cost or 0 for c in calls if c.tool == tool)
        t = sum(c.cost or 0 for c in calls if c.tool == tool and c.ts >= today)
        p = report.pace(spent, budget, now, cfg.get("workdays"))
        left_today = p["per_workday"] - t
        print("%-6s today %s of %s (%s left) · month %s of %s%s" % (
            tool.capitalize(), money(t, unit), money(p["per_workday"], unit), money(max(0, left_today), unit),
            money(spent, unit), money(budget, unit),
            (" · runs out %s" % p["runout"].strftime("%d %b")) if p["runout"] else ""))


def cmd_statusline(a):
    """Claude Code status line. Cached for 60 s so it stays cheap."""
    try:
        ev = json.load(sys.stdin)
    except ValueError:
        ev = {}
    from .hooks import state_dir
    cache = state_dir() / "statusline.json"
    cfg = config.load()
    now = report.local_now()
    data = None
    try:
        data = json.loads(cache.read_text())
        if time.time() - data["at"] > 60:
            data = None
    except (OSError, ValueError, KeyError):
        pass
    if data is None:
        calls = logs.claude_calls(report.month_start(now))
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        data = {"at": time.time(), "month": sum(c.cost or 0 for c in calls),
                "today": sum(c.cost or 0 for c in calls if c.ts >= today)}
        try:
            cache.write_text(json.dumps(data))
        except OSError:
            pass
    p = report.pace(data["month"], cfg["claude_budget_usd"], now, cfg.get("workdays"))
    sess = ((ev.get("cost") or {}).get("total_cost_usd"))
    model = ((ev.get("model") or {}).get("display_name")) or ""
    parts = [model] if model else []
    if sess is not None:
        parts.append("session $%.2f" % sess)
    parts.append("today $%.2f/$%.2f" % (data["today"], p["per_workday"]))
    parts.append("month $%.0f/$%.0f" % (data["month"], cfg["claude_budget_usd"]))
    if p["ratio"] > 1.05:
        parts.append("%.1fx pace" % p["ratio"])
    print(" · ".join(parts))


def cmd_setup(a):
    targets = a.target or ["claude", "codex"]
    for t in targets:
        print("\n== %s" % t)
        if t == "claude":
            files, notes = setup.plan_claude(force=a.force, statusline=not a.no_statusline)
        elif t == "codex":
            files, notes = setup.plan_codex(force=a.force, model=a.codex_model)
        else:
            files, notes = setup.plan_project(a.dir)
        setup.run(files, notes, a.apply)


def cmd_uninstall(a):
    files, notes = setup.unplan_claude()
    setup.run(files, notes, a.apply)


def cmd_route(a):
    if a.check is None and a.decided is None and a.hard is None:
        answers = route.ask()
    else:
        answers = {"check": a.check, "decided": a.decided, "hard": a.hard}
    print(route.describe(route.decide(answers)))


def cmd_estimate(a):
    total, parts, unit = estimate.estimate(a.model, a.calls, a.start * 1000, a.end * 1000, a.out, a.breaks, a.ttl)
    cfg = config.load()
    budget = cfg["claude_budget_usd"] if unit == "$" else cfg["codex_budget_credits"]
    print("%s · %d calls · prompt %dk to %dk · %d output tokens a call · %d breaks%s"
          % (a.model, a.calls, a.start, a.end, a.out, a.breaks, (" · %s cache" % a.ttl) if unit == "$" else ""))
    for k in ("read", "write", "rebuild", "output"):
        print("  %-8s %10s" % (k, money(parts[k], unit)))
    print("  %-8s %10s   (%d such sessions per monthly budget)" % ("total", money(total, unit), int(budget // total) if total else 0))


def cmd_bench(a):
    from . import bench
    bench.run(a.source, a.models)


def cmd_hook(a):
    from . import hooks
    hooks.main([a.event])


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tke", description="AI token economy toolkit for Claude Code and Codex. Offline; spends no tokens.")
    ap.add_argument("--version", action="version", version="tke " + __version__)
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("init", help="set your monthly budgets")
    p.add_argument("--claude", type=float, help="monthly Claude budget in dollars")
    p.add_argument("--codex", type=float, help="monthly Codex credits")
    p.add_argument("--workdays", type=int, help="workdays per month (default: counted Mon-Fri)")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("report", help="pace against budget, where tokens go, costliest sessions")
    p.add_argument("period", nargs="?", default="month", help="today | week | month | Nd (e.g. 7d)")
    p.add_argument("--top", type=int, default=5)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("status", help="one line per tool: today and month against budget")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("statusline", help="Claude Code status line (reads the status JSON on stdin)")
    p.set_defaults(fn=cmd_statusline)

    p = sub.add_parser("setup", help="apply the recommended settings (dry run unless --apply)")
    p.add_argument("target", nargs="*", choices=["claude", "codex", "project"], help="default: claude codex")
    p.add_argument("--apply", action="store_true", help="write the changes (backups are made)")
    p.add_argument("--force", action="store_true", help="replace values you already set")
    p.add_argument("--no-statusline", action="store_true")
    p.add_argument("--codex-model", help="everyday Codex model (default gpt-6.1-sol)")
    p.add_argument("--dir", default=".", help="project folder for the 'project' target")
    p.set_defaults(fn=cmd_setup)

    p = sub.add_parser("uninstall", help="remove tke hooks and status line from Claude Code")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(fn=cmd_uninstall)

    p = sub.add_parser("route", help="which model for this task, and the command to start it")
    for flag in ("check", "decided", "hard"):
        p.add_argument("--" + flag, dest=flag, action="store_true", default=None)
        p.add_argument("--no-" + flag, dest=flag, action="store_false")
    p.set_defaults(fn=cmd_route)

    p = sub.add_parser("estimate", help="price a session before you run it")
    p.add_argument("--model", default="claude-opus-5-5", help="e.g. claude-sonnet-5-5, gpt-6.1-sol")
    p.add_argument("--calls", type=int, default=60)
    p.add_argument("--start", type=int, default=25, help="prompt size at the start, in thousands of tokens")
    p.add_argument("--end", type=int, default=120, help="prompt size at the end, in thousands of tokens")
    p.add_argument("--out", type=int, default=600, help="output tokens per call, thinking included")
    p.add_argument("--breaks", type=int, default=0, help="breaks longer than the cache lifetime")
    p.add_argument("--ttl", choices=["5m", "1h"], default="5m")
    p.set_defaults(fn=cmd_estimate)

    p = sub.add_parser("bench", help="time a local Ollama model reading an agent-sized prompt")
    p.add_argument("source", nargs="?", default=".", help="a source folder to build the prompt from")
    p.add_argument("models", nargs="*", default=["gpt-oss:20b"])
    p.set_defaults(fn=cmd_bench)

    p = sub.add_parser("hook", help=argparse.SUPPRESS)
    p.add_argument("event")
    p.set_defaults(fn=cmd_hook)

    a = ap.parse_args(argv)
    if not getattr(a, "fn", None):
        ap.print_help()
        return 0
    a.fn(a)
    return 0
