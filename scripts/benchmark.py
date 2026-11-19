"""Real-machine benchmark: start-up, memory, CPU, power draw, page loads, analytics, sync, LLM briefing & chat.

  uv run python scripts/benchmark.py            # writes bench/results/<stamp>.json + .md

Uses your real local database (read-only except one incremental sync = ~7 Garmin API requests).
Power is read from the battery gauge (InstantAmperage × Voltage) and is only meaningful on battery power.
Close the app before running (the benchmark starts its own server instance).
"""

from __future__ import annotations

import json
import os
import platform
import re
import resource
import shutil
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UV = shutil.which("uv") or str(Path.home() / ".local/bin/uv")
OUT = ROOT / "bench" / "results"
PORT = 8799


def sh(cmd: str) -> str:
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()


def rss_mb(pid: int | None) -> float | None:
    if not pid:
        return None
    v = sh(f"ps -o rss= -p {pid}")
    return round(int(v) / 1024, 1) if v.isdigit() else None


def cpu_seconds(pid: int) -> float:
    t = sh(f"ps -o time= -p {pid}")  # [[dd-]hh:]mm:ss.cc
    parts = [float(x) for x in re.split("[:-]", t)] if t else [0]
    s = 0.0
    for p in parts:
        s = s * 60 + p
    return s


def pgrep(pattern: str) -> int | None:
    """Matching PID with the largest RSS (skips thin wrappers such as `uv run`)."""
    pids = [int(x) for x in sh(f"pgrep -f '{pattern}'").split()]
    if not pids:
        return None
    return max(pids, key=lambda p: rss_mb(p) or 0)


# ------------------------------------------------------------------ power (battery gauge)

def battery_watts() -> float | None:
    raw = sh("ioreg -rn AppleSmartBattery")
    ext = re.search(r'"ExternalConnected" = (\w+)', raw)
    amp = re.search(r'"InstantAmperage" = (\d+)', raw)
    volt = re.search(r'"Voltage" = (\d+)', raw)
    if not (amp and volt) or (ext and ext.group(1) == "Yes"):
        return None
    a = int(amp.group(1))
    if a >= 2 ** 63:
        a -= 2 ** 64
    return round(-a * int(volt.group(1)) / 1e6, 2)


class PowerSampler:
    def __init__(self, interval: float = 1.0):
        self.interval, self.samples, self._stop = interval, [], threading.Event()

    def __enter__(self):
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()
        return self

    def _run(self):
        while not self._stop.is_set():
            w = battery_watts()
            if w is not None:
                self.samples.append(w)
            time.sleep(self.interval)

    def __exit__(self, *a):
        self._stop.set()
        self._t.join()

    @property
    def mean(self):
        return round(statistics.mean(self.samples), 1) if self.samples else None

    @property
    def peak(self):
        return round(max(self.samples), 1) if self.samples else None


def du_mb(p: Path) -> float | None:
    if not p.exists():
        return None
    out = sh(f"du -skL '{p}'").split()
    return round(int(out[0]) / 1024, 1) if out else None


# ------------------------------------------------------------------ phases

def phase_baseline(r):
    print("• baseline power (15 s, nothing running)…", flush=True)
    sh("ollama stop qwen3:4b-instruct")
    time.sleep(3)
    with PowerSampler() as p:
        time.sleep(15)
    r["power_baseline_w"] = p.mean


def phase_app(r):
    print("• app cold start + idle…", flush=True)
    t0 = time.time()
    proc = subprocess.Popen([UV, "run", "garmin-app", "run", "--server-only", "--no-sync", "--port", str(PORT)],
                            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{PORT}/"
    while time.time() - t0 < 60:
        try:
            urllib.request.urlopen(url, timeout=1)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.1)
    r["app_cold_start_s"] = round(time.time() - t0, 2)
    time.sleep(8)  # warm-up thread builds analytics bundle
    pid = pgrep(f"garmin-app run --server-only --no-sync --port {PORT}")
    r["app_rss_after_start_mb"] = rss_mb(pid)
    c0 = cpu_seconds(pid)
    with PowerSampler() as p:
        time.sleep(20)
    r["app_idle_cpu_pct"] = round(100 * (cpu_seconds(pid) - c0) / 20, 1)
    r["power_app_idle_w"] = p.mean
    return proc, pid


def phase_pages(r, pid):
    print("• page loads (headless Chromium, end-to-end incl. callbacks)…", flush=True)
    script = ROOT / "bench" / "_pages.py"
    script.write_text(f'''
import json, time
from playwright.sync_api import sync_playwright
pages = ["/", "/sleep", "/recovery", "/stress", "/muscle", "/training", "/running", "/longevity", "/journal",
         "/explore", "/settings"]
res = {{}}
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(viewport={{"width": 1360, "height": 900}})
    pg.goto("http://127.0.0.1:{PORT}/"); pg.wait_for_selector(".section", timeout=60000)
    for rep in range(3):
        for path in pages:
            t = time.time()
            pg.goto("http://127.0.0.1:{PORT}" + path)
            pg.wait_for_selector(".content .section", timeout=60000)
            pg.wait_for_load_state("networkidle")
            res.setdefault(path, []).append(round(time.time() - t, 3))
    b.close()
print(json.dumps(res))
''')
    with PowerSampler() as p:
        out = subprocess.run([UV, "tool", "run", "--from", "playwright", "python", str(script)], capture_output=True,
                             text=True, cwd=ROOT)
    script.unlink(missing_ok=True)
    try:
        res = json.loads(out.stdout.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        r["pages_error"] = out.stderr[-500:]
        return
    r["page_load_s"] = {k: round(statistics.median(v), 2) for k, v in res.items()}
    allv = [x for v in res.values() for x in v]
    r["page_load_median_s"] = round(statistics.median(allv), 2)
    r["page_load_p90_s"] = round(sorted(allv)[int(0.9 * (len(allv) - 1))], 2)
    r["app_rss_after_pages_mb"] = rss_mb(pid)
    r["power_browsing_w"] = p.mean


def phase_analytics(r):
    print("• analytics bundle + Chronos forecasts…", flush=True)
    sys.path.insert(0, str(ROOT / "src"))
    from garmin_insights.analytics import forecast
    from garmin_insights.analytics.frame import build

    times = []
    for _ in range(3):
        t = time.time()
        b = build()
        times.append(time.time() - t)
    r["analytics_build_s"] = round(statistics.median(times), 2)
    r["history_days"] = int(b.daily["has_data"].sum())
    rss0 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 / 1024
    with PowerSampler() as p:
        t = time.time()
        n = forecast.compute_all(b.daily)
        r["forecast_first_s"] = round(time.time() - t, 2)
        t = time.time()
        forecast.compute_all(b.daily)
        r["forecast_warm_s"] = round(time.time() - t, 2)
    r["forecast_values"] = n
    r["forecast_extra_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 / 1024 - rss0, 1)
    r["power_forecast_w"] = p.mean


def phase_sync(r):
    print("• incremental Garmin sync…", flush=True)
    t = time.time()
    with PowerSampler() as p:
        subprocess.run([UV, "run", "garmin-app", "sync"], cwd=ROOT, capture_output=True)
    r["sync_incremental_s"] = round(time.time() - t, 1)
    from garmin_insights.storage import db

    log = db.get_state("api_call_log", []) or []
    r["sync_api_requests"] = log[-1]["total"] if log else None
    r["power_sync_w"] = p.mean


def phase_llm(r):
    print("• LLM briefing (cold + warm) & chat…", flush=True)
    from garmin_insights import config
    from garmin_insights.analytics.frame import build
    from garmin_insights.recommend import briefing, llm, tools

    model = config.Settings.load().llm_chat_model
    llm.ensure_ollama()
    sh(f"ollama stop {model}")
    time.sleep(2)
    facts = briefing.morning(build(), use_llm=False).facts
    msgs = [{"role": "system", "content": llm.BRIEFING_SYSTEM}, {"role": "user", "content": json.dumps(facts, default=str)}]

    def one():
        t = time.time()
        resp = llm._client().chat(model=model, messages=msgs, think=False, keep_alive="10m",
                                  options={"temperature": 0.4, "num_ctx": 4096, "num_predict": 320})
        wall = time.time() - t
        return {"wall_s": round(wall, 2), "load_s": round((resp.load_duration or 0) / 1e9, 2),
                "prompt_tokens": resp.prompt_eval_count, "prompt_tok_s": round(resp.prompt_eval_count / (resp.prompt_eval_duration / 1e9), 1),
                "gen_tokens": resp.eval_count, "gen_tok_s": round(resp.eval_count / (resp.eval_duration / 1e9), 1)}

    with PowerSampler(0.5) as p_cold:
        cold = one()
    r["briefing_cold"] = cold
    r["power_llm_cold_w"] = p_cold.mean
    warm = []
    with PowerSampler(0.5) as p_warm:
        for _ in range(3):
            warm.append(one())
    r["briefing_warm_s_median"] = round(statistics.median(w["wall_s"] for w in warm), 2)
    r["gen_tok_s_median"] = round(statistics.median(w["gen_tok_s"] for w in warm), 1)
    r["prompt_tok_s_cold"] = cold["prompt_tok_s"]  # warm runs reuse Ollama's prompt cache → not comparable
    r["power_llm_generation_w"] = p_warm.mean
    r["power_llm_generation_peak_w"] = p_warm.peak
    runner = pgrep("llama-server") or pgrep("ollama runner")
    r["ollama_runner_rss_mb"] = rss_mb(runner)
    r["ollama_ps"] = sh("ollama ps").splitlines()[1:] if sh("ollama ps") else []
    qs = ["What was my average resting heart rate over the last 7 days?",
          "Which muscle did I train least in the last 28 days?",
          "Was my average sleep duration higher in the last 7 days or in the 7 days before that? Give both averages."]
    chat = []
    with PowerSampler(0.5) as p_chat:
        for q in qs:
            t = time.time()
            res = llm.chat(q, [], model, tools)
            chat.append({"q": q, "s": round(time.time() - t, 2), "tools": [x["tool"] for x in res["tool_log"]],
                         "status": res["status"]})
    r["chat"] = chat
    r["chat_median_s"] = round(statistics.median(c["s"] for c in chat), 2)
    r["power_chat_w"] = p_chat.mean
    r["ollama_runner_rss_after_chat_mb"] = rss_mb(pgrep("llama-server") or pgrep("ollama runner"))


def phase_disk(r):
    from garmin_insights import config

    r["disk_mb"] = {
        "python_env (.venv, incl. PyTorch)": du_mb(ROOT / ".venv"),
        "app data (~/.garmin_insights)": du_mb(config.data_dir()),
        "Ollama models": du_mb(Path.home() / ".ollama" / "models"),
        "Chronos model cache": du_mb(Path.home() / ".cache" / "huggingface" / "hub" / "models--amazon--chronos-bolt-tiny"),
    }


def report(r: dict) -> str:
    L = [f"Measured {r['at']} on **{r['machine']}**, macOS {r['macos']}, {'battery' if r['on_battery'] else 'AC power'}; "
         f"{r.get('history_days', '?')} days of history.", "",
         "| What | Result |", "|---|---|",
         f"| App cold start (server ready) | {r['app_cold_start_s']} s |",
         f"| App memory (after start / after browsing all pages) | {r['app_rss_after_start_mb']} MB / {r.get('app_rss_after_pages_mb')} MB |",
         f"| App CPU when idle | {r['app_idle_cpu_pct']} % of one core |",
         f"| Page load, end-to-end (median / p90) | {r.get('page_load_median_s')} s / {r.get('page_load_p90_s')} s |",
         f"| Analytics rebuild (all metrics) | {r['analytics_build_s']} s |",
         f"| Chronos-Bolt-tiny forecasts ({r['forecast_values']} values; first run / warm) | {r['forecast_first_s']} s / {r['forecast_warm_s']} s (+{r['forecast_extra_rss_mb']} MB) |",
         f"| Incremental Garmin sync | {r['sync_incremental_s']} s, {r['sync_api_requests']} API requests |",
         f"| Briefing, cold (model load + generation) | {r['briefing_cold']['wall_s']} s (load {r['briefing_cold']['load_s']} s) |",
         f"| Briefing, warm (median of 3) | {r['briefing_warm_s_median']} s |",
         f"| Qwen3 4B Instruct speed | {r['gen_tok_s_median']} tokens/s generation · {r['prompt_tok_s_cold']} tokens/s prompt processing (cold) |",
         f"| Chat answer incl. tool calls (median of 3) | {r['chat_median_s']} s |",
         f"| Ollama memory with model loaded | {r['ollama_runner_rss_mb']} MB (after chat: {r.get('ollama_runner_rss_after_chat_mb')} MB; freed after 10 min idle) |",
         ]
    if r.get("power_baseline_w"):
        L += [f"| Power draw: baseline / app idle / browsing / LLM generating (peak) / chat | "
              f"{r['power_baseline_w']} W / {r['power_app_idle_w']} W / {r.get('power_browsing_w')} W / "
              f"{r['power_llm_generation_w']} W ({r['power_llm_generation_peak_w']} W) / {r['power_chat_w']} W |"]
    disk = " · ".join(f"{k}: {v / 1024:.1f} GB" if v and v > 1024 else f"{k}: {v} MB" for k, v in r["disk_mb"].items())
    L += [f"| Disk | {disk} |"]
    return "\n".join(L) + "\n"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    r = {"at": datetime.now().isoformat(timespec="seconds"),
         "machine": sh("sysctl -n hw.model") + " · " + sh("sysctl -n machdep.cpu.brand_string") + f" · {int(sh('sysctl -n hw.memsize')) // 2**30} GB",
         "macos": platform.mac_ver()[0], "on_battery": battery_watts() is not None}
    phase_baseline(r)
    proc, pid = phase_app(r)
    try:
        phase_pages(r, pid)
    finally:
        pass
    phase_analytics(r)
    phase_sync(r)
    phase_llm(r)
    proc.terminate()
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        proc.kill()
    sh(f"pkill -f 'garmin-app run --server-only --no-sync --port {PORT}'")
    phase_disk(r)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (OUT / f"{stamp}.json").write_text(json.dumps(r, indent=1, default=str))
    md = report(r)
    (OUT / f"{stamp}.md").write_text(md)
    print("\n" + md)
    print(json.dumps({k: r[k] for k in ("page_load_s", "chat") if k in r}, indent=1))
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONWARNINGS", "ignore")
    sys.exit(main())
