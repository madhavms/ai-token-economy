"""Where tke keeps its settings, and where the agents keep their logs."""
import json
import os
import sys
from pathlib import Path

DEFAULTS = {
    "claude_budget_usd": 500,        # monthly Claude credit
    "codex_budget_credits": 13000,   # monthly Codex credits
    "workdays": 22,                  # workdays per month for pacing
    "reserve_pct": 15,               # held back for the last week
    "big_context": 150000,           # a call with a prompt above this is "fat"
}


def config_dir():
    if os.environ.get("TKE_HOME"):
        return Path(os.environ["TKE_HOME"])
    if sys.platform == "win32" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "token-economy"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "token-economy"


def claude_home():
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))


def codex_home():
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))


def load():
    cfg = dict(DEFAULTS)
    try:
        with open(config_dir() / "config.json", encoding="utf-8") as fh:
            cfg.update(json.load(fh))
    except (OSError, ValueError):
        pass
    from . import prices
    prices.load_overrides(config_dir() / "prices.json")
    return cfg


def save(cfg):
    d = config_dir()
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "config.json", "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
    return d / "config.json"
