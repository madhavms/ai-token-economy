"""tke ui: a local control centre. Stdlib http.server on 127.0.0.1; never calls a model.

Security: binds to 127.0.0.1 only, rejects requests whose Host isn't localhost (DNS rebinding),
and every state-changing POST must carry a per-launch token that only the served page knows.
"""
import json
import secrets
from collections import defaultdict
import threading
import time
import webbrowser
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__, agents, analyze, config, estimate, health, logs, prices, report, route, setup

WEB = Path(__file__).resolve().parent / "web"
TOKEN = secrets.token_hex(16)
_cache = {"at": 0, "calls": None, "sum_at": 0, "sums": None}
_lock = threading.Lock()


def pretty_project(p):
    if not p:
        return ""
    home = str(Path.home()).replace("\\", "-").replace("/", "-").replace(":", "-")
    return (p[len(home):] if p.startswith(home) else p).lstrip("-") or "~"


def calls(force=False):
    """All calls since the start of last month, cached for 60 s (parsing takes about a second)."""
    with _lock:
        if force or _cache["calls"] is None or time.time() - _cache["at"] > 60:
            now = report.local_now()
            since = report.month_start(report.month_start(now) - timedelta(days=1))
            _cache.update(at=time.time(), calls=logs.all_calls(since))
        return _cache["calls"]


def summary(period, refresh=False):
    cfg = config.load()
    now = report.local_now()
    allc = calls(refresh)
    month = report.month_start(now)
    since = {"today": now.replace(hour=0, minute=0, second=0, microsecond=0),
             "week": (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0),
             "30d": now - timedelta(days=30)}.get(period, month)
    out = {"now": now.isoformat(), "period": period, "prices_date": prices.PRICES_DATE, "config": cfg,
           "version": __version__, "tools": {}}
    for tool, budget, unit in (("claude", cfg["claude_budget_usd"], "$"), ("codex", cfg["codex_budget_credits"], "cr")):
        mc = [c for c in allc if c.tool == tool and c.ts >= month]
        pc = [c for c in allc if c.tool == tool and c.ts >= since]
        spent = sum(c.cost or 0 for c in mc)
        p = report.pace(spent, budget, now, cfg.get("workdays"))
        p["runout"] = p["runout"].isoformat() if p["runout"] else None
        days = {}
        for c in mc:
            d = c.ts.astimezone(now.tzinfo).date().isoformat()
            days[d] = days.get(d, 0) + (c.cost or 0)
        lk = report.leaks(pc, cfg["big_context"])
        top = report.sessions(pc, cfg["big_context"], 12)
        for s in top:
            s["models"] = sorted(m for m in s["models"] if m)
            s["project"] = pretty_project(s["project"])
            s["start"], s["end"] = s["start"].isoformat(), s["end"].isoformat()
        out["tools"][tool] = {"unit": unit, "pace": p, "days": days, "leaks": lk, "tips": report.tips_for(lk),
                              "sessions": top, "calls": len(pc), "has_data": bool(pc or mc)}
    return out


def all_summaries(force=False):
    """Session summaries since the start of last month (disk-cached per file; 60 s in memory)."""
    with _lock:
        if force or _cache["sums"] is None or time.time() - _cache["sum_at"] > 60:
            now = report.local_now()
            since = report.month_start(report.month_start(now) - timedelta(days=1))
            _cache.update(sum_at=time.time(), sums=analyze.summaries(since, None, config.load()["big_context"]))
        return _cache["sums"]


def _range(period, now):
    from .cli import since_for
    since = since_for(period, now)
    until = (report.month_start(now) - timedelta(days=1)) if period == "lastmonth" else now
    return since.date().isoformat(), until.date().isoformat()


def _lite(s, ip=None):
    keep = ("tool", "session", "title", "project", "cwd", "start", "end", "calls", "cost", "lead_cost", "sub_cost",
            "main_model", "peak", "rebuilds", "compactions", "big_calls", "headline", "unit")
    d = {k: s.get(k) for k in keep}
    d["subagent_runs"] = len(s.get("subagents") or [])
    d["models"] = sorted(s.get("all_models") or s.get("models") or {}, key=lambda m: -(s.get("all_models") or {}).get(m, 0))
    if ip is not None:
        d["period_cost"] = ip["cost"]
        d["period_share"] = ip["share"]
    return d


def overview(period, tool, project=None, refresh=False):
    cfg = config.load()
    now = report.local_now()
    sums = [s for s in all_summaries(refresh) if s["tool"] == tool]
    projects = sorted({s["project"] for s in sums if s["project"]})
    if project:
        sums = [s for s in sums if s["project"] == project]
    since_day, until_day = _range(period, now)
    agg = analyze.period_report(sums, since_day, until_day)
    unit = "$" if tool == "claude" else "cr"
    total = agg["totals"].get(unit, 0.0)
    month0 = report.month_start(now).date().isoformat()
    today = now.date().isoformat()
    # month to date, by day and model, for the budget charts (ignores the period filter on purpose)
    mdays = defaultdict(lambda: defaultdict(float))
    last_month = 0.0
    lm0 = report.month_start(report.month_start(now) - timedelta(days=1)).date().isoformat()
    for s in sums:
        for d, mv in (s.get("day_models") or {}).items():
            if d >= month0:
                for m, v in mv.items():
                    mdays[d][m] += v
            elif lm0 <= d < month0:
                last_month += sum(mv.values())
    budget = cfg["claude_budget_usd"] if tool == "claude" else cfg["codex_budget_credits"]
    spent = sum(sum(v.values()) for v in mdays.values())
    pace = report.pace(spent, budget, now, cfg.get("workdays"))
    pace["runout"] = pace["runout"].isoformat() if pace["runout"] else None
    pace["today"] = sum(mdays.get(today, {}).values())
    left_wd = max(0, pace["workdays_month"] - pace["workdays_done"])
    pace["projected"] = spent + pace["daily_avg"] * left_wd
    pace["last_month"] = last_month
    names = {"big": "Calls over %dK tokens" % (cfg["big_context"] // 1000), "model": "Top-tier model premium",
             "rebuild": "Cache rebuilt after breaks", "haiku_long": "Haiku over 100K tokens",
             "repeat": "Same result loaded repeatedly", "startup": "Heavy startup context"}
    fix = {"big": "Fresh session per task or phase (/clear), with a short handoff note; compact earlier.",
           "model": "Start routine work on the everyday model; switch up on purpose for hard problems.",
           "rebuild": analyze.CATEGORIES["rebuild"][1], "haiku_long": "Cap Haiku subagents with maxTurns and narrow briefs.",
           "repeat": "Read it once and refer back, or read only the part that changed.",
           "startup": analyze.CATEGORIES["startup"][1]}
    causes = sorted(({"key": k, "label": analyze.CATEGORIES[k][0], "fix": analyze.CATEGORIES[k][1], "cost": v}
                     for (u, k), v in agg["categories"].items() if u == unit and v > 0), key=lambda r: -r["cost"])
    levers = sorted(({"key": k, "label": names.get(k, k), "fix": fix.get(k, ""), "cost": v}
                     for (u, k), v in agg["levers"].items() if u == unit and v > 0), key=lambda r: -r["cost"])
    models = sorted(({"model": m, "cost": v} for (u, m), v in agg["models"].items() if u == unit and v > 0), key=lambda r: -r["cost"])
    projs = sorted(({"project": k, "cost": v["cost"], "sessions": v["sessions"]} for (u, k), v in agg["projects"].items() if u == unit),
                   key=lambda r: -r["cost"])
    items = sorted(({"label": k, "cat": v["cat"], "cat_label": analyze.CATEGORIES.get(v["cat"], ("?",))[0], "cost": v["cost"],
                     "sessions": v["sessions"], "tokens": v["tokens"]} for (u, k), v in agg["labels"].items() if u == unit),
                   key=lambda r: -r["cost"])[:40]
    pdays = {d: dict(mv) for (u, d), mv in agg["days"].items() if u == unit}
    sess = [_lite(s, {"cost": s["period_cost"], "share": s["period_share"]}) for s in agg["sessions"] if s["unit"] == unit]
    calls_n = sum(s["calls"] * s["period_share"] for s in agg["sessions"] if s["unit"] == unit)
    return {"now": now.isoformat(), "period": period, "since": since_day, "until": until_day, "tool": tool, "unit": unit,
            "prices_date": prices.PRICES_DATE, "version": __version__, "config": cfg, "projects": projects, "project": project,
            "pace": pace, "month_days": {d: dict(v) for d, v in mdays.items()},
            "total": total, "sessions": sess, "calls": int(round(calls_n)), "causes": causes, "levers": levers,
            "models": models, "by_project": projs, "items": items, "days": pdays,
            "has_claude": any(s["tool"] == "claude" for s in all_summaries()),
            "has_codex": any(s["tool"] == "codex" for s in all_summaries())}


def session_full(tool, sid):
    found = analyze.find_session(sid)
    if not found:
        return {"error": "not found"}
    s, rows = analyze.analyze_file(found[1], found[0], config.load()["big_context"])
    models = sorted({r["model"] for r in rows})
    mi = {m: i for i, m in enumerate(models)}
    s = dict(s)
    s["call_models"] = models
    s["rows"] = [[r["i"], r["ts"].isoformat() if r["ts"] else None, mi[r["model"]], r["prompt"], r["read"], r["new"], r["out"],
                  round(r["cost"], 6), 1 if r["rebuild"] else 0, 1 if r["compaction"] else 0, int(r["gap"]),
                  ",".join(sorted(set(r["tools"])))] for r in rows]
    s["labels"] = {k: v[0] for k, v in analyze.CATEGORIES.items()}
    return s


def agents_report(period, directory=None):
    from .cli import since_for
    now = report.local_now()
    since = since_for(period, now)
    chk = agents.setup_check(directory) if directory else agents.setup_check()
    sessions, runs = agents.usage(since)
    summ = agents.summarize(sessions, runs)
    leaks = agents.findings(sessions, runs, summ)
    weakest = sorted([r for r in runs if r["has_brief"] and r["brief_missing"] and agents.UNSEEN not in r["brief_missing"]],
                     key=lambda r: (-len(r["brief_missing"]), -r["cost"]))[:12]
    cx = agents.codex_usage(since)
    for t in cx["types"]:
        t["models"] = sorted(t["models"])
    return {"setup": chk, "summary": summ, "findings": [{"dollars": d, "text": t} for d, t in leaks],
            "weakest": [{k: r[k] for k in ("type", "description", "cost", "brief_missing", "brief_chars", "start")} for r in weakest],
            "codex": cx, "template": agents.BRIEF_TEMPLATE}


def mcp_status():
    cmd, args = setup._cmd_parts("mcp")
    files, notes, commands = setup.plan_mcp()
    return {"command": " ".join(setup._q(x) for x in [cmd] + args), "notes": notes,
            "claude_cli": " ".join(setup._q(x) for x in (commands[0] if commands else ["claude", "mcp", "add", "--scope", "user", "tke", "--", cmd] + args))}


def session_detail(tool, sid):
    rows = sorted((c for c in calls() if c.tool == tool and c.session == sid), key=lambda c: c.ts)
    return {"tool": tool, "session": sid, "calls": [{
        "ts": c.ts.isoformat(), "model": c.model, "prompt": c.prompt, "read": c.read, "written": (c.w5 or 0) + (c.w1h or 0),
        "uncached": c.uncached, "out": c.out, "cost": c.cost, "rebuild": c.rebuild, "waste": c.rebuild_waste,
        "subagent": c.subagent} for c in rows]}


class Handler(BaseHTTPRequestHandler):
    server_version = "tke/" + __version__

    def log_message(self, *a):
        pass

    def _host_ok(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"error": "bad host"})
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/favicon.ico":
                return self._send(204, b"", "image/x-icon")
            if u.path == "/":
                html = (WEB / "index.html").read_text(encoding="utf-8").replace("__TKE_TOKEN__", TOKEN)
                return self._send(200, html.encode(), "text/html; charset=utf-8")
            if u.path == "/learn":
                page = WEB.parent.parent / "docs" / "basics.html"
                if page.exists():
                    return self._send(200, page.read_bytes(), "text/html; charset=utf-8")
                return self._send(404, b"The basics page ships with the git clone (docs/basics.html).", "text/plain")
            if u.path == "/api/summary":
                return self._send(200, summary(q.get("period", "month"), q.get("refresh") == "1"))
            if u.path == "/api/session":
                return self._send(200, session_detail(q.get("tool", "claude"), q.get("id", "")))
            if u.path == "/api/overview":
                return self._send(200, overview(q.get("period", "month"), q.get("tool", "claude"), q.get("project") or None,
                                                q.get("refresh") == "1"))
            if u.path == "/api/why":
                return self._send(200, session_full(q.get("tool", "claude"), q.get("id", "")))
            if u.path == "/api/agents":
                return self._send(200, agents_report(q.get("period", "month"), q.get("dir") or None))
            if u.path == "/api/mcp":
                return self._send(200, mcp_status())
            if u.path == "/api/health":
                plan = {}
                for t, fn in (("claude", setup.plan_claude), ("codex", setup.plan_codex)):
                    plan[t] = fn()[1]
                return self._send(200, {"checks": health.check(), "plan": plan})
            if u.path == "/api/estimate":
                total, parts, unit = estimate.estimate(q.get("model", "claude-opus-5-5"), int(q.get("calls", 60)),
                                                       float(q.get("start", 25)) * 1000, float(q.get("end", 120)) * 1000,
                                                       int(q.get("out", 600)), int(q.get("breaks", 0)), q.get("ttl", "5m"))
                return self._send(200, {"total": total, "parts": parts, "unit": unit})
            if u.path == "/api/models":
                return self._send(200, {"claude": sorted(prices.CLAUDE), "codex": sorted(prices.CODEX)})
            if u.path == "/api/route":
                lane = route.decide({k: q.get(k) == "1" for k in ("check", "decided", "hard")})
                return self._send(200, dict(route.LANES[lane], lane=lane))
        except Exception as e:  # report, don't crash the server
            return self._send(500, {"error": str(e)})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        # read the body before answering: replying first makes some systems reset the connection
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n > 65536:
            return self._send(413, {"error": "too large"})
        raw = self.rfile.read(n) if n > 0 else b""
        if not self._host_ok() or self.headers.get("X-TKE-Token") != TOKEN:
            return self._send(403, {"error": "forbidden"})
        try:
            body = json.loads(raw or b"{}")
            if self.path == "/api/budget":
                cfg = config.load()
                for k in ("claude_budget_usd", "codex_budget_credits", "workdays"):
                    if body.get(k) not in (None, ""):
                        cfg[k] = float(body[k]) if k != "workdays" else int(body[k])
                config.save(cfg)
                return self._send(200, {"ok": True, "config": cfg})
            if self.path == "/api/setup":
                written = []
                for t in body.get("targets", []):
                    if t == "claude":
                        files, notes = setup.plan_claude(force=bool(body.get("force")))
                    elif t == "codex":
                        files, notes = setup.plan_codex(force=bool(body.get("force")))
                    else:
                        continue
                    for path, text in files:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        b = setup._backup(path)
                        path.write_text(text, encoding="utf-8")
                        written.append({"file": str(path), "backup": str(b) if b else None})
                return self._send(200, {"ok": True, "written": written})
        except Exception as e:
            return self._send(500, {"error": str(e)})
        self._send(404, {"error": "not found"})


def serve(port=8790, open_browser=True):
    srv = None
    for p in range(port, port + 20):          # the port may be taken by another app: try the next ones
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", p), Handler)
            break
        except OSError:
            continue
    if srv is None:
        raise SystemExit("tke: no free port between %d and %d; pass --port" % (port, port + 19))
    url = "http://127.0.0.1:%d/" % srv.server_address[1]
    print("tke control centre on %s  (local only; Ctrl+C to stop)" % url, flush=True)
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
