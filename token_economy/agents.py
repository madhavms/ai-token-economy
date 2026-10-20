"""tke agents: is the subagent setup priced right, and how are subagents actually used?

Two halves, both offline:
- setup: reads the agent files for Claude Code, Codex and Copilot CLI and flags the ones that
  will cost more than they need to (no model set, every tool allowed, no turn cap, no report format).
- usage: reads Claude Code transcripts and splits each session into the lead conversation and
  each subagent it started: what it cost, which model ran it, how good the brief was, and how
  long the report was that came back into the lead's context.

The rules come from a 182-run test of seven lead/subagent setups on real bug fixes:
the lead's model moved the bill most; code-writing on Haiku solved fewer tasks; forcing every
subagent onto one model (CLAUDE_CODE_SUBAGENT_MODEL_FORCE) moved the implementer too.
"""
import json
import re
from collections import defaultdict
from pathlib import Path

from . import config, logs, prices

READ_ONLY_TYPES = {"explore", "plan", "scoper", "reviewer", "claude-code-guide", "statusline-setup"}
WRITE_TOOLS = {"write", "edit", "multiedit", "notebookedit"}
HAIKU_LONG = 100000
BIG_REPORT_CHARS = 6000          # about 1,500 tokens landing in the lead's context
REPEAT_SPAWNS = 3

# ------------------------------------------------------------------ file parsing
FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.S)


def frontmatter(text):
    """Small YAML subset: `key: value`, `key: [a, b]`, and `key:` followed by `- item` lines."""
    m = FRONT.match(text or "")
    if not m:
        return {}, text or ""
    meta, key = {}, None
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        item = re.match(r"\s+-\s*(.+)$", line)
        if item and key:
            if not isinstance(meta.get(key), list):
                meta[key] = []
            meta[key].append(item.group(1).strip().strip("'\""))
            continue
        kv = re.match(r"([A-Za-z0-9_-]+)\s*:\s*(.*)$", line)
        if kv:
            key, val = kv.group(1), kv.group(2).strip()
            if val.startswith("[") and val.endswith("]"):
                val = [v.strip().strip("'\"") for v in val[1:-1].split(",") if v.strip()]
            else:
                val = val.strip("'\"")
            meta[key] = val
    return meta, m.group(2)


def tool_list(val):
    if val is None or val == "":
        return None
    if isinstance(val, list):
        return [v for v in val if v]
    return [v.strip() for v in str(val).split(",") if v.strip()]


def _toml_str(text, key):
    m = re.search(r'(?m)^\s*%s\s*=\s*"""(.*?)"""' % re.escape(key), text, re.S)
    if m:
        return m.group(1)
    m = re.search(r'(?m)^\s*%s\s*=\s*"([^"]*)"' % re.escape(key), text)
    return m.group(1) if m else None


def _toml_section(text, name):
    m = re.search(r"(?ms)^\s*\[%s\]\s*$(.*?)(?=^\s*\[|\Z)" % re.escape(name), text)
    return m.group(1) if m else ""


REPORT_RE = re.compile(r"(report|reply|return|respond|answer|summar)[^\n]{0,80}?"
                       r"(\d+\s*(lines|words|bullets|sentences)|at most|no more than|under \d+|short|brief|concise)", re.I)


SHAPE_RE = re.compile(r"(when (you're |you are )?(done|finished)|final (message|answer|reply|text)|in your (report|reply))[^\n]{0,40}[:,]"
                      r"|\b(report|return|reply with|respond with)\b\s*(:|only\b|exactly\b|the following|these|a (list|table)|in this format)"
                      r"|^#{2,3} \S", re.I | re.M)


def has_report_format(text):
    """The brief or agent file says what to send back: a length cap, a list of what to report, or headings."""
    t = text or ""
    return bool(REPORT_RE.search(t) or SHAPE_RE.search(t))


def model_tier(model):
    m = (model or "").lower()
    if not m or m == "inherit":
        return "inherit"
    for t in ("haiku", "sonnet", "opus", "fable", "mythos"):
        if t in m:
            return t
    if "luna" in m or "mini" in m:
        return "small"
    if "astra" in m:
        return "fable"
    if "sol" in m or "gpt" in m:
        return "sonnet"
    return "other"


# ------------------------------------------------------------------ setup check
def _finding(level, where, text):
    return {"level": level, "where": where, "text": text}


def check_claude_agent(path, meta, body, subagent_env):
    where = str(path)
    name = meta.get("name") or path.stem
    out = []
    model = meta.get("model")
    tier = model_tier(model)
    tools = tool_list(meta.get("tools"))
    writes = tools is None or any(t.lower() in WRITE_TOOLS for t in tools)
    readonly = (not writes) or name.lower() in READ_ONLY_TYPES or meta.get("permissionMode") == "plan"
    if tier == "inherit":
        fall = subagent_env or "your main model"
        out.append(_finding("warn", where, "%s: no model set, so it runs on %s. Set `model:` for the role "
                            "(haiku for search and scoping, sonnet for writing code)." % (name, fall)))
    elif tier in ("opus", "fable", "mythos") and readonly:
        out.append(_finding("warn", where, "%s is read-only but runs on %s: its searches are billed at the top rate. "
                            "Try `model: haiku` (search) or `model: sonnet` (review)." % (name, model)))
    if tier == "haiku" and writes and not readonly:
        out.append(_finding("warn", where, "%s writes code on Haiku. In testing, Haiku implementers were cheaper but "
                            "solved fewer tasks; keep code-writing on sonnet." % name))
    if tier == "haiku" and not meta.get("maxTurns"):
        out.append(_finding("warn", where, "%s runs on Haiku with no `maxTurns`. Haiku costs 5x per token once a request "
                            "passes 100K tokens; cap its turns (8 to 15)." % name))
    if tools is None:
        out.append(_finding("info", where, "%s: every tool allowed. A `tools:` list keeps read-only roles from editing." % name))
    if not has_report_format(body):
        out.append(_finding("warn", where, "%s: no report format. Ask for a short report (e.g. 'at most 15 lines: files, "
                            "results, open questions'); a long one is re-read by the lead on every later turn." % name))
    if not re.search(r"\b(use|when|before|after|whenever)\b", meta.get("description", ""), re.I):
        out.append(_finding("info", where, "%s: the description doesn't say when to use it; the lead picks subagents "
                            "from that line." % name))
    return {"tool": "claude", "name": name, "path": where, "model": model or "inherit", "tools": tools,
            "readonly": readonly, "findings": out}


def check_codex_agent(path, text, default_sub):
    where = str(path)
    name = _toml_str(text, "name") or path.stem
    model = _toml_str(text, "model")
    effort = _toml_str(text, "model_reasoning_effort")
    instr = _toml_str(text, "developer_instructions") or ""
    out = []
    if not model:
        out.append(_finding("warn", where, "%s: no `model`, so it runs on %s." % (name, default_sub or "the parent session's model")))
    elif prices.is_frontier(model):
        out.append(_finding("warn", where, "%s runs on %s, the frontier tier; use it on purpose only." % (name, model)))
    if not effort:
        out.append(_finding("info", where, "%s: no `model_reasoning_effort`; it inherits the parent's." % name))
    if not has_report_format(instr):
        out.append(_finding("warn", where, "%s: no report format in developer_instructions; ask for a short report." % name))
    return {"tool": "codex", "name": name, "path": where, "model": model or "inherit", "findings": out}


def check_copilot_agent(path, meta, body):
    where = str(path)
    name = meta.get("name") or path.name.split(".")[0]
    out = []
    model = meta.get("model")
    if not model:
        out.append(_finding("warn", where, "%s: no `model`, so it runs on your default Copilot model." % name))
    elif re.search(r"opus|fable|astra", str(model), re.I) and re.search(r"search|explor|scop|find", meta.get("description", ""), re.I):
        out.append(_finding("warn", where, "%s searches on %s; a small model is enough." % (name, model)))
    if tool_list(meta.get("tools")) in (None, ["*"]):
        out.append(_finding("info", where, "%s: every tool allowed." % name))
    if not has_report_format(body):
        out.append(_finding("warn", where, "%s: no report format; ask for a short report." % name))
    return {"tool": "copilot", "name": name, "path": where, "model": model or "inherit", "findings": out}


def _read(p):
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _json(p):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def setup_check(project=None, home=None, codex_home=None, copilot_home=None):
    """Agent files and settings for the three tools, user scope and (optionally) one project."""
    home = home or config.claude_home()
    codex_home = codex_home or config.codex_home()
    copilot_home = copilot_home or (Path.home() / ".copilot")
    project = Path(project) if project else None
    agents, general = [], []

    # Claude Code settings: user, project, project-local (later ones win)
    env = {}
    sources = [home / "settings.json"]
    if project:
        sources += [project / ".claude" / "settings.json", project / ".claude" / "settings.local.json"]
    for s in sources:
        env.update(_json(s).get("env") or {})
    sub = env.get("CLAUDE_CODE_SUBAGENT_MODEL")
    if str(env.get("CLAUDE_CODE_SUBAGENT_MODEL_FORCE", "")).lower() in ("1", "true", "yes"):
        general.append(_finding("bad", "settings env", "CLAUDE_CODE_SUBAGENT_MODEL_FORCE is on: every subagent, the "
                                "implementer included, runs on %s whatever its file says. Remove it and set models per "
                                "agent file." % (sub or "the subagent model")))
    if not sub:
        general.append(_finding("info", "settings env", "CLAUDE_CODE_SUBAGENT_MODEL not set: agents without a `model:` "
                                "line (and general-purpose) run on your main model."))

    dirs = [("user", home / "agents")]
    if project:
        dirs.append(("project", project / ".claude" / "agents"))
    for scope, d in dirs:
        for p in sorted(d.glob("*.md")) if d.is_dir() else []:
            meta, body = frontmatter(_read(p))
            if meta:
                r = check_claude_agent(p, meta, body, sub)
                r["scope"] = scope
                agents.append(r)

    # Codex
    cfg_text = _read(codex_home / "config.toml")
    default_sub = _toml_str(_toml_section(cfg_text, "agents"), "default_subagent_model")
    cdirs = [("user", codex_home / "agents")] + ([("project", project / ".codex" / "agents")] if project else [])
    for scope, d in cdirs:
        for p in sorted(d.glob("*.toml")) if d.is_dir() else []:
            r = check_codex_agent(p, _read(p), default_sub)
            r["scope"] = scope
            agents.append(r)
    has_codex_agents = any(a["tool"] == "codex" for a in agents)
    v2 = re.search(r"(?m)^\s*multi_agent_v2\s*=\s*true", _toml_section(cfg_text, "features"))
    if has_codex_agents and not v2:
        general.append(_finding("warn", str(codex_home / "config.toml"), "Codex agent files found, but multi_agent_v2 is "
                                "off. In Codex 0.160 the lead could only pick a custom agent's model with it on; add "
                                "`multi_agent_v2 = true` under [features]."))
    if cfg_text and not default_sub:
        general.append(_finding("info", str(codex_home / "config.toml"), "Codex has no [agents] default_subagent_model: "
                                "spawned agents without a model inherit the parent session's."))

    # Copilot CLI
    gdirs = [("user", copilot_home / "agents")] + ([("project", project / ".github" / "agents")] if project else [])
    for scope, d in gdirs:
        for p in sorted(d.glob("*.md")) if d.is_dir() else []:
            meta, body = frontmatter(_read(p))
            r = check_copilot_agent(p, meta, body)
            r["scope"] = scope
            agents.append(r)
    return {"agents": agents, "general": general, "subagent_model": sub}


# ------------------------------------------------------------------ brief scoring
PATH_RE = re.compile(r"([\w.-]+/)+[\w.-]+|\b[\w-]+\.(py|ts|tsx|js|jsx|java|kt|go|rs|rb|cs|sql|scala|sh|ya?ml|toml|json|md)\b|:\d+\b")
DONE_RE = re.compile(r"pytest|npm (run )?test|yarn test|pnpm test|go test|cargo test|mvn |gradle|make test|tox\b|jest|vitest|"
                     r"swift test|xcodebuild test|ctest|phpunit|rspec|unittest|playwright|tests? (must )?pass|"
                     r"dotnet test|done when|must pass|should pass|passes|acceptance|success criteria|verify", re.I)
LIMIT_RE = re.compile(r"do not|don't|never|only (edit|change|touch|modify)|read-only|no new dependenc|out of scope|"
                      r"stop (and|if)|without (changing|editing)|leave .* alone", re.I)
CHANGE_RE = re.compile(r"\b(implement|fix|change|edit|write|add|refactor|update|rename|remove|delete|migrate)\b", re.I)
NO_EDIT_RE = re.compile(r"do not (edit|change|modify|write)|don't (edit|change|modify|write)|read-only|report findings only|"
                        r"do not propose changes|no edits", re.I)
READ_TASK_RE = re.compile(r"^\s*(review|audit|research|find|explore|investigate|analy[sz]e|check|verify|survey|scope|read|look|search|panel)", re.I)


def writes_code(prompt, agent_type="", description=""):
    if agent_type.lower() in READ_ONLY_TYPES or NO_EDIT_RE.search(prompt or ""):
        return False
    if READ_TASK_RE.search(description or ""):
        return False
    return bool(CHANGE_RE.search((prompt or "")[:600]))


UNSEEN = "can't check: it points to instruction files no longer on this machine"
MD_PATH_RE = re.compile(r"((?:~|[A-Za-z]:)?[/\\][^\s`'\"()]+\.md)\b")


def referenced_text(prompt, limit=60000):
    """Text of local .md files the brief tells the subagent to read (instructions, task briefs, specs)."""
    out, refs = [], 0
    for line in (prompt or "").splitlines():
        if re.search(r"\b(write|save|report to|output to)\b", line, re.I):
            continue
        for m in MD_PATH_RE.finditer(line):
            refs += 1
            path = Path(m.group(1)).expanduser()
            try:
                if path.is_file() and path.stat().st_size <= limit:
                    out.append(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
    return "\n".join(out), refs - len(out)


def score_brief(prompt, agent_type="", description="", follow=True):
    """The four things a subagent can't guess. Returns (missing list, applicable count).

    A brief may point at an instructions file ("read your brief first: /path/task-7.md"); when that
    file is on this machine its text counts too.
    """
    short = len(prompt or "") < 160
    extra, gone = referenced_text(prompt) if follow else ("", 0)
    p = (prompt or "") + "\n" + extra
    checks = [("where to look", bool(PATH_RE.search(p))),
              ("limits", bool(LIMIT_RE.search(p))),
              ("report format", has_report_format(p))]
    if writes_code(p, agent_type, description):
        checks.insert(1, ("done-when check", bool(DONE_RE.search(p))))
    missing = [name for name, ok in checks if not ok]
    if missing and gone:
        return [UNSEEN], len(checks)
    if short and missing:
        missing.append("very short (%d chars)" % len(prompt or ""))
    return missing, len(checks)


# ------------------------------------------------------------------ usage audit
def _last_text(path):
    """The subagent's final text reply: the report that went back to the lead."""
    last = ""
    for d in logs._lines(path):
        if d.get("type") != "assistant":
            continue
        msg = d.get("message") or {}
        parts = msg.get("content")
        if isinstance(parts, list):
            text = "".join(c.get("text", "") for c in parts if isinstance(c, dict) and c.get("type") == "text")
            if text.strip():
                last = text
    return last


def _spawns_and_lead_tools(path):
    spawns, tools = {}, defaultdict(int)
    for d in logs._lines(path):
        if d.get("type") != "assistant" or d.get("isSidechain"):
            continue
        for c in (d.get("message") or {}).get("content") or []:
            if not isinstance(c, dict) or c.get("type") != "tool_use":
                continue
            name = c.get("name") or ""
            if name in ("Agent", "Task"):
                i = c.get("input") or {}
                spawns[c.get("id")] = {"type": i.get("subagent_type") or "general-purpose", "prompt": i.get("prompt") or "",
                                       "description": i.get("description") or "", "model_arg": i.get("model"),
                                       "background": bool(i.get("run_in_background"))}
            else:
                tools[name] += 1
                if name == "Bash" and re.search(r"pytest|npm (run )?test|go test|cargo test|jest|vitest|mvn|gradle|make test",
                                                str((c.get("input") or {}).get("command", ""))):
                    tools["_tests"] += 1
    return spawns, tools


def usage(since=None, home=None, project_filter=None):
    root = (home or config.claude_home()) / "projects"
    sessions, runs = [], []
    for path in logs._files(root, "*.jsonl", since):
        if "subagents" in path.parts:
            continue
        rel = path.relative_to(root).parts
        if project_filter and project_filter not in rel[0]:
            continue
        lead_calls = logs.claude_file_calls(path, since, rel[0], False)
        sub_dir = path.with_suffix("") / "subagents"
        sub_files = sorted(sub_dir.glob("agent-*.jsonl")) if sub_dir.is_dir() else []
        if not lead_calls and not sub_files:
            continue
        spawns, tools = _spawns_and_lead_tools(path)
        sess_runs = []
        for sf in sub_files:
            meta = _json(sf.with_name(sf.name[:-len(".jsonl")] + ".meta.json"))
            calls = logs.claude_file_calls(sf, since, rel[0], True)
            if not calls:
                continue
            sp = spawns.get(meta.get("toolUseId")) or {}
            atype = meta.get("agentType") or sp.get("type") or "unknown"
            models = sorted({c.model for c in calls if c.model})
            report = _last_text(sf)
            haiku_long = [c for c in calls if "haiku" in (c.model or "") and c.prompt > HAIKU_LONG]
            missing, n_checks = score_brief(sp.get("prompt", ""), atype, sp.get("description", "")) if sp else ([], 0)
            r = {"session": path.stem, "project": rel[0], "type": atype,
                 "description": meta.get("description") or sp.get("description", ""),
                 "models": models, "cost": sum(c.cost or 0 for c in calls), "calls": len(calls),
                 "peak": max(c.prompt for c in calls), "report_chars": len(report),
                 "haiku_long": len(haiku_long), "brief_chars": len(sp.get("prompt", "")),
                 "brief_missing": missing, "brief_checks": n_checks, "has_brief": bool(sp),
                 "start": min(c.ts for c in calls)}
            sess_runs.append(r)
        runs += sess_runs
        lead_models = defaultdict(float)
        for c in lead_calls:
            lead_models[c.model] += c.cost or 0
        sessions.append({
            "session": path.stem, "project": rel[0],
            "start": min([c.ts for c in lead_calls] + [r["start"] for r in sess_runs]) if (lead_calls or sess_runs) else None,
            "lead_cost": sum(c.cost or 0 for c in lead_calls), "lead_calls": len(lead_calls),
            "lead_model": max(lead_models, key=lead_models.get) if lead_models else "",
            "sub_cost": sum(r["cost"] for r in sess_runs), "runs": len(sess_runs),
            "lead_edits": sum(tools[t] for t in ("Edit", "Write", "MultiEdit", "NotebookEdit")),
            "lead_tests": tools["_tests"], "types": sorted({r["type"] for r in sess_runs})})
    return sessions, runs


def codex_usage(since=None, home=None):
    """Codex: each subagent writes its own rollout whose session_meta names the parent thread."""
    lead, subs = 0.0, defaultdict(lambda: {"runs": 0, "cost": 0.0, "models": set()})
    for path in logs.codex_files(since, home):
        meta = logs.codex_meta(path)
        calls = logs.codex_file_calls(path, since)
        cost = sum(c.cost or 0 for c in calls)
        if not meta["subagent"]:
            lead += cost
            continue
        name = (meta["role"] or (meta["agent"] or "subagent").rsplit("/", 1)[-1])
        s = subs[name]
        s["runs"] += 1
        s["cost"] += cost
        s["models"].update(c.model for c in calls if c.model)
    types = [{"type": k, "runs": v["runs"], "cost": v["cost"], "models": sorted(v["models"])}
             for k, v in sorted(subs.items(), key=lambda kv: -kv[1]["cost"])]
    return {"lead": lead, "sub": sum(t["cost"] for t in types), "types": types}


def summarize(sessions, runs):
    lead = sum(s["lead_cost"] for s in sessions)
    sub = sum(s["sub_cost"] for s in sessions)
    by_type = defaultdict(lambda: {"runs": 0, "cost": 0.0, "models": set(), "report": 0, "briefs_ok": 0, "briefs": 0,
                                   "haiku_long": 0, "peak": 0})
    for r in runs:
        t = by_type[r["type"]]
        t["runs"] += 1
        t["cost"] += r["cost"]
        t["models"].update(r["models"])
        t["report"] += r["report_chars"]
        t["haiku_long"] += r["haiku_long"]
        t["peak"] = max(t["peak"], r["peak"])
        if r["has_brief"]:
            t["briefs"] += 1
            t["briefs_ok"] += 1 if not r["brief_missing"] or UNSEEN in r["brief_missing"] else 0
    types = []
    for name, t in sorted(by_type.items(), key=lambda kv: -kv[1]["cost"]):
        types.append({"type": name, "runs": t["runs"], "cost": t["cost"], "avg": t["cost"] / t["runs"],
                      "models": sorted(_short(m) for m in t["models"]), "avg_report": t["report"] // t["runs"],
                      "briefs": t["briefs"], "briefs_ok": t["briefs_ok"], "haiku_long": t["haiku_long"], "peak": t["peak"]})
    return {"lead": lead, "sub": sub, "sessions": len(sessions),
            "with_subagents": sum(1 for s in sessions if s["runs"]), "runs": len(runs), "types": types}


def _short(model):
    m = (model or "").replace("claude-", "")
    return re.sub(r"-\d{8}$", "", m)


def findings(sessions, runs, summary):
    """Plain-language leaks, biggest first. Each: (dollars at stake or None, text)."""
    out = []
    total = summary["lead"] + summary["sub"]
    # 1. heavy lead sessions on a frontier model that did all the editing themselves
    loops = [s for s in sessions if model_tier(s["lead_model"]) in ("opus", "fable", "mythos")
             and s["lead_edits"] + s["lead_tests"] >= 25
             and not any(t.lower() not in READ_ONLY_TYPES for t in s["types"])]
    if loops:
        cost = sum(s["lead_cost"] for s in loops)
        out.append((cost, "%d %s sessions ran their own long edit-and-test loop (25+ edits/test runs, no implementer): "
                    "$%.2f. Either start routine changes on a Sonnet lead, or hand the loop to an implementer subagent "
                    "on sonnet and review the diff." % (len(loops), _short(loops[0]["lead_model"]).split("-")[0].capitalize(), cost)))
    # 2. read-only subagents on the top tier
    pricey = [r for r in runs if r["type"].lower() in READ_ONLY_TYPES and any(model_tier(m) in ("opus", "fable", "mythos") for m in r["models"])]
    if pricey:
        cost = sum(r["cost"] for r in pricey)
        out.append((cost, "%d read-only subagent runs (%s) ran on Opus/Fable: $%.2f. On Haiku the same tokens cost "
                    "about a fortieth; set `model: haiku` in those agent files." % (
                        len(pricey), ", ".join(sorted({r["type"] for r in pricey})), cost)))
    # 3. general-purpose runs inherit the lead model
    gp = [r for r in runs if r["type"] == "general-purpose" and any(model_tier(m) in ("opus", "fable", "mythos") for m in r["models"])]
    if gp:
        cost = sum(r["cost"] for r in gp)
        out.append((cost, "%d general-purpose subagent runs on Opus/Fable: $%.2f. They inherit the lead's model; give the "
                    "job a named agent with its own `model:` (implementer on sonnet, scoper on haiku)." % (len(gp), cost)))
    # 4. Haiku over the 100K line
    hl = [r for r in runs if r["haiku_long"]]
    if hl:
        out.append((None, "%d Haiku subagent runs had requests over 100K tokens, where Haiku costs 5x. Narrow the brief "
                    "and add `maxTurns`." % len(hl)))
    # 5. long reports
    big = [r for r in runs if r["report_chars"] > BIG_REPORT_CHARS]
    if big:
        out.append((None, "%d subagent reports were over %s characters (%s). The lead re-reads them on every later "
                    "turn; ask for at most 15 lines." % (len(big), format(BIG_REPORT_CHARS, ","),
                                                         ", ".join(sorted({r["type"] for r in big}))[:80])))
    # 6. weak briefs
    briefed = [r for r in runs if r["has_brief"] and UNSEEN not in r["brief_missing"]]
    weak = [r for r in briefed if len(r["brief_missing"]) >= 2]
    if briefed and weak:
        common = defaultdict(int)
        for r in weak:
            for m in r["brief_missing"]:
                common[m.split(" (")[0]] += 1
        top = ", ".join("%s (%d)" % kv for kv in sorted(common.items(), key=lambda kv: -kv[1])[:3])
        out.append((sum(r["cost"] for r in weak), "%d of %d briefs left out two or more of: where to look, done-when, "
                    "limits, report format. Most often missing: %s. A subagent re-discovers what the brief leaves out, "
                    "on your bill; see `tke agents --brief`." % (len(weak), len(briefed), top)))
    # 7. the same type spawned again and again in one session
    rep = defaultdict(int)
    for r in runs:
        rep[(r["session"], r["type"])] += 1
    many = [(k, n) for k, n in rep.items() if n >= REPEAT_SPAWNS and k[1].lower() not in ("explore",)]
    if many:
        out.append((None, "%d sessions started the same subagent type %d+ times. A follow-up fix can go to the same "
                    "subagent (SendMessage), which still has its context." % (len(many), REPEAT_SPAWNS)))
    if total and summary["sub"] / total < 0.05 and summary["runs"] < 5:
        out.append((None, "Subagents are under 5%% of spend. Savings here come from the lead: start routine sessions on "
                    "Sonnet (tke route) and keep sessions short."))
    out.sort(key=lambda x: -(x[0] or 0))
    return out


BRIEF_TEMPLATE = """Goal        one sentence: what must be true when you're done
Where       the files and functions to start from (paths, line numbers)
Done when   the test command that must pass, and what must not change
Limits      don't edit outside <dir>; no new dependencies; stop and report if the plan looks wrong
Report      at most 15 lines: files changed, tests run with results, open questions"""

REVIEW_PROMPT = """Review how this repository uses subagents and make the setup cheaper without losing quality.

1. Run `{cmd} agents --dir . --json` and read the output: agent files with their findings, spend split
   between the lead conversation and each subagent type, and the weakest briefs.
2. For each agent file: set a model that fits the role (haiku: search, scoping, git, log triage, with a
   maxTurns cap; sonnet: writing code and review; opus only on purpose), a `tools:` list (read-only roles
   get no Edit/Write), and a report format of at most 15 lines. Don't use CLAUDE_CODE_SUBAGENT_MODEL_FORCE.
3. For the weakest briefs: rewrite one or two as examples using this template:
{template}
4. Propose the changes as a diff. Don't apply them until I agree. Note anything you couldn't check.
"""
