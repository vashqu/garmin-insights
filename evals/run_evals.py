"""Eval runner for the local Qwen models.

  uv run python evals/run_evals.py                       # both suites, models from settings
  uv run python evals/run_evals.py --suite briefing --briefing-models qwen3:4b-instruct,<other-model> --repeats 3
  uv run python evals/run_evals.py --suite chat --repeats 2

Writes evals/results/<timestamp>.json (every output + check) and .md (metrics tables).
Chat cases run against a fresh deterministic synthetic DB in a temp dir — never your personal data.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*")
DOMAIN_OK = {"hrv", "rmssd", "bpm", "spo2", "delts", "delt", "glutes", "quads", "hamstrings", "triceps", "biceps",
             "lats", "abs", "trimp", "vo2max", "km", "min", "mins", "workout", "workouts", "ok", "okay", "nap",
             "naps", "1rm", "e1rm", "rpe", "midday", "mid", "afternoon", "taper", "tempo", "intervals", "warmup",
             "weekday", "weekdays", "bicep", "tricep", "jog", "jogging", "jogs", "cooldown", "rehydrate", "today's", "you're", "you've", "it's", "don't", "let's", "that's", "isn't",
             "aren't", "prioritise", "prioritize", "prioritised", "prioritized", "prioritising", "prioritizing", "here's", "there's", "you'll", "won't", "can't", "body's", "pre", "re", "s", "x", "h"}


def load_dict() -> set[str]:
    p = Path("/usr/share/dict/words")
    return {w.strip().lower() for w in p.read_text().splitlines()} if p.exists() else set()


DICT = load_dict()


def unknown_words(text: str, facts: dict) -> list[str]:
    if not DICT:
        return []
    fact_words = {w.lower() for w in WORD.findall(json.dumps(facts))}
    bad = []
    for w in WORD.findall(text):
        lw = w.lower().replace("’", "'").strip("'-")
        if not lw or lw in DICT or lw in DOMAIN_OK or lw in fact_words:
            continue
        parts = re.split(r"[-']", lw)
        if all(p in DICT or p in DOMAIN_OK or p in fact_words or not p for p in parts):
            continue
        stems = []
        for suf, reps in (("ies", ["y"]), ("es", ["", "e"]), ("s", [""]), ("ed", ["", "e"]), ("ing", ["", "e"]),
                          ("ly", [""]), ("er", ["", "e"]), ("est", ["", "e"]), ("'s", [""])):
            if lw.endswith(suf):
                stems += [lw[: -len(suf)] + r for r in reps]
        us = lw.replace("isation", "ization").replace("ise", "ize").replace("our", "or").replace("tre", "ter")
        stems += [us] + [us[: -len(x)] for x in ("s", "d", "ing") if us.endswith(x)]
        if any(s in DICT for s in stems if len(s) > 2):
            continue
        bad.append(w)
    return bad


# ------------------------------------------------------------------ briefing

def check_briefing(text: str, case: dict) -> dict:
    from garmin_insights.recommend.llm import unsupported_numbers

    t = text.lower()
    words = len(text.split())
    unsupported = unsupported_numbers(text, case["facts"])
    inc_fail = [g for g in case.get("must_include", []) if not any(x.lower() in t for x in g)]
    exc_hit = [x for x in case.get("must_exclude", []) if x.lower() in t]
    fmt_bad = []
    if re.search(r"^\s*([-*•]|\d+\.)\s", text, re.M):
        fmt_bad.append("list")
    if re.search(r"^\s*#", text, re.M) or "**" in text:
        fmt_bad.append("markdown")
    if EMOJI.search(text):
        fmt_bad.append("emoji")
    if "json" in t:
        fmt_bad.append("mentions JSON")
    first_rec = any(x.lower() in t for x in case.get("first_rec_terms", [])) if case.get("first_rec_terms") else True
    typos = unknown_words(text, case["facts"])
    checks = {
        "numbers_faithful": not unsupported,
        "length_ok": 60 <= words <= 170,
        "format_ok": not fmt_bad,
        "includes_ok": not inc_fail,
        "excludes_ok": not exc_hit,
        "second_person": bool(re.search(r"\byou(r|'re|’re)?\b", t)),
        "top_rec_reflected": first_rec,
    }
    return {
        "checks": checks, "passed": all(checks.values()),
        "length_strict_80_140": 80 <= words <= 140, "words": words, "unsupported_numbers": unsupported,
        "missing_groups": inc_fail, "forbidden_hits": exc_hit, "format_issues": fmt_bad,
        "unknown_words": typos,
    }


def run_briefing(models: list[str], repeats: int) -> dict:
    from garmin_insights.recommend import llm

    cases = json.loads((HERE / "briefing_cases.json").read_text())["cases"]
    out = {}
    for model in models:
        llm.raw_briefing(cases[0]["facts"], model, 0.4, seed=0)  # warm-up (load model)
        rows = []
        for c in cases:
            for r in range(repeats):
                t0 = time.time()
                text = llm.raw_briefing(c["facts"], model, 0.4, seed=1000 + r)
                dt = time.time() - t0
                res = check_briefing(text, c)
                retry = None
                if not res["checks"]["numbers_faithful"] or res["words"] < 40:  # production retry path (temp 0)
                    t1 = time.time()
                    text2 = llm.raw_briefing(c["facts"], model, 0.0, seed=1000 + r)
                    retry = {"text": text2, "seconds": round(time.time() - t1, 2), **check_briefing(text2, c)}
                served = "llm" if res["checks"]["numbers_faithful"] and res["words"] >= 40 else (
                    "llm_retry" if retry and retry["checks"]["numbers_faithful"] and retry["words"] >= 40 else "template")
                rows.append({"case": c["id"], "repeat": r, "seconds": round(dt, 2), "text": text, **res,
                             "retry": retry, "served": served})
                mark = "PASS" if res["passed"] else "FAIL " + ",".join(k for k, v in res["checks"].items() if not v)
                print(f"  [{model}] {c['id']:<22} #{r} {dt:5.1f}s {res['words']:>3}w  {mark}", flush=True)
        out[model] = rows
    return out


# ------------------------------------------------------------------ chat

def run_chat(model: str, repeats: int) -> list[dict]:
    from garmin_insights.analytics import frame
    from garmin_insights.recommend import llm, tools
    from garmin_insights.storage import db

    tmp = tempfile.mkdtemp(prefix="gi-eval-")
    os.environ["GI_DATA_DIR"] = tmp
    db.reset_connections()
    frame._cache.clear()
    from garmin_insights import demo

    demo.generate(200, seed=7)
    from chat_cases import build_cases

    b = frame.get_bundle()
    cases = build_cases(b)
    llm.chat("hello", [], model, tools)  # warm-up
    rows = []
    for c in cases:
        for r in range(repeats):
            t0 = time.time()
            res = llm.chat(c["q"], [], model, tools)
            dt = time.time() - t0
            ans = res["answer"]
            ok, detail = c["check"](ans, c["truth"])
            used = [x["tool"] for x in res["tool_log"]]
            tool_ok = (not c["tools"] and not used) or (not c["tools"]) or any(u in c["tools"] for u in used)
            rows.append({"case": c["id"], "repeat": r, "question": c["q"], "seconds": round(dt, 2), "answer": ans,
                         "correct": bool(ok), "detail": detail, "truth": c["truth"], "tools_used": used,
                         "expected_tools": c["tools"], "tool_ok": bool(tool_ok),
                         "faithful": res["status"] == "ok", "unverified": res.get("unverified", []),
                         "status": res["status"], "n_tool_calls": len(used)})
            print(f"  [{model}] {c['id']:<22} #{r} {dt:5.1f}s tools={used} {'PASS' if ok else 'FAIL'} "
                  f"{'' if res['status'] == 'ok' else '(unverified: ' + ','.join(res.get('unverified', [])) + ')'}",
                  flush=True)
    return rows


# ------------------------------------------------------------------ report

def pct(xs) -> str:
    xs = list(xs)
    return f"{100 * sum(xs) / len(xs):.0f}%" if xs else "–"


def p90(xs):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(0.9 * (len(xs) - 1))))] if xs else 0


def report(results: dict) -> str:
    L = [f"# Local model evals — {results['started']}", ""]
    if "briefing" in results:
        L += ["## Morning briefing", "",
              "| Model | Runs | **Overall pass** | Numbers faithful | Includes required | No forbidden | Top rec reflected "
              "| Format | Length 60–170 | Length 80–140 (spec) | Typo-free | Served by LLM (after guard+retry) "
              "| Median s | p90 s |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for m, rows in results["briefing"].items():
            ch = lambda k, rows=rows: (r["checks"][k] for r in rows)  # noqa: E731
            L.append(f"| {m} | {len(rows)} | **{pct(r['passed'] for r in rows)}** | {pct(ch('numbers_faithful'))} | "
                     f"{pct(ch('includes_ok'))} | {pct(ch('excludes_ok'))} | {pct(ch('top_rec_reflected'))} | "
                     f"{pct(ch('format_ok'))} | {pct(ch('length_ok'))} | {pct(r['length_strict_80_140'] for r in rows)} | "
                     f"{pct(not r['unknown_words'] for r in rows)} | {pct(r['served'] != 'template' for r in rows)} | "
                     f"{statistics.median(r['seconds'] for r in rows):.1f} | {p90([r['seconds'] for r in rows]):.1f} |")
        L += ["", "### Per scenario (pass rate)", ""]
        models = list(results["briefing"])
        cases = list(dict.fromkeys(r["case"] for r in results["briefing"][models[0]]))
        L += ["| Scenario | " + " | ".join(models) + " |", "|---|" + "---|" * len(models)]
        for c in cases:
            cells = []
            for m in models:
                rs = [r for r in results["briefing"][m] if r["case"] == c]
                fails = sorted({k for r in rs for k, v in r["checks"].items() if not v})
                cells.append(pct(r["passed"] for r in rs) + (f" ({', '.join(fails)})" if fails else ""))
            L.append(f"| {c} | " + " | ".join(cells) + " |")
        L += ["", "### Failure examples", ""]
        for m, rows in results["briefing"].items():
            for r in [x for x in rows if not x["passed"]][:4]:
                why = []
                if r["unsupported_numbers"]:
                    why.append(f"invented numbers {r['unsupported_numbers']}")
                if r["missing_groups"]:
                    why.append(f"missing any of {r['missing_groups']}")
                if r["forbidden_hits"]:
                    why.append(f"forbidden {r['forbidden_hits']}")
                if r["format_issues"]:
                    why.append(f"format {r['format_issues']}")
                if not r["checks"]["top_rec_reflected"]:
                    why.append("top recommendation not reflected")
                if not r["checks"]["length_ok"]:
                    why.append(f"{r['words']} words")
                L.append(f"- **{m} · {r['case']}** — {'; '.join(why)}  \n  > {r['text'][:400]}")
        typos = [(m, w) for m, rows in results["briefing"].items() for r in rows for w in r["unknown_words"]]
        if typos:
            L += ["", "Unknown/misspelled words: " + ", ".join(f"{w} ({m})" for m, w in typos[:25])]
    if "chat" in results:
        rows = results["chat"]["rows"]
        m = results["chat"]["model"]
        L += ["", "## Ask-your-data chat", "",
              "| Model | Runs | **Answer correct** | Right tool used | Numbers faithful | Median s | p90 s | Avg tool calls |",
              "|---|---|---|---|---|---|---|---|",
              f"| {m} | {len(rows)} | **{pct(r['correct'] for r in rows)}** | {pct(r['tool_ok'] for r in rows)} | "
              f"{pct(r['faithful'] for r in rows)} | {statistics.median(r['seconds'] for r in rows):.1f} | "
              f"{p90([r['seconds'] for r in rows]):.1f} | {statistics.mean(r['n_tool_calls'] for r in rows):.1f} |",
              "", "| Case | Correct | Tools used | Expected | Example answer |", "|---|---|---|---|---|"]
        for c in dict.fromkeys(r["case"] for r in rows):
            rs = [r for r in rows if r["case"] == c]
            ans = rs[0]["answer"].replace("\n", " ").replace("|", "/")[:160]
            L.append(f"| {c} | {pct(r['correct'] for r in rs)} | {', '.join(sorted({t for r in rs for t in r['tools_used']})) or '–'} "
                     f"| {rs[0]['detail']} | {ans} |")
    return "\n".join(L) + "\n"


HISTORY_FIELDS = ["run", "suite", "model", "runs", "overall_pass", "numbers_faithful", "includes_required",
                  "no_forbidden", "top_rec_reflected", "format_ok", "length_60_170", "length_80_140", "typo_free",
                  "answer_correct", "right_tool", "median_s", "p90_s", "source_file"]


def summary_rows(res: dict, source: str) -> list[dict]:
    def rate(xs):
        xs = list(xs)
        return round(sum(xs) / len(xs), 3) if xs else ""
    out = []
    for m, rows in (res.get("briefing") or {}).items():
        ch = lambda k, rows=rows: (r["checks"][k] for r in rows)  # noqa: E731
        out.append({"run": res["started"], "suite": "briefing", "model": m, "runs": len(rows),
                    "overall_pass": rate(r["passed"] for r in rows), "numbers_faithful": rate(ch("numbers_faithful")),
                    "includes_required": rate(ch("includes_ok")), "no_forbidden": rate(ch("excludes_ok")),
                    "top_rec_reflected": rate(ch("top_rec_reflected")), "format_ok": rate(ch("format_ok")),
                    "length_60_170": rate(ch("length_ok")), "length_80_140": rate(r["length_strict_80_140"] for r in rows),
                    "typo_free": rate(not r["unknown_words"] for r in rows),
                    "median_s": round(statistics.median(r["seconds"] for r in rows), 2),
                    "p90_s": round(p90([r["seconds"] for r in rows]), 2), "source_file": source})
    if res.get("chat"):
        rows = res["chat"]["rows"]
        out.append({"run": res["started"], "suite": "chat", "model": res["chat"]["model"], "runs": len(rows),
                    "overall_pass": rate(r["correct"] for r in rows), "answer_correct": rate(r["correct"] for r in rows),
                    "right_tool": rate(r["tool_ok"] for r in rows), "numbers_faithful": rate(r["faithful"] for r in rows),
                    "median_s": round(statistics.median(r["seconds"] for r in rows), 2),
                    "p90_s": round(p90([r["seconds"] for r in rows]), 2), "source_file": source})
    return out


def append_history(rows: list[dict]) -> Path:
    import csv

    p = HERE / "results" / "history.csv"
    new = not p.exists()
    with p.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HISTORY_FIELDS)
        if new:
            w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in HISTORY_FIELDS})
    return p


def main() -> int:
    from garmin_insights import config

    s = config.Settings.load()
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", choices=["all", "briefing", "chat"], default="all")
    ap.add_argument("--briefing-models", default=s.llm_briefing_model)
    ap.add_argument("--chat-model", default=s.llm_chat_model)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--chat-repeats", type=int, default=2)
    ap.add_argument("--report", help="re-render the .md report from saved result JSON files (comma-separated)")
    a = ap.parse_args()
    if a.report:
        res = {}
        for f in a.report.split(","):
            res.update({k: v for k, v in json.loads(Path(f).read_text()).items()})
        cases = {c["id"]: c for c in json.loads((HERE / "briefing_cases.json").read_text())["cases"]}
        for rows in res.get("briefing", {}).values():
            for r in rows:
                r["unknown_words"] = unknown_words(r["text"], cases[r["case"]]["facts"])
        md = report(res)
        out = HERE / "results" / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-combined.md")
        out.write_text(md)
        print(md)
        print(f"Saved: {out}")
        return 0
    from garmin_insights.recommend import llm

    if not llm.ensure_ollama():
        print("Ollama not running/installed", file=sys.stderr)
        return 1
    res = {"started": datetime.now().isoformat(timespec="seconds"), "repeats": a.repeats}
    if a.suite in ("all", "briefing"):
        print("Briefing suite")
        res["briefing"] = run_briefing([m.strip() for m in a.briefing_models.split(",")], a.repeats)
    if a.suite in ("all", "chat"):
        print("Chat suite")
        res["chat"] = {"model": a.chat_model, "rows": run_chat(a.chat_model, a.chat_repeats)}
    outdir = HERE / "results"
    outdir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (outdir / f"{stamp}.json").write_text(json.dumps(res, indent=1, default=str))
    hist = append_history(summary_rows(res, f"{stamp}.json"))
    md = report(res)
    (outdir / f"{stamp}.md").write_text(md)
    print("\n" + md)
    print(f"Saved: {outdir / stamp}.json / .md · metrics history: {hist}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
