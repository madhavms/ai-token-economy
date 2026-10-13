"""tke bench: how long does a local model take to read an agent-sized prompt on this machine?

Builds a ~11k-token prompt from source files (about Claude Code's smallest starting prompt), calls a
local Ollama server with the GPU allowed and with it switched off, and prints Ollama's own timers.
"""
import json
import time
import urllib.request
from pathlib import Path

EXTS = (".py", ".js", ".ts", ".java", ".go", ".rs", ".cs", ".kt", ".rb", ".php", ".cpp", ".c", ".md")


def build_prompt(source, chars=44000):
    text = ""
    for p in sorted(Path(source).rglob("*")):
        if p.suffix in EXTS and p.is_file() and not any(part.startswith(".") or part in ("node_modules", "venv")
                                                        for part in p.parts):
            try:
                text += "\n\n# FILE: %s\n%s" % (p.name, p.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                continue
            if len(text) > chars:
                break
    return text[:chars] + "\n\nIn three short bullets, say what this code does."


def one(model, prompt, cpu_only, host="http://127.0.0.1:11434"):
    opts = {"num_ctx": 16384, "temperature": 0, "num_predict": 200}
    if cpu_only:
        opts["num_gpu"] = 0
    body = json.dumps({"model": model, "prompt": prompt, "stream": False, "options": opts, "keep_alive": "30s"}).encode()
    req = urllib.request.Request(host + "/api/generate", body, {"Content-Type": "application/json"})
    r = json.load(urllib.request.urlopen(req, timeout=1800))
    pe, pd = r["prompt_eval_count"], r["prompt_eval_duration"] / 1e9
    ec, ed = r["eval_count"], r["eval_duration"] / 1e9
    return pe, pd, pe / pd, ec / ed


def run(source, models):
    prompt = build_prompt(source)
    if len(prompt) < 20000:
        print("note: only %d characters of source found in %s; the prompt is smaller than an agent's" % (len(prompt), source))
    for m in models:
        for cpu in (False, True):
            try:
                pe, pd, rs, ws = one(m, prompt, cpu)
            except Exception as e:  # Ollama not running, model not pulled, ...
                print("%s %s: %s" % (m, "cpu" if cpu else "gpu", e))
                continue
            print("%-16s %-8s reads %6.0f tok/s · %6.1f s for %d tokens · writes %5.1f tok/s"
                  % (m, "CPU only" if cpu else "GPU", rs, pd, pe, ws))
    print("Agent harnesses start around 11k tokens a turn: if reading takes over ~30 s here, use the local "
          "model for short questions only.")
