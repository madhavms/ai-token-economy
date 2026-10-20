"""Check whether Claude Code and Codex are set up for a budget, and say what to fix."""
import json

from . import config, prices, setup


def _settings():
    try:
        return json.loads((config.claude_home() / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def check():
    s = _settings()
    env = s.get("env", {})
    hooks = s.get("hooks", {})
    ours = sum(1 for entries in hooks.values() for e in entries if setup._ours(e))
    items = []

    def add(area, name, ok, now, why, fix="tke setup claude --apply"):
        items.append({"area": area, "name": name, "ok": bool(ok), "now": now, "why": why, "fix": None if ok else fix})

    ttl = s.get("promptCacheTtl") or env.get("CLAUDE_CODE_PROMPT_CACHE_TTL")
    add("Claude Code", "Prompt cache lifetime", ttl == "1h", ttl or "default (5 min when paying by the token)",
        "Breaks longer than the cache lifetime re-write the whole prompt at full price.")
    model = s.get("model") or "default (Opus 5.5)"
    add("Claude Code", "Default model", str(model).lower() not in ("fable", "best") and "fable" not in str(model).lower(),
        model, "A frontier default bills every routine task at the top price.",
        'set "model" to sonnet or opus (tke setup claude --apply --force sets sonnet)')
    eff = s.get("effortLevel") or env.get("CLAUDE_CODE_EFFORT_LEVEL")
    add("Claude Code", "Default effort", eff in ("low", "medium"), eff or "model default",
        "Thinking is billed as output; medium is enough for routine work.")
    add("Claude Code", "Subagent model", bool(env.get("CLAUDE_CODE_SUBAGENT_MODEL")),
        env.get("CLAUDE_CODE_SUBAGENT_MODEL") or "inherits your main model",
        "Without it, subagents run on Opus or Fable whenever you switch up.")
    forced = str(env.get("CLAUDE_CODE_SUBAGENT_MODEL_FORCE", "")).lower() in ("1", "true", "yes")
    add("Claude Code", "Subagent models per role", not forced, "forced for every subagent" if forced else "per agent file",
        "CLAUDE_CODE_SUBAGENT_MODEL_FORCE moves every subagent, the code-writing one included, to one model.",
        "remove CLAUDE_CODE_SUBAGENT_MODEL_FORCE from env; set `model:` in each agent file (tke setup roles)")
    explore = config.claude_home() / "agents" / "Explore.md"
    exp_text = explore.read_text(encoding="utf-8") if explore.exists() else ""
    add("Claude Code", "Explore agent on a small model", "model: haiku" in exp_text,
        "custom (%s)" % ("haiku" if "model: haiku" in exp_text else "other model") if exp_text else "built-in",
        "Codebase searches read many files; on Haiku 5.5 they cost cents.")
    add("Claude Code", "Token-saving hooks", ours >= 4, "%d of 4 installed" % ours,
        "Block giant file reads, trim test output, warn before resuming a cold session.")
    win = env.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW") or s.get("autoCompactWindow")
    add("Claude Code", "Auto-compact backstop", bool(win), win or "near 1M tokens",
        "Without it a forgotten session can grow towards 1M tokens, re-read on every call.")
    fast_on = s.get("fastMode") is True
    try:
        from datetime import datetime, timedelta, timezone
        from . import logs
        recent = logs.claude_calls(datetime.now(timezone.utc) - timedelta(days=30))
        fast_extra = sum(c.fast_extra or 0 for c in recent)
        total = sum(c.cost or 0 for c in recent) or 1e-9
    except Exception:
        fast_extra, total = 0.0, 1e-9
    add("Claude Code", "Fast mode off by default", not fast_on and fast_extra / total < 0.05,
        ("on" if fast_on else "off") + " · fast-mode premium last 30 days $%.2f (%.0f%% of spend)" % (fast_extra, 100 * fast_extra / total),
        "Fast mode bills Opus at 2x every rate, is paid from usage credits even when plan usage is left, and carries "
        "into new sessions once turned on.",
        'run /fast to turn it off; add "fastModePerSessionOptIn": true to settings.json so each session starts standard')
    add("Claude Code", "Agent teams off", not env.get("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"),
        "on" if env.get("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS") else "off",
        "Agent teams use about 7x the tokens of a normal session.", "remove CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS from env")

    path = config.codex_home() / "config.toml"
    if path.exists():
        keys = setup._toml_top_keys(path.read_text(encoding="utf-8"))
        cm = keys.get("model", "").strip('"')
        add("Codex", "Default model", cm and not prices.is_frontier(cm), cm or "Codex default",
            "GPT-6 Astra costs 5x GPT-6.1 Sol per token; use it on purpose via a profile.",
            'set model = "gpt-6.1-sol" (tke setup codex --apply --force)')
        ce = keys.get("model_reasoning_effort", "").strip('"')
        add("Codex", "Default effort", ce in ("low", "medium", "minimal"), ce or "Codex default",
            "Higher effort takes longer and uses more tokens.", "tke setup codex --apply --force")
        add("Codex", "Tool output cap", "tool_output_token_limit" in keys, keys.get("tool_output_token_limit", "not set"),
            "Caps how much of each tool's output is stored and re-sent.", "tke setup codex --apply")
        speed, mult, src = config.codex_speed()
        add("Codex", "Speed tier", speed in ("standard", "flex"), "%s (x%g, %s)" % (speed, mult, src),
            "Codex Fast uses 2x credits (2.5x of included plan usage); Ultrafast 6x (8x).",
            'remove service_tier = "fast" from ~/.codex/config.toml, or run /fast off in Codex')
        profiles = [n for n in ("luna", "astra") if (config.codex_home() / ("%s.config.toml" % n)).exists()]
        add("Codex", "Cheap and frontier profiles", len(profiles) == 2, ", ".join(profiles) or "none",
            "codex --profile luna for chores, --profile astra only on purpose.", "tke setup codex --apply")
    else:
        items.append({"area": "Codex", "name": "Codex config", "ok": True, "now": "Codex not set up on this machine",
                      "why": "", "fix": None})
    return items
