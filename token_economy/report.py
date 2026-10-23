"""Turn priced calls into the numbers that matter: pace against the budget, and where the money leaks."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from . import prices


def local_now():
    return datetime.now().astimezone()


def month_start(now):
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def workdays_between(start, end):
    """Mon-Fri days from start's date to end's date, inclusive."""
    n, d = 0, start.date()
    while d <= end.date():
        if d.weekday() < 5:
            n += 1
        d += timedelta(days=1)
    return n


def workdays_in_month(now):
    first = month_start(now)
    nxt = (first + timedelta(days=32)).replace(day=1)
    return workdays_between(first, nxt - timedelta(days=1))


def pace(spent, budget, now, workdays=None):
    """How the month is going for one budget. Spent and budget share a unit ($ or credits)."""
    wd_month = workdays or workdays_in_month(now)
    wd_done = max(1, workdays_between(month_start(now), now))
    per_day = budget / wd_month
    daily_avg = spent / wd_done
    left = budget - spent
    runout = None
    if daily_avg > 0 and left > 0:
        days_left = left / daily_avg
        d, n = now, 0
        while n < days_left:
            d += timedelta(days=1)
            if d.weekday() < 5:
                n += 1
        runout = d.date()
    elif left <= 0:
        runout = now.date()
    return {"spent": spent, "budget": budget, "per_workday": per_day, "daily_avg": daily_avg,
            "expected_by_now": per_day * wd_done, "ratio": (daily_avg / per_day) if per_day else 0,
            "runout": runout, "workdays_done": wd_done, "workdays_month": wd_month}


def leaks(calls, big_context):
    """Split the cost of priced calls by the habits the playbook targets."""
    priced = [c for c in calls if c.cost is not None]
    total = sum(c.cost for c in priced) or 0.0
    res = {
        "total": total,
        "calls": len(priced),
        "unpriced_models": sorted({c.model or "?" for c in calls if c.cost is None}),
        "rebuild_waste": sum(c.rebuild_waste for c in priced),
        "rebuilds": sum(1 for c in priced if c.rebuild),
        "fat_context": sum(c.cost for c in priced if c.prompt >= big_context),
        "frontier": sum(c.cost for c in priced if prices.is_frontier(c.model)),
        "subagents": sum(c.cost for c in priced if c.subagent),
        "fast": sum(c.fast_extra or 0 for c in priced),
        "searches": sum(c.searches or 0 for c in priced),
    }
    reads = writes = outs = 0.0
    for c in priced:
        if c.tool == "claude":
            p = prices.claude_rates(c.model, c.prompt, c.speed, c.geo)
            reads += c.read * p["read"] / 1e6
            writes += (c.w5 * p["w5"] + c.w1h * p["w1h"] + c.uncached * p["inp"]) / 1e6 + (c.searches or 0) * prices.WEB_SEARCH
            outs += c.out * p["out"] / 1e6
        else:
            p = prices.codex_price(c.model)
            m = 1.0 / (1 - c.fast_extra / c.cost) if (c.cost and c.fast_extra) else 1.0
            reads += c.read * p["cached"] * m / 1e6
            writes += c.uncached * p["inp"] * m / 1e6
            outs += c.out * p["out"] * m / 1e6
    res.update({"reads": reads, "writes": writes, "output": outs})
    return res


def sessions(calls, big_context, top=5):
    agg = defaultdict(lambda: {"cost": 0.0, "calls": 0, "peak": 0, "rebuilds": 0, "models": set(),
                               "subagent_cost": 0.0, "start": None, "end": None, "project": None})
    for c in calls:
        if c.cost is None:
            continue
        s = agg[(c.tool, c.session)]
        s["cost"] += c.cost
        s["calls"] += 1
        s["peak"] = max(s["peak"], c.prompt)
        s["rebuilds"] += c.rebuild
        s["models"].add(c.model)
        s["subagent_cost"] += c.cost if c.subagent else 0
        s["project"] = s["project"] or c.project
        s["start"] = min(s["start"] or c.ts, c.ts)
        s["end"] = max(s["end"] or c.ts, c.ts)
    out = []
    for (tool, sid), s in agg.items():
        tags = []
        if s["peak"] >= big_context:
            tags.append("big context")
        if s["rebuilds"]:
            tags.append("%d cache rebuild%s" % (s["rebuilds"], "s" if s["rebuilds"] > 1 else ""))
        if any(prices.is_frontier(m) for m in s["models"]):
            tags.append("frontier model")
        if s["subagent_cost"] > 0.3 * s["cost"]:
            tags.append("fan-out")
        if s["calls"] >= 150:
            tags.append("long session")
        out.append(dict(s, tool=tool, session=sid, tags=tags))
    out.sort(key=lambda s: -s["cost"])
    return out[:top]


def daily(calls, days=7, now=None):
    now = now or local_now()
    rows = defaultdict(lambda: {"claude": 0.0, "codex": 0.0})
    for c in calls:
        if c.cost is None:
            continue
        day = c.ts.astimezone(now.tzinfo).date()
        rows[day][c.tool] += c.cost
    first = (now - timedelta(days=days - 1)).date()
    return [(d, rows[d]["claude"], rows[d]["codex"]) for d in sorted(rows) if d >= first]


TIPS = {
    "rebuild": "Cache rebuilds after breaks: on an API key or usage credits set \"promptCacheTtl\": \"1h\" (tke setup "
               "claude does; a Claude subscription already gets the 1-hour cache), and /clear before a longer break "
               "instead of coming back to a cold, big session.",
    "fat": "Big contexts: /clear between tasks, one fresh session per phase (spec, plan, build, review), "
           "and hand wide searches to the Explore subagent.",
    "frontier": "Frontier models: use Fable or GPT-6 Astra on purpose for one hard problem, then switch back "
                "(tke route helps pick).",
    "fanout": "Subagents and agent teams: run them on a cheaper model (Explore on Haiku), fan out only for "
              "truly parallel work.",
    "output": "Output is a big share: lower effort for routine work (medium, or low for chores).",
    "fast": "Fast mode doubles every Opus rate (and on a subscription it is paid from usage credits, not the plan): "
            "turn it off with /fast, or set fastModePerSessionOptIn so it doesn't carry into new sessions.",
}


def tips_for(lk):
    t = lk["total"] or 1
    out = []
    if lk["rebuild_waste"] / t > 0.05:
        out.append(TIPS["rebuild"])
    if lk["fat_context"] / t > 0.3:
        out.append(TIPS["fat"])
    if lk["frontier"] / t > 0.2:
        out.append(TIPS["frontier"])
    if lk["subagents"] / t > 0.3:
        out.append(TIPS["fanout"])
    if lk["output"] / t > 0.45:
        out.append(TIPS["output"])
    if lk.get("fast", 0) / t > 0.05:
        out.append(TIPS["fast"])
    return out
