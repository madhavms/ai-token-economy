"""Build docs/basics.html from page.html, kg.css, measured.json and the figures below.

    python3 docs/basics/measure.py    # re-measure with the real claude CLI (a few cents)
    python3 docs/basics/build.py      # rebuild the page; no network, no model calls

Every number on the page is either in measured.json, in token_economy/prices.py (dated), or computed
here from those two. Figures follow the Knowledge Graph diagram language, declared once in FIG 0.
"""
import html
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
from token_economy import estimate, prices  # noqa: E402

M = json.loads((HERE / "measured.json").read_text(encoding="utf-8"))
esc = html.escape


# ------------------------------------------------------------------ small svg kit
def t(x, y, s, cls="", anchor="start"):
    c = (' class="%s"' % cls) if cls else ""
    a = (' text-anchor="%s"' % anchor) if anchor != "start" else ""
    return '<text x="%s" y="%s"%s%s>%s</text>' % (_n(x), _n(y), c, a, esc(str(s)))


def r(x, y, w, h, cls="box", rx=0):
    return '<rect x="%s" y="%s" width="%s" height="%s" class="%s"%s/>' % (_n(x), _n(y), _n(w), _n(h), cls, (' rx="%s"' % rx) if rx else "")


def ln(x1, y1, x2, y2, cls="ln", head="ah"):
    m = (' marker-end="url(#%s)"' % head) if head else ""
    return '<line x1="%s" y1="%s" x2="%s" y2="%s" class="%s"%s/>' % (_n(x1), _n(y1), _n(x2), _n(y2), cls, m)


def path(d, cls="ln", head="ah"):
    m = (' marker-end="url(#%s)"' % head) if head else ""
    return '<path d="%s" class="%s"%s/>' % (d, cls, m)


def icon(name, x, y, size=22, cls="logo"):
    return '<use href="#i-%s" x="%s" y="%s" width="%s" height="%s" class="%s %s"/>' % (name, _n(x), _n(y), size, size, cls, name)


def _n(v):
    return ("%.1f" % v).rstrip("0").rstrip(".") if isinstance(v, float) else str(v)


def svg(w, h, body, label):
    return '<svg viewBox="0 0 %d %d" role="img" aria-label="%s">%s</svg>' % (w, h, esc(label), "".join(body))


def k(n):
    """12,345 -> 12.3K"""
    return ("%.1fK" % (n / 1000.0)).replace(".0K", "K") if n >= 1000 else str(int(n))


def usd(x, digits=None):
    if digits is None:
        digits = 4 if x < 0.01 else (3 if x < 0.1 else 2)
    return "$%.*f" % (digits, x)


# ------------------------------------------------------------------ derived numbers
P = {name: prices.CLAUDE[name] for name in ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1")}
toy, sub = M["toy"], M["toy_sub"]
rows = toy["rows"]


def reprice(rows, model):
    p = P[model]
    tot = 0.0
    for x in rows:
        w1h = x.get("w1h", 0)
        tot += (x["read"] * p["read"] + w1h * p["w1h"] + (x["new"] - w1h) * p["w5"] + x["out"] * p["out"]) / 1e6
    return tot


startup_cost = toy["categories"].get("startup", 0.0)
N = {
    "date": M["date"], "prices_date": M["prices_date"], "claude_version": M["claude_version"].split(" ")[0],
    "startup_min": "{:,}".format(M["startup_minimal"]), "startup_full": "{:,}".format(M["startup_full"]),
    "setup_extra": "{:,}".format(M["startup_full"] - M["startup_minimal"]),
    "startup_min_k": k(M["startup_minimal"]), "startup_full_k": k(M["startup_full"]),
    "hello1_read": "{:,}".format(M["hello_first"]["cache_read_input_tokens"]),
    "hello1_write": "{:,}".format(M["hello_first"]["cache_creation_input_tokens"]),
    "hello2_read": "{:,}".format(M["hello_second"]["cache_read_input_tokens"]),
    "md_lines": M["claude_md_lines"], "md_chars": M["claude_md_chars"], "md_tokens": M["claude_md_tokens"],
    "md_per_1k_calls": usd(M["claude_md_tokens"] * 1000 * P["claude-opus-5-5"]["read"] / 1e6, 2),
    "en_chars": M["english_chars"], "en_tokens": M["english_tokens"], "en_words": M["english_words"],
    "en_cpt": "%.1f" % (M["english_chars"] / M["english_tokens"]),
    "en_tpw": "%.2f" % (M["english_tokens"] / M["english_words"]),
    "code_chars": M["code_chars"], "code_tokens": M["code_tokens"],
    "code_cpt": "%.1f" % (M["code_chars"] / M["code_tokens"]),
    "toy_calls": toy["calls"], "toy_cost": usd(toy["cost"]), "toy_reported": usd(toy["reported_cost"]),
    "toy_peak": "{:,}".format(toy["peak"]),
    "toy_startup_pct": "%.0f%%" % (100 * startup_cost / toy["cost"]),
    "toy_opus": usd(reprice(rows, "claude-opus-5-5")), "toy_haiku": usd(reprice(rows, "claude-haiku-5-5")),
    "toy_read_pct": "%.0f%%" % (100 * sum(x["read"] for x in rows) / sum(x["prompt"] for x in rows)),
    "toy_out_tokens": "{:,}".format(sum(x["out"] for x in rows)),
    "toy_in_tokens": "{:,}".format(sum(x["prompt"] for x in rows)),
    "sub_cost": usd(sub["cost"]), "sub_lead": usd(sub["lead_cost"]), "sub_sub": usd(sub["sub_cost"], 4),
    "sub_calls": len(sub["sub_calls"]), "sub_first": "{:,}".format(sub["sub_calls"][0]["prompt"]),
    "sub_last": "{:,}".format(sub["sub_calls"][-1]["prompt"]),
    "sub_lead_first": "{:,}".format(sub["rows"][0]["prompt"]),
    "sub_report": "{:,}".format(next((i["tokens"] for i in sub["items"] if i["cat"] == "agent"), 0)),
    "sub_vs": "%.0f%%" % (100 * (sub["cost"] - toy["cost"]) / toy["cost"]),
    "sub_model": sub["sub_calls"][0]["model"].replace("claude-", ""),
}
for name, p in P.items():
    short = name.replace("claude-", "").replace("-5-5", "").replace("-5-1", "")
    for key in ("inp", "out", "read", "w5", "w1h"):
        N["%s_%s" % (short, key)] = ("%.3f" % p[key]).rstrip("0").rstrip(".")
N["read_mult"] = "%.2f" % (P["claude-sonnet-5-5"]["read"] / P["claude-sonnet-5-5"]["inp"])
N["haiku_long_inp"] = "%.2f" % P["claude-haiku-5-5"]["long"]["inp"]
N["haiku_inp"] = "%.2f" % P["claude-haiku-5-5"]["inp"]

# a long session, modelled with tke estimate from the measured starting size
START = M["startup_minimal"]
one = estimate.estimate("claude-sonnet-5-5", 60, START, 150000, 400, 0, "1h")
three = [estimate.estimate("claude-sonnet-5-5", 20, START, START + (150000 - START) / 3, 400, 0, "1h") for _ in range(3)]
brk = estimate.estimate("claude-sonnet-5-5", 60, START, 150000, 400, 3, "5m")
opus_one = estimate.estimate("claude-opus-5-5", 60, START, 150000, 400, 0, "1h")
N.update({"long_cost": usd(one[0], 2), "long_read": usd(one[1]["read"], 2), "long_write": usd(one[1]["write"], 2),
          "long_out": usd(one[1]["output"], 2), "three_cost": usd(sum(x[0] for x in three), 2),
          "brk_cost": usd(brk[0], 2), "brk_rebuild": usd(brk[1]["rebuild"], 2), "opus_long": usd(opus_one[0], 2),
          "long_saving": "%.0f%%" % (100 * (1 - sum(x[0] for x in three) / one[0]))})


# "do it" problems, worked from the measured numbers
tok_per_line = M["claude_md_tokens"] / float(M["claude_md_lines"])
big_md = 400 * tok_per_line
op = P["claude-opus-5-5"]
son = P["claude-sonnet-5-5"]
N.update({
    "tok_per_line": "%.0f" % tok_per_line, "big_md": "{:,}".format(int(round(big_md, -2))),
    "big_md_write": usd(big_md * op["w1h"] / 1e6, 3), "big_md_reads": usd(200 * big_md * op["read"] / 1e6, 2),
    "big_md_total": usd((big_md * op["w1h"] + 200 * big_md * op["read"]) / 1e6, 2),
    "pause_rebuild": usd(150000 * son["w5"] / 1e6, 3), "pause_read": usd(150000 * son["read"] / 1e6, 3),
    "pause_x": "%.0f" % (son["w5"] / son["read"]),
})


# ------------------------------------------------------------------ figures
def fig_dna():
    b = [r(10, 14, 96, 34, "frozen"), t(116, 30, "fixed", "t-b"), t(116, 46, "loaded before you type", "t-s"),
         r(300, 14, 96, 34, "data"), t(406, 30, "carried", "t-b"), t(406, 46, "already in the context", "t-s"),
         r(590, 14, 96, 34, "train"), t(696, 30, "new", "t-b"), t(696, 46, "added on this call", "t-s"),
         r(10, 70, 96, 34, "box"), t(116, 86, "a program", "t-b"), t(116, 102, "harness, model, tool", "t-s"),
         ln(300, 87, 390, 87), t(406, 86, "every call", "t-b"), t(406, 102, "solid line", "t-s"),
         ln(590, 87, 680, 87, "ln-d", "ah-d"), t(696, 86, "once", "t-b"), t(696, 102, "dashed line", "t-s")]
    return svg(880, 116, b, "Legend for every diagram on the page")


def fig_big():
    b = []
    b += [r(8, 8, 570, 444, "zone"), t(20, 28, "Your machine", "t-s"), r(600, 8, 272, 444, "zone"), t(612, 28, "Anthropic", "t-s")]
    b += [r(24, 48, 110, 56, "box"), t(79, 72, "You", "t-b", "middle"), t(79, 92, "type a task", "t-s", "middle")]
    b += [r(170, 40, 390, 72, "box"), icon("claude", 184, 54, 26), t(222, 66, "The harness", "t-b"),
          t(222, 86, "Claude Code · Codex · Copilot CLI", "t-s"), t(222, 102, "keeps the conversation, runs tools", "t-s")]
    b += [ln(134, 76, 168, 76)]
    # the context stack the harness sends
    b += [t(170, 142, "What every call sends", "t-s")]
    stack = [("frozen", "System prompt + tool list"), ("frozen", "CLAUDE.md, memory, skill list"),
             ("data", "Messages, replies, tool results"), ("train", "New since the last call")]
    for i, (cls, label) in enumerate(stack):
        y = 152 + i * 40
        b += [r(170, y, 300, 32, cls), t(182, y + 21, label)]
    b += [t(480, 262, "~%s" % N["startup_min_k"], "t-m"), t(480, 278, "tokens", "t-s"), t(480, 294, "before", "t-s"), t(480, 310, "you type", "t-s")]
    b += [r(170, 360, 390, 72, "box"), t(184, 386, "Your repo", "t-b"), t(184, 406, "files, tests, the shell", "t-s"),
          t(184, 422, "where tools run", "t-s")]
    b += [ln(330, 112, 330, 150)]                              # harness builds the stack
    b += [path("M 470 312 L 470 356", "ln", "ah"), t(478, 340, "run a tool", "t-s")]
    b += [path("M 250 358 L 250 318", "ln", "ah"), t(258, 340, "its output", "t-s")]
    # model, cache, bill
    b += [r(624, 150, 224, 76, "box"), icon("anthropic", 638, 166, 22), t(670, 182, "The model", "t-b"),
          t(638, 206, "Opus · Sonnet · Haiku", "t-s"), t(638, 220, "remembers nothing", "t-s")]
    b += [r(624, 256, 224, 64, "data"), t(638, 280, "Prompt cache", "t-b"), t(638, 302, "recent prompts, 5 min or 1 h", "t-s")]
    b += [r(624, 350, 224, 82, "train"), t(638, 374, "The bill, per call", "t-b"), t(638, 396, "read × read price", "t-m"),
          t(638, 412, "+ new × write price", "t-m"), t(638, 428, "+ output × output price", "t-m")]
    b += [ln(470, 200, 620, 188), t(490, 176, "every call", "t-s")]
    b += [path("M 624 168 C 590 120 590 80 564 76", "ln", "ah"), t(610, 110, "reply or", "t-s"), t(610, 124, "tool call", "t-s")]
    b += [ln(736, 226, 736, 252, "ln", "ah"), ln(736, 320, 736, 346, "ln", "ah")]
    return svg(880, 460, b, "The big picture: you, the harness, the context it sends on every call, the model, the cache and the bill")


def fig_tokens():
    words = ["Every", " call", " sends", " the", " whole", " convers", "ation", " again", "."]
    b, x = [], 10
    for w in words:
        wd = 14 + 8.6 * len(w)
        b += [r(x, 14, wd, 34, "data"), t(x + wd / 2, 36, w.replace(" ", "·"), "t-m", "middle")]
        x += wd + 6
    b += [t(10, 72, "Illustration only: the real split is the tokenizer's. The counts below are measured.", "t-s")]
    rows2 = [("English", M["english_tokens"] * 1000.0 / M["english_chars"], "%s chars → %s tokens" % (N["en_chars"], N["en_tokens"])),
             ("Python code", M["code_tokens"] * 1000.0 / M["code_chars"], "%s chars → %s tokens" % (N["code_chars"], N["code_tokens"]))]
    for i, (name, per1k, note) in enumerate(rows2):
        y = 98 + i * 46
        w = per1k * 1.2
        b += [t(10, y + 21, name), r(130, y, w, 30, "train" if i else "data"), t(130 + w + 10, y + 20, "%.0f tokens per 1,000 characters" % per1k, "t-m"),
              t(130 + w + 10, y + 36, note, "t-s")]
    return svg(880, 196, b, "A sentence split into tokens, and measured tokens per 1,000 characters for English and Python")


def fig_calls():
    b = []
    scale = 600.0 / 24000
    for i, x in enumerate(rows):
        y = 16 + i * 62
        prev = rows[i - 1]["prompt"] if i else 0
        start = M["toy"]["startup_tokens"]
        carried = max(0, (prev if i else start) - start)
        new = x["prompt"] - start - carried
        xs = 110
        b += [t(10, y + 22, "Call %d" % (i + 1), "t-b")]
        b += [r(xs, y, start * scale, 32, "frozen")]
        if carried:
            b += [r(xs + start * scale, y, carried * scale, 32, "data")]
        b += [r(xs + (start + carried) * scale, y, max(3, new * scale), 32, "train")]
        b += [t(xs + 12, y + 21, "startup %s" % k(start), "t-m")]
        endx = xs + x["prompt"] * scale
        b += [t(endx + 12, y + 14, "%s tokens sent" % "{:,}".format(x["prompt"]), "t-m"),
              t(endx + 12, y + 30, "%s from cache, %s new" % (k(x["read"]), k(x["new"])), "t-s")]
    b += [t(110, 206, "Each call re-sends everything. Only the orange part is new.", "t-h")]
    return svg(880, 220, b, "The three calls of the toy task: each re-sends the whole context")


def fig_cache():
    b = []
    W = 520.0
    lines = [("Call 1", [("frozen", 0.6, "cached by earlier sessions"), ("train", 0.4, "written: 1.25× or 2×")], "first call"),
             ("Call 2", [("data", 0.8, "read from cache: %s×" % N["read_mult"]), ("train", 0.2, "new")], "seconds later"),
             ("Call 3", [("data", 0.9, "read from cache"), ("train", 0.1, "new")], ""),
             ("Call 4", [("train", 1.0, "the whole prompt written again: 1.25× or 2×")], "after a long pause")]
    for i, (name, segs, note) in enumerate(lines):
        y = 14 + i * 56 + (20 if i == 3 else 0)
        x = 110
        b += [t(10, y + 22, name, "t-b")]
        for cls, frac, label in segs:
            w = W * frac
            b += [r(x, y, w, 34, cls), t(x + 10, y + 22, label, "t-s")]
            x += w
        if note:
            b += [t(110 + W + 14, y + 22, note, "t-s")]
    b += [ln(110, 186, 630, 186, "ln-d", None), t(640, 190, "pause > 5 min (or 1 h)", "t-h")]
    return svg(880, 260, b, "How the prompt cache prices each call: a cached prefix is read cheaply, new text is written, and after a long pause everything is written again")


def fig_loop():
    b = [r(20, 70, 230, 90, "box"), icon("anthropic", 34, 86, 22), t(66, 102, "The model", "t-b"),
         t(34, 128, "reads the context,", "t-s"), t(34, 144, "replies or asks for a tool", "t-s")]
    b += [r(330, 70, 230, 90, "box"), icon("claude", 344, 86, 24), t(378, 102, "The harness", "t-b"),
          t(344, 128, "runs it, adds the result,", "t-s"), t(344, 144, "calls the model again", "t-s")]
    tools = ["Read", "Edit", "Bash", "Grep"]
    for i, name in enumerate(tools):
        y = 30 + i * 44
        b += [r(650, y, 120, 34, "box"), t(710, y + 22, name, "t-m", "middle"), ln(550, 115, 646, y + 17, "ln", "ah")]
    b += [path("M 240 92 C 280 60 300 60 326 86", "ln", "ah"), t(250, 52, "tool call", "t-s")]
    b += [path("M 330 140 C 300 172 270 172 244 146", "ln", "ah"), t(252, 190, "tool result", "t-s")]
    b += [path("M 130 160 L 130 220", "ln-hi", "ah-hi"), t(142, 212, "no tool call: the reply goes to you, the loop ends", "t-h")]
    return svg(880, 236, b, "The agent loop: the model asks for a tool, the harness runs it and calls the model again")


def fig_anatomy():
    scale = 780.0 / M["startup_full"]
    parts = [("frozen", M["startup_minimal"], "Claude Code's system prompt + tools"),
             ("train", M["claude_md_tokens"], "CLAUDE.md"),
             ("frozen", M["startup_full"] - M["startup_minimal"], "plugins, skills, MCP")]
    b, x = [], 50
    for i, (cls, n, label) in enumerate(parts):
        w = max(4, n * scale)
        b += [r(x, 40, w, 40, cls)]
        lx, anchor = (x + 8, "start") if i == 0 else ((x + w - 2, "end") if i == 2 else (x - 4, "end"))
        b += [ln(x + w / 2, 80, x + w / 2, 104 + i * 28, "ln-d", None), t(lx, 116 + i * 28,
              "%s · %s tokens" % (label, "{:,}".format(n)), "t-m", anchor)]
        x += w
    b += [t(50, 28, "Before you type: measured in an empty folder", "t-s")]
    return svg(880, 186, b, "What the startup context is made of, measured")


def fig_claudemd():
    b = []
    files = [("Managed policy CLAUDE.md", "set by your company's IT", "start"),
             ("~/.claude/CLAUDE.md", "you, every project", "start"),
             ("repo/CLAUDE.md or repo/.claude/CLAUDE.md", "the team, in git", "start"),
             ("repo/CLAUDE.local.md", "you, this project, git-ignored", "start"),
             ("repo/sub/CLAUDE.md", "loads when Claude works in sub/", "demand"),
             ("MEMORY.md (auto memory)", "Claude's notes: 200 lines or 25 KB", "start")]
    for i, (name, who, when) in enumerate(files):
        y = 12 + i * 46
        b += [r(10, y, 390, 34, "data" if when == "demand" else "frozen"), t(22, y + 22, name, "t-m"),
              t(420, y + 22, who, "t-s"),
              ln(690, y + 17, 760, y + 17, "ln" if when == "start" else "ln-d", "ah" if when == "start" else "ah-d")]
    b += [r(764, 12, 108, 264, "box"), t(818, 128, "the", "t-s", "middle"), t(818, 146, "context", "t-b", "middle"),
          t(818, 164, "every call", "t-s", "middle")]
    return svg(880, 290, b, "Which instruction files Claude Code loads and when")


def fig_sub():
    sc = 480.0 / 22000
    lead = sub["rows"][0]["prompt"]
    s0 = sub["sub_calls"][0]["prompt"]
    lw, sw = lead * sc, s0 * sc
    b = [t(10, 24, "Lead · %s" % sub["rows"][0]["model"].replace("claude-", ""), "t-b"),
         r(10, 34, lw, 38, "frozen"), t(22, 58, "%s tokens on its first call" % "{:,}".format(lead), "t-m")]
    b += [path("M 40 72 L 40 140", "ln", "ah"), t(52, 108, "brief: what to find, how to report", "t-s")]
    b += [r(10, 144, sw, 38, "data"), t(sw + 24, 160, "Subagent · %s" % N["sub_model"], "t-b"),
          t(sw + 24, 178, "%s tokens: own short prompt + CLAUDE.md + brief" % "{:,}".format(s0), "t-s")]
    b += [path("M 60 182 C 60 226 640 226 640 130 C 640 84 600 53 %.1f 53" % (lw + 14), "ln-hi", "ah-hi"),
          t(652, 168, "report: about %s tokens," % N["sub_report"], "t-h"), t(652, 188, "all the lead keeps", "t-h")]
    return svg(880, 236, b, "A subagent starts with a small fresh context and returns only its report")


def fig_where():
    left = ["CLAUDE.md, CLAUDE.local.md, rules", "settings.json, agent files, skills", "Auto memory (MEMORY.md)",
            "Transcripts: ~/.claude/projects/…/*.jsonl", "Codex logs: ~/.codex/sessions/…"]
    right = ["The model itself", "Prompt cache: 5 min or 1 h, per org", "Usage and billing"]
    b = [r(8, 8, 420, 270, "zone"), t(22, 32, "On your machine", "t-b"), r(452, 8, 420, 270, "zone"), t(466, 32, "At Anthropic (or OpenAI)", "t-b")]
    for i, s in enumerate(left):
        b += [r(22, 48 + i * 44, 392, 34, "frozen" if i < 3 else "data"), t(34, 70 + i * 44, s, "t-m")]
    for i, s in enumerate(right):
        y = 48 + i * 44
        b += [r(466, y, 392, 34, "box"), t(478, y + 22, s, "t-m")]
    b += [ln(428, 240, 452, 240), t(320, 296, "Every call ships the context across. Nothing is remembered there except the cache.", "t-h", "middle")]
    return svg(880, 310, b, "What lives on your machine and what lives at the model provider")


def fig_glue():
    b = [r(250, 110, 380, 92, "train"), t(440, 136, "cost of a session", "t-b", "middle"),
         t(440, 160, "Σ calls × ( carried × read price", "t-m", "middle"),
         t(440, 178, "+ new × write price + output × output price )", "t-m", "middle")]
    levers = [(20, 20, "Fewer calls", "batch questions, plan first"), (330, 20, "Smaller context", "/clear, short CLAUDE.md"),
              (640, 20, "Warm cache", "no long pauses, 1 h cache"),
              (20, 250, "Cheaper model", "Sonnet or Haiku for routine"), (330, 250, "Short outputs", "lower effort, concise"),
              (640, 250, "Subagents", "big reads kept out")]
    for x, y, h, s in levers:
        b += [r(x, y, 220, 58, "box"), t(x + 12, y + 24, h, "t-b"), t(x + 12, y + 44, s, "t-s")]
        tx, ty = x + 110, (y + 58 if y < 100 else y)
        b += [ln(tx, ty, 440 + (tx - 440) * 0.35, 110 if y < 100 else 202, "ln", "ah")]
    return svg(880, 320, b, "How every idea on the page meets in one cost formula, and the six levers that move it")


def fig_scale():
    W, H, L, B, T = 880, 260, 60, 34, 16
    n = 60
    calls1 = estimate.per_call(n, START, 150000, 0)
    calls3 = []
    for s in range(3):
        calls3 += estimate.per_call(20, START, START + (150000 - START) / 3, 0)
    ymax = 160000.0
    x = lambda i: L + (W - L - 20) * i / (n - 1)
    y = lambda v: T + (H - T - B) * (1 - v / ymax)
    b = []
    for v in (0, 50000, 100000, 150000):
        b += ['<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" class="grid"/>' % (L, W - 20, y(v), y(v)), t(L - 8, y(v) + 4, k(v), "t-s", "end")]
    for i in (0, 19, 39, 59):
        b += [t(x(i), H - 12, "call %d" % (i + 1), "t-s", "end" if i == 59 else "middle")]
    p1 = " ".join("%s%.1f %.1f" % ("M" if i == 0 else "L", x(i), y(c[0])) for i, c in enumerate(calls1))
    p3 = " ".join("%s%.1f %.1f" % ("M" if i % 20 == 0 else "L", x(i), y(c[0])) for i, c in enumerate(calls3))
    b += ['<path d="%s" class="viz-line v1"/>' % p1, '<path d="%s" class="viz-line v2"/>' % p3]
    b += [t(x(59) - 6, y(150000) - 10, "one long session: %s" % N["long_cost"], "t-m", "end"),
          t(x(50), y(70000) - 12, "three fresh sessions: %s" % N["three_cost"], "t-m", "end")]
    return svg(W, H, b, "Context per call in one long session against the same work in three fresh sessions")


def icons_defs():
    out = []
    for name in ("claude", "anthropic", "githubcopilot", "python"):
        s = (HERE / "icons" / ("%s.svg" % name)).read_text(encoding="utf-8")
        d = re.search(r'<path d="([^"]+)"', s).group(1)
        out.append('<symbol id="i-%s" viewBox="0 0 24 24"><path d="%s"/></symbol>' % (name, d))
    return "".join(out)


FIGS = {"dna": fig_dna, "big": fig_big, "tokens": fig_tokens, "calls": fig_calls, "cache": fig_cache, "loop": fig_loop,
        "anatomy": fig_anatomy, "claudemd": fig_claudemd, "sub": fig_sub, "where": fig_where, "glue": fig_glue,
        "scale": fig_scale}


def toy_table():
    out = []
    for x in rows:
        out.append("<tr><td class='num'>%d</td><td>%s</td><td class='num'>%s</td><td class='num'>%s</td><td class='num'>%s</td>"
                   "<td class='num'>%s</td><td class='num'>%s</td><td class='num'>%s</td><td class='num'>%s</td></tr>" % (
                       x["i"] + 1, esc(", ".join(x["tools"]) or "final reply"), "{:,}".format(x["prompt"]),
                       "{:,}".format(x["read"]), "{:,}".format(x["new"]), "{:,}".format(x["out"]),
                       usd(x["c_read"]), usd(x["c_write"]), usd(x["c_out"])))
    tot = lambda key: sum(x[key] for x in rows)
    out.append("<tr class='sum'><td></td><td><b>Total</b></td><td class='num'>%s</td><td class='num'>%s</td><td class='num'>%s</td>"
               "<td class='num'>%s</td><td class='num'>%s</td><td class='num'>%s</td><td class='num'>%s</td></tr>" % (
                   "{:,}".format(tot("prompt")), "{:,}".format(tot("read")), "{:,}".format(tot("new")), "{:,}".format(tot("out")),
                   usd(tot("c_read")), usd(tot("c_write")), usd(tot("c_out"))))
    return "".join(out)


def money2(v):
    s = "%.3f" % v
    return "$" + (s[:-1] if s.endswith("0") else s)


def price_table():
    out = []
    for name, p in P.items():
        label = name.replace("claude-", "").replace("-", " ").title().replace(" 5 5", " 5.5").replace(" 5 1", " 5.1")
        fmt = money2
        out.append("<tr><td><b>%s</b></td><td class='num'>%s</td><td class='num'>%s</td><td class='num'>%s</td>"
                   "<td class='num'>%s</td><td class='num'>%s</td></tr>" % (label, fmt(p["inp"]), fmt(p["w5"]), fmt(p["w1h"]),
                                                                            fmt(p["read"]), fmt(p["out"])))
    return "".join(out)


def main():
    page = (HERE / "page.html").read_text(encoding="utf-8")
    css = (HERE / "kg.css").read_text(encoding="utf-8")
    page = page.replace("{{css}}", css).replace("{{icons}}", icons_defs())
    page = page.replace("{{toy_table}}", toy_table()).replace("{{price_table}}", price_table())
    page = page.replace("{{prices_json}}", json.dumps({k2: {kk: P[k2][kk] for kk in ("inp", "out", "read", "w5", "w1h")} for k2 in P}))
    page = page.replace("{{start_tokens}}", str(START))
    page = re.sub(r"\{\{fig:(\w+)\}\}", lambda m: FIGS[m.group(1)](), page)

    def num(m):
        if m.group(1) not in N:
            raise SystemExit("unknown number: %s" % m.group(1))
        return esc(str(N[m.group(1)]))
    page = re.sub(r"\{\{(\w+)\}\}", num, page)
    (ROOT / "docs" / "basics.html").write_text(page, encoding="utf-8")
    print("wrote docs/basics.html (%d KB)" % (len(page) // 1024))


if __name__ == "__main__":
    main()
