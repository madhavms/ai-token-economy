"""tke route: pick the cheapest lane that can do the job, and print the command to start it."""

LANES = {
    "fast": {
        "why": "a check can prove it right, so a small model is safe",
        "claude": "claude --model haiku --effort low",
        "codex": "codex --profile luna",
        "local": "ollama run gpt-oss:20b   (short prompts only; ask IT first at work)",
    },
    "workhorse": {
        "why": "the approach is written down; this is building, not deciding",
        "claude": "claude --model sonnet --effort medium   (point it at SPEC.md or PLAN.md)",
        "codex": "codex   (GPT-6.1 Sol at medium, the tke default)",
    },
    "strong": {
        "why": "the approach isn't decided yet: plan first, in its own session",
        "claude": 'claude --model opus --effort medium   then: "write the plan to PLAN.md", /clear, build on sonnet',
        "codex": 'codex --config model_reasoning_effort="high"   then start a new thread to build',
    },
    "frontier": {
        "why": "a design call or a bug that survived two attempts: pay for the best model, on purpose, once",
        "claude": "claude --model fable   (one focused session, then back to your default)",
        "codex": "codex --profile astra",
    },
}

QUESTIONS = [
    ("check", "Can a check prove it right (tests pass, lint clean, a one-line answer)?", "fast"),
    ("decided", "Is the approach already written down (a spec or PLAN.md names the files)?", "workhorse"),
    ("hard", "Is it a design call, or a bug that two attempts didn't fix?", "frontier"),
]


def decide(answers):
    """answers: dict of question key -> bool. First yes wins; all no means plan with a strong model."""
    for key, _, lane in QUESTIONS:
        if answers.get(key):
            return lane
    return "strong"


def ask(stdin_input=input):
    answers = {}
    for key, q, lane in QUESTIONS:
        a = stdin_input(q + " [y/N] ").strip().lower()
        answers[key] = a.startswith("y")
        if answers[key]:
            break
    return answers


def describe(lane):
    d = LANES[lane]
    lines = ["Lane: %s  (%s)" % (lane.upper(), d["why"]),
             "  Claude Code: %s" % d["claude"],
             "  Codex:       %s" % d["codex"]]
    if "local" in d:
        lines.append("  Out of credit: %s" % d["local"])
    lines.append("  Start a fresh session for it, and give it the command that proves the work.")
    return "\n".join(lines)
