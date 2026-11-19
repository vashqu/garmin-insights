# Garmin Insights

A local macOS app for your Garmin and Strong data. It compares every metric with **your own** baselines using
methods from the sports-science literature. Each morning it gives you a briefing and recommendations, written by
a small AI model running on your Mac. It also has a race planner, a what-if planner, a lifestyle and diet
journal, and a chat for asking questions about your data.

Everything runs on your Mac. The only network calls go to Garmin (to sync your data) and, once, to Hugging Face
and Ollama to download the models.

## Quick start

```bash
brew install ollama                     # local LLM runtime
uv sync --extra forecast                # Python 3.12 env + deps (incl. Chronos forecasting)
uv run garmin-app sync                  # first sync (≈2 min for ~3 months of history)
uv run garmin-app                       # open the app (native window)
./scripts/make_app_bundle.sh            # optional: ~/Applications/Garmin Insights.app launcher
```

The first time the app uses the local models, install them from Settings → "Download models", or run
`ollama pull qwen3:4b-instruct` (one model serves both the briefing and the chat).

### Credentials
- **Garmin Personal API key** (official, read-only): create it in Garmin Connect → Personal API Key page. Put it
  in `.env` as `GARMIN_API_KEY=...`, or paste it in Settings, which stores it in the macOS Keychain.
- **Garmin Connect login** (optional, unofficial): `uv run garmin-app login` or Settings. This adds data the
  official API doesn't provide: race predictions, personal records, stress timeline and Body Battery events.
- **Strong**: export the CSV from the Strong app into the watch folder
  (`Google Drive/My Drive/GarminInsights/strong` by default), or drop the file on the Muscle page.

## CLI

| Command | What it does |
|---|---|
| `garmin-app` / `garmin-app run [--browser] [--no-sync]` | open the app (sync starts in the background) |
| `garmin-app sync [--days N] [--full]` | sync now |
| `garmin-app import-strong [file.csv]` | import a Strong export |
| `garmin-app recompute` | rebuild analytics and forecasts |
| `garmin-app login` | log in to the optional unofficial source |

## What's computed (and why)

| Metric | Method | Evidence |
|---|---|---|
| Personal status (green/amber/red) | rolling 60-day median ± MAD, z-score, ≥14 days required | — |
| HRV | 7-day ln(RMSSD) vs smallest worthwhile change (±0.5 SD) + 7-day CV | strong (AIS guide, Plews/Buchheit) |
| Illness watch | 4-day windowed CuSum on resting-HR z-scores + respiration/temp/HRV corroboration | strong signal (Mishra 2020, Alavi 2022); thresholds heuristic |
| Sleep regularity | Sleep Regularity Index (minute-by-minute, 7 & 30 nights) | strong (Windred 2024) |
| Sleep need / debt | 60-day median bounded to 7–9 h; 7-night shortfall | moderate (Walsh 2021 consensus) |
| Training load | Banister TRIMP; fitness 42 d / fatigue 7 d EWMA; Foster monotony & strain; uncoupled ACWR | moderate |
| Readiness | weighted z-composite (HRV 35, RHR 20, sleep 15, SRI 10, stress 10, Body Battery 10), missing inputs re-weighted | heuristic |
| Muscle volume | fractional weekly hard sets per muscle (primary 1, secondary 0.5) vs 10–20 range | strong (Schoenfeld 2017; Pelland) |
| Strength | Epley e1RM per exercise, PRs, muscle freshness (≥48 h) | moderate |
| Running | efficiency factor & Pa:HR decoupling; Riegel predictions | heuristic / moderate |
| Swimming | pace/100 m, CSS from a 400/200 test, SWOLF within the same pool length and stroke only | strong (CSS) |
| Longevity | VO2max vs FRIEND median for your age, steps vs plateau band, WHO activity minutes, strength days | strong (observational) |
| Journal | tagged vs untagged days, median difference with bootstrap 90% CI (≥10 days per group) | personal association |
| Forecast | Chronos-Bolt-tiny (9M params, local CPU), 7-day 10/50/90% band | heuristic |

## Local AI
- **Briefing**: Qwen3 4B Instruct (chosen by evals: 92% vs 54% pass for Qwen3 1.7B — see `evals/`), no thinking mode. Every number in the text must appear in the input facts. Comparisons
  such as "short of your need" are pre-computed as verdicts, so the model never compares numbers itself. If the
  check fails, the app shows the template text.
- **Chat**: Qwen3 4B Instruct with read-only tools over your database. The answer shows which data it used, and
  flags any number that doesn't appear in the fetched data.

### Eval results (`uv run python evals/run_evals.py`, history in `evals/results/history.csv`)

| Suite | Model | Pass | Numbers faithful | Median latency |
|---|---|---|---|---|
| Briefing (8 scenarios × 3) | Qwen3 4B Instruct | 92% | 96% (guard catches the rest) | 3.9 s |
| Briefing (same set, replaced) | Qwen3 1.7B | 54% | 100% | 1.9 s |
| Chat (12 questions × 2, exact ground truth) | Qwen3 4B Instruct | 100% correct | 92% | 5.8 s |

## Performance on a MacBook Air M3

Measured with `uv run python scripts/benchmark.py` (results in `bench/results/`) on a MacBook Air (Mac15,12),
Apple M3 with 16 GB, macOS 26.6, **on battery**, with 77 days of history:

| What | Result |
|---|---|
| App start (server ready) | 1.6 s |
| App memory | ~155–205 MB (+~300 MB briefly while Chronos forecasts are computed after a sync) |
| App CPU when idle | 0% |
| Page load, end-to-end incl. callbacks (median / p90) | 0.63 s / 0.73 s |
| Recompute all analytics | 0.22 s |
| Chronos-Bolt-tiny forecasts (4 metrics × 7 days) | 4.1 s first run (model load), then 0.02 s |
| Incremental Garmin sync | 7.3 s, 7 API requests |
| Morning briefing, cold (model load 2.8 s + generation) | 8.3 s, shown asynchronously and then cached for the day |
| Morning briefing, warm | 4.4 s |
| Qwen3 4B Instruct speed | ~30 tokens/s generation, ~340 tokens/s prompt processing |
| Chat answer incl. tool calls (median) | 9.4 s |
| Ollama memory while the model is loaded | ~3.0–3.3 GB, freed automatically after 10 min idle |
| Whole-system power draw (battery gauge): baseline / app idle / LLM generating / chat | 13.2 W / 13.2 W / 33 W / 33 W |
| Disk | Python env (incl. PyTorch) 1.2 GB · Ollama model 2.3 GB · Chronos model 66 MB · app data 7 MB |

What this means in practice:
- With the app open and idle, there's no measurable extra CPU or power.
- The only heavy moment is the LLM. Generation takes the whole system to about 33 W (20 W above baseline) for
  4–10 s. One briefing a day plus a few chat questions costs well under 1% of the battery.
- On a 16 GB Mac, peak usage is about 3.5 GB (model plus app). The model is unloaded after 10 minutes, after which
  the app uses about 0.2 GB.
- Power figures are whole-system readings from the battery gauge and include the screen and other open apps, so
  compare them relative to the baseline.

## Data & privacy
Data is stored in `~/.garmin_insights/` (SQLite, raw JSON cache, FIT samples, logs). Secrets are kept in the
Keychain or `.env`, both git-ignored. The local server listens on 127.0.0.1 only.

None of your personal data is in this repository. Tests and evals use synthetic data (`garmin_insights.demo`),
and raw eval outputs stay local (`evals/results/*.json|md` are git-ignored). Only aggregate metrics are committed.

## Development
```bash
uv run pytest        # 42 tests: formulas, parsers, normalizers, API auth, every page renders (synthetic data)
uv run ruff check src tests
```
Synthetic demo data for tests: `garmin_insights.demo.generate()`.

## Attributions

### Open-source models
| Model | Used for | Authors | License | Citation |
|---|---|---|---|---|
| [Qwen3-4B-Instruct-2507](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507) (run via Ollama as `qwen3:4b-instruct`) | Morning briefing and the "ask your data" chat | Qwen Team, Alibaba Cloud | Apache-2.0 | Qwen Team, *Qwen3 Technical Report*, 2025, [arXiv:2505.09388](https://arxiv.org/abs/2505.09388) |
| [Chronos-Bolt-tiny](https://huggingface.co/amazon/chronos-bolt-tiny) (`amazon/chronos-bolt-tiny`, 9M params) | 7-day forecasts | Amazon Science | Apache-2.0 | Ansari et al., *Chronos: Learning the Language of Time Series*, Transactions on Machine Learning Research, 2024 |

Model weights are downloaded at runtime (by Ollama and Hugging Face) and are not redistributed in this repository.

### Runtime and main libraries
[Ollama](https://ollama.com) (MIT) · [Dash](https://dash.plotly.com) & [Plotly](https://plotly.com/python/) (MIT) ·
[dash-bootstrap-components](https://dash-bootstrap-components.opensource.faculty.ai) (Apache-2.0) ·
[pandas](https://pandas.pydata.org) / [NumPy](https://numpy.org) / [SciPy](https://scipy.org) (BSD) ·
[PyTorch](https://pytorch.org) (BSD-style) · [chronos-forecasting](https://github.com/amazon-science/chronos-forecasting) (Apache-2.0) ·
[pyarrow](https://arrow.apache.org) (Apache-2.0) · [fitdecode](https://github.com/polyvertex/fitdecode) (MIT) ·
[pywebview](https://pywebview.flowrl.com) (BSD-3-Clause) · [httpx](https://www.python-httpx.org) (BSD-3-Clause) ·
[keyring](https://github.com/jaraco/keyring) (MIT) · [garminconnect](https://github.com/cyberjunky/python-garminconnect) (MIT, optional unofficial fallback).

### Data sources & trademarks
Data comes from the official [Garmin Connect Developer Program – Personal API](https://developer.garmin.com/gc-developer-program/personal/)
for personal, non-commercial use, and from your own [Strong](https://www.strong.app) CSV exports. This project is
not affiliated with or endorsed by Garmin or Strong. Garmin, Garmin Connect and Body Battery are trademarks of
Garmin Ltd. or its subsidiaries.

### Science
The metric methods and their sources (HRV/SWC, Sleep Regularity Index, Banister TRIMP, fractional set volume,
CSS, Riegel, and others) are listed with references in [PLAN.md §14](PLAN.md) and in the in-app ⓘ tooltips.

Not medical advice.
