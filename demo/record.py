"""Record the README demo: screenshots and GIFs of `tke ui` and the CLI, on made-up logs only.

Runs anywhere Playwright for Python and ffmpeg are installed; demo/Dockerfile packages both so the
recording is the same on every machine:

    docker build -f demo/Dockerfile -t tke-demo . && docker run --rm -v "$PWD/docs/media:/out" tke-demo

Steps: write a made-up month of logs (demo/make_logs.py), start `tke ui` on them, tour the dashboard with
Playwright (video + screenshots), run the CLI on the same logs and play its real output back in a
terminal-styled page (video), then turn both videos into small GIFs with ffmpeg.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "demo"))
import make_logs  # noqa: E402

W, H = 1280, 800


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def env_for(data):
    e = dict(os.environ, CLAUDE_CONFIG_DIR=str(data / "claude"), CODEX_HOME=str(data / "codex"), TKE_HOME=str(data / "tke"),
             COLUMNS="100", NO_COLOR="1")
    return e


def tke(data, *args):
    r = subprocess.run([sys.executable, str(ROOT / "tke.py")] + list(args), env=env_for(data),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=120)
    return r.stdout.rstrip()


def to_gif(webm, gif, width=960, fps=10, start=0.0):
    """Two-pass palette GIF: small and sharp."""
    pal = gif.with_suffix(".png")
    vf = "fps=%d,scale=%d:-1:flags=lanczos" % (fps, width)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(start), "-i", str(webm), "-vf", vf + ",palettegen=stats_mode=diff",
                    str(pal)], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(start), "-i", str(webm), "-i", str(pal), "-lavfi",
                    vf + " [x]; [x][1:v] paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle", str(gif)], check=True)
    pal.unlink()


TERM_PAGE = """<!doctype html><meta charset="utf-8"><title>tke</title>
<style>
:root{--bg:#0f1720;--fg:#d8e1ea;--dim:#7d8b99;--acc:#7cc4a4}
html,body{margin:0;height:100%%;background:var(--bg)}
.win{margin:28px;border-radius:10px;background:#16212c;box-shadow:0 10px 40px #0008;height:calc(100%% - 56px);overflow:hidden}
.bar{height:30px;background:#1d2a37;border-radius:10px 10px 0 0;display:flex;gap:8px;align-items:center;padding-left:14px}
.bar i{width:12px;height:12px;border-radius:50%%;background:#3a4856;display:block}
pre{margin:0;padding:18px 22px;color:var(--fg);font:15px/1.45 "DejaVu Sans Mono",Menlo,monospace;white-space:pre-wrap}
.p{color:var(--acc)} .c{color:#fff}
</style>
<div class="win"><div class="bar"><i></i><i></i><i></i></div><pre id="t"></pre></div>
<script>
const steps = %s;
const t = document.getElementById("t");
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  for (const s of steps) {
    t.innerHTML = "";
    const line = document.createElement("span"); line.className = "p"; line.textContent = "$ "; t.appendChild(line);
    const c = document.createElement("span"); c.className = "c"; t.appendChild(c);
    for (const ch of s.cmd) { c.textContent += ch; await sleep(55); }
    await sleep(450);
    t.appendChild(document.createTextNode("\\n" + s.out + "\\n"));
    await sleep(s.hold);
  }
  document.title = "done";
})();
</script>"""


def record(out_dir):
    from playwright.sync_api import sync_playwright
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="tke-rec-"))
    data = work / "data"
    make_logs.main(str(data))

    # ---- CLI output, captured for real from the made-up logs
    steps = [{"cmd": "tke status", "out": tke(data, "status"), "hold": 1800},
             {"cmd": "tke report", "out": "\n".join(tke(data, "report").splitlines()[:30]), "hold": 4200},
             {"cmd": "tke why week --brief", "out": "\n".join(tke(data, "why", "week", "--brief").splitlines()[:30]),
              "hold": 4500}]
    (out / "cli-sample.txt").write_text("\n\n".join("$ %s\n%s" % (s["cmd"], s["out"]) for s in steps) + "\n", encoding="utf-8")
    term = work / "term.html"
    term.write_text(TERM_PAGE % json.dumps(steps), encoding="utf-8")

    port = free_port()
    ui = subprocess.Popen([sys.executable, str(ROOT / "tke.py"), "ui", "--port", str(port), "--no-browser"],
                          env=env_for(data), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), 0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        url = "http://127.0.0.1:%d/" % port
        with sync_playwright() as p:
            browser = p.chromium.launch()
            # dashboard tour (light), with video
            ctx = browser.new_context(viewport={"width": W, "height": H}, color_scheme="light",
                                      record_video_dir=str(work / "v-ui"), record_video_size={"width": W, "height": H})
            page = ctx.new_page()
            shots = []
            for view, wait in (("overview", 2600), ("sessions", 2200), ("session", 3200), ("causes", 2600),
                               ("subagents", 2400), ("setup", 2400), ("planner", 2600)):
                if view == "session":
                    page.goto(url + "#/sessions")
                    page.wait_for_load_state("networkidle")
                    page.locator("tbody tr").first.click()
                else:
                    page.goto(url + "#/" + view)
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(900)
                shot = out / ("ui-%s.png" % view)
                page.screenshot(path=str(shot))
                shots.append(shot.name)
                page.mouse.wheel(0, 380)
                page.wait_for_timeout(wait - 900)
            video = page.video.path()
            ctx.close()
            shutil.copy(video, work / "ui.webm")
            # one dark screenshot of the overview
            dark = browser.new_context(viewport={"width": W, "height": H}, color_scheme="dark")
            dp = dark.new_page()
            dp.goto(url + "#/overview")
            dp.wait_for_load_state("networkidle")
            dp.wait_for_timeout(900)
            dp.screenshot(path=str(out / "ui-overview-dark.png"))
            dark.close()
            # terminal playback, with video
            tctx = browser.new_context(viewport={"width": 1100, "height": 720}, record_video_dir=str(work / "v-cli"),
                                       record_video_size={"width": 1100, "height": 720})
            tp = tctx.new_page()
            tp.goto(term.as_uri())
            tp.wait_for_function("document.title === 'done'", timeout=120000)
            tp.wait_for_timeout(600)
            cvideo = tp.video.path()
            tctx.close()
            shutil.copy(cvideo, work / "cli.webm")
            browser.close()
    finally:
        ui.terminate()
    to_gif(work / "ui.webm", out / "tke-ui.gif", width=880, fps=6, start=0.6)
    to_gif(work / "cli.webm", out / "tke-cli.gif", width=880, fps=10, start=0.3)
    for f in sorted(out.iterdir()):
        print("%-26s %7.0f KB" % (f.name, f.stat().st_size / 1024))
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    record(sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "docs" / "media"))
