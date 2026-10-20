"""Read Claude Code transcripts and Codex session logs into one list of priced model calls.

Nothing leaves the machine: both tools already write these files locally.
- Claude Code: <claude home>/projects/<project>/<session>.jsonl, subagents under <session>/subagents/.
  One reply can span several lines that repeat the same usage, so calls are de-duplicated by
  message id + request id.
- Codex: <codex home>/sessions/YYYY/MM/DD/rollout-*.jsonl. token_count events carry a running
  total per session; input_tokens includes cached_input_tokens.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config, prices

FIVE_MIN = timedelta(minutes=5)
ONE_HOUR = timedelta(hours=1)


class Call:
    __slots__ = ("tool", "session", "project", "ts", "model", "prompt", "read", "w5", "w1h",
                 "uncached", "out", "cost", "subagent", "rebuild", "rebuild_waste", "speed", "geo", "searches",
                 "fast_extra")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))
        self.rebuild = bool(self.rebuild)
        self.rebuild_waste = self.rebuild_waste or 0.0
        self.searches = self.searches or 0
        self.fast_extra = self.fast_extra or 0.0


def parse_ts(s):
    try:
        return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _files(root, pattern, since):
    if not root.exists():
        return []
    cutoff = since.timestamp() if since else 0
    out = []
    for p in root.rglob(pattern):
        try:
            if p.stat().st_mtime >= cutoff:
                out.append(p)
        except OSError:
            pass
    return out


def _lines(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.strip():
                    try:
                        yield json.loads(line)
                    except ValueError:
                        continue
    except OSError:
        return


# ------------------------------------------------------------------ Claude Code
def claude_file_calls(path, since=None, project=None, subagent=None):
    """Priced calls from one Claude Code transcript file."""
    seen = {}
    for d in _lines(path):
        msg = d.get("message") if isinstance(d.get("message"), dict) else None
        if not msg or d.get("type") != "assistant" or not isinstance(msg.get("usage"), dict):
            continue
        model = msg.get("model") or ""
        if model.startswith("<"):          # "<synthetic>" placeholder replies
            continue
        key = (msg.get("id"), d.get("requestId")) if msg.get("id") else id(d)
        seen[key] = (d, msg)               # keep the last copy of a repeated reply
    calls = []
    for d, msg in seen.values():
        u = msg["usage"]
        cc = u.get("cache_creation") or {}
        write = u.get("cache_creation_input_tokens", 0) or 0
        w1h = cc.get("ephemeral_1h_input_tokens", 0) or 0
        w5 = cc.get("ephemeral_5m_input_tokens", write - w1h) or 0
        inp = u.get("input_tokens", 0) or 0
        read = u.get("cache_read_input_tokens", 0) or 0
        out = u.get("output_tokens", 0) or 0
        ts = parse_ts(d.get("timestamp"))
        if ts is None or (since and ts < since):
            continue
        speed, geo = u.get("speed"), u.get("inference_geo")
        searches = ((u.get("server_tool_use") or {}).get("web_search_requests") or 0)
        cost = prices.claude_cost(msg.get("model"), inp, w5, w1h, read, out, speed, geo, searches)
        mult = prices.fast_multiplier(msg.get("model"), speed)
        calls.append(Call(
            tool="claude", session=d.get("sessionId") or path.stem, project=project,
            ts=ts, model=msg.get("model"), prompt=inp + w5 + w1h + read, read=read,
            w5=w5, w1h=w1h, uncached=inp, out=out, cost=cost, speed=speed, geo=geo, searches=searches,
            fast_extra=(cost - searches * prices.WEB_SEARCH) * (1 - 1 / mult) if (cost and mult > 1) else 0.0,
            subagent=bool(d.get("isSidechain")) if subagent is None else subagent))
    return calls


def claude_calls(since=None, home=None):
    root = (home or config.claude_home()) / "projects"
    calls = []
    for path in _files(root, "*.jsonl", since):
        rel = path.relative_to(root).parts
        calls += claude_file_calls(path, since, rel[0], True if "subagents" in rel else None)
    _mark_rebuilds(calls)
    return calls


def _mark_rebuilds(calls):
    """A call that writes most of a big prompt after the cache lifetime has passed is a rebuild."""
    by_chain = {}
    for c in calls:
        by_chain.setdefault((c.session, c.subagent), []).append(c)
    for chain in by_chain.values():
        chain.sort(key=lambda c: c.ts)
        for prev, c in zip(chain, chain[1:]):
            ttl = ONE_HOUR if prev.w1h else FIVE_MIN
            written = c.w5 + c.w1h
            if c.ts - prev.ts > ttl and c.prompt >= 20000 and written >= 0.6 * c.prompt:
                c.rebuild = True
                p = prices.claude_rates(c.model, c.prompt, c.speed, c.geo)
                if p:
                    c.rebuild_waste = (c.w5 * p["w5"] + c.w1h * p["w1h"] - written * p["read"]) / 1e6


# ------------------------------------------------------------------------ Codex
def codex_meta(path):
    """Who ran this rollout: the main session, or a subagent (with its parent thread and role)."""
    for d in _lines(path):
        if d.get("type") == "session_meta":
            p = d.get("payload") or {}
            src = p.get("source")
            spawn = (src.get("subagent") or {}).get("thread_spawn") if isinstance(src, dict) else None
            return {"id": p.get("id") or path.stem[-36:], "parent": (spawn or {}).get("parent_thread_id"),
                    "agent": (spawn or {}).get("agent_path") or p.get("agent_role"), "role": p.get("agent_role"),
                    "subagent": bool(spawn)}
        break
    return {"id": path.stem[-36:], "parent": None, "agent": None, "role": None, "subagent": False}


def codex_file_calls(path, since=None, mult=None):
    if mult is None:
        mult = config.codex_speed()[1]
    calls, model, prev = [], None, None
    sub = codex_meta(path)["subagent"]
    for d in _lines(path):
        p = d.get("payload") or {}
        if d.get("type") == "turn_context":
            model = p.get("model") or model
            continue
        if p.get("type") != "token_count" or not isinstance(p.get("info"), dict):
            continue
        tot = p["info"].get("total_token_usage") or {}
        cur = (tot.get("input_tokens", 0), tot.get("cached_input_tokens", 0), tot.get("output_tokens", 0))
        if prev is None or cur[0] < prev[0]:
            last = p["info"].get("last_token_usage") or tot
            delta = (last.get("input_tokens", 0), last.get("cached_input_tokens", 0), last.get("output_tokens", 0))
        else:
            delta = tuple(a - b for a, b in zip(cur, prev))
        prev = cur
        if delta == (0, 0, 0):
            continue                      # a repeated event
        ts = parse_ts(d.get("timestamp"))
        if ts is None or (since and ts < since):
            continue
        inp, cached, out = delta
        calls.append(Call(
            tool="codex", session=path.stem, project=None, ts=ts, model=model, prompt=inp,
            read=cached, w5=0, w1h=0, uncached=max(0, inp - cached), out=out,
            cost=prices.codex_credits(model, max(0, inp - cached), cached, out, mult), subagent=sub,
            speed=None if mult == 1 else "fast",
            fast_extra=(prices.codex_credits(model, max(0, inp - cached), cached, out, mult) or 0) * (1 - 1 / mult)))
    return calls


def codex_files(since=None, home=None):
    base = home or config.codex_home()
    return _files(base / "sessions", "*.jsonl", since) + _files(base / "archived_sessions", "*.jsonl", since)


def codex_calls(since=None, home=None):
    calls, mult = [], config.codex_speed()[1]
    for path in codex_files(since, home):
        calls += codex_file_calls(path, since, mult)
    return calls


def all_calls(since=None):
    return claude_calls(since) + codex_calls(since)
