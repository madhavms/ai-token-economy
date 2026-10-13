"""Claude Code hooks: deterministic token controls that cost no tokens.

    python -m token_economy.hooks <event>      (tke setup claude registers these)

pre_tool       deny whole-file reads/cats of big files and long sleep-polling, each with a reason
               Claude can act on; pipe test-runner output through trim.py.
prompt         if the session is big and its prompt cache has expired, block once and explain the
               rebuild cost (send again to continue). Nudge when the context is very large.
stop           write a handoff note for this folder (last reply + git status).
session_start  on startup or /clear, load a recent handoff note so /clear doesn't lose the thread.

Every handler fails open: on any error the tool call or prompt is allowed.
Thresholds come from environment variables (TKE_*) listed in DEFAULTS.
"""
import calendar
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

DEFAULTS = {
    "TKE_READ_MAX_BYTES": 60000,     # ~15k tokens; bigger files must be read by range
    "TKE_SLEEP_MAX_SEC": 20,
    "TKE_IDLE_CTX": 100000,          # the cold-cache guard applies above this context size
    "TKE_NUDGE_CTX": 400000,
    "TKE_HANDOFF_MAX_AGE_H": 24,
    "TKE_TRIM_TESTS": 1,
}
TEST_CMD = re.compile(
    r"^\s*(?:npx\s+|uv run\s+|poetry run\s+)?(pytest|python3? -m pytest|npm (?:run )?test|yarn test|pnpm test|"
    r"go test|cargo test|mvn(?:w)? test|\./gradlew test|gradle test|jest|vitest|dotnet test|rspec|"
    r"bundle exec rspec|phpunit)\b")
BINARY_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".ipynb")
HERE = Path(__file__).resolve().parent


def cfg(k):
    try:
        return int(os.environ.get(k, DEFAULTS[k]))
    except ValueError:
        return DEFAULTS[k]


def state_dir():
    from .config import config_dir
    d = config_dir() / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d


def out(obj):
    sys.stdout.write(json.dumps(obj))
    sys.exit(0)


def deny(reason):
    out({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                "permissionDecision": "deny", "permissionDecisionReason": reason}})


def size_of(path, cwd):
    if not path:
        return None
    p = Path(os.path.expanduser(path))
    if not p.is_absolute():
        p = Path(cwd or ".") / p
    try:
        return p.stat().st_size
    except OSError:
        return None


# ---------------------------------------------------------------- PreToolUse
def pre_tool(ev):
    name, inp, cwd = ev.get("tool_name", ""), ev.get("tool_input") or {}, ev.get("cwd")
    max_b = cfg("TKE_READ_MAX_BYTES")
    if name == "Read":
        path = inp.get("file_path", "")
        size = size_of(path, cwd)
        if size and size > max_b and not path.lower().endswith(BINARY_EXT) \
                and not inp.get("limit") and not inp.get("offset"):
            deny("tke: %s is %d KB (~%dk tokens) and would stay in context, re-read on every later call. "
                 "Grep for the symbol first, then Read with offset/limit (a few hundred lines)."
                 % (os.path.basename(path), size // 1024, size // 4000))
    if name == "Bash":
        cmd = inp.get("command", "")
        m = re.search(r"(?:^|[;&|(]\s*|\bthen\s+|\bdo\s+)sleep\s+(\d+)", cmd)
        if m and int(m.group(1)) > cfg("TKE_SLEEP_MAX_SEC") and not inp.get("run_in_background"):
            deny("tke: don't poll with `sleep %s`; every wake-up re-sends the whole context. Run the long "
                 "command in the background or wait on a condition instead." % m.group(1))
        m = re.match(r"\s*cat\s+([^\s|;&<>]+)\s*$", cmd)
        if m:
            size = size_of(m.group(1).strip("'\""), cwd)
            if size and size > max_b:
                deny("tke: `cat` would dump %d KB (~%dk tokens) into context. Use grep/head/tail, or Read "
                     "with offset/limit." % (size // 1024, size // 4000))
        if cfg("TKE_TRIM_TESTS") and TEST_CMD.match(cmd) and "|" not in cmd and "trim.py" not in cmd:
            py = Path(sys.executable).as_posix()
            trim = (HERE / "trim.py").as_posix()
            new = 'set -o pipefail; ( %s ) 2>&1 | "%s" "%s"' % (cmd, py, trim)
            out({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                        "updatedInput": dict(inp, command=new)}})
    sys.exit(0)


# ------------------------------------------------------------ prompt submit
def last_usage(transcript_path):
    """(context tokens, timestamp, model, wrote 1h cache) of the last response, reading only the tail."""
    try:
        with open(transcript_path, "rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 400000))
            lines = fh.read().decode("utf-8", "ignore").splitlines()
    except (OSError, TypeError):
        return 0, None, None, False
    for line in reversed(lines):
        if '"usage"' not in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        msg = d.get("message") or {}
        u = msg.get("usage")
        if u:
            ctx = (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                   + u.get("cache_creation_input_tokens", 0))
            h1 = bool((u.get("cache_creation") or {}).get("ephemeral_1h_input_tokens"))
            return ctx, d.get("timestamp"), msg.get("model"), h1
    return 0, None, None, False


def ttl_minutes(wrote_1h):
    env = os.environ.get("CLAUDE_CODE_PROMPT_CACHE_TTL", "")
    if env in ("1h", "5m"):
        return 60 if env == "1h" else 5
    return 60 if wrote_1h else 5


def prompt(ev):
    ctx, ts, model, h1 = last_usage(ev.get("transcript_path", ""))
    if not ctx or not ts:
        sys.exit(0)
    try:
        t = calendar.timegm(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        sys.exit(0)
    idle = (time.time() - t) / 60
    ttl = ttl_minutes(h1)
    if ctx >= cfg("TKE_IDLE_CTX") and idle >= ttl:
        sid = re.sub(r"[^A-Za-z0-9_.-]", "_", ev.get("session_id") or "x")
        marker = state_dir() / ("cold-%s" % sid)
        if not marker.exists() or time.time() - marker.stat().st_mtime > 300:
            marker.write_text(str(time.time()))
            from .prices import claude_price
            p = claude_price(model) or {"w5": 5, "w1h": 8}
            cost = ctx * (p["w1h"] if ttl == 60 else p["w5"]) / 1e6
            out({"decision": "block",
                 "reason": "tke: this session holds %dk tokens and has been idle %d min, longer than its %d-minute "
                           "prompt cache. Continuing writes the whole context again (about $%.2f on %s). Run /clear "
                           "to start fresh (the handoff note loads automatically), or send your message again to "
                           "continue anyway." % (ctx // 1000, idle, ttl, cost, model or "this model")})
    if ctx >= cfg("TKE_NUDGE_CTX"):
        out({"systemMessage": "tke: context is %dk tokens and every call re-reads all of it. If this is a new task, "
                              "/clear is cheaper." % (ctx // 1000)})
    sys.exit(0)


# ---------------------------------------------------------- stop / start
def handoff_path(cwd):
    d = state_dir() / "handoffs"
    d.mkdir(exist_ok=True)
    return d / (re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(cwd or "."))[-150:] + ".md")


def stop(ev):
    if ev.get("stop_hook_active"):
        sys.exit(0)
    last = (ev.get("last_assistant_message") or "").strip()
    if not last:
        sys.exit(0)
    cwd = ev.get("cwd") or os.getcwd()
    try:
        r = subprocess.run(["git", "status", "--short"], cwd=cwd, capture_output=True, text=True, timeout=3)
        gs = r.stdout.strip()[:1500] if r.returncode == 0 else ""
    except Exception:
        gs = ""
    note = ["# Handoff", "", "- When: %s" % time.strftime("%Y-%m-%d %H:%M"),
            "- Session: %s" % ev.get("session_id", "?"), "", "## Where we left off", "", last[:3000]]
    if gs:
        note += ["", "## Uncommitted changes", "", "```", gs, "```"]
    handoff_path(cwd).write_text("\n".join(note) + "\n", encoding="utf-8")
    sys.exit(0)


def session_start(ev):
    p = handoff_path(ev.get("cwd") or os.getcwd())
    try:
        age_h = (time.time() - p.stat().st_mtime) / 3600
        text = p.read_text(encoding="utf-8")
    except OSError:
        sys.exit(0)
    if age_h > cfg("TKE_HANDOFF_MAX_AGE_H") or (ev.get("session_id") and ev["session_id"] in text):
        sys.exit(0)
    out({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                "additionalContext": "Handoff note from the previous session in this folder "
                                                     "(%.1f h ago). Use it only if the user continues that work.\n\n%s"
                                                     % (age_h, text[:6000])}})


HANDLERS = {"pre_tool": pre_tool, "prompt": prompt, "stop": stop, "session_start": session_start}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        event = json.load(sys.stdin)
    except ValueError:
        sys.exit(0)
    handler = HANDLERS.get(argv[0] if argv else "")
    if not handler:
        sys.exit(0)
    try:
        handler(event)
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)   # fail open


if __name__ == "__main__":
    main()
