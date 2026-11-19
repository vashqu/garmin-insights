"""Local LLM via Ollama: morning briefing (Qwen3 4B Instruct) with a number-faithfulness guard, and an
'ask your data' chat (Qwen3 4B) that answers through read-only tools."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import subprocess
import time
from typing import Any

from garmin_insights.storage import db

log = logging.getLogger(__name__)
OLLAMA_URL = "http://127.0.0.1:11434"
ALWAYS_OK_NUMBERS = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 14, 15, 20, 24, 30, 45, 48, 60, 100}
NUM_RE = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:[.,]\d+)?")


# ------------------------------------------------------------------ ollama process

def _client(timeout: float = 90):
    import ollama

    return ollama.Client(host=OLLAMA_URL, timeout=timeout)


def ollama_status() -> dict:
    try:
        models = [m.model for m in _client(5).list().models]
        return {"running": True, "models": models}
    except Exception as e:  # noqa: BLE001
        return {"running": False, "models": [], "error": str(e)}


def ensure_ollama() -> bool:
    """Start `ollama serve` in the background if it's installed but not running."""
    if ollama_status()["running"]:
        return True
    exe = shutil.which("ollama") or "/opt/homebrew/bin/ollama"
    if not shutil.which(exe) and not __import__("os").path.exists(exe):
        return False
    try:
        subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return False
    for _ in range(20):
        time.sleep(0.5)
        if ollama_status()["running"]:
            return True
    return False


def pull_model(name: str) -> bool:
    try:
        _client(3600).pull(name)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("pull %s failed: %s", name, e)
        return False


# ------------------------------------------------------------------ number guard

def _numbers_in(obj: Any) -> set[float]:
    out: set[float] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            out |= _numbers_in(k) | _numbers_in(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out |= _numbers_in(v)
    elif isinstance(obj, bool) or obj is None:
        pass
    elif isinstance(obj, (int, float)):
        out.add(float(obj))
    elif isinstance(obj, str):
        for m in NUM_RE.findall(obj):
            try:
                out.add(float(m.replace(",", ".")))
            except ValueError:
                pass
    return out


def unsupported_numbers(text: str, source: Any, extra_ok: set[float] | None = None) -> list[str]:
    """Numbers in `text` that don't match any number in `source` (with rounding tolerance)."""
    allowed = _numbers_in(source) | {float(x) for x in ALWAYS_OK_NUMBERS} | (extra_ok or set())
    bad = []
    for m in NUM_RE.findall(text):
        try:
            v = abs(float(m.replace(",", ".")))
        except ValueError:
            continue
        ok = any(abs(v - abs(a)) <= max(0.051, 0.006 * abs(a)) or round(abs(a)) == v for a in allowed)
        if not ok:
            bad.append(m)
    return bad


def _strip_think(t: str) -> str:
    t = re.sub(r"<think>.*?</think>", "", t or "", flags=re.S)
    if "</think>" in t:  # reasoning without an opening tag (thinking-only models)
        t = t.split("</think>")[-1]
    return t.strip()


# ------------------------------------------------------------------ briefing

BRIEFING_SYSTEM = """You write a short morning health & training briefing for one person, based ONLY on the JSON facts provided.
Rules:
- 80 to 140 words, friendly and direct, second person ("you"). Plain text, no headings, no lists, no emojis.
- Start with how recovery looks today, then what to do today (follow the recommendations' priorities), then one habit tip.
- When a "verdict" field is given, use it as-is; never compare numbers yourself.
- Use only numbers that appear in the JSON. Round sensibly. Never invent values, dates or targets.
- Never diagnose illness or give medical advice; if an illness signal is present, say signals suggest taking it easy.
- Do not mention JSON, data fields or the app."""


def briefing_cache_key(facts: dict, model: str) -> str:
    return "brief:" + hashlib.sha256((model + json.dumps(facts, sort_keys=True, default=str)).encode()).hexdigest()[:24]


def raw_briefing(facts: dict, model: str, temperature: float = 0.4, seed: int | None = None) -> str:
    """One model call with the production prompt and options (no guard, no cache). Used by the app and evals."""
    msgs = [{"role": "system", "content": BRIEFING_SYSTEM},
            {"role": "user", "content": json.dumps(facts, default=str)}]
    opts = {"temperature": temperature, "num_ctx": 4096, "num_predict": 320}
    if seed is not None:
        opts["seed"] = seed
    r = _client().chat(model=model, messages=msgs, think=False, keep_alive="10m", options=opts)
    return _strip_think(r.message.content)


def generate_briefing(facts: dict, model: str, use_cache: bool = True) -> tuple[str | None, str]:
    """Returns (text or None, status) where status ∈ {'llm','cached','guard_failed','offline','error'}."""
    key = briefing_cache_key(facts, model)
    if use_cache:
        row = db.query("SELECT text FROM llm_cache WHERE key=?", (key,))
        if row:
            return row[0]["text"], "cached"
    if not ensure_ollama():
        return None, "offline"
    for temp in (0.4, 0.0):
        try:
            text = raw_briefing(facts, model, temp)
        except Exception as e:  # noqa: BLE001
            log.warning("briefing LLM failed: %s", e)
            return None, "error"
        bad = unsupported_numbers(text, facts)
        if not bad and len(text.split()) >= 40:
            db.upsert("llm_cache", [{"key": key, "kind": "briefing", "text": text, "created": db.now_iso()}])
            return text, "llm"
        log.info("briefing guard rejected numbers %s (temp %s)", bad, temp)
    return None, "guard_failed"


# ------------------------------------------------------------------ chat with tools

CHAT_SYSTEM = """You are a careful assistant answering questions about ONE person's own Garmin health, sleep and training data
(and their Strong strength-training log). Use the tools to fetch data before answering — never guess numbers.
Dates are ISO (YYYY-MM-DD). Today is {today}. Date anchors: last 7 days = {d7}..{today}; last 4 weeks / 28 days =
{d28}..{today}; last 90 days = {d90}..{today}; this month starts {m0}.
Keep answers short (≤150 words) and cite the numbers you used exactly as returned by tools (don't compute new
numbers like unit conversions unless asked). Say clearly when data is missing. Never claim statistical significance.
No medical diagnoses. Available metric names: {metrics}."""


def chat(question: str, history: list[dict], model: str, tools_mod) -> dict:
    """Run a tool-using chat turn. Returns {'answer', 'tool_log', 'status'}."""
    from datetime import date

    if not ensure_ollama():
        return {"answer": "The local AI model isn't running. Install/start Ollama in Settings.", "tool_log": [],
                "status": "offline"}
    funcs = tools_mod.TOOLS
    from datetime import timedelta

    t = date.today()
    sysmsg = CHAT_SYSTEM.format(today=t.isoformat(), d7=(t - timedelta(days=6)).isoformat(),
                                d28=(t - timedelta(days=27)).isoformat(), d90=(t - timedelta(days=89)).isoformat(),
                                m0=t.replace(day=1).isoformat(), metrics=", ".join(tools_mod.metric_names()))
    msgs = [{"role": "system", "content": sysmsg}] + history[-6:] + [{"role": "user", "content": question}]
    log_ = []
    try:
        for _ in range(6):
            r = _client(180).chat(model=model, messages=msgs, tools=list(funcs.values()), think=False,
                                  keep_alive="10m", options={"temperature": 0.2, "num_ctx": 6144})
            msg = r.message
            if not msg.tool_calls:
                answer = _strip_think(msg.content)
                bad = unsupported_numbers(answer, [t["result"] for t in log_] + [question])
                status = "ok" if not bad else "unverified"
                return {"answer": answer, "tool_log": log_, "status": status, "unverified": bad}
            msgs.append(msg)
            for tc in msg.tool_calls:
                fn = funcs.get(tc.function.name)
                try:
                    res = fn(**(tc.function.arguments or {})) if fn else {"error": "unknown tool"}
                except Exception as e:  # noqa: BLE001
                    res = {"error": str(e)}
                log_.append({"tool": tc.function.name, "args": tc.function.arguments, "result": res})
                msgs.append({"role": "tool", "content": json.dumps(res, default=str)[:6000],
                             "tool_name": tc.function.name})
        return {"answer": "I couldn't finish that within the tool-call limit — try a more specific question.",
                "tool_log": log_, "status": "limit"}
    except Exception as e:  # noqa: BLE001
        log.warning("chat failed: %s", e)
        return {"answer": f"The local model failed: {e}", "tool_log": log_, "status": "error"}
