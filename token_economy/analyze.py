"""Why a session cost what it did.

Every model call re-sends the whole conversation. Its bill has three parts: tokens read from the
prompt cache, new tokens written (or sent uncached), and output. This module splits each part over
what is actually in the conversation at that call:

- read cost is shared over everything already in the context, in proportion to its size, so a
  40K-token log read at call 12 is charged a little on every later call that re-reads it;
- write cost goes to what is new since the previous call (tool results, your message, Claude's
  previous reply, reminders), split by size, or to "cache rebuilt after a break" when the cache
  had expired and the whole prompt was written again;
- output cost goes to "output and thinking".

The parts add up to the session's cost exactly. Token counts of individual items are estimated
from their length and scaled to the exact new-token count the API reported for that call.

Works on Claude Code transcripts and Codex rollouts. Never calls a model.
"""
import json
import re
from collections import OrderedDict, defaultdict
from datetime import timedelta
from pathlib import Path

from . import config, logs, prices

FIVE_MIN = timedelta(minutes=5)
ONE_HOUR = timedelta(hours=1)
CACHE_VERSION = 6

# key: (label, what to do about it)
CATEGORIES = OrderedDict([
    ("startup", ("Startup context", "System prompt, tool definitions, CLAUDE.md/AGENTS.md, memory and MCP/skill listings, "
                 "re-read on every call. Trim always-loaded instructions and disconnect MCP servers this repo doesn't use.")),
    ("read", ("File reads", "Read line ranges after a grep instead of whole files; hand wide reading to a search subagent "
              "that returns a short report.")),
    ("bash", ("Command output", "Pipe long output through tail/grep, run one test file first with quiet flags "
              "(tke's test-output trim hook does this).")),
    ("search", ("Search results", "Narrow the pattern or path; ask for file names first, contents second.")),
    ("web", ("Web pages", "Fetch the specific page, or ask a subagent to read it and report back.")),
    ("mcp", ("MCP tool results", "Ask MCP tools for less (filters, limits) or move them to a subagent.")),
    ("agent", ("Subagent reports", "Ask subagents for at most 15 lines; long reports are re-read on every later call.")),
    ("edit", ("Edits and file writes", "Use targeted edits; whole-file rewrites put the whole file in the context twice.")),
    ("other_tool", ("Other tool results", "")),
    ("user", ("Your messages", "Paste paths, not file contents or logs; attach only what's needed.")),
    ("attach", ("Reminders, hooks and attachments", "Hook output and injected reminders ride along on every call; "
                "keep hook output short.")),
    ("assistant", ("Earlier replies and tool calls", "Long replies and big tool inputs are carried forward; ask for concise "
                   "replies and targeted edits.")),
    ("compact", ("Compacted summary", "")),
    ("output", ("Output and thinking", "Lower effort for routine work (medium, or low for chores); thinking is billed as output.")),
    ("rebuild", ("Cache rebuilt (break or switch)", "The whole prompt was written again: after a break longer than the cache "
                 "lifetime, or after switching model or fast mode mid-session. Set a 1-hour cache (promptCacheTtl), "
                 "/clear before a long break, and pick the model and speed when the session starts.")),
    ("subagents", ("Subagents' own work", "Run search and scoping subagents on a small model with a turn cap (tke agents).")),
])

TOOL_CAT = {
    "read": "read", "notebookread": "read", "read_file": "read", "view": "read", "view_image": "read",
    "bash": "bash", "bashoutput": "bash", "powershell": "bash", "exec_command": "bash", "shell": "bash",
    "local_shell": "bash", "write_stdin": "bash", "monitor": "bash",
    "grep": "search", "glob": "search", "ls": "search", "list_dir": "search", "find": "search", "toolsearch": "search",
    "webfetch": "web", "websearch": "web", "web_search": "web", "web_search_call": "web",
    "agent": "agent", "task": "agent", "spawn_agent": "agent", "wait_agent": "agent", "wait": "agent",
    "sendmessage": "agent", "taskoutput": "agent",
    "edit": "edit", "write": "edit", "multiedit": "edit", "notebookedit": "edit", "apply_patch": "edit",
}

SKIP_ATTACH = {"prompt_snapshot", "queued_command", "file_history", "remote_session_change", "credential_org",
               "batching_reminder_sent", "total_tokens_reminder"}


def tool_category(name):
    n = (name or "").lower()
    if n.startswith("mcp__") or n.startswith("mcp_"):
        return "mcp"
    return TOOL_CAT.get(n, "other_tool")


def _short(s, n=70):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[:n - 1] + "…"


def item_label(name, inp):
    """A human label for a tool call: the file, the command, the pattern, the URL."""
    inp = inp if isinstance(inp, dict) else {}
    n = (name or "").lower()
    for key in ("file_path", "notebook_path", "path"):
        if inp.get(key) and n in ("read", "edit", "write", "multiedit", "notebookedit", "notebookread", "read_file", "view"):
            return "%s %s" % (name, _tail_path(inp[key]))
    if inp.get("command") or inp.get("cmd"):
        c = inp.get("command") or inp.get("cmd")
        return "%s: %s" % (name, _short(" ".join(c) if isinstance(c, list) else c, 60))
    if inp.get("pattern"):
        return "%s %s%s" % (name, _short(inp["pattern"], 40), (" in " + _tail_path(inp["path"])) if inp.get("path") else "")
    if inp.get("url"):
        return "%s %s" % (name, _short(inp["url"], 60))
    if inp.get("query"):
        return "%s %s" % (name, _short(inp["query"], 50))
    if inp.get("description"):
        return "%s: %s" % (name, _short(inp["description"], 50))
    if n == "apply_patch" and isinstance(inp.get("input"), str):
        m = re.search(r"\*\*\* (?:Update|Add|Delete) File: (\S+)", inp["input"])
        if m:
            return "apply_patch %s" % _tail_path(m.group(1))
    return name or "tool"


def _tail_path(p, parts=3):
    bits = re.split(r"[\\/]+", str(p))
    return "/".join(bits[-parts:]) if len(bits) > parts else str(p)


def _chars(v):
    if v is None:
        return 0
    if isinstance(v, str):
        return len(v)
    if isinstance(v, list):
        n = 0
        for b in v:
            if isinstance(b, dict):
                if b.get("type") == "image":
                    n += 6000                       # images are ~1.5K tokens
                else:
                    n += _chars(b.get("text") if "text" in b else b.get("content"))
            else:
                n += _chars(b)
        return n
    if isinstance(v, dict):
        return _chars(v.get("text") or v.get("content") or json.dumps(v))
    return len(str(v))


# ------------------------------------------------------------------ event streams
def claude_events(path):
    """Normalise a Claude Code transcript (main chain only) into an ordered event list."""
    events, meta = [], {"title": None, "cwd": None, "first_prompt": None, "version": None}
    cur = None                                  # the assistant message being assembled
    tools = {}
    seen = {}                                   # message id -> call (parallel tool results can split a reply)

    def flush():
        if cur and not cur.get("_done"):
            cur["_done"] = True
            events.append(("call", cur))

    for d in logs._lines(path):
        t = d.get("type")
        if d.get("isSidechain"):
            continue
        if t == "ai-title" and d.get("aiTitle"):
            meta["title"] = d["aiTitle"]
            continue
        if d.get("cwd"):
            meta["cwd"] = d["cwd"]
        if d.get("version"):
            meta["version"] = d["version"]
        if t == "assistant":
            msg = d.get("message") or {}
            mid = msg.get("id") or id(d)
            if mid in seen:
                if cur is not seen[mid]:
                    flush()
                    cur = seen[mid]
                    cur["_done"] = True             # already in the event list
            elif cur is None or cur["id"] != mid:
                flush()
                cur = {"id": mid, "ts": logs.parse_ts(d.get("timestamp")), "model": msg.get("model") or "",
                       "usage": None, "out_chars": 0, "tools": [], "big_tool": None, "big_size": 0}
                seen[mid] = cur
            if isinstance(msg.get("usage"), dict):
                cur["usage"] = msg["usage"]
            for b in msg.get("content") or []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text":
                    cur["out_chars"] += len(b.get("text") or "")
                elif b.get("type") == "tool_use":
                    size = len(json.dumps(b.get("input") or {}))
                    cur["out_chars"] += size
                    tools[b.get("id")] = (b.get("name"), b.get("input"))
                    cur["tools"].append(b.get("name"))
                    if size > cur["big_size"]:
                        cur["big_size"], cur["big_tool"] = size, item_label(b.get("name"), b.get("input"))
            continue
        if t == "system" and d.get("subtype") == "compact_boundary":
            flush()
            cur = None
            events.append(("reset", {"reason": "compact"}))
            continue
        if t == "user":
            flush()
            cur = None
            msg = d.get("message") or {}
            content = msg.get("content")
            if d.get("isCompactSummary"):
                events.append(("item", {"cat": "compact", "label": "Compacted summary", "chars": _chars(content)}))
                continue
            if isinstance(content, str):
                kind = "attach" if (d.get("isMeta") or content.lstrip().startswith("<")) else "user"
                if kind == "user" and not meta["first_prompt"]:
                    meta["first_prompt"] = _short(content, 120)
                events.append(("item", {"cat": kind, "label": "Your message" if kind == "user" else "Reminder",
                                        "chars": len(content)}))
                continue
            for b in content or []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_result":
                    name, inp = tools.get(b.get("tool_use_id"), ("tool", {}))
                    events.append(("item", {"cat": tool_category(name), "label": item_label(name, inp), "tool": name,
                                            "chars": _chars(b.get("content"))}))
                elif b.get("type") in ("text", "image"):
                    txt = b.get("text") or ""
                    kind = "attach" if (d.get("isMeta") or txt.lstrip().startswith("<")) else "user"
                    if kind == "user" and txt and not meta["first_prompt"]:
                        meta["first_prompt"] = _short(txt, 120)
                    events.append(("item", {"cat": kind, "label": "Your message" if kind == "user" else "Reminder",
                                            "chars": _chars([b])}))
            continue
        if t == "attachment":
            a = d.get("attachment") or {}
            if a.get("type") in SKIP_ATTACH:
                continue
            n = sum(len(v) for v in a.values() if isinstance(v, str) and v not in (a.get("type"),))
            if n == 0:
                n = sum(_chars(v) for k, v in a.items() if k not in ("type",) and isinstance(v, (list, dict)))
            if n:
                events.append(("item", {"cat": "attach", "label": "Attachment: %s" % a.get("type", "?"), "chars": n}))
    flush()
    return events, meta


def codex_events(path):
    events, meta = [], {"title": None, "cwd": None, "first_prompt": None, "version": None}
    tools, model, prev_total = {}, None, None
    out_chars, called = 0, []
    for d in logs._lines(path):
        p = d.get("payload") or {}
        t, pt = d.get("type"), p.get("type")
        if t == "session_meta":
            meta["cwd"] = p.get("cwd")
            meta["version"] = p.get("cli_version")
            continue
        if t == "turn_context":
            model = p.get("model") or model
            meta["cwd"] = p.get("cwd") or meta["cwd"]
            continue
        if t == "compacted" or pt == "compacted":
            events.append(("reset", {"reason": "compact"}))
            continue
        if t == "response_item":
            if pt == "message":
                role = p.get("role")
                n = _chars(p.get("content"))
                if role == "assistant":
                    out_chars += n
                elif role == "user":
                    txt = "".join(c.get("text", "") for c in p.get("content") or [] if isinstance(c, dict))
                    kind = "attach" if txt.lstrip().startswith("<") else "user"
                    if kind == "user" and not meta["first_prompt"]:
                        meta["first_prompt"] = _short(txt, 120)
                    events.append(("item", {"cat": kind, "label": "Your message" if kind == "user" else "Context note", "chars": n}))
                else:
                    events.append(("item", {"cat": "startup", "label": "Instructions", "chars": n}))
            elif pt in ("function_call", "custom_tool_call", "local_shell_call"):
                args = p.get("arguments") if pt == "function_call" else p.get("input")
                try:
                    inp = json.loads(args) if isinstance(args, str) and args.strip().startswith("{") else {"input": args}
                except ValueError:
                    inp = {"input": args}
                if pt == "local_shell_call":
                    inp = (p.get("action") or {})
                name = p.get("name") or "shell"
                tools[p.get("call_id")] = (name, inp)
                out_chars += len(str(args or ""))
                called.append(name)
            elif pt in ("function_call_output", "custom_tool_call_output", "local_shell_call_output"):
                name, inp = tools.get(p.get("call_id"), ("tool", {}))
                events.append(("item", {"cat": tool_category(name), "label": item_label(name, inp), "tool": name,
                                        "chars": _chars(p.get("output"))}))
            elif pt == "web_search_call":
                q = (p.get("action") or {}).get("query")
                events.append(("item", {"cat": "web", "label": "web_search %s" % _short(q, 50), "tool": "web_search", "chars": 2000}))
            continue
        if t == "event_msg" and pt == "token_count" and isinstance(p.get("info"), dict):
            tot = p["info"].get("total_token_usage") or {}
            cur = (tot.get("input_tokens", 0), tot.get("cached_input_tokens", 0), tot.get("output_tokens", 0))
            if prev_total is not None and cur == prev_total:
                continue
            last = p["info"].get("last_token_usage") or tot
            prev_total = cur
            inp, cached, out = last.get("input_tokens", 0), last.get("cached_input_tokens", 0), last.get("output_tokens", 0)
            events.append(("call", {"id": len(events), "ts": logs.parse_ts(d.get("timestamp")), "model": model or "",
                                    "usage": {"codex": True, "input": inp, "cached": cached, "output": out},
                                    "out_chars": out_chars, "tools": called}))
            out_chars, called = 0, []
    return events, meta


# ------------------------------------------------------------------ pricing
def _unit_prices(tool, model, prompt, speed=None, geo=None, mult=1.0):
    """$ (or credits) per token for read, write-5m, write-1h, uncached input, output."""
    if tool == "codex":
        p = prices.codex_price(model)
        if not p:
            return None
        m = mult / 1e6
        return {"read": p["cached"] * m, "w5": p["inp"] * m, "w1h": p["inp"] * m, "inp": p["inp"] * m, "out": p["out"] * m}
    p = prices.claude_rates(model, prompt, speed, geo)
    if not p:
        return None
    return {k: p[k] / 1e6 for k in ("read", "w5", "w1h", "inp", "out")}


def _usage(u):
    if u.get("codex"):
        cached = u.get("cached", 0)
        return {"read": cached, "w5": 0, "w1h": 0, "uncached": max(0, u.get("input", 0) - cached), "out": u.get("output", 0)}
    cc = u.get("cache_creation") or {}
    write = u.get("cache_creation_input_tokens", 0) or 0
    w1h = cc.get("ephemeral_1h_input_tokens", 0) or 0
    w5 = cc.get("ephemeral_5m_input_tokens", write - w1h) or 0
    return {"read": u.get("cache_read_input_tokens", 0) or 0, "w5": w5, "w1h": w1h,
            "uncached": u.get("input_tokens", 0) or 0, "out": u.get("output_tokens", 0) or 0}


# ------------------------------------------------------------------ the decomposition
class Item:
    __slots__ = ("cat", "label", "tool", "chars", "tokens", "first", "write", "read", "carried")

    def __init__(self, cat, label, chars=0, tool=None, first=0):
        self.cat, self.label, self.tool, self.chars, self.first = cat, label, tool, chars, first
        self.tokens = 0.0
        self.write = self.read = 0.0
        self.carried = 0

    def cost(self):
        return self.write + self.read


def decompose(events, tool="claude", big_context=150000, codex_mult=1.0):
    items, pending, retired = [], [], []
    startup = None
    cat_cost = defaultdict(float)
    day_cost = defaultdict(lambda: defaultdict(float))
    rows, prev = [], None
    reset = False
    unpriced = set()
    for kind, ev in events:
        if kind == "reset":
            reset = True
            continue
        if kind == "item":
            pending.append(Item(ev["cat"], ev["label"], ev.get("chars", 0), ev.get("tool"), len(rows)))
            continue
        if not ev.get("usage"):
            continue
        u = _usage(ev["usage"])
        prompt = u["read"] + u["w5"] + u["w1h"] + u["uncached"]
        if prompt == 0 and u["out"] == 0:
            continue
        new = u["w5"] + u["w1h"] + u["uncached"]
        snap = dict(cat_cost)
        speed, geo = ev["usage"].get("speed"), ev["usage"].get("inference_geo")
        if tool == "codex" and codex_mult != 1.0:
            speed = "fast"
        searches = ((ev["usage"].get("server_tool_use") or {}).get("web_search_requests") or 0)
        p = _unit_prices(tool, ev["model"], prompt, speed, geo, codex_mult)
        if p is None:
            unpriced.add(ev["model"] or "?")
            p = {"read": 0, "w5": 0, "w1h": 0, "inp": 0, "out": 0}
        c_read = u["read"] * p["read"]
        c_write = u["w5"] * p["w5"] + u["w1h"] * p["w1h"] + u["uncached"] * p["inp"]
        c_out = u["out"] * p["out"]
        c_search = searches * prices.WEB_SEARCH
        fmult = (codex_mult if tool == "codex" else prices.fast_multiplier(ev["model"], speed))
        fast_extra = (c_read + c_write + c_out) * (1 - 1 / fmult) if fmult > 1 else 0.0
        gap = (ev["ts"] - prev["ts"]) if (prev and ev["ts"] and prev["ts"]) else timedelta(0)
        compaction = bool(prev) and (reset or (prompt < 0.6 * prev["prompt"] and prompt > 0))
        if tool == "codex":
            rebuild = bool(prev) and not compaction and prompt >= 20000 and u["read"] < 0.4 * prompt and gap > FIVE_MIN
        else:
            ttl = ONE_HOUR if (prev and prev["w1h"]) else FIVE_MIN
            rebuild = bool(prev) and not compaction and gap > ttl and prompt >= 20000 and new >= 0.6 * prompt
        switched = bool(prev) and not compaction and (prev["model"] != ev["model"] or prev["speed"] != speed)
        switch_rebuild = switched and prompt >= 20000 and new >= 0.6 * prompt and not rebuild
        rebuild = rebuild or switch_rebuild
        reset = False

        est = sum(i.chars for i in pending) / 4.0
        if startup is None or compaction:
            if startup is None:
                startup = Item("startup", "Startup context", 0, None, 0)
            retired.extend(i for i in items if i is not startup)      # dropped from the context, kept for the report
            items = [startup]
            share = min(new, est) if est else 0
            startup.tokens = max(startup.tokens if compaction else 0, prompt - share)
            # read: what was cached already (usually the system prompt) belongs to startup
            startup.read += c_read
            startup.carried += 1
            cat_cost["startup"] += c_read
            new_for_pending, startup_new = share, max(0.0, new - share)
            w_unit = (c_write / new) if new else 0
            startup.write += startup_new * w_unit
            cat_cost["startup"] += startup_new * w_unit
            _spread(pending, new_for_pending, new_for_pending * w_unit, cat_cost)
        else:
            total = sum(i.tokens for i in items) or 0
            if total > 0:
                for i in items:
                    s = i.tokens / total
                    i.read += c_read * s
                    i.carried += 1
                    cat_cost[i.cat] += c_read * s
            else:
                startup.read += c_read
                cat_cost["startup"] += c_read
            if rebuild:
                cat_cost["rebuild"] += c_write
                for i in pending:
                    i.tokens = i.chars / 4.0
            elif pending:
                _spread(pending, new, c_write, cat_cost)
            else:
                cat_cost["assistant"] += c_write
        items.extend(pending)
        pending = []
        cat_cost["output"] += c_out
        cat_cost["web"] += c_search
        cost = c_read + c_write + c_out + c_search
        day = ev["ts"].astimezone().date().isoformat() if ev["ts"] else "?"
        for k, v in cat_cost.items():
            delta = v - snap.get(k, 0.0)
            if delta:
                day_cost[day][k] += delta
        snap = dict(cat_cost)
        rows.append({"i": len(rows), "ts": ev["ts"], "model": ev["model"], "prompt": prompt, "read": u["read"],
                     "new": new, "out": u["out"], "cost": cost, "rebuild": rebuild, "compaction": compaction,
                     "gap": gap.total_seconds(), "tools": ev.get("tools", []), "w1h": u["w1h"],
                     "c_read": c_read, "c_write": c_write, "c_out": c_out, "speed": speed, "geo": geo,
                     "searches": searches, "fast_extra": fast_extra, "switch": switch_rebuild})
        prev = {"ts": ev["ts"], "prompt": prompt, "w1h": u["w1h"], "model": ev["model"], "speed": speed}
        # this call's reply is in the next call's prompt
        if ev.get("out_chars"):
            who = "Claude" if tool == "claude" else "Codex"
            label = ("%s's call: %s" % (who, ev["big_tool"])) if ev.get("big_tool") and ev.get("big_size", 0) > 2000 \
                else "%s's reply" % who
            pending.append(Item("assistant", label, ev["out_chars"], None, len(rows)))
    return {"rows": rows, "items": retired + [i for i in items if i is not startup] + ([startup] if startup else []),
            "categories": dict(cat_cost), "unpriced": sorted(unpriced),
            "days": {d: dict(v) for d, v in day_cost.items()}}


def _spread(pending, tokens, cost, cat_cost):
    chars = sum(i.chars for i in pending)
    if not pending:
        cat_cost["assistant"] += cost
        return
    for i in pending:
        s = (i.chars / chars) if chars else 1.0 / len(pending)
        i.tokens = tokens * s
        i.write += cost * s
        cat_cost[i.cat] += cost * s


# ------------------------------------------------------------------ one session
def subagent_runs(path):
    """Claude Code subagent transcripts for a session: cost, model and type of each run."""
    d = path.with_suffix("") / "subagents"
    out = []
    if not d.is_dir():
        return out
    for f in sorted(d.glob("agent-*.jsonl")):
        calls = logs.claude_file_calls(f, None, None, True)
        if not calls:
            continue
        try:
            meta = json.loads(f.with_name(f.name[:-6] + ".meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
        out.append({"type": meta.get("agentType") or "subagent", "description": meta.get("description") or "",
                    "cost": sum(c.cost or 0 for c in calls), "calls": len(calls), "by_model": _by_model(calls),
                    "by_day": _by_day(calls),
                    "models": sorted({c.model for c in calls if c.model}), "peak": max(c.prompt for c in calls),
                    "start": min(c.ts for c in calls).isoformat()})
    return out


def _by_model(calls):
    d = defaultdict(float)
    for c in calls:
        d[c.model or "?"] += c.cost or 0
    return dict(d)


def _by_day(calls):
    d = defaultdict(float)
    for c in calls:
        d[c.ts.astimezone().date().isoformat()] += c.cost or 0
    return dict(d)


def codex_children(path, all_files=None):
    """Codex subagents: rollouts whose session_meta names this rollout as parent."""
    me = logs.codex_meta(path)["id"]
    out = []
    for f in all_files if all_files is not None else logs.codex_files():
        m = logs.codex_meta(f)
        if m["parent"] and m["parent"] == me:
            calls = logs.codex_file_calls(f)
            if calls:
                out.append({"type": m["role"] or (m["agent"] or "subagent").rsplit("/", 1)[-1], "description": "",
                            "cost": sum(c.cost or 0 for c in calls), "calls": len(calls), "by_model": _by_model(calls),
                            "by_day": _by_day(calls),
                            "models": sorted({c.model for c in calls if c.model}), "peak": max(c.prompt for c in calls),
                            "start": min(c.ts for c in calls).isoformat()})
    return out


def analyze_file(path, tool, big_context=150000, codex_all=None):
    path = Path(path)
    events, meta = (claude_events if tool == "claude" else codex_events)(path)
    res = decompose(events, tool, big_context, config.codex_speed()[1] if tool == "codex" else 1.0)
    subs = subagent_runs(path) if tool == "claude" else codex_children(path, codex_all)
    rows = res["rows"]
    lead = sum(r["cost"] for r in rows)
    sub_cost = sum(s["cost"] for s in subs)
    cats = dict(res["categories"])
    if sub_cost:
        cats["subagents"] = cats.get("subagents", 0) + sub_cost
    by_model = defaultdict(float)
    for r in rows:
        by_model[r["model"]] += r["cost"]
    all_models = defaultdict(float, by_model)
    days = {d: dict(v) for d, v in res["days"].items()}
    day_models = defaultdict(lambda: defaultdict(float))
    for r in rows:
        if r["ts"]:
            day_models[r["ts"].astimezone().date().isoformat()][r["model"]] += r["cost"]
    for sub in subs:
        for m, v in sub["by_model"].items():
            all_models[m] += v
        for d, v in sub["by_day"].items():
            days.setdefault(d, {})
            days[d]["subagents"] = days[d].get("subagents", 0) + v
    for sub in subs:
        share = {m: v / sub["cost"] for m, v in sub["by_model"].items()} if sub["cost"] else {}
        for d, v in sub["by_day"].items():
            for m, sh in share.items():
                day_models[d][m] += v * sh
    items = sorted(res["items"], key=lambda i: -i.cost())
    keep, per_cat = [], defaultdict(int)
    for n, i in enumerate(items):
        if i.cost() <= 0:
            break
        if n < 25 or per_cat[i.cat] < 5:
            keep.append(i)
        per_cat[i.cat] += 1
    top = [{"cat": i.cat, "label": i.label, "tool": i.tool, "tokens": int(i.tokens), "carried": i.carried,
            "first": i.first, "cost": i.cost(), "write": i.write, "read": i.read} for i in keep]
    # the same thing loaded again and again (a file read five times)
    groups = defaultdict(lambda: {"n": 0, "cost": 0.0, "tokens": 0.0, "cat": None})
    for i in res["items"]:
        if i.cat in ("startup", "assistant", "user", "attach", "compact"):
            continue
        g = groups[i.label]
        g["n"] += 1
        g["cost"] += i.cost()
        g["tokens"] += i.tokens
        g["cat"] = i.cat
    repeats = sorted(({"label": k, **v} for k, v in groups.items() if v["n"] > 1), key=lambda g: -g["cost"])[:10]
    start = rows[0]["ts"] if rows else None
    end = rows[-1]["ts"] if rows else None
    tail = [r for r in rows if r["prompt"] >= big_context]
    out = {
        "tool": tool, "session": path.stem if tool == "claude" else logs.codex_meta(path)["id"], "path": str(path),
        "title": meta["title"] or meta["first_prompt"] or "", "cwd": meta["cwd"] or "",
        "project": _project_name(meta["cwd"], path, tool), "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None, "calls": len(rows), "cost": lead + sub_cost, "lead_cost": lead,
        "sub_cost": sub_cost, "categories": cats, "models": dict(by_model), "all_models": dict(all_models),
        "days": days, "day_models": {d: {m: v for m, v in mv.items() if v} for d, mv in day_models.items()},
        "main_model": max(by_model, key=by_model.get) if by_model else "", "peak": max((r["prompt"] for r in rows), default=0),
        "startup_tokens": int(next((i.tokens for i in res["items"] if i.cat == "startup"), 0)),
        "rebuilds": sum(1 for r in rows if r["rebuild"]), "compactions": sum(1 for r in rows if r["compaction"]),
        "big_calls": len(tail), "big_cost": sum(r["cost"] for r in tail),
        "first_big": tail[0]["i"] if tail else None, "top_items": top, "repeats": repeats, "subagents": subs,
        "unpriced": res["unpriced"], "unit": "$" if tool == "claude" else "cr",
        "output_tokens": sum(r["out"] for r in rows),
        "read_tokens": sum(r["read"] for r in rows), "new_tokens": sum(r["new"] for r in rows),
    }
    out["fast_calls"] = sum(1 for r in rows if r.get("fast_extra"))
    out["fast_extra"] = sum(r.get("fast_extra", 0) for r in rows)
    out["switch_rebuilds"] = sum(1 for r in rows if r.get("switch"))
    out["switch_cost"] = sum(r["c_write"] for r in rows if r.get("switch"))
    out["searches"] = sum(r.get("searches", 0) for r in rows)
    out["us_geo_calls"] = sum(1 for r in rows if r.get("geo") == "us")
    out["premium"] = model_premium(rows, tool)
    out["haiku_long"] = haiku_long_surcharge(rows)
    f = findings(out)
    out["causes"], out["levers"] = f["causes"], f["levers"]
    top = f["levers"][0] if f["levers"] and f["levers"][0]["share"] >= 0.25 else (f["causes"][0] if f["causes"] else None)
    out["headline"] = top["title"] if top else ""
    return out, rows


def _project_name(cwd, path, tool):
    if cwd:
        bits = [b for b in re.split(r"[\\/]+", cwd) if b]
        return "/".join(bits[-2:]) if len(bits) >= 2 else (bits[-1] if bits else cwd)
    return path.parent.name if tool == "claude" else ""


def model_premium(rows, tool):
    """What the same tokens would have cost on the everyday model (Sonnet 5.5 / GPT-6.1 Sol)."""
    base = "claude-sonnet-5-5" if tool == "claude" else "gpt-6.1-sol"
    actual = alt = 0.0
    for r in rows:
        m = (r["model"] or "").lower()
        tier = "top" if any(t in m for t in ("opus", "fable", "mythos", "astra")) else "other"
        if tier != "top":
            continue
        p = _unit_prices(tool, base, r["prompt"])
        if not p:
            continue
        w5 = r["new"] - r.get("w1h", 0)
        alt_cost = r["read"] * p["read"] + max(0, w5) * p["w5"] + r.get("w1h", 0) * p["w1h"] + r["out"] * p["out"]
        actual += r["cost"]
        alt += alt_cost
    return {"model": base, "actual": actual, "alternative": alt, "difference": max(0.0, actual - alt)}


def haiku_long_surcharge(rows):
    extra = 0.0
    n = 0
    for r in rows:
        if "haiku" in (r["model"] or "") and r["prompt"] > 100000:
            p = prices.claude_price(r["model"])
            short = (r["read"] * p["read"] + r["new"] * p["w5"] + r["out"] * p["out"]) / 1e6
            extra += max(0.0, r["cost"] - short)
            n += 1
    return {"calls": n, "extra": extra}


# ------------------------------------------------------------------ plain-language findings
def money(x, unit="$"):
    if unit == "$":
        return "$%.2f" % x if x < 1000 else "$%s" % format(int(round(x)), ",")
    return "%s cr" % format(int(round(x)), ",")


def findings(s):
    """Causes (they add up to the total) then levers (cross-cutting things to change), each with dollars."""
    unit, total = s["unit"], s["cost"] or 1e-9
    cats = s["categories"]
    causes, levers = [], []

    def add(lst, key, dollars, title, detail, fix):
        if dollars > 0:
            lst.append({"key": key, "kind": "cause" if lst is causes else "lever", "dollars": dollars,
                        "share": dollars / total, "title": title, "detail": detail, "fix": fix})

    for key, (label, fix) in CATEGORIES.items():
        v = cats.get(key, 0)
        if v <= 0 or v / total < 0.03:
            continue
        detail = ""
        if key == "startup":
            detail = "About %sK tokens of system prompt, tools, instructions and listings, re-read on each of %d calls." % (
                s["startup_tokens"] // 1000, s["calls"])
        elif key == "output":
            detail = "%s output tokens, thinking included." % format(s["output_tokens"], ",")
        elif key == "rebuild":
            detail = "%d time%s the cache had expired after a break and the whole prompt was written again." % (
                s["rebuilds"], "" if s["rebuilds"] == 1 else "s")
        elif key == "subagents":
            sub = sorted(s["subagents"], key=lambda r: -r["cost"])[:3]
            detail = "%d runs. Biggest: " % len(s["subagents"]) + "; ".join("%s \u201c%s\u201d on %s (%s)" % (
                r["type"], _short(r["description"], 40), ", ".join(m.replace("claude-", "") for m in r["models"]),
                money(r["cost"], unit)) for r in sub) + "."
        else:
            biggest = [i for i in s["top_items"] if i["cat"] == key and (key != "assistant" or "call:" in i["label"])][:3]
            if biggest:
                detail = "Biggest: " + "; ".join("%s (%sK tokens, carried %d calls, %s)" % (
                    i["label"], max(1, i["tokens"] // 1000), i["carried"], money(i["cost"], unit)) for i in biggest) + "."
        add(causes, key, v, "%s: %s (%.0f%%)" % (label, money(v, unit), 100 * v / total), detail, fix)

    if s["big_calls"]:
        add(levers, "big", s["big_cost"], "Big context: %s on %d calls over 150K tokens" % (money(s["big_cost"], unit), s["big_calls"]),
            "The prompt passed 150K tokens at call %d (peak %sK); every later call re-read all of it."
            % (s["first_big"] + 1, s["peak"] // 1000),
            "Start a fresh session per task or phase (/clear), with a short handoff note; compact earlier.")
    pr = s["premium"]
    if pr["difference"] > 0.05 * total and pr["actual"] > 0:
        add(levers, "model", pr["difference"], "Model choice: %s more than %s would cost for the same tokens" % (
            money(pr["difference"], unit), pr["model"].replace("claude-", "")),
            "%s ran on the top tier; the same tokens on %s would be %s. Only a saving if the cheaper model would not "
            "have needed more turns or a redo." % (money(pr["actual"], unit), pr["model"].replace("claude-", ""), money(pr["alternative"], unit)),
            "Start routine work on the everyday model; switch up on purpose for hard problems (tke route).")
    v = cats.get("rebuild", 0)
    if v > 0.02 * total:
        add(levers, "rebuild", v, "Breaks: %s re-writing an expired cache %d time%s" % (money(v, unit), s["rebuilds"], "" if s["rebuilds"] == 1 else "s"),
            "Each break longer than the cache lifetime wrote the whole prompt again.", CATEGORIES["rebuild"][1])
    if s.get("fast_extra", 0) > 0.01 * total:
        add(levers, "fast", s["fast_extra"], "Fast mode: %s more than standard speed on %d calls" % (money(s["fast_extra"], unit), s["fast_calls"]),
            "Fast mode bills Opus at 2x every rate (Codex: 2x credits, 6x for Ultrafast). On a Claude subscription it is "
            "paid from usage credits even when plan usage is left, and it stays on for new sessions until turned off.",
            "Turn it off with /fast unless you are waiting on every reply; set fastModePerSessionOptIn so it doesn't carry over.")
    if s.get("switch_rebuilds"):
        add(levers, "switch", s.get("switch_cost", 0), "Switched model or speed mid-session %d time%s: %s" % (
            s["switch_rebuilds"], "" if s["switch_rebuilds"] == 1 else "s", money(s.get("switch_cost", 0), unit)),
            "Each switch wrote the whole conversation again at the new model's write price (counted under cache rebuilds).",
            "Choose the model and fast mode when the session starts; start a new session to change them.")
    hl = s["haiku_long"]
    if hl["extra"] > 0:
        add(levers, "haiku_long", hl["extra"], "Haiku over 100K tokens: %s extra on %d calls" % (money(hl["extra"], unit), hl["calls"]),
            "Haiku 5.5 charges 5x per token once a request passes 100K tokens.", "Cap Haiku subagents with maxTurns and narrow briefs.")
    for r in s["repeats"][:2]:
        if r["cost"] > 0.02 * total:
            add(levers, "repeat", r["cost"], "Loaded %d times: %s (%s)" % (r["n"], r["label"], money(r["cost"], unit)),
                "The same result entered the context %d times; each copy was carried forward." % r["n"],
                "Read it once and refer back, or read only the part that changed.")
    st = cats.get("startup", 0)
    if s["startup_tokens"] > 30000 and st > 0.1 * total:
        add(levers, "startup", st, "Heavy startup: %sK tokens on every call (%s)" % (s["startup_tokens"] // 1000, money(st, unit)),
            "Instructions, tool definitions and listings loaded before any work.", CATEGORIES["startup"][1])
    causes.sort(key=lambda f: -f["dollars"])
    levers.sort(key=lambda f: -f["dollars"])
    return {"causes": causes, "levers": levers}


# ------------------------------------------------------------------ finding sessions, caching, periods
def _cache_path():
    return config.config_dir() / "cache" / ("why-v%d.json" % CACHE_VERSION)


def _load_cache():
    try:
        return json.loads(_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cache(c):
    try:
        p = _cache_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(c), encoding="utf-8")
        tmp.replace(p)
    except OSError:
        pass


def main_files(since=None, tool=None):
    """(tool, path) for every lead-session file touched since `since`."""
    out = []
    if tool in (None, "claude"):
        root = config.claude_home() / "projects"
        for p in logs._files(root, "*.jsonl", since):
            if "subagents" not in p.parts:
                out.append(("claude", p))
    if tool in (None, "codex"):
        for p in logs.codex_files(since):
            if not logs.codex_meta(p)["subagent"]:
                out.append(("codex", p))
    return out


def summaries(since=None, tool=None, big_context=150000, progress=None):
    """Session summaries (no per-call rows), cached on disk by file size and mtime."""
    cache = _load_cache()
    changed = False
    out = []
    files = main_files(since, tool)
    codex_all = logs.codex_files(since) if tool in (None, "codex") else []
    for n, (t, p) in enumerate(files):
        try:
            st = p.stat()
        except OSError:
            continue
        key = str(p)
        sub_dir = p.with_suffix("") / "subagents"
        sub_sig = 0
        if t == "claude" and sub_dir.is_dir():
            for f in sub_dir.glob("*.jsonl"):
                try:
                    sub_sig += int(f.stat().st_mtime) + f.stat().st_size
                except OSError:
                    pass
        sig = [st.st_size, int(st.st_mtime), sub_sig]
        hit = cache.get(key)
        if hit and hit.get("sig") == sig:
            s = hit["s"]
        else:
            s, _ = analyze_file(p, t, big_context, codex_all)
            cache[key] = {"sig": sig, "s": s}
            changed = True
        if progress:
            progress(n + 1, len(files))
        if s["calls"] or s["sub_cost"]:
            out.append(s)
    if changed:
        _save_cache(cache)
    if since:
        cutoff = since.isoformat()
        out = [s for s in out if (s["end"] or "") >= cutoff[:19] or (s["start"] or "") >= cutoff[:19]]
    return out


def find_session(ref="latest", cwd=None):
    """A session file by id (or prefix), 'latest' anywhere, or 'current' = latest in this folder."""
    ref = (ref or "latest").strip()
    if ref in ("latest", "current", "last"):
        files = main_files(None)
        if ref == "current" and cwd:
            enc = re.sub(r"[^A-Za-z0-9]", "-", str(cwd))
            here = [f for f in files if f[0] == "claude" and f[1].parent.name == enc]
            files = here or files
        files = [f for f in files if f[1].exists()]
        if not files:
            return None
        return max(files, key=lambda f: f[1].stat().st_mtime)
    for t, p in main_files(None):
        sid = p.stem if t == "claude" else logs.codex_meta(p)["id"]
        if sid.startswith(ref) or p.stem.endswith(ref):
            return (t, p)
    return None


def explain(ref="latest", cwd=None, big_context=150000):
    found = find_session(ref, cwd)
    if not found:
        return None, None
    return analyze_file(found[1], found[0], big_context)


def in_period(s, since_day=None, until_day=None):
    """The part of a session that falls between two dates (inclusive): cost, causes, models."""
    days = [d for d in s.get("days", {}) if (not since_day or d >= since_day) and (not until_day or d <= until_day)]
    cats, models = defaultdict(float), defaultdict(float)
    for d in days:
        for k, v in s["days"][d].items():
            cats[k] += v
        for m, v in (s.get("day_models") or {}).get(d, {}).items():
            models[m] += v
    cost = sum(cats.values())
    return {"cost": cost, "categories": dict(cats), "models": dict(models), "days": sorted(days),
            "share": (cost / s["cost"]) if s["cost"] else 0.0}


def period_report(sessions, since_day=None, until_day=None):
    """Aggregate sessions over a date range: by cause, model, project and day; the costliest things carried.

    A session that spans the range boundary contributes only the calls made inside it. Item and lever
    amounts are scaled by the session's in-range share (they aren't kept per day).
    """
    tot = defaultdict(float)
    cats = defaultdict(float)
    models = defaultdict(float)
    projects = defaultdict(lambda: {"cost": 0.0, "sessions": 0})
    days = defaultdict(lambda: defaultdict(float))
    labels = defaultdict(lambda: {"cost": 0.0, "n": 0, "sessions": 0, "cat": None, "tokens": 0})
    lever = defaultdict(float)
    rows = []
    for s in sessions:
        ip = in_period(s, since_day, until_day)
        if ip["cost"] <= 0:
            continue
        u, sh = s["unit"], ip["share"]
        rows.append(dict(s, period_cost=ip["cost"], period_share=sh))
        tot[u] += ip["cost"]
        for k, v in ip["categories"].items():
            cats[(u, k)] += v
        for m, v in ip["models"].items():
            models[(u, m)] += v
        pj = projects[(u, s["project"])]
        pj["cost"] += ip["cost"]
        pj["sessions"] += 1
        for d in ip["days"]:
            for m, v in (s.get("day_models") or {}).get(d, {}).items():
                days[(u, d)][m] += v
        seen = set()
        for i in s["top_items"]:
            if i["cat"] in ("startup", "assistant", "user", "attach", "compact"):
                continue
            g = labels[(u, i["label"])]
            g["cost"] += i["cost"] * sh
            g["n"] += 1
            g["cat"] = i["cat"]
            g["tokens"] = max(g["tokens"], i["tokens"])
            if i["label"] not in seen:
                g["sessions"] += 1
                seen.add(i["label"])
        for f in s["levers"]:
            lever[(u, f["key"])] += f["dollars"] * sh
    rows.sort(key=lambda r: -r["period_cost"])
    return {"totals": dict(tot), "categories": cats, "models": models, "projects": projects, "days": days,
            "labels": labels, "levers": lever, "sessions": rows}
