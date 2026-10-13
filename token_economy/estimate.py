"""tke estimate: price a session before you run it.

A session makes `calls` model calls while the prompt grows evenly from `start` to `end` tokens.
Every call re-sends the whole prompt: the part sent before is a cache read, the new part is a cache
write (Claude) or plain input (Codex). After a break longer than the cache lifetime the whole prompt
is written again; `breaks` spreads that many through the session.
"""
from . import prices


def per_call(calls, start, end, breaks=0):
    n = max(1, int(calls))
    k = max(0, int(breaks))
    miss = {min(n - 1, int(round(j * n / (k + 1.0)))) for j in range(1, k + 1)}
    rows, prev = [], 0.0
    for i in range(n):
        ctx = start if n == 1 else start + (end - start) * i / (n - 1)
        new = ctx if i == 0 else ctx - prev
        if i > 0 and i in miss:
            rows.append((ctx, 0.0, ctx, True))
        else:
            rows.append((ctx, ctx - new, new, False))
        prev = ctx
    return rows


def estimate(model, calls, start, end, out, breaks=0, ttl="5m"):
    """Returns (total, parts, unit). parts: read, write, rebuild, output."""
    parts = {"read": 0.0, "write": 0.0, "rebuild": 0.0, "output": 0.0}
    cp, xp = prices.claude_price(model), prices.codex_price(model)
    if cp is None and xp is None:
        raise ValueError("no price for model %r; add it to prices.json" % model)
    for ctx, read, write, miss in per_call(calls, start, end, breaks):
        if cp is not None:
            p = cp["long"] if "long" in cp and ctx > cp.get("long_at", 1e18) else cp
            w = p["w1h"] if ttl == "1h" else p["w5"]
            r, wr, o = read * p["read"], write * w, out * p["out"]
        else:
            r, wr, o = read * xp["cached"], write * xp["inp"], out * xp["out"]
        parts["read"] += r / 1e6
        parts["rebuild" if miss else "write"] += wr / 1e6
        parts["output"] += o / 1e6
    return sum(parts.values()), parts, ("$" if cp is not None else "credits")
