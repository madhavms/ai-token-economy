"""Plain-text reports for `tke why` and the MCP tools: one session, or a period."""
from collections import defaultdict
from datetime import datetime

from . import analyze, config, report
from .analyze import CATEGORIES, money

BAR = 24


def _bar(frac, width=BAR):
    n = max(0, min(width, int(round(frac * width))))
    return "█" * n + "·" * (width - n)


def _when(iso):
    if not iso:
        return "?"
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%a %d %b %H:%M")
    except ValueError:
        return iso[:16]


def _dur(a, b):
    try:
        secs = (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds()
    except (TypeError, ValueError):
        return "?"
    h, m = divmod(int(secs // 60), 60)
    return ("%dh %02dm" % (h, m)) if h else ("%dm" % m)


def session_text(s, rows=None, calls=False, brief=False):
    u = s["unit"]
    total = s["cost"] or 1e-9
    L = []
    L.append("%s · %s" % (s["title"] or "(untitled session)", s["project"] or s["tool"]))
    L.append("%s session %s · %s → %s (%s) · %d calls · peak %sK tokens" % (
        s["tool"].capitalize(), s["session"][:8], _when(s["start"]), _when(s["end"])[-5:], _dur(s["start"], s["end"]),
        s["calls"], s["peak"] // 1000))
    models = ", ".join("%s %s" % (m.replace("claude-", ""), money(v, u)) for m, v in sorted(s["models"].items(), key=lambda kv: -kv[1]) if v)
    L.append("Cost %s  =  lead %s + subagents %s   [%s]" % (money(s["cost"], u), money(s["lead_cost"], u), money(s["sub_cost"], u), models))
    if s["unpriced"]:
        L.append("No price for: %s (counted as 0; add to prices.json)" % ", ".join(s["unpriced"]))
    L.append("")
    L.append("Where it went (these add up to the total)")
    for f in s["causes"]:
        L.append("  %-34s %10s %4.0f%%  %s" % (CATEGORIES[f["key"]][0], money(f["dollars"], u), 100 * f["share"], _bar(f["share"], 16)))
        if f["detail"] and not brief:
            L.append("      " + f["detail"])
    small = total - sum(f["dollars"] for f in s["causes"])
    if small > 0.005 * total:
        L.append("  %-34s %10s %4.0f%%" % ("Smaller causes", money(small, u), 100 * small / total))
    if s["levers"]:
        L.append("")
        L.append("What to change (dollars at stake; these overlap the causes above)")
        for f in s["levers"]:
            L.append("  -> %s" % f["title"])
            if not brief:
                L.append("     %s %s" % (f["detail"], f["fix"]))
    if s["top_items"] and not brief:
        L.append("")
        L.append("Costliest things in the context (cost of writing it once + re-reading it on later calls)")
        for i in [x for x in s["top_items"] if x["cat"] not in ("startup",)][:10]:
            L.append("  %9s  %5sK tok  x%-4d %-18s %s" % (money(i["cost"], u), max(1, i["tokens"] // 1000), i["carried"],
                                                        CATEGORIES[i["cat"]][0][:18], i["label"][:70]))
    if calls and rows:
        L.append("")
        L.append("Call by call")
        L.append("  %4s %-5s %-14s %8s %8s %7s %9s  %s" % ("#", "time", "model", "prompt", "new", "out", "cost", "notes"))
        for r in rows:
            notes = []
            if r["rebuild"]:
                notes.append("cache rebuilt after %dm idle" % (r["gap"] // 60))
            if r["compaction"]:
                notes.append("after compaction")
            if r["tools"]:
                notes.append(",".join(sorted(set(r["tools"])))[:40])
            L.append("  %4d %-5s %-14s %7dK %7dK %6dK %9s  %s" % (
                r["i"] + 1, r["ts"].astimezone().strftime("%H:%M") if r["ts"] else "", (r["model"] or "").replace("claude-", "")[:14],
                r["prompt"] // 1000, r["new"] // 1000, r["out"] // 1000, money(r["cost"], u), "; ".join(notes)))
    return "\n".join(L)


def period_text(sessions, label, top=8, brief=False, since_day=None, until_day=None):
    agg = analyze.period_report(sessions, since_day, until_day)
    L = []
    for unit, name in (("$", "Claude Code"), ("cr", "Codex")):
        ss = [s for s in agg["sessions"] if s["unit"] == unit]
        if not ss:
            continue
        total = agg["totals"].get(unit, 0) or 1e-9
        L.append("%s · %s · %s across %d sessions" % (name, label, money(total, unit), len(ss)))
        L.append("")
        L.append("Where it went (add up to the total)")
        rows = sorted(((k, v) for (u, k), v in agg["categories"].items() if u == unit), key=lambda kv: -kv[1])
        for k, v in rows:
            if v / total < 0.01:
                continue
            L.append("  %-34s %10s %4.0f%%  %s" % (CATEGORIES[k][0], money(v, unit), 100 * v / total, _bar(v / total, 16)))
        lv = sorted(((k, v) for (u, k), v in agg["levers"].items() if u == unit), key=lambda kv: -kv[1])
        if lv:
            names = {"big": "Calls over 150K tokens", "model": "Top-tier model premium (vs the everyday model)",
                     "rebuild": "Cache rebuilt after breaks", "haiku_long": "Haiku over 100K tokens",
                     "repeat": "The same result loaded repeatedly", "startup": "Heavy startup context"}
            L.append("")
            L.append("Levers (dollars at stake; they overlap)")
            for k, v in lv:
                L.append("  %-48s %10s %4.0f%%" % (names.get(k, k), money(v, unit), 100 * v / total))
        L.append("")
        L.append("By model")
        for (u, m), v in sorted(agg["models"].items(), key=lambda kv: -kv[1]):
            if u == unit and v / total >= 0.005:
                L.append("  %-34s %10s %4.0f%%" % ((m or "?").replace("claude-", ""), money(v, unit), 100 * v / total))
        L.append("")
        L.append("Costliest sessions (cost inside the period; * = the session also ran outside it)")
        for s in ss[:top]:
            L.append("  %9s%s %-8s %-34s %s" % (money(s["period_cost"], unit), "*" if s["period_share"] < 0.995 else " ",
                                                s["session"][:8], (s["title"] or s["project"])[:34], s["headline"]))
        if not brief:
            L.append("")
            L.append("Costliest things carried in context (summed over sessions)")
            items = sorted(((k, v) for (u, k), v in agg["labels"].items() if u == unit), key=lambda kv: -kv[1]["cost"])[:top]
            for k, v in items:
                L.append("  %9s  %2d session%s  %-16s %s" % (money(v["cost"], unit), v["sessions"], " " if v["sessions"] == 1 else "s",
                                                            CATEGORIES[v["cat"]][0][:16], k[:70]))
            pj = sorted(((k, v) for (u, k), v in agg["projects"].items() if u == unit), key=lambda kv: -kv[1]["cost"])[:top]
            L.append("")
            L.append("By project")
            for k, v in pj:
                L.append("  %9s  %3d sessions  %s" % (money(v["cost"], unit), v["sessions"], k or "?"))
        L.append("")
    if not L:
        return "No sessions in %s." % label
    L.append("List-price estimates from local logs. `tke why <session-id>` explains one session.")
    return "\n".join(L)


def period_since(period, now=None):
    from .cli import since_for
    return since_for(period, now or report.local_now())
