"""tke as an MCP server (stdio), so a coding agent can answer "why did this cost so much?" itself.

Claude Code, Codex and Copilot CLI start it as a local process and talk JSON-RPC over stdin/stdout.
Every tool is read-only, runs offline on the local logs, and returns a short plain-text report
(kept small on purpose: the agent pays to read it).
"""
import json
import os
import sys
import traceback

from . import __version__, agents, analyze, config, explain, logs, report

PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")

INSTRUCTIONS = (
    "Read-only spend analysis for Claude Code and Codex, from local logs (list-price estimates). "
    "Use why_session for 'why did this session cost so much' (session='current' is the latest session in "
    "this folder), why_period for a day/week/month, and budget_status for pace against the monthly budget. "
    "Quote the dollar figures and the fixes; don't re-derive them."
)

PERIOD = {"type": "string", "description": "today | week | month | lastmonth | Nd (e.g. 7d, 30d)", "default": "week"}
TOOL = {"type": "string", "enum": ["all", "claude", "codex"], "default": "all"}

TOOLS = [
    {"name": "budget_status", "title": "Budget status",
     "description": "This month's spend against the monthly budgets for Claude Code and Codex: pace, daily allowance, "
                    "projected run-out date, and today's spend.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "why_session", "title": "Why a session cost what it did",
     "description": "Explains one session's cost: the causes (startup context, file reads, command output, subagents, "
                    "output, cache rebuilds...) that add up to the total, the levers with dollars at stake, and the "
                    "costliest things carried in the context. session: 'current' (latest in this folder), 'latest' "
                    "(anywhere), or a session id / prefix.",
     "inputSchema": {"type": "object", "properties": {
         "session": {"type": "string", "default": "current"},
         "detail": {"type": "string", "enum": ["brief", "full", "calls"], "default": "full",
                    "description": "calls adds a call-by-call table (long)"}}, "additionalProperties": False}},
    {"name": "why_period", "title": "Why a period cost what it did",
     "description": "Spend over a period by cause, lever, model and project, with the costliest sessions and the "
                    "costliest things carried in context.",
     "inputSchema": {"type": "object", "properties": {
         "period": PERIOD, "tool": TOOL,
         "project": {"type": "string", "description": "only sessions whose project or folder contains this text"},
         "top": {"type": "integer", "default": 8, "minimum": 1, "maximum": 30}}, "additionalProperties": False}},
    {"name": "top_sessions", "title": "Costliest sessions",
     "description": "The costliest sessions in a period with their main reason, as a compact list.",
     "inputSchema": {"type": "object", "properties": {
         "period": PERIOD, "tool": TOOL, "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 50}},
         "additionalProperties": False}},
    {"name": "subagent_audit", "title": "Subagent audit",
     "description": "How subagents are set up and used: agent files for Claude Code, Codex and Copilot CLI (model, "
                    "tools, turn caps, report format), lead vs subagent spend, and the weakest briefs.",
     "inputSchema": {"type": "object", "properties": {
         "period": dict(PERIOD, default="month"),
         "dir": {"type": "string", "description": "repo to check (default: the current folder)"}}, "additionalProperties": False}},
]
for t in TOOLS:
    t["annotations"] = {"readOnlyHint": True, "openWorldHint": False}


# ------------------------------------------------------------------ tool bodies
def _since(period):
    from .cli import since_for
    now = report.local_now()
    return since_for(period or "week", now), now


def t_budget_status(args):
    cfg = config.load()
    now = report.local_now()
    calls = logs.all_calls(report.month_start(now))
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    lines = []
    for tool, budget, unit in (("claude", cfg["claude_budget_usd"], "$"), ("codex", cfg["codex_budget_credits"], "cr")):
        spent = sum(c.cost or 0 for c in calls if c.tool == tool)
        t = sum(c.cost or 0 for c in calls if c.tool == tool and c.ts >= today)
        p = report.pace(spent, budget, now, cfg.get("workdays"))
        lines.append("%s: %s of %s this month (%.0f%%); today %s of a %s daily allowance; average %s a workday, "
                     "%.1fx the pace that lasts%s." % (
                         "Claude Code" if tool == "claude" else "Codex", analyze.money(spent, unit), analyze.money(budget, unit),
                         100 * spent / budget if budget else 0, analyze.money(t, unit), analyze.money(p["per_workday"], unit),
                         analyze.money(p["daily_avg"], unit), p["ratio"],
                         ("; already over budget by %s" % analyze.money(spent - budget, unit)) if spent >= budget else
                         ("; at this rate it runs out on %s" % p["runout"].strftime("%a %d %b")) if p["runout"] and p["ratio"] > 1 else
                         "; on pace to last the month"))
    return "\n".join(lines)


def t_why_session(args):
    cfg = config.load()
    s, rows = analyze.explain(args.get("session") or "current", os.getcwd(), cfg["big_context"])
    if s is None:
        return "No session found for %r." % args.get("session")
    d = args.get("detail") or "full"
    return explain.session_text(s, rows, calls=(d == "calls"), brief=(d == "brief"))


def _sessions(args):
    cfg = config.load()
    since, now = _since(args.get("period"))
    tool = args.get("tool") if args.get("tool") in ("claude", "codex") else None
    ss = analyze.summaries(since, tool, cfg["big_context"])
    if args.get("project"):
        ss = [s for s in ss if args["project"].lower() in (s["project"] + s["cwd"]).lower()]
    return ss, since, now


def t_why_period(args):
    ss, since, now = _sessions(args)
    return explain.period_text(ss, args.get("period") or "week", int(args.get("top") or 8), False, since.date().isoformat())


def t_top_sessions(args):
    ss, since, now = _sessions(args)
    agg = analyze.period_report(ss, since.date().isoformat())
    out = []
    for s in agg["sessions"][:int(args.get("limit") or 10)]:
        out.append("%s  %s %s  %s · %s" % (analyze.money(s["period_cost"], s["unit"]), s["tool"], s["session"][:8],
                                           (s["title"] or s["project"])[:50], s["headline"]))
    return "\n".join(out) or "No sessions in that period."


def t_subagent_audit(args):
    since, now = _since(args.get("period") or "month")
    chk = agents.setup_check(args.get("dir") or os.getcwd())
    sessions, runs = agents.usage(since)
    summ = agents.summarize(sessions, runs)
    leaks = agents.findings(sessions, runs, summ)
    L = ["Agent files:"]
    for g in chk["general"]:
        L.append("  - " + g["text"])
    for a in chk["agents"]:
        warn = [f["text"] for f in a["findings"] if f["level"] != "info"]
        L.append("  %s %s %s (%s): %s" % (a["tool"], a["scope"], a["name"], a["model"], "; ".join(warn) or "ok"))
    if not chk["agents"]:
        L.append("  none found (tke setup roles --dir <repo> writes implementer, scoper and reviewer)")
    tot = summ["lead"] + summ["sub"]
    L.append("Claude Code: lead %s, subagents %s (%.0f%%), %d runs." % (
        analyze.money(summ["lead"]), analyze.money(summ["sub"]), 100 * summ["sub"] / tot if tot else 0, summ["runs"]))
    for t in summ["types"][:6]:
        L.append("  %s: %d runs, %s, %s per run, models %s, %d/%d complete briefs" % (
            t["type"], t["runs"], analyze.money(t["cost"]), analyze.money(t["avg"]), ", ".join(t["models"]), t["briefs_ok"], t["briefs"]))
    if leaks:
        L.append("What to change:")
        L += ["  - " + t for _, t in leaks]
    return "\n".join(L)


HANDLERS = {"budget_status": t_budget_status, "why_session": t_why_session, "why_period": t_why_period,
            "top_sessions": t_top_sessions, "subagent_audit": t_subagent_audit}


# ------------------------------------------------------------------ JSON-RPC
def handle(msg):
    """One JSON-RPC message in, one response (or None for a notification) out."""
    mid = msg.get("id")
    method = msg.get("method")
    if mid is None:
        return None                                    # notifications: initialized, cancelled, ...
    try:
        if method == "initialize":
            want = (msg.get("params") or {}).get("protocolVersion")
            return _ok(mid, {"protocolVersion": want if want in PROTOCOLS else PROTOCOLS[0],
                             "capabilities": {"tools": {"listChanged": False}},
                             "serverInfo": {"name": "tke", "title": "AI token economy", "version": __version__},
                             "instructions": INSTRUCTIONS})
        if method == "ping":
            return _ok(mid, {})
        if method == "tools/list":
            return _ok(mid, {"tools": TOOLS})
        if method == "tools/call":
            p = msg.get("params") or {}
            fn = HANDLERS.get(p.get("name"))
            if not fn:
                return _err(mid, -32602, "unknown tool: %s" % p.get("name"))
            try:
                text = fn(p.get("arguments") or {})
                return _ok(mid, {"content": [{"type": "text", "text": text}], "isError": False})
            except Exception as e:                       # a tool error is reported to the model, not the client
                traceback.print_exc(file=sys.stderr)
                return _ok(mid, {"content": [{"type": "text", "text": "tke error: %s" % e}], "isError": True})
        if method in ("resources/list", "prompts/list"):
            return _ok(mid, {method.split("/")[0]: []})
        return _err(mid, -32601, "method not found: %s" % method)
    except Exception as e:
        return _err(mid, -32603, str(e))


def _ok(mid, result):
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _err(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def serve(stdin=None, stdout=None):
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    for raw in stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw.decode("utf-8"))
        except ValueError:
            resp = _err(None, -32700, "parse error")
        else:
            if isinstance(msg, list):                      # batches (older protocol versions)
                resp = [r for r in (handle(m) for m in msg) if r is not None] or None
            else:
                resp = handle(msg)
        if resp is not None:
            stdout.write((json.dumps(resp, default=str) + "\n").encode("utf-8"))
            stdout.flush()
