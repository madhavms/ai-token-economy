"""Price tables. Override any entry with prices.json in the config folder (see config.py).

Claude: $ per million tokens, from platform.claude.com/docs/en/about-claude/pricing (2026-10-13).
Codex:  credits per million tokens, from learn.chatgpt.com/docs/pricing (2026-10-13).
"""
import json

PRICES_DATE = "2026-10-13"

# inp = base input, w5 = 5-minute cache write, w1h = 1-hour cache write, read = cache hit, out = output
CLAUDE = {
    "claude-fable-5-1":  {"inp": 10,   "w5": 12.5,  "w1h": 20,   "read": 0.25, "out": 50},
    "claude-mythos-5-1": {"inp": 10,   "w5": 12.5,  "w1h": 20,   "read": 0.25, "out": 50},
    "claude-fable-5":    {"inp": 10,   "w5": 12.5,  "w1h": 20,   "read": 1.00, "out": 50},
    "claude-opus-5-5":   {"inp": 4,    "w5": 5,     "w1h": 8,    "read": 0.20, "out": 20},
    "claude-opus-5":     {"inp": 5,    "w5": 6.25,  "w1h": 10,   "read": 0.50, "out": 25},
    "claude-opus-4-8":   {"inp": 5,    "w5": 6.25,  "w1h": 10,   "read": 0.50, "out": 25},
    "claude-opus-4-7":   {"inp": 5,    "w5": 6.25,  "w1h": 10,   "read": 0.50, "out": 25},
    "claude-opus-4-6":   {"inp": 5,    "w5": 6.25,  "w1h": 10,   "read": 0.50, "out": 25},
    "claude-sonnet-5-5": {"inp": 2,    "w5": 2.5,   "w1h": 4,    "read": 0.10, "out": 10},
    "claude-sonnet-5":   {"inp": 2,    "w5": 2.5,   "w1h": 4,    "read": 0.20, "out": 10},
    "claude-sonnet-4-6": {"inp": 3,    "w5": 3.75,  "w1h": 6,    "read": 0.30, "out": 15},
    "claude-haiku-5-5":  {"inp": 0.10, "w5": 0.125, "w1h": 0.20, "read": 0.01, "out": 0.50,
                          "long_at": 100000,
                          "long": {"inp": 0.50, "w5": 0.625, "w1h": 1, "read": 0.05, "out": 2.50}},
    "claude-haiku-4-5":  {"inp": 1,    "w5": 1.25,  "w1h": 2,    "read": 0.10, "out": 5},
    # older models, still on the pricing page
    "claude-opus-4-5":   {"inp": 5,    "w5": 6.25,  "w1h": 10,   "read": 0.50, "out": 25},
    "claude-opus-4-1":   {"inp": 15,   "w5": 18.75, "w1h": 30,   "read": 1.50, "out": 75},
    "claude-opus-4":     {"inp": 15,   "w5": 18.75, "w1h": 30,   "read": 1.50, "out": 75},
    "claude-sonnet-4-5": {"inp": 3,    "w5": 3.75,  "w1h": 6,    "read": 0.30, "out": 15},
    "claude-sonnet-4":   {"inp": 3,    "w5": 3.75,  "w1h": 6,    "read": 0.30, "out": 15},
    "claude-haiku-3-5":  {"inp": 0.80, "w5": 1,     "w1h": 1.60, "read": 0.08, "out": 4},
}

# inp = uncached input, cached = cached input, out = output. Codex has no cache-write charge.
CODEX = {
    "gpt-6-astra":   {"inp": 250,  "cached": 25,   "out": 1250},
    "gpt-6.1-sol":   {"inp": 50,   "cached": 2.5,  "out": 250},
    "gpt-6-sol":     {"inp": 50,   "cached": 5,    "out": 250},
    "gpt-6-luna":    {"inp": 2.5,  "cached": 0.25, "out": 12.5},
    "gpt-5.6-sol":   {"inp": 100,  "cached": 10,   "out": 500},
    "gpt-5.6-terra": {"inp": 50,   "cached": 5,    "out": 300},
    "gpt-5.6-luna":  {"inp": 5,    "cached": 0.5,  "out": 30},
    "gpt-5.5":       {"inp": 125,  "cached": 12.5, "out": 750},
}

# Tier labels used by reports and the router.
FRONTIER = ("claude-fable", "claude-mythos", "gpt-6-astra")


def load_overrides(path):
    """Merge a prices.json of the form {"claude": {...}, "codex": {...}} into the tables."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return
    CLAUDE.update(data.get("claude", {}))
    CODEX.update(data.get("codex", {}))


def _match(table, model):
    """Exact id, else the longest table key that the model id starts with (handles date suffixes)."""
    if not model:
        return None
    m = model.lower()
    if m in table:
        return table[m]
    best = None
    for key in table:
        if m.startswith(key) and (best is None or len(key) > len(best)):
            best = key
    return table[best] if best else None


def claude_price(model):
    return _match(CLAUDE, model)


def codex_price(model):
    return _match(CODEX, model)


# Fast mode: Opus at premium rates ($8/$40 on Opus 5.5, $10/$50 on Opus 5 and 4.8), i.e. 2x every rate;
# the cache multipliers apply on top. Claude Code records it per call as usage.speed == "fast".
FAST = {"claude-opus-5-5": 2.0, "claude-opus-5": 2.0, "claude-opus-4-8": 2.0}
# US-only inference (usage.inference_geo == "us"): 1.1x on every rate, Claude 4.6 and later.
US_GEO = 1.1
WEB_SEARCH = 10.0 / 1000          # $ per web search, on top of tokens
# Codex Fast / Ultrafast: x2 / x6 on purchased credits and pay-as-you-go; x2.5 / x8 on included plan usage.
CODEX_SPEED = {"credits": {"standard": 1.0, "fast": 2.0, "ultrafast": 6.0},
               "plan": {"standard": 1.0, "fast": 2.5, "ultrafast": 8.0}}


def fast_multiplier(model, speed):
    if speed != "fast" or not model:
        return 1.0
    m = model.lower()
    best = None
    for key in FAST:
        if m.startswith(key) and (best is None or len(key) > len(best)):
            best = key
    return FAST[best] if best else 1.0


def claude_rates(model, prompt=0, speed=None, geo=None):
    """$ per million tokens for one response: base, Haiku long-prompt step, fast mode, data residency."""
    p = claude_price(model)
    if p is None:
        return None
    if "long" in p and prompt > p.get("long_at", 1e18):
        p = p["long"]
    mult = fast_multiplier(model, speed) * (US_GEO if geo == "us" else 1.0)
    return {k: p[k] * mult for k in ("inp", "w5", "w1h", "read", "out")}


def claude_cost(model, inp=0, w5=0, w1h=0, read=0, out=0, speed=None, geo=None, searches=0):
    """Dollars for one Claude response. None if the model has no price."""
    p = claude_rates(model, inp + w5 + w1h + read, speed, geo)
    if p is None:
        return None
    return (inp * p["inp"] + w5 * p["w5"] + w1h * p["w1h"] + read * p["read"] + out * p["out"]) / 1e6 + searches * WEB_SEARCH


def codex_credits(model, uncached=0, cached=0, out=0, mult=1.0):
    """Credits for Codex usage. None if the model has no price. mult: the Fast/Ultrafast multiplier."""
    p = codex_price(model)
    if p is None:
        return None
    return (uncached * p["inp"] + cached * p["cached"] + out * p["out"]) * mult / 1e6


def is_frontier(model):
    return bool(model) and model.lower().startswith(FRONTIER)
