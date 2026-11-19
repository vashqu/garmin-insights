# Garmin Insights — Final Plan (v2)

A local macOS desktop app (Apple M3, 16 GB). It syncs the owner's Garmin data and Strong workout logs, assesses every metric against the owner's **own** baselines using science-based methods, and produces a plain-language morning briefing plus recommendations from small local LLMs. It also offers race preparation, a what-if planner, a lifestyle/diet journal, and an "ask your data" chat.

Built by Opus, phase by phase (§12). Every phase ends with tests passing and a git commit.

---

## 1. Decisions (agreed with the owner)

| Topic | Decision |
|---|---|
| Primary goals | **Longevity, sleep, muscle.** Running performance is occasional, via **Race mode**. |
| Sports | Running, strength, swimming, stretching (+ breathwork recommended when stress is high) |
| Usage | **Morning check-in** daily; deeper exploring every 1–2 weeks |
| Data sources | Official Garmin **Personal API** first → fallback to `garminconnect` (unofficial). Strong CSV for strength sets. |
| App shell | **Native macOS window** (pywebview wrapping a local Dash server), launched from an app icon |
| UI | Dash + Plotly + dash-bootstrap-components; **briefing-first** home screen; **metric** units |
| Sync | On launch (background) + "Sync now" button. No background jobs, no notifications. |
| History | First sync **1 year** |
| Training load | Owner does **not** rate effort on the watch, so we use **HR-based TRIMP**. Strength load comes from Strong sets (RPE used if logged in Strong). |
| Forecasting | **Chronos-Bolt-tiny** (9M params, Apache-2.0, CPU) |
| Briefing LLM | **Qwen3 4B Instruct** via Ollama (2.5 GB, Apache-2.0) — switched from Qwen3 1.7B on 2026-11-19 after evals (92% vs 54% scenario pass) |
| Chat LLM | **Qwen3 4B** via Ollama (2.5 GB, Apache-2.0) with tool calling |
| Strong import | Watch folder (configurable; intended for **Google Drive for desktop** synced folder) **+** upload button |
| Journal | Built-in tag sets: **Lifestyle** + **Diet**, plus custom tags |
| Interactive extras | Journal tags, Ask-your-data chat, What-if planner, Race mode |
| Race mode depth | Weekly targets + key sessions, readiness % and predicted vs goal time. Nothing is written to Garmin. |

## 2. Raw metrics in scope (owner-selected)

O = official Personal API; U = `garminconnect` only. Verify each `garminconnect` method with `dir(Garmin)` (lib ≥0.3.20). **Never call write methods.**

| # | Metric | Src | Fallback method(s) |
|---|---|---|---|
| 1–5 | Steps(+goal), distance, floors, calories (active/BMR/total), intensity minutes | O | `get_user_summary`, `get_floors`, `get_intensity_minutes_data` |
| 6 | Resting HR | O | `get_rhr_day` |
| 7 | All-day HR curve, min/max/avg | O | `get_heart_rates` |
| 9–10 | Stress timeline + rest/low/med/high durations | O | `get_all_day_stress` / `get_stress_data` |
| 11 | Body Battery high/low + timeline | O | `get_body_battery` |
| 12 | Body Battery events | U | `get_body_battery_events` |
| 13–15 | Sleep stages, score + sub-scores, sleep HR/stress/respiration, **bed/wake times** | O | `get_sleep_data` |
| 17–18 | HRV nightly avg (RMSSD), Garmin baseline/status, overnight readings | O | `get_hrv_data` |
| 19 | SpO2 | O | `get_spo2_data` |
| 20 | Respiration | O | `get_respiration_data` |
| 21 | Skin temperature deviation | O | sleep payload fields if present, else null |
| 22 | Health Snapshot sessions | O | `get_health_snapshots_range` |
| 30 | Training Effect aero/anaero | O | activity summary |
| 31 | VO2max | O | `get_max_metrics(_range)` |
| 32 | Fitness age | O | `get_fitnessage_data` |
| 33 | Race predictions | U | `get_race_predictions` |
| 37 | Running economy | U | `get_running_economy(_range)` |
| 38 | Running tolerance | U | `get_running_tolerance` |
| 42 | Activities list/summary | O | `get_activities_by_date` |
| 43 | Activity time-series | O (.FIT) | `download_activity(ORIGINAL)` → `fitdecode`; fallback `get_activity_details` |
| 51 | Swim lengths, stroke, SWOLF | O | `get_activity_swim_details` / FIT lengths |
| 52–53 | Strength sets, 1RM | U | **Primary source = Strong CSV.** Garmin sets used only if no Strong match. |
| 55 | Yoga/Pilates/stretching sessions | O | activity type filter |
| 56 | Personal records | U | `get_personal_record` |
| 57 | Weekly/monthly totals | — | computed locally |
| — | User profile (birth year, sex, height, weight, max HR) | O/U | `get_user_profile` / `get_personal_information` (needed for norms & TRIMP) |

## 3. Science-based derived metrics

Every derived metric declares its method, its evidence level (`strong` / `moderate` / `heuristic`), and a short "why this matters" text shown in the UI behind an ⓘ. Thresholds live in one `config/thresholds.toml` so they can be tuned.

### 3.1 Personal baseline engine (all daily metrics)
- Baseline = rolling **median** and **MAD×1.4826** of the previous 60 days, excluding today. At least 14 points are required, else `calibrating`.
- Status by z-score in the metric's bad direction: green <0.75, amber 0.75–1.5, red ≥1.5. Show the color **plus** an icon/text (▲▼●), never color alone.

### 3.2 HRV (strong)
- Daily value = ln(nightly RMSSD).
- **7-day rolling mean of lnRMSSD** compared with the **smallest worthwhile change (SWC) band = baseline mean ± 0.5 SD** (60-day). This is the AIS HRV best-practice guide method, from Plews/Buchheit.
- **CV of lnRMSSD over 7 days**: a rising CV alongside a falling mean signals maladaptation. This rests on case-study evidence, so it is labelled moderate.
- Garmin's own HRV status/baseline is shown alongside for comparison.

### 3.3 Resting HR & illness early warning (strong for RHR; heuristic for the combined rule)
- RHR status via §3.1.
- **Illness watch**: a one-sided CuSum on nightly RHR residuals vs the personal baseline, in the spirit of the NightSignal/CuSum methods of Alavi et al. 2022 and Mishra et al. 2020. Raise "watch" when the CuSum crosses its threshold. Escalate to "likely strain/illness" if ≥2 of these corroborate: skin temp > +0.5 °C vs baseline, respiration > +1 brpm, HRV 7d below the SWC band. Show it as "signals consistent with your body fighting something". **No diagnosis language.**

### 3.4 Sleep
- **Sleep Regularity Index (SRI)** from bed/wake times (approximated from sleep windows) over 7 and 30 days, on a 0–100 scale. In UK Biobank (Windred et al., *SLEEP* 2024), regularity predicted mortality more strongly than duration (strong, observational).
- **Personal sleep need**: duration vs the owner's 60-day median, plus the general 7–9 h adult range as a reference. Individualised per the 2021 BJSM athlete sleep consensus.
- Sleep debt: 7-day sum of (need − actual).
- Sleep timing consistency: SD of bedtime and of wake time.

### 3.5 Longevity panel
- **VO2max percentile** for age/sex, using ACSM/FRIEND registry norm tables (cite source; label it as a Garmin estimate). Mandsager 2018: fitness is inversely associated with mortality with no upper limit (strong, observational).
- **Steps** vs plateau band: 8–10k/day if age <60, 6–8k if ≥60 (Paluch 2022, *Lancet Public Health*).
- **Intensity minutes** vs WHO 150–300 moderate-equivalent per week (vigorous counts ×2).
- **Strength sessions/week** vs WHO ≥2 days muscle-strengthening.
- RHR long-term trend, fitness age, SRI.
- A longevity scorecard combining these, each item green/amber/red against its evidence-based target.

### 3.6 Training load (HR-based, no RPE)
- Per-session **Banister TRIMP** (sex-specific coefficients; HRr from profile RHR/max HR). If an activity has no HR, use duration × a default intensity factor per sport.
- **Fitness (42-day EWMA)**, **Fatigue (7-day EWMA)**, **Form** = Fitness − Fatigue.
- **Monotony** = 7-day mean daily load / SD; **Strain** = weekly load × monotony (Foster). These are screening signals only (moderate).
- **Uncoupled ACWR** (acute 7 d ÷ the preceding 21 days' weekly average, acute excluded) is shown only as a secondary flag at >1.5. The coupled version is avoided (Lolli et al.; Impellizzeri critiques).
- Load mix per sport.

### 3.7 Readiness score 0–100 (app's own; heuristic composite of strong inputs)
Weighted, z-clipped ±2.5, oriented + = good:
HRV 7d vs SWC 35% · RHR 20% · sleep (duration vs need 15% + SRI-7d 10%) · previous-day stress 10% · morning Body Battery 10%.
`score = 50 + 20·Σw·z`, clipped to 0–100. Modifiers: −10 if the illness watch is active; −5 if Form is very negative (below −2 SD of the owner's own history) or monotony > 2. The UI shows the component waterfall and states that it is a heuristic.

### 3.8 Muscle (strength goal)
- **Weekly hard sets per muscle group** from Strong sets. **Fractional counting**: primary muscle = 1, secondary = 0.5 (Pelland et al. meta-regression; Schoenfeld 2017 dose-response). Reference band 10–20 sets/week per muscle, labelled "typical evidence-informed range".
- "Hard set" = working set (warm-ups excluded via Strong's set type). If RPE is logged, count RPE ≥7 only.
- **Estimated 1RM** per exercise (Epley, sets with ≤10 reps), with trend lines and a PR flag.
- **Muscle freshness**: hours since each group was last trained hard (≥48 h = fresh).
- Exercise→muscle mapping in `data/exercise_muscles.json`, covering Strong's default library. Unknown exercises appear in a "map this exercise" UI.
- Protein reminder reference: ~1.6 g/kg/day for hypertrophy (Morton 2018 meta-analysis), surfaced only through the diet journal.

### 3.9 Running
- **Efficiency Factor** (speed ÷ HR) and **aerobic decoupling Pa:HR** (EF first half vs second half) on steady runs ≥20 min with low pace CV. <5% is a "coaching rule of thumb" (heuristic). Hills: use grade-adjusted speed from FIT elevation.
- Weekly km, long-run share, and run consistency.
- Garmin VO2max, race predictions, running economy and tolerance.

### 3.10 Swimming
- Pace/100 m by stroke; **CSS** from a logged 400 m + 200 m test, or estimated from best sustained efforts (reliable, CV <4%, Scott/Burden/Dekerle). CSS-based zones.
- SWOLF compared **only within the same pool length + stroke**.

### 3.11 Stretching & breathing
- Stretching minutes/week vs **5–10+ min/week** reference (2024 static-stretching dose review: >5 min/week improves ROM). The app explicitly **does not claim injury prevention** (evidence does not support it).
- Breathwork is recommended when stress or Body Battery drain is high: **slow breathing ~6 breaths/min, 5–10 min** (moderate evidence for stress/HRV: Laborde 2021; Shao 2023 meta).

### 3.12 Journal correlations
- Spearman correlation with a lag of 0–1 day between tags/inputs and next-night outcomes (HRV, sleep score, SRI, RHR, deep sleep). Also an **on-vs-off comparison** (median outcome on tag days vs non-tag days, bootstrap 90% CI).
- Only surface results with n ≥ 10 tag days and a CI excluding 0. Always worded as "associated with".

### 3.13 Forecast
- Chronos-Bolt-tiny forecasts 7 days of HRV-7d, RHR, sleep duration and readiness. Needs ≥60 days of history. Shown as a 10/50/90 quantile band. Cached daily.

## 4. Recommendations engine

Pure-function rules over a `DayContext` → `Recommendation(category, priority, title, action, reason, evidence{numbers}, evidence_level)`. At most 3 shown on Home, ordered by priority.

Core rules (thresholds in `thresholds.toml`):
1. **Illness watch active** → rest or very easy movement only; recheck tomorrow.
2. **Readiness ≥70 & HRV within/above SWC** → good day for hard strength or a quality session. Name the muscle groups that are freshest *and* under their weekly set target.
3. **Readiness 40–69** → moderate: strength for fresh groups at normal volume, easy run/swim.
4. **Readiness <40 or HRV below SWC for ≥2 days** → recovery: easy 20–30 min, stretching, breathwork.
5. **Muscle under target** (projected weekly sets < 10 by Sunday) → suggest which group and how many sets.
6. **Same group trained hard <48 h ago** → train another group.
7. **SRI-7d low / bedtime SD > 60 min** → consistent wake time advice.
8. **Sleep debt > 5 h over 7 days** → prioritise an earlier bedtime.
9. **High stress / BB drain** → 6-breaths/min breathing session, 5–10 min.
10. **No stretching in ≥4 days** and strength/run load high → 10-minute stretch for the groups trained.
11. **Monotony > 2 or Form very negative** → vary intensity / lighter day.
12. **Steps below plateau band 3+ days** → walk suggestion.
13. **Journal-driven**: alcohol logged and HRV is down → note the observed personal association, if significant.
14. **Race mode** rules (§6) override the cardio parts of 2–4 when a race is active.

## 5. LLM layer (Ollama, fully offline)

- Setup: `brew install ollama`, then `ollama pull qwen3:4b-instruct`. Settings shows model status and a "Pull model" button.
- **Briefing (Qwen3 4B Instruct, `think=False`)**: input is compact JSON (statuses, readiness waterfall, top recommendations, notable changes, journal yesterday). Output is 90–150 words, friendly, no diagnosis, numbers only from input. `keep_alive=5m`.
- **Number guard**: extract every number in the output and check that each matches a number in the input (with rounding tolerance). On failure, retry once at temperature 0, then fall back to the template briefing. Show a small "LLM" / "template" badge.
- **Chat (Qwen3 4B, tool calling)**: tools are read-only functions over the DB — `list_metrics`, `get_series(metric, start, end, agg)`, `compare_periods(metric, a, b)`, `get_activities(filter)`, `get_journal(range)`, `get_correlation(input, outcome, lag)`, `get_race_status()`. Max 5 tool calls per question. The answer is checked by the number guard against tool outputs. Each answer has a collapsible "data used" section. A system prompt explains the metric definitions and scope (no medical advice). History is kept per session only.
- Cache briefings by date + input hash.

## 6. Race mode

Input: race name, date, distance (5K/10K/HM/M/custom), goal time (optional), terrain (flat/hilly).

Outputs:
- **Predicted time**: shown as a range from (a) Garmin race prediction and (b) Riegel (T2 = T1·(D2/D1)^1.06) from recent best efforts/time trials. If a goal time is set, show the gap.
- **Readiness %** for that race: weighted from predicted-vs-goal gap, current weekly km vs distance-appropriate target, longest run in the last 4 weeks vs target, consistency (weeks with ≥3 runs), and Form on race day (projected).
- **Phase** by weeks-out: Base → Build → Peak → **Taper (2 weeks: cut volume ~40–60%, keep intensity & frequency; Bosquet 2007 meta-analysis)**.
- **Weekly targets**: km, long run, 1–2 key sessions (e.g. tempo / intervals / race-pace), all at HR or pace derived from recent data. Strength stays at ≥2 sessions with reduced leg volume in Peak/Taper.
- Guardrail: flag if weekly run km rises > 30% vs the 4-week average (labelled heuristic).
- Adapts each morning: if readiness is low, the key session moves later in the week.

## 7. What-if planner

The owner adds planned sessions for the next 1–14 days (sport, duration, intensity: easy/moderate/hard, or a race-plan session). The app estimates TRIMP from the owner's historical sessions of that type and intensity, then projects Fitness/Fatigue/Form, monotony and muscle sets/week. Charts show actual vs planned. Plans are stored locally and can be compared with what actually happened.

## 8. Journal

- One-tap morning card on Home ("Yesterday:") plus a full Journal page with a calendar view.
- **Lifestyle**: alcohol (drinks count), caffeine after 14:00, late meal (<3 h before bed), late screen time, sauna/cold (optional).
- **Diet**: protein target met (y/n), high-sugar day, heavy/processed food day, low-carb day, fasting/skipped meals, hydration good (y/n).
- Custom tags (boolean or numeric). Stored in the `journal` table and fed into §3.12.

## 9. Architecture

```
GarminInsights.app (launcher) → pywebview window → Dash server (127.0.0.1, random port, thread)
                                              │
 ┌────────────── sources ──────────────┐      │  pages read only from DB + derived tables
 │ official.py   (Personal API, httpx) │      │
 │ unofficial.py (garminconnect)       │──router──► raw_cache (JSON per type/day) ──► normalize ──► SQLite
 │ strong_csv.py (watch folder/upload) │                                                            │
 └─────────────────────────────────────┘                                    analytics/ (after each sync)
                                                                            → derived_daily, derived_activity,
                                                                              muscle_weekly, forecasts
                                                   recommend/ (rules → Ollama briefing; chat tools)
```

### 9.1 Stack
Python **3.12** (pinned via uv; the system has 3.14), `pyproject.toml`, src layout.
`dash`, `dash-bootstrap-components`, `plotly`, `pywebview`, `pandas`, `numpy`, `scipy`, `pyarrow`, `httpx`, `garminconnect>=0.3.20`, `fitdecode`, `keyring`, `ollama`, `chronos-forecasting` + `torch` (CPU), `diskcache` (background callbacks), `watchdog` is not needed (scan on launch), `pytest`, `ruff`.

### 9.2 Official Personal API
- Key from Garmin Connect Web → stored in the Keychain.
- Token: `POST https://personal.connectapi.garmin.com/personalapitoken-service/personal-api/personal-api-key/token`, header `Authorization: ApiKey <key>`. Valid ≤24 h, **no refresh token**: cache it with its expiry and re-request on expiry/401.
- Data endpoint paths come from Garmin's **Personal API Swagger docs**. Fetch the OpenAPI JSON if it is publicly reachable; otherwise ship an editable endpoint map in Settings, with unmapped types falling back. **No invented paths.**

### 9.3 Unofficial
- Login via Settings (email/password + MFA using `return_on_mfa` / `resume_login`). Password goes to the Keychain; tokens go to `~/.garmin_insights/tokens/` (0700).
- 1 s between calls, exponential backoff on 429/5xx (max 3), auth failure → "re-login needed" banner. Known fragility: Garmin blocks third-party clients via TLS fingerprinting. The app must remain fully usable on cached data.

### 9.4 Sync
- First run backfills 365 days, newest first, so Home becomes useful within minutes. A resumable cursor is stored in the DB.
- Later runs fetch from the last synced day − 2 through today. Low-frequency metrics are fetched once per run.
- Progress object → UI sync bar (dcc.Interval). Sync runs in a background thread started at launch; Dash background callbacks with a diskcache manager handle long UI jobs (forecast, chat).
- Raw JSON is always cached → `garmin-app renormalize` can rebuild tables offline.
- After a sync, recompute derived tables incrementally (last 70 days for rolling metrics).

### 9.5 Strong importer
- Watch folder path in Settings (default `~/Library/CloudStorage/GoogleDrive-*/My Drive/GarminInsights/strong/` if present, else `~/GarminInsights/strong/`). Scan on launch. Upload button (dcc.Upload) as well.
- Parse defensively: auto-detect the header row and delimiter (`,` or `;`), date formats, and kg/lb (setting). Columns are expected per set: Date, Workout Name, Exercise Name, Set Order, Weight, Reps, Distance, Seconds, RPE, Notes. Verify against a real export the owner provides.
- Dedupe key: (workout start datetime, exercise, set order). **Match** each Strong workout to the Garmin strength activity whose time window overlaps (±30 min) → attach HR/TRIMP.

### 9.6 Storage
`~/.garmin_insights/`: `garmin.db` (SQLite, WAL mode), `raw/`, `samples/<activity_id>.parquet`, `settings.toml`, `tokens/`, `logs/` (rotating). Tables: daily_summary, hr_intraday, stress_intraday, bb_intraday, bb_events, sleep, sleep_intraday, hrv, hrv_intraday, daily_vitals, health_snapshot, fitness_metrics, race_predictions, activity, swim_length, strength_set, one_rep_max, personal_record, profile, journal, journal_tag_def, race, plan_session, derived_daily, derived_activity, muscle_weekly, forecast, llm_cache, sync_state. Every source row has `source` + `fetched_at`. SI units internally.

### 9.7 Desktop packaging
`garmin-app` CLI → starts Dash on a free localhost port in a thread → opens a pywebview window (1280×860, min 1024×700) → shuts the server down when the window closes. `scripts/make_app_bundle.sh` builds `~/Applications/Garmin Insights.app` (shell launcher calling `uv run garmin-app`, with an icon). Single-instance lock.

## 10. UI

Global: left sidebar (icons + labels), top bar (date, sync status + button), light/dark following macOS. Plotly charts share one theme and categorical palette (follow the dataviz skill guidance). Every chart has hover tooltips, zoom, and a range selector (30d/90d/1y) where it is a trend. Clicking a day on any trend opens Home for that date. Empty and calibrating states are designed, not blank.

Pages:
1. **Home (briefing-first)**: greeting + date navigator; LLM briefing; flags strip (illness watch, sleep debt, etc.); top-3 recommendations with "why"; status cards (HRV-7d, RHR, Sleep, SRI, Stress, Body Battery, SpO2, Skin temp) with 14-day sparklines; readiness score + waterfall; Form mini-chart; 7-day forecast mini; "Yesterday" journal quick-tags; race countdown card if a race is active.
2. **Sleep**: stage bars per night, duration vs need, SRI trend, bedtime/wake scatter, hypnogram + sleep HR/respiration for the selected night, sleep debt.
3. **Recovery** (HRV, RHR, vitals): lnRMSSD 7d with SWC band + CV, Garmin HRV status, overnight HRV, RHR + illness CuSum, SpO2, respiration, skin temp, Health Snapshots.
4. **Stress & Energy**: stress breakdown, intraday stress + Body Battery with events for the selected day, breathwork suggestions log.
5. **Muscle**: body-map or heatmap of weekly sets per group vs 10–20 band, freshness per group, per-exercise volume and e1RM trends, PRs, unmapped-exercise fixer, Strong import status.
6. **Training**: activity table (filter by sport) → **Activity detail** (synced HR/pace/cadence/elevation subplots, HR zones, EF/decoupling for runs, swim lengths/SWOLF, strength sets from Strong); Fitness/Fatigue/Form; monotony/strain; load by sport; weekly/monthly totals; **What-if planner** tab.
7. **Running & Race**: VO2max + percentile, race predictions, EF trend, economy/tolerance, weekly km; **Race mode** (setup form, readiness %, predicted vs goal, phase timeline, weekly targets, key sessions).
8. **Swimming**: pace/100 m, CSS + zones, SWOLF (same pool/stroke), weekly distance.
9. **Longevity**: scorecard (VO2max percentile, RHR trend, steps vs plateau, intensity minutes vs WHO, strength days/week, SRI, fitness age), each with evidence ⓘ.
10. **Journal**: calendar of tags, tag editor, "what affects you" associations (on-vs-off comparisons with CI).
11. **Explore**: pick 2 metrics + lag → dual-axis chart, scatter + trendline, stats; **Ask your data** chat panel.
12. **Settings**: Garmin login/MFA, Personal API key + endpoint map, Strong folder + upload + unit, Ollama status/models, profile overrides (max HR, sex for TRIMP), thresholds viewer, re-normalize, data folder.

## 11. Quality & safety
- Tests never hit the network. Use fixtures plus a **synthetic data generator** (`scripts/make_fixture_db.py`) producing 400 days of realistic correlated data, including illness episodes, alcohol tags and Strong logs. It drives UI development, demos and tests.
- Unit tests for every analytics formula (SWC, CuSum, SRI, TRIMP/EWMA, monotony, uncoupled ACWR, fractional sets, Epley, Riegel, EF/decoupling, CSS), the rules, the number guard, and the Strong parser.
- No medical-diagnosis language anywhere. A footer notes "Not medical advice".
- Bind to 127.0.0.1 only. Secrets go only in the Keychain. Data and secrets are never committed (`.gitignore`).
- Performance targets on the M3 Air: Home renders <1 s from DB, briefing <5 s (shown async, page doesn't wait), page switches <500 ms.

## 12. Build phases

| Phase | Scope | Acceptance |
|---|---|---|
| P0 | git init, uv project (py3.12), layout, settings, logging, CLI | `uv run garmin-app --help`; pytest + ruff pass |
| P1 | Schema, synthetic data generator, repo/query layer | Fixture DB with 400 days of data; query tests pass |
| P2 | Analytics §3 (all derived metrics) + derived tables | All formula unit tests pass on synthetic data |
| P3 | Dash shell + pywebview + Home + Sleep + Recovery + Stress | Window opens; pages render on the fixture DB and an empty DB |
| P4 | Muscle, Training (+activity detail), Running, Swimming, Longevity pages | All render; click-through to day/activity works |
| P5 | Recommendations + Ollama briefing + number guard + template fallback | Rule tests; briefing works with Ollama stopped and running |
| P6 | Unofficial source, sync engine, normalizers, Garmin login/MFA in Settings | Normalizer tests on fixtures. **Owner**: logs in and syncs 1 year; all pages show real data |
| P7 | Official Personal API source + router/fallback | Mocked httpx tests; owner pastes key; fallback verified |
| P8 | Strong importer (folder + upload + matching) | Parser tests. **Owner** provides a real export; sets appear on the Muscle page |
| P9 | Journal + correlations, What-if planner, Race mode | Feature tests; race readiness on synthetic + real data |
| P10 | Chronos forecast, Ask-your-data chat (Qwen3 4B tools) | Forecast band shown; chat answers with "data used" |
| P11 | App bundle, README, polish, performance pass | App launches from the icon; perf targets met |

## 13. Owner actions needed
1. Install Ollama (`brew install ollama`) — or let the app guide you; then the models are pulled from Settings.
2. Install **Google Drive for desktop** if the Strong watch folder should be in Drive (otherwise use the upload button).
3. Create a Garmin **Personal API key** (Garmin Connect Web → Personal API Key page).
4. Log in to Garmin in Settings (password stays in the macOS Keychain).
5. Provide one real Strong CSV export (to verify the parser).

## 14. Key references
- AIS HRV best-practice guide; Plews et al. (7-day lnRMSSD vs SWC)
- Mishra et al. 2020 *Nat Biomed Eng*; Alavi et al. 2022 *Nat Med* (RHR-based pre-symptomatic detection)
- Windred et al. 2024 *SLEEP* (Sleep Regularity Index vs mortality); Walsh et al. 2021 *BJSM* (athlete sleep consensus)
- Paluch et al. 2022 *Lancet Public Health* (steps); WHO 2020 physical activity guidelines
- Mandsager et al. 2018 *JAMA Netw Open* (CRF & mortality); FRIEND registry VO2max norms
- Banister TRIMP; Foster 1998 (monotony/strain); Lolli et al. 2019 (ACWR coupling)
- Schoenfeld et al. 2017; Pelland et al. (weekly set volume, fractional sets); Morton et al. 2018 (protein)
- Bosquet et al. 2007 (taper meta-analysis); Riegel 1981 (race time model)
- Scott, Burden & Dekerle (CSS reliability)
- Behm et al. 2023; 2024 static stretching dose review (ROM; no injury-prevention claim)
- Laborde et al. 2021; Shao et al. 2023 (slow-paced breathing)
- Chronos-Bolt (Amazon, Apache-2.0); Qwen3 (Alibaba, Apache-2.0)
