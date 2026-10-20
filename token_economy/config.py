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
    "codex_speed": "auto",           # standard | fast | ultrafast | auto (read service_tier from ~/.codex/config.toml)
    "codex_billing": "credits",      # credits (purchased / pay-as-you-go) | plan (included subscription usage)
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


def codex_speed(cfg=None):
    """(speed, multiplier, where it came from). Codex logs don't record speed per turn, so this is a setting."""
    from . import prices
    cfg = cfg or load()
    speed, src = str(cfg.get("codex_speed", "auto")).lower(), "tke config"
    if speed == "auto":
        speed, src = "standard", "default"
        try:
            import re
            text = (codex_home() / "config.toml").read_text(encoding="utf-8")
            head = text.split("\n[", 1)[0]
            m = re.search(r'(?m)^\s*service_tier\s*=\s*"([^"]+)"', head)
            if m:
                speed, src = m.group(1).lower(), "service_tier in ~/.codex/config.toml"
        except OSError:
            pass
    table = prices.CODEX_SPEED.get(cfg.get("codex_billing", "credits"), prices.CODEX_SPEED["credits"])
    return speed, table.get(speed, 1.0), src


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
