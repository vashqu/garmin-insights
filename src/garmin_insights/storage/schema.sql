PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS daily (
    date TEXT PRIMARY KEY,
    has_wellness INTEGER,
    steps INTEGER, step_goal INTEGER, distance_m REAL, floors_up REAL,
    cal_total REAL, cal_active REAL, cal_bmr REAL,
    im_moderate INTEGER, im_vigorous INTEGER, im_goal INTEGER,
    rhr REAL, hr_min REAL, hr_max REAL, rhr_7d_garmin REAL,
    stress_avg REAL, stress_max REAL,
    stress_rest_s REAL, stress_low_s REAL, stress_med_s REAL, stress_high_s REAL, stress_activity_s REAL,
    bb_min REAL, bb_max REAL, bb_charged REAL, bb_drained REAL, bb_latest REAL,
    resp_avg REAL, resp_min REAL, resp_max REAL,
    spo2_avg REAL, spo2_low REAL,
    source TEXT, fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS sleep (
    date TEXT PRIMARY KEY,
    start_local TEXT, end_local TEXT, start_gmt TEXT, end_gmt TEXT,
    duration_s REAL, deep_s REAL, light_s REAL, rem_s REAL, awake_s REAL, nap_s REAL,
    score REAL, score_qualifier TEXT, awake_count REAL,
    resp_avg REAL, resp_low REAL, resp_high REAL,
    sleep_stress REAL, spo2_avg REAL, skin_temp_dev REAL,
    feedback TEXT,
    source TEXT, fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS hrv (
    date TEXT PRIMARY KEY,
    rmssd_night REAL, weekly_avg REAL, baseline_low REAL, baseline_high REAL, status TEXT,
    source TEXT, fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS intraday (
    date TEXT NOT NULL, metric TEXT NOT NULL, ts INTEGER NOT NULL, value REAL,
    PRIMARY KEY (date, metric, ts)
);

CREATE TABLE IF NOT EXISTS bb_event (
    date TEXT NOT NULL, start_local TEXT NOT NULL, event_type TEXT, duration_s REAL, impact REAL,
    feedback TEXT, PRIMARY KEY (date, start_local, event_type)
);

CREATE TABLE IF NOT EXISTS vo2max (
    date TEXT PRIMARY KEY, value REAL, value_precise REAL, sport TEXT, source TEXT
);

CREATE TABLE IF NOT EXISTS fitness_age (
    date TEXT PRIMARY KEY, fitness_age REAL, chrono_age REAL, achievable REAL, bmi REAL, source TEXT
);

CREATE TABLE IF NOT EXISTS race_prediction (
    date TEXT PRIMARY KEY, t5k REAL, t10k REAL, thalf REAL, tmarathon REAL, source TEXT
);

CREATE TABLE IF NOT EXISTS running_metric (
    date TEXT NOT NULL, metric TEXT NOT NULL, value REAL, source TEXT,
    PRIMARY KEY (date, metric)
);

CREATE TABLE IF NOT EXISTS activity (
    activity_id INTEGER PRIMARY KEY,
    start_local TEXT, start_gmt TEXT, date TEXT,
    type_key TEXT, sport_group TEXT, name TEXT,
    duration_s REAL, moving_s REAL, distance_m REAL,
    avg_hr REAL, max_hr REAL, calories REAL,
    aerobic_te REAL, anaerobic_te REAL, garmin_load REAL,
    avg_speed REAL, avg_cadence REAL, elev_gain REAL, avg_power REAL,
    hr_z1 REAL, hr_z2 REAL, hr_z3 REAL, hr_z4 REAL, hr_z5 REAL,
    total_sets INTEGER, total_reps INTEGER, pool_length_m REAL,
    is_pr INTEGER, has_samples INTEGER DEFAULT 0,
    source TEXT, fetched_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_activity_date ON activity(date);

CREATE TABLE IF NOT EXISTS swim_length (
    activity_id INTEGER NOT NULL, idx INTEGER NOT NULL,
    stroke TEXT, distance_m REAL, time_s REAL, strokes REAL, swolf REAL, length_type TEXT,
    PRIMARY KEY (activity_id, idx)
);

CREATE TABLE IF NOT EXISTS strength_set (
    workout_start TEXT NOT NULL, exercise TEXT NOT NULL, set_idx INTEGER NOT NULL,
    workout_no INTEGER, workout_name TEXT, workout_duration_s REAL,
    set_type TEXT, weight_kg REAL, reps REAL, rpe REAL, distance_m REAL, seconds REAL, notes TEXT,
    date TEXT, matched_activity_id INTEGER, source TEXT,
    PRIMARY KEY (workout_start, exercise, set_idx)
);
CREATE INDEX IF NOT EXISTS ix_strength_date ON strength_set(date);

CREATE TABLE IF NOT EXISTS exercise_map (
    exercise TEXT PRIMARY KEY, primary_muscles TEXT, secondary_muscles TEXT, user_defined INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS personal_record (
    type_key TEXT PRIMARY KEY, label TEXT, value REAL, unit TEXT, date TEXT, activity_id INTEGER, source TEXT
);

CREATE TABLE IF NOT EXISTS profile (
    key TEXT PRIMARY KEY, value TEXT
);

CREATE TABLE IF NOT EXISTS journal_tag_def (
    tag TEXT PRIMARY KEY, label TEXT, kind TEXT, category TEXT, active INTEGER DEFAULT 1, sort INTEGER DEFAULT 100
);

CREATE TABLE IF NOT EXISTS journal (
    date TEXT NOT NULL, tag TEXT NOT NULL, value REAL,
    PRIMARY KEY (date, tag)
);

CREATE TABLE IF NOT EXISTS race (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT, date TEXT, distance_m REAL, goal_s REAL, terrain TEXT, active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS plan_session (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT, sport TEXT, duration_min REAL, intensity TEXT, note TEXT
);

CREATE TABLE IF NOT EXISTS derived_daily (
    date TEXT PRIMARY KEY,
    data TEXT  -- JSON blob of derived metrics for the day
);

CREATE TABLE IF NOT EXISTS forecast (
    made_on TEXT NOT NULL, metric TEXT NOT NULL, target_date TEXT NOT NULL,
    q10 REAL, q50 REAL, q90 REAL,
    PRIMARY KEY (made_on, metric, target_date)
);

CREATE TABLE IF NOT EXISTS llm_cache (
    key TEXT PRIMARY KEY, kind TEXT, text TEXT, created TEXT
);

CREATE TABLE IF NOT EXISTS sync_state (
    key TEXT PRIMARY KEY, value TEXT
);
