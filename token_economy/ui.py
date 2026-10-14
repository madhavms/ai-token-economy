"""tke ui: a local control centre. Stdlib http.server on 127.0.0.1; never calls a model.

Security: binds to 127.0.0.1 only, rejects requests whose Host isn't localhost (DNS rebinding),
and every state-changing POST must carry a per-launch token that only the served page knows.
"""
import json
import secrets
import threading
import time
import webbrowser
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__, config, estimate, health, logs, prices, report, route, setup

WEB = Path(__file__).resolve().parent / "web"
TOKEN = secrets.token_hex(16)
_cache = {"at": 0, "calls": None}
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
            if u.path == "/api/summary":
                return self._send(200, summary(q.get("period", "month"), q.get("refresh") == "1"))
            if u.path == "/api/session":
                return self._send(200, session_detail(q.get("tool", "claude"), q.get("id", "")))
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
