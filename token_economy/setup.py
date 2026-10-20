"""tke setup: apply the token-economy settings to Claude Code, Codex and a project.

Safe by default: prints the plan and changes nothing unless --apply is given. Never overwrites a
value you already set (pass --force to replace), and backs up every file it edits.
"""
import json
import re
import shutil
import sys
import time
from pathlib import Path

from . import config

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = Path(__file__).resolve().parent / "templates"
MARKS = ("tke.py", "-m token_economy")   # how tke finds its own entries again


def _cmd(*args):
    """Run from the clone if tke.py is there, else as an installed package."""
    py = Path(sys.executable).as_posix()
    script = ROOT / "tke.py"
    if script.exists():
        return '"%s" "%s" %s' % (py, script.as_posix(), " ".join(args))
    return '"%s" -m token_economy %s' % (py, " ".join(args))


def _is_ours(command):
    return any(m in (command or "") for m in MARKS)


CLAUDE_SETTINGS = {
    "model": "sonnet",
    "effortLevel": "medium",
    "promptCacheTtl": "1h",
}
CLAUDE_ENV = {
    "CLAUDE_CODE_SUBAGENT_MODEL": "sonnet",
    "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "200000",
    "BASH_MAX_OUTPUT_LENGTH": "15000",
    "CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS": "15000",
}
WHY = {
    "model": "start sessions on Sonnet 5.5; switch with /model at the start of a session for hard work",
    "effortLevel": "medium effort for routine work",
    "promptCacheTtl": "1-hour prompt cache; on credits/API keys the default is 5 minutes",
    "CLAUDE_CODE_SUBAGENT_MODEL": "subagents don't inherit Opus/Fable when you switch up",
    "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "compact at 200k instead of near 1M (a backstop)",
    "BASH_MAX_OUTPUT_LENGTH": "cap one command's output at 15,000 characters (default 30,000)",
    "CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS": "cap one file read at 15,000 tokens (default 25,000)",
}


def hook_block():
    return {
        "PreToolUse": [{"matcher": "Read|Bash", "hooks": [{"type": "command", "command": _cmd("hook", "pre_tool"), "timeout": 10}]}],
        "UserPromptSubmit": [{"hooks": [{"type": "command", "command": _cmd("hook", "prompt"), "timeout": 10}]}],
        "Stop": [{"hooks": [{"type": "command", "command": _cmd("hook", "stop"), "timeout": 10}]}],
        "SessionStart": [{"matcher": "startup|clear", "hooks": [{"type": "command", "command": _cmd("hook", "session_start"), "timeout": 10}]}],
    }


def _ours(entry):
    return any(_is_ours(h.get("command")) for h in entry.get("hooks", []))


def _load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError:
        sys.exit("tke: %s is not valid JSON; fix it first, nothing was changed." % path)


def _backup(path):
    if path.exists():
        b = path.with_name(path.name + ".tke-backup-" + time.strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(path, b)
        return b
    return None


# ------------------------------------------------------------------ Claude Code
def plan_claude(force=False, statusline=True):
    home = config.claude_home()
    path = home / "settings.json"
    cur = _load_json(path)
    new = json.loads(json.dumps(cur))
    notes = []
    for k, v in CLAUDE_SETTINGS.items():
        if k not in cur or (force and cur[k] != v):
            new[k] = v
            notes.append("set %s = %r  (%s)" % (k, v, WHY[k]))
        elif cur[k] != v:
            notes.append("kept your %s = %r (recommended %r; --force to change)" % (k, cur[k], v))
    env = new.setdefault("env", {})
    for k, v in CLAUDE_ENV.items():
        if k not in env or (force and env[k] != v):
            env[k] = v
            notes.append("set env %s = %s  (%s)" % (k, v, WHY[k]))
        elif env[k] != v:
            notes.append("kept your env %s = %s" % (k, env[k]))
    hooks = new.setdefault("hooks", {})
    for event, entries in hook_block().items():
        existing = [e for e in hooks.get(event, []) if not _ours(e)]
        hooks[event] = existing + entries
    notes.append("register 4 hooks: big-file and sleep guards, test-output trimming, cold-cache warning, handoff notes")
    if statusline:
        sl = cur.get("statusLine")
        if not sl or _is_ours(sl.get("command")):
            new["statusLine"] = {"type": "command", "command": _cmd("statusline")}
            notes.append("status line: today's spend against your daily budget")
        else:
            notes.append("kept your existing statusLine (run `tke statusline` from your own script to add the budget)")
    files = [(path, json.dumps(new, indent=2) + "\n")]
    explore = home / "agents" / "Explore.md"
    if not explore.exists() or force:
        files.append((explore, (TEMPLATES / "Explore.md").read_text(encoding="utf-8")))
        notes.append("Explore subagent on Haiku 5.5 at low effort (overrides the built-in): %s" % explore)
    else:
        notes.append("kept your existing %s" % explore)
    skill = home / "skills" / "subagent-review" / "SKILL.md"
    if not skill.exists() or skill.read_text(encoding="utf-8") != review_skill():
        files.append((skill, review_skill()))
    notes.append("skill /subagent-review: audits a repo's subagent files and briefs, proposes a diff: %s" % skill)
    return files, notes


def review_skill():
    from . import agents
    body = agents.REVIEW_PROMPT.format(cmd=_cmd().rstrip(), template="\n".join("   " + l for l in agents.BRIEF_TEMPLATE.splitlines()))
    return ("---\nname: subagent-review\ndescription: Use when asked to review, audit or cut the cost of how this "
            "repository uses subagents (agent files, models per role, briefs).\n---\n" + body)


def unplan_claude():
    path = config.claude_home() / "settings.json"
    cur = _load_json(path)
    for event in list(cur.get("hooks", {})):
        cur["hooks"][event] = [e for e in cur["hooks"][event] if not _ours(e)]
        if not cur["hooks"][event]:
            del cur["hooks"][event]
    if _is_ours((cur.get("statusLine") or {}).get("command")):
        del cur["statusLine"]
    return [(path, json.dumps(cur, indent=2) + "\n")], ["remove tke hooks and status line (settings values are left as they are)"]


# ------------------------------------------------------------------------ Codex
CODEX_KEYS = [("model", '"gpt-6.1-sol"', "everyday default: GPT-6.1 Sol"),
              ("model_reasoning_effort", '"medium"', "medium effort"),
              ("model_verbosity", '"low"', "shorter replies"),
              ("tool_output_token_limit", "12000", "cap each stored tool output")]


def _toml_top_keys(text):
    """Top-level key = value lines (before the first [table])."""
    keys = {}
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("["):
            break
        m = re.match(r"([A-Za-z0-9_.-]+)\s*=\s*(.+)$", s)
        if m and not s.startswith("#"):
            keys[m.group(1)] = m.group(2).strip()
    return keys


def plan_codex(force=False, model=None):
    home = config.codex_home()
    path = home / "config.toml"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    have = _toml_top_keys(text)
    add, notes = [], []
    lines = text.splitlines()
    for k, v, why in CODEX_KEYS:
        if k == "model" and model:
            v = '"%s"' % model
        if k not in have:
            add.append("%s = %s" % (k, v))
            notes.append("set %s = %s  (%s)" % (k, v, why))
        elif have[k] != v and force:
            lines = [("%s = %s" % (k, v)) if re.match(r"\s*%s\s*=" % re.escape(k), l) else l for l in lines]
            notes.append("replace %s = %s with %s" % (k, have[k], v))
        elif have[k] != v:
            notes.append("kept your %s = %s (recommended %s; --force to change)" % (k, have[k], v))
    files = []
    if add or force:
        header = ["# --- added by tke (token economy) ---"] + add + ["# --- end tke ---"] if add else []
        files.append((path, "\n".join(header + lines) + "\n"))
    for name in ("luna", "astra"):
        p = home / ("%s.config.toml" % name)
        if not p.exists() or force:
            files.append((p, (TEMPLATES / ("codex-%s.config.toml" % name)).read_text(encoding="utf-8")))
            notes.append("profile %s: codex --profile %s" % (p, name))
    notes.append("check Fast mode is off (/fast) and that code review isn't automatic on every pull request")
    return files, notes


# ---------------------------------------------------------------------- project
def plan_project(cwd):
    cwd = Path(cwd)
    rules = (TEMPLATES / "token-rules.md").read_text(encoding="utf-8")
    files, notes = [], []
    for name in ("CLAUDE.md", "AGENTS.md"):
        p = cwd / name
        text = p.read_text(encoding="utf-8") if p.exists() else ""
        if "tke:token-rules" in text:
            notes.append("%s already has the token rules" % name)
            continue
        files.append((p, (text.rstrip() + "\n\n" if text.strip() else "") + rules))
        notes.append("%s token rules to %s" % ("append" if text else "create", p))
    spec = cwd / "SPEC.md"
    if not spec.exists():
        files.append((spec, (TEMPLATES / "SPEC.md").read_text(encoding="utf-8")))
        notes.append("create a spec template: %s" % spec)
    return files, notes


def _cmd_parts(*args):
    """(command, [args]) for launching tke from another program."""
    py = Path(sys.executable).as_posix()
    script = ROOT / "tke.py"
    if script.exists():
        return py, [script.as_posix()] + list(args)
    return py, ["-m", "token_economy"] + list(args)


def plan_mcp(tools=("claude", "codex", "copilot")):
    """Register `tke mcp` with each agent. Returns (files, notes, commands to run)."""
    cmd, args = _cmd_parts("mcp")
    files, notes, commands = [], [], []
    if "claude" in tools:
        found = shutil.which("claude")
        line = ["claude", "mcp", "add", "--scope", "user", "tke", "--", cmd] + args
        if found:
            commands.append([found] + line[1:])            # full path: on Windows it is claude.cmd
            notes.append("Claude Code: run `%s`" % " ".join(_q(x) for x in line))
        else:
            notes.append("Claude Code not on PATH; run this yourself: %s" % " ".join(_q(x) for x in line))
    if "codex" in tools:
        path = config.codex_home() / "config.toml"
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        if "[mcp_servers.tke]" in text:
            notes.append("Codex already has [mcp_servers.tke] in %s" % path)
        else:
            block = "\n[mcp_servers.tke]\ncommand = '%s'\nargs = [%s]\n" % (cmd, ", ".join("'%s'" % a for a in args))
            files.append((path, text.rstrip("\n") + "\n" + block))
            notes.append("Codex: add [mcp_servers.tke] to %s" % path)
    if "copilot" in tools:
        path = Path.home() / ".copilot" / "mcp-config.json"
        cur = _load_json(path)
        servers = cur.setdefault("mcpServers", {})
        entry = {"type": "local", "command": cmd, "args": args, "env": {}, "tools": ["*"]}
        if servers.get("tke") == entry:
            notes.append("Copilot CLI already has tke in %s" % path)
        else:
            servers["tke"] = entry
            files.append((path, json.dumps(cur, indent=2) + "\n"))
            notes.append("Copilot CLI: add tke to %s" % path)
    notes.append("VS Code: Command Palette > MCP: Add Server > Command (stdio) > %s" % " ".join(_q(x) for x in [cmd] + args))
    return files, notes, commands


def _q(x):
    return '"%s"' % x if (" " in x or "\\" in x) else x


ROLE_FILES = {
    "claude": (".claude/agents", "claude", ".md"),
    "codex": (".codex/agents", "codex", ".toml"),
    "copilot": (".github/agents", "copilot", ".agent.md"),
}


def plan_roles(cwd, tools=("claude", "codex", "copilot"), force=False):
    """Role-based subagent files for a repo, committed and reviewed like code."""
    cwd = Path(cwd)
    files, notes = [], []
    for tool in tools:
        dest, src, ext = ROLE_FILES[tool]
        for t in sorted((TEMPLATES / "roles" / src).glob("*" + ext)):
            p = cwd / dest / t.name
            if p.exists() and not force:
                notes.append("kept your existing %s (--force to replace)" % p)
                continue
            files.append((p, t.read_text(encoding="utf-8")))
            notes.append("%s %s" % ("replace" if p.exists() else "create", p))
    block = (TEMPLATES / "delegation.md").read_text(encoding="utf-8")
    for name in ("CLAUDE.md", "AGENTS.md"):
        p = cwd / name
        text = p.read_text(encoding="utf-8") if p.exists() else ""
        if "tke:delegation" in text:
            notes.append("%s already has the subagent rules" % name)
            continue
        files.append((p, (text.rstrip() + "\n\n" if text.strip() else "") + block))
        notes.append("%s %s with the subagent rules and the brief template" % ("append to" if text else "create", p))
    if "codex" in tools:
        notes.append("Codex: custom agents need `multi_agent_v2 = true` under [features] in ~/.codex/config.toml "
                     "(tested on Codex 0.160)")
    if "copilot" in tools:
        notes.append("Copilot model names follow its model picker; if your org hides a model, edit the `model:` line")
    return files, notes


def run(files, notes, apply):
    print("\n".join("  - " + n for n in notes))
    if not apply:
        print("\nDry run: nothing was changed. Re-run with --apply to write %d file(s)." % len(files))
        return
    for path, text in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        b = _backup(path)
        path.write_text(text, encoding="utf-8")
        print("wrote %s%s" % (path, ("  (backup: %s)" % b.name) if b else ""))
