#!/usr/bin/env python3
import csv
import hmac
import io
import json
import os
import secrets
import sqlite3
import subprocess
from contextlib import closing
from datetime import date, datetime, timedelta
from functools import wraps
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import (
    Flask,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"
DB_PATH = INSTANCE_DIR / "workout_tracker.db"
SECRET_PATH = BASE_DIR / "instance" / "secret_key.txt"
API_TOKEN_PATH = INSTANCE_DIR / "api_token.txt"
WORKSPACE_DIR = Path('/home/node/.openclaw/workspace')
LOCATION_DB_PATH = WORKSPACE_DIR / 'location-automation' / 'data' / 'geofence_hooks.db'
CRON_JOBS_PATH = Path('/home/node/.openclaw/cron/jobs.json')
CRON_RUNS_DIR = Path('/home/node/.openclaw/cron/runs')
WHOOP_STATUS_CMD = ['python3', str(WORKSPACE_DIR / '.local' / 'bin' / 'whoop_agent.py'), 'status']
LA_TZ = ZoneInfo('America/Los_Angeles')

app = Flask(
    __name__,
    instance_path=str(INSTANCE_DIR),
    instance_relative_config=True,
    template_folder=str(BASE_DIR / 'app' / 'templates'),
    static_folder=str(BASE_DIR / 'app' / 'static'),
)
INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
if SECRET_PATH.exists():
    app.secret_key = SECRET_PATH.read_text().strip()
else:
    key = secrets.token_hex(32)
    SECRET_PATH.write_text(key)
    os.chmod(SECRET_PATH, 0o600)
    app.secret_key = key
is_secure_cookie = os.environ.get("SESSION_COOKIE_SECURE", "").lower() in {"1", "true", "yes", "on"}
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=is_secure_cookie,
    MAX_CONTENT_LENGTH=10 * 1024 * 1024,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    email TEXT,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS exercises (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    category TEXT,
    equipment TEXT,
    tracking_mode TEXT NOT NULL DEFAULT 'strength',
    notes TEXT,
    is_archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workout_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    notes TEXT,
    is_archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workout_template_exercises (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id INTEGER NOT NULL,
    exercise_id INTEGER NOT NULL,
    sort_order INTEGER NOT NULL,
    target_sets INTEGER,
    target_reps_min INTEGER,
    target_reps_max INTEGER,
    target_weight REAL,
    target_rpe REAL,
    rest_seconds INTEGER,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS workouts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    template_id INTEGER,
    name TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    status TEXT NOT NULL DEFAULT 'in_progress',
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workout_exercises (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workout_id INTEGER NOT NULL,
    exercise_id INTEGER,
    sort_order INTEGER NOT NULL,
    exercise_name_snapshot TEXT NOT NULL,
    notes TEXT,
    rest_seconds INTEGER
);
CREATE TABLE IF NOT EXISTS sets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workout_exercise_id INTEGER NOT NULL,
    set_number INTEGER NOT NULL,
    set_type TEXT NOT NULL DEFAULT 'normal',
    weight REAL,
    reps INTEGER,
    duration_seconds INTEGER,
    distance_miles REAL,
    calories INTEGER,
    rpe REAL,
    is_completed INTEGER NOT NULL DEFAULT 1,
    notes TEXT,
    logged_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, key)
);
"""

DEFAULT_TRAINING_PLAN = {
    "version": 1,
    "name": "6-Week Workout Block",
    "start_date": "2026-03-23",
    "end_date": "2026-05-01",
    "goal": "Build a consistent 6-week training block focused on heart health, progressive strength, and sustainable adherence.",
    "weekly_structure": [
        {"day": "Monday", "title": "💪 Pull (Back & Biceps)", "type": "pull"},
        {"day": "Tuesday", "title": "💪 Push (Chest & Triceps)", "type": "push"},
        {"day": "Wednesday", "title": "🧘 Recovery / Mobility", "type": "recovery"},
        {"day": "Thursday", "title": "🏋️ Legs + Core", "type": "legs"},
        {"day": "Friday", "title": "🫀 Cardio / Conditioning", "type": "cardio"},
        {"day": "Saturday", "title": "Optional easy activity", "type": "optional"},
        {"day": "Sunday", "title": "Full rest", "type": "rest"},
    ],
    "progression_rules": [
        "Complete all work sets with good form and about 1–2 reps in reserve before increasing next week.",
        "If reps fall off badly or form gets sloppy, repeat the same weight next week.",
        "On push day, prioritize clean reps over weight jumps.",
        "For isolation lifts, add reps before load when needed.",
        "Cardio should improve heart health and work capacity without wrecking recovery.",
    ],
    "milestones": [
        {
            "label": "By end of Week 2",
            "items": [
                "Pendlay row: hold 115 strongly or move toward 120",
                "Squat: move from 125 toward 130",
                "Incline bench: stabilize clean sets at 85",
                "Complete cardio without skipping both week-1 and week-2 sessions",
            ],
        },
        {
            "label": "By end of Week 4",
            "items": [
                "Pendlay row: 120 x 8–10 working range",
                "Squat: 130–135 working range",
                "RDL: 135 working range",
                "Incline bench: 87.5 x 6–8 or 85 with cleaner volume",
            ],
        },
        {
            "label": "By end of Week 6",
            "items": [
                "Pendlay row: 125 x 8 target if recovery allows",
                "Squat: 135 x 6–8 target if form stays solid",
                "Flat bench: 85 x 8 working sets",
                "Cardio: maintain one steady session per week across the block",
            ],
        },
    ],
    "weeks": [
        {
            "week": 1,
            "label": "Week 1 — Mar 23 to Mar 27",
            "focus": "Set the rhythm and hit clean baseline numbers.",
            "targets": {
                "pull": [
                    "Pendlay Row: 115 × 10 × 3",
                    "Seated Cable Row: 75–80 × 10 × 3",
                    "Pull-ups / negatives: 3 sets",
                    "Single-arm Lat Pulldown: 35 × 12/side × 3",
                    "Face Pull: 25 × 15 × 3",
                    "DB Curl: 25 × 10–12 × 3",
                ],
                "push": [
                    "Incline Bench: 85 × 8 × 3",
                    "Flat Bench: 80 × 8 × 3",
                    "Cable Fly: 20–25 × 10–12 × 3",
                    "Tricep Pushdown: 45 × 8–10 × 3",
                    "Tricep Extension: 10–15 × 10–12 × 3",
                    "Lateral Raise: 5 × 15–20 × 3",
                ],
                "recovery": [
                    "Easy recovery only — walk, mobility, or light movement.",
                ],
                "legs": [
                    "Squat: 125 × 8 × 3",
                    "RDL: 125 × 10 × 3",
                    "DB Lunges: 25 lb × 20 total × 3",
                    "Bulgarian Split Squat: last good load × 24 total × 3",
                    "Cable Crunch: 35 × 15 × 3",
                    "Plank: 60–75 sec × 3",
                ],
                "cardio": [
                    "30–35 min easy/moderate run, incline walk, or intervals",
                ],
            },
        },
        {
            "week": 2,
            "label": "Week 2 — Mar 30 to Apr 3",
            "focus": "Small bump on the main lifts if Week 1 felt clean.",
            "targets": {
                "pull": [
                    "Pendlay Row: 120 × 8–10 × 3",
                    "Seated Cable Row: 80 × 10 × 3",
                    "Pull-ups / negatives: 3 sets",
                    "Single-arm Lat Pulldown: 35–40 × 12/side × 3",
                    "Face Pull: 25–30 × 15 × 3",
                    "DB Curl: 25 × 12 × 3",
                ],
                "push": [
                    "Incline Bench: 87.5 × 6–8 × 3 if Week 1 is clean; otherwise repeat 85",
                    "Flat Bench: 82.5–85 × 8 × 3",
                    "Cable Fly: 20–25 × 10–12 × 3",
                    "Tricep Pushdown: 45 × 10 × 3",
                    "Tricep Extension: 10–15 × 10–12 × 3",
                    "Lateral Raise: 5 × 15–20 × 3",
                ],
                "recovery": [
                    "Easy recovery only — walk, mobility, or light movement.",
                ],
                "legs": [
                    "Squat: 130 × 8 × 3",
                    "RDL: 135 × 8–10 × 3",
                    "DB Lunges: 25 lb × 20 total × 3",
                    "Bulgarian Split Squat: slight bump only if stable",
                    "Cable Crunch: 35–40 × 15 × 3",
                    "Plank: 75 sec × 3",
                ],
                "cardio": [
                    "35–40 min steady or intervals",
                ],
            },
        },
        {
            "week": 3,
            "label": "Week 3 — Apr 6 to Apr 10",
            "focus": "Hardest week of the first half; no ego lifting.",
            "targets": {
                "pull": [
                    "Pendlay Row: 120–125 × 8 × 3",
                    "Seated Cable Row: 80–85 × 10 × 3",
                    "Pull-ups / negatives: 3 sets",
                    "Single-arm Lat Pulldown: 40 × 10–12/side × 3",
                    "Face Pull: 30 × 15 × 3",
                    "DB Curl: 25–30 × 10–12 × 3",
                ],
                "push": [
                    "Incline Bench: 90 × 6–8 × 3 if recovery allows; otherwise 87.5",
                    "Flat Bench: 85 × 8 × 3",
                    "Cable Fly: 20–25 × 10–12 × 3",
                    "Tricep Pushdown: 45–50 × 8–10 × 3",
                    "Tricep Extension: 10–15 × 10–12 × 3",
                    "Lateral Raise: 5 × 15–20 × 3, strict form",
                ],
                "recovery": [
                    "Easy recovery only — walk, mobility, or light movement.",
                ],
                "legs": [
                    "Squat: 135 × 6–8 × 3",
                    "RDL: 135 × 10 × 3",
                    "DB Lunges: 25–30 lb × 20 total × 3",
                    "Bulgarian Split Squat: hold or slight bump",
                    "Cable Crunch: 40 × 12–15 × 3",
                    "Plank: 75–90 sec × 3",
                ],
                "cardio": [
                    "30–40 min steady or intervals",
                ],
            },
        },
        {
            "week": 4,
            "label": "Week 4 — Apr 13 to Apr 17",
            "focus": "Consolidation week: repeat the best successful loads or add a small bump.",
            "targets": {
                "pull": [
                    "Repeat the best successful row week or add a small bump if reps stay clean",
                    "Keep accessories clean and steady",
                ],
                "push": [
                    "Repeat the best successful incline week or add a small bump if bar speed stays good",
                    "Keep lateral raises and triceps strict",
                ],
                "recovery": [
                    "Easy recovery only — walk, mobility, or light movement.",
                ],
                "legs": [
                    "Repeat or slightly improve the best squat/RDL week",
                    "No grinding",
                ],
                "cardio": [
                    "One steady 30–40 min session",
                ],
            },
        },
        {
            "week": 5,
            "label": "Week 5 — Apr 20 to Apr 24",
            "focus": "Push again if Week 4 felt solid.",
            "targets": {
                "pull": [
                    "Pendlay Row: aim for 125 × 8 × 3",
                    "Seated Cable Row: 85 × 10 × 3",
                ],
                "push": [
                    "Incline Bench: 90 × 6–8 × 3 or best clean repeat",
                    "Flat Bench: 85 × 8 × 3",
                ],
                "recovery": [
                    "Easy recovery only — walk, mobility, or light movement.",
                ],
                "legs": [
                    "Squat: 135 × 8 or small bump if clearly ready",
                    "RDL: 135–145 range depending on feel",
                ],
                "cardio": [
                    "35–40 min steady cardio or intervals",
                ],
            },
        },
        {
            "week": 6,
            "label": "Week 6 — Apr 27 to May 1",
            "focus": "Finish strong and assess what to change next.",
            "targets": {
                "pull": [
                    "Hit the best clean row performance of the block",
                ],
                "push": [
                    "Hit the best clean incline and flat bench performance of the block",
                ],
                "recovery": [
                    "Easy recovery only — walk, mobility, or light movement.",
                ],
                "legs": [
                    "Hit the best clean squat and RDL performance of the block",
                ],
                "cardio": [
                    "Maintain cardio consistency and assess recovery at the end of the block",
                ],
            },
        },
    ],
}


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def utc_now():
    return datetime.now(ZoneInfo('UTC'))


def parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None


def format_relative(dt):
    if not dt:
        return 'never'
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo('UTC'))
    delta = utc_now() - dt.astimezone(ZoneInfo('UTC'))
    seconds = int(delta.total_seconds())
    future = seconds < 0
    seconds = abs(seconds)
    if seconds < 60:
        amount, unit = seconds, 's'
    elif seconds < 3600:
        amount, unit = seconds // 60, 'm'
    elif seconds < 86400:
        amount, unit = seconds // 3600, 'h'
    else:
        amount, unit = seconds // 86400, 'd'
    return f"in {amount}{unit}" if future else f"{amount}{unit} ago"


def to_local_label(value):
    dt = parse_iso(value) if isinstance(value, str) else value
    if not dt:
        return 'never'
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo('UTC'))
    return dt.astimezone(LA_TZ).strftime('%b %-d, %-I:%M %p')


def _bars_for_daily_counts(rows, days=14):
    today = datetime.now(LA_TZ).date()
    start = today - timedelta(days=days - 1)
    counts = {row['d']: row['c'] for row in rows}
    max_count = max([counts.get((start + timedelta(days=i)).isoformat(), 0) for i in range(days)] + [1])
    items = []
    for i in range(days):
        d = start + timedelta(days=i)
        count = counts.get(d.isoformat(), 0)
        items.append({
            'date': d.isoformat(),
            'label': d.strftime('%m/%d'),
            'count': count,
            'percent': round((count / max_count) * 100, 1) if max_count else 0,
        })
    return items


def _level_from_age(hours, warn_hours, error_hours):
    if hours is None:
        return 'ERROR'
    if hours >= error_hours:
        return 'ERROR'
    if hours >= warn_hours:
        return 'WARN'
    return 'OK'


def collect_ops_snapshot(user_id):
    now = utc_now()
    snapshot = {
        'generated_at_local': now.astimezone(LA_TZ).strftime('%Y-%m-%d %I:%M:%S %p %Z'),
        'status_cards': [],
        'recent_signals': [],
    }

    active = query_one("SELECT id, name, started_at FROM workouts WHERE user_id = ? AND status = 'in_progress' ORDER BY started_at DESC LIMIT 1", (user_id,))
    completed_rows = query_all("SELECT date(started_at) AS d, count(*) AS c FROM workouts WHERE user_id = ? AND status = 'completed' AND date(started_at) >= date('now','-13 day') GROUP BY date(started_at) ORDER BY d", (user_id,))
    top_types = query_all("SELECT name, count(*) AS c FROM workouts WHERE user_id = ? AND status = 'completed' AND date(started_at) >= date('now','-30 day') GROUP BY name ORDER BY c DESC, name LIMIT 8", (user_id,))
    summary = query_one("SELECT count(*) AS total, sum(case when status='completed' then 1 else 0 end) AS completed, sum(case when status='in_progress' then 1 else 0 end) AS in_progress FROM workouts WHERE user_id = ?", (user_id,))
    last_completed = query_one("SELECT name, started_at, ended_at FROM workouts WHERE user_id = ? AND status = 'completed' ORDER BY started_at DESC LIMIT 1", (user_id,))
    completed_30d_row = query_one("SELECT count(*) AS c FROM workouts WHERE user_id = ? AND status = 'completed' AND date(started_at) >= date('now','-30 day')", (user_id,))
    workouts = {
        'completed_14d': _bars_for_daily_counts(completed_rows, 14),
        'completed_30d': (completed_30d_row['c'] if completed_30d_row else 0) or 0,
        'in_progress': (summary['in_progress'] if summary else 0) or 0,
        'last_completed_label': to_local_label(last_completed['ended_at'] or last_completed['started_at']) if last_completed else 'never',
        'top_types': [{'name': row['name'], 'count': row['c']} for row in top_types],
    }
    snapshot['workouts'] = workouts
    workout_level = 'OK' if workouts['completed_30d'] >= 8 else ('WARN' if workouts['completed_30d'] >= 4 else 'ERROR')
    workout_summary = f"{workouts['completed_30d']} completed in 30d"
    if active:
        workout_summary += f" · active: {active['name']}"
        snapshot['recent_signals'].append({'title': 'Active workout open', 'detail': f"{active['name']} started {to_local_label(active['started_at'])}"})
    snapshot['status_cards'].append({'name': 'Workout tracker', 'level': workout_level, 'summary': workout_summary, 'detail': f"Last completed {workouts['last_completed_label']}"})

    location = {
        'visits_14d': _bars_for_daily_counts([], 14),
        'events_30d': 0,
        'last_event_label': 'never',
        'top_zones': [],
    }
    if LOCATION_DB_PATH.exists():
        with sqlite3.connect(LOCATION_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            visit_rows = conn.execute("SELECT date(timestamp) AS d, count(*) AS c FROM inbound_events WHERE event='arrive' AND date(timestamp) >= date('now','-13 day') GROUP BY date(timestamp) ORDER BY d").fetchall()
            top_zone_rows = conn.execute("SELECT coalesce(trigger_name, place_name, address, 'unknown') AS zone, count(*) AS c FROM inbound_events WHERE date(timestamp) >= date('now','-30 day') GROUP BY 1 ORDER BY c DESC, zone LIMIT 8").fetchall()
            last_event = conn.execute("SELECT event, timestamp, coalesce(trigger_name, place_name, address, 'unknown') AS label FROM inbound_events ORDER BY timestamp DESC LIMIT 1").fetchone()
            events_30d = conn.execute("SELECT count(*) AS c FROM inbound_events WHERE date(timestamp) >= date('now','-30 day')").fetchone()
            location['visits_14d'] = _bars_for_daily_counts(visit_rows, 14)
            location['events_30d'] = (events_30d['c'] if events_30d else 0) or 0
            location['top_zones'] = [{'name': row['zone'], 'count': row['c']} for row in top_zone_rows]
            if last_event:
                last_dt = parse_iso(last_event['timestamp'])
                hours = ((now - last_dt.astimezone(ZoneInfo('UTC'))).total_seconds() / 3600.0) if last_dt else None
                location['last_event_label'] = f"{last_event['event']} · {last_event['label']} · {to_local_label(last_event['timestamp'])}"
                snapshot['status_cards'].append({'name': 'Location tracker', 'level': _level_from_age(hours, 24, 72), 'summary': f"Last event {format_relative(last_dt)}", 'detail': location['last_event_label']})
                snapshot['recent_signals'].append({'title': 'Latest location event', 'detail': location['last_event_label']})
            else:
                snapshot['status_cards'].append({'name': 'Location tracker', 'level': 'ERROR', 'summary': 'No location events found', 'detail': 'The location DB exists but no inbound events are stored yet.'})
    else:
        snapshot['status_cards'].append({'name': 'Location tracker', 'level': 'ERROR', 'summary': 'DB missing', 'detail': str(LOCATION_DB_PATH)})
    snapshot['location'] = location

    browser_card = {'name': 'Browser automation', 'level': 'WARN', 'summary': 'Unknown', 'detail': ''}
    browser_path = str(Path.home() / '.cache' / 'ms-playwright')
    try:
        chrome_check = subprocess.run(['bash', '-lc', "find ~/.cache/ms-playwright -name chrome -type f | head -1"], capture_output=True, text=True, timeout=4)
        chrome_path = (chrome_check.stdout or '').strip()
        cdp_check = subprocess.run(['bash', '-lc', "curl -fsS http://127.0.0.1:18800/json/version | head -c 300"], capture_output=True, text=True, timeout=4)
        if cdp_check.returncode == 0 and cdp_check.stdout.strip():
            browser_card.update({'level': 'OK', 'summary': 'CDP responding', 'detail': cdp_check.stdout.strip()[:240]})
        elif chrome_path:
            browser_card.update({'level': 'WARN', 'summary': 'Installed but stopped', 'detail': chrome_path})
        else:
            browser_card.update({'level': 'ERROR', 'summary': 'Browser binary missing', 'detail': browser_path})
    except Exception as exc:
        browser_card.update({'level': 'WARN', 'summary': 'Probe uncertain', 'detail': str(exc)})
    snapshot['status_cards'].append(browser_card)

    whoop_card = {'name': 'WHOOP token', 'level': 'WARN', 'summary': 'Unknown', 'detail': ''}
    try:
        result = subprocess.run(WHOOP_STATUS_CMD, capture_output=True, text=True, timeout=12)
        data = json.loads(result.stdout or '{}') if result.returncode == 0 else {}
        authenticated = bool(data.get('authenticated'))
        needs_refresh = bool(data.get('needs_refresh'))
        if authenticated and not needs_refresh:
            whoop_card.update({'level': 'OK', 'summary': 'Healthy', 'detail': 'Authenticated and current.'})
        elif authenticated and needs_refresh:
            whoop_card.update({'level': 'WARN', 'summary': 'Needs refresh', 'detail': 'Token exists but currently needs refresh.'})
        else:
            whoop_card.update({'level': 'ERROR', 'summary': 'Not authenticated', 'detail': (result.stderr or result.stdout).strip()[:240]})
    except Exception as exc:
        whoop_card.update({'level': 'ERROR', 'summary': 'Probe failed', 'detail': str(exc)})
    snapshot['status_cards'].append(whoop_card)

    cron_jobs = []
    scheduler_total = scheduler_ok = task_total = task_ok = hidden_failures = 0
    if CRON_JOBS_PATH.exists():
        cron_doc = json.loads(CRON_JOBS_PATH.read_text())
        for job in cron_doc.get('jobs', []):
            state = job.get('state', {})
            scheduler_total += 1
            if state.get('lastRunStatus') == 'ok':
                scheduler_ok += 1
            task_level = 'OK' if state.get('lastRunStatus') == 'ok' else 'ERROR'
            last_result = state.get('lastRunStatus') or 'n/a'
            run_file = CRON_RUNS_DIR / f"{job['id']}.jsonl"
            if run_file.exists():
                lines = [line for line in run_file.read_text(errors='ignore').splitlines() if line.strip()]
                if lines:
                    try:
                        event = json.loads(lines[-1])
                        summary = (event.get('summary') or event.get('error') or '').strip()
                        if summary:
                            last_result = summary
                        lower = summary.lower()
                        if any(token in lower for token in [' failed', 'failure', 'error', 'expired_access_token', 'not-delivered']):
                            task_level = 'WARN' if task_level == 'OK' else 'ERROR'
                            hidden_failures += 1
                    except Exception:
                        pass
            task_total += 1
            if task_level == 'OK':
                task_ok += 1
            next_run_ms = state.get('nextRunAtMs')
            next_run_label = 'n/a'
            if next_run_ms:
                next_run_label = datetime.fromtimestamp(next_run_ms / 1000, tz=ZoneInfo('UTC')).astimezone(LA_TZ).strftime('%b %-d, %-I:%M %p')
            cron_jobs.append({
                'name': job.get('name', job.get('id')),
                'level': task_level,
                'schedule': (job.get('schedule') or {}).get('expr') or (job.get('schedule') or {}).get('kind') or 'unknown',
                'next_run_label': next_run_label,
                'last_result': (last_result[:110] + '…') if len(last_result) > 110 else last_result,
            })
    scheduler_rate = f"{round((scheduler_ok / scheduler_total) * 100)}%" if scheduler_total else 'n/a'
    task_rate = f"{round((task_ok / task_total) * 100)}%" if task_total else 'n/a'
    snapshot['cron'] = {'jobs': cron_jobs, 'scheduler_success_rate': scheduler_rate, 'task_success_rate': task_rate}
    snapshot['status_cards'].append({'name': 'Cron jobs', 'level': 'WARN' if hidden_failures else 'OK', 'summary': f"{scheduler_rate} scheduler success · {task_rate} task success", 'detail': f"{len(cron_jobs)} jobs tracked" + (f" · {hidden_failures} hidden failure(s) found in run summaries" if hidden_failures else '')})

    startup_checks = []
    if (WORKSPACE_DIR / 'scripts' / 'container-startup.sh').exists():
        startup_checks.append('container startup present')
    if (WORKSPACE_DIR / 'career' / 'site' / 'server.py').exists():
        startup_checks.append('career portal installed')
    snapshot['status_cards'].append({'name': 'Workspace plumbing', 'level': 'INFO', 'summary': 'Persistent startup hooks configured', 'detail': ', '.join(startup_checks) if startup_checks else 'No extra startup hooks detected'})

    if hidden_failures:
        snapshot['recent_signals'].append({'title': 'Cron hidden failures', 'detail': f'{hidden_failures} job(s) looked scheduler-green but included failure text in their run output.'})
    if len(snapshot['recent_signals']) < 3:
        snapshot['recent_signals'].append({'title': 'Workout cadence', 'detail': f"{workouts['completed_30d']} completed workout(s) in the last 30 days."})
    return snapshot


def get_db():
    if "db" not in g:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        g.db = conn
    return g.db


def init_db():
    db = sqlite3.connect(DB_PATH)
    try:
        db.executescript(SCHEMA)
        # lightweight migrations
        cols = {row[1] for row in db.execute("PRAGMA table_info(exercises)")}
        if 'tracking_mode' not in cols:
            db.execute("ALTER TABLE exercises ADD COLUMN tracking_mode TEXT NOT NULL DEFAULT 'strength'")
        set_cols = {row[1] for row in db.execute("PRAGMA table_info(sets)")}
        if 'distance_miles' not in set_cols:
            db.execute("ALTER TABLE sets ADD COLUMN distance_miles REAL")
        if 'calories' not in set_cols:
            db.execute("ALTER TABLE sets ADD COLUMN calories INTEGER")
        db.commit()
    finally:
        db.close()


@app.before_request
def setup_db():
    init_db()


@app.teardown_appcontext
def close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


@app.context_processor
def inject_globals():
    user = current_user()
    return {
        "current_user": user,
        "active_workout": get_active_workout(user["id"]) if user else None,
        "active_training_plan": get_training_plan(user["id"]) if user else None,
    }


def query_one(sql, args=()):
    return get_db().execute(sql, args).fetchone()


def query_all(sql, args=()):
    return get_db().execute(sql, args).fetchall()


def execute(sql, args=()):
    db = get_db()
    cur = db.execute(sql, args)
    db.commit()
    return cur


def get_setting(user_id, key, default=None):
    row = query_one("SELECT value_json FROM settings WHERE user_id = ? AND key = ?", (user_id, key))
    if not row:
        return default
    try:
        return json.loads(row["value_json"])
    except json.JSONDecodeError:
        return default


def set_setting(user_id, key, value):
    execute(
        """
        INSERT INTO settings (user_id, key, value_json, updated_at)
        VALUES (?,?,?,?)
        ON CONFLICT(user_id, key)
        DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at
        """,
        (user_id, key, json.dumps(value), now_iso()),
    )


def get_training_plan(user_id):
    plan = get_setting(user_id, "active_training_plan")
    if plan:
        return plan
    plan = json.loads(json.dumps(DEFAULT_TRAINING_PLAN))
    set_setting(user_id, "active_training_plan", plan)
    return plan


def parse_iso_date(value):
    return datetime.fromisoformat(value).date()


def get_plan_week(plan, on_date=None):
    on_date = on_date or date.today()
    start_date = parse_iso_date(plan["start_date"])
    if on_date < start_date:
        return plan["weeks"][0]
    delta_days = (on_date - start_date).days
    week_num = delta_days // 7 + 1
    weeks = plan.get("weeks") or []
    if not weeks:
        return None
    week_num = max(1, min(week_num, len(weeks)))
    return next((w for w in weeks if w.get("week") == week_num), weeks[min(week_num - 1, len(weeks) - 1)])


def get_workout_type_from_name(name):
    lowered = (name or "").lower()
    if "pull" in lowered:
        return "pull"
    if "push" in lowered:
        return "push"
    if "leg" in lowered:
        return "legs"
    if "cardio" in lowered or "condition" in lowered or "run" in lowered:
        return "cardio"
    if "recovery" in lowered or "mobility" in lowered:
        return "recovery"
    return None


def get_current_targets_for_workout(plan, workout_name, on_date=None):
    week = get_plan_week(plan, on_date=on_date)
    workout_type = get_workout_type_from_name(workout_name)
    if not week or not workout_type:
        return None
    targets = week.get("targets", {}).get(workout_type)
    if not targets:
        return None
    return {"week": week, "type": workout_type, "items": targets}


def has_completed_workout_type_on_date(user_id, workout_type, on_date):
    rows = query_all(
        "SELECT name FROM workouts WHERE user_id = ? AND status = 'completed' AND date(started_at) = ?",
        (user_id, on_date.isoformat()),
    )
    return any(get_workout_type_from_name(row["name"]) == workout_type for row in rows)


def get_next_plan_day(plan, user_id=None, on_date=None):
    on_date = on_date or date.today()
    weekdays = {
        0: "Monday",
        1: "Tuesday",
        2: "Wednesday",
        3: "Thursday",
        4: "Friday",
        5: "Saturday",
        6: "Sunday",
    }
    by_day = {item["day"]: item for item in plan.get("weekly_structure", [])}
    for offset in range(14):
        d = on_date.fromordinal(on_date.toordinal() + offset)
        entry = by_day.get(weekdays[d.weekday()])
        if not entry or entry.get("type") in {"optional", "rest"}:
            continue
        if user_id and has_completed_workout_type_on_date(user_id, entry.get("type"), d):
            continue
        return {"date": d.isoformat(), **entry}
    return None


def current_user():
    uid = session.get("user_id")
    if uid:
        user = query_one("SELECT * FROM users WHERE id = ?", (uid,))
        if user:
            return user
    # single-user mode: auto-use the first account if it exists
    user = query_one("SELECT * FROM users ORDER BY id LIMIT 1")
    if user:
        session["user_id"] = user["id"]
    return user


def load_api_token():
    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    if not API_TOKEN_PATH.exists() or not API_TOKEN_PATH.read_text(encoding="utf-8").strip():
        API_TOKEN_PATH.write_text(secrets.token_urlsafe(32), encoding="utf-8")
        os.chmod(API_TOKEN_PATH, 0o600)
    return API_TOKEN_PATH.read_text(encoding="utf-8").strip()


def has_valid_api_token() -> bool:
    expected = load_api_token()
    authz = request.headers.get("Authorization", "")
    bearer = authz.removeprefix("Bearer ").strip() if authz.startswith("Bearer ") else None
    alt = request.headers.get("X-Workout-Token", "").strip() or None
    provided = bearer or alt
    return bool(provided and hmac.compare_digest(provided, expected))


def csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def validate_csrf() -> bool:
    expected = session.get("csrf_token")
    provided = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    return bool(expected and provided and hmac.compare_digest(expected, provided))


@app.before_request
def enforce_csrf():
    if request.method == "POST":
        if request.path.startswith("/api/") and has_valid_api_token():
            return None
        if not validate_csrf():
            if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.path.startswith("/api/"):
                return jsonify({"error": "CSRF validation failed"}), 400
            abort(400, description="CSRF validation failed")


@app.context_processor
def inject_csrf_token():
    return {"csrf_token": csrf_token}

app.jinja_env.globals["csrf_token"] = csrf_token


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user():
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper


def user_count():
    row = query_one("SELECT COUNT(*) AS c FROM users")
    return row["c"]


def get_active_workout(user_id):
    return query_one(
        "SELECT * FROM workouts WHERE user_id = ? AND status = 'in_progress' ORDER BY started_at DESC LIMIT 1",
        (user_id,),
    )


def get_workout(workout_id, user_id):
    workout = query_one("SELECT * FROM workouts WHERE id = ? AND user_id = ?", (workout_id, user_id))
    if not workout:
        abort(404)
    return workout


def get_workout_exercises(workout_id):
    exercises = query_all(
        "SELECT * FROM workout_exercises WHERE workout_id = ? ORDER BY sort_order, id",
        (workout_id,),
    )
    enriched = []
    for ex in exercises:
        sets = query_all(
            "SELECT * FROM sets WHERE workout_exercise_id = ? ORDER BY set_number, id",
            (ex["id"],),
        )
        ex_meta = query_one("SELECT * FROM exercises WHERE id = ?", (ex["exercise_id"],)) if ex["exercise_id"] else None
        completed_sets = sum(1 for s in sets if s["is_completed"])
        total_sets = len(sets)
        enriched.append({
            "exercise": ex,
            "exercise_meta": ex_meta,
            "sets": sets,
            "last": get_last_performance(ex["exercise_id"], workout_id) if ex["exercise_id"] else None,
            "completed_sets": completed_sets,
            "total_sets": total_sets,
            "is_complete": total_sets > 0 and completed_sets == total_sets,
        })
    return enriched


def get_last_performance(exercise_id, current_workout_id):
    if not exercise_id:
        return None
    row = query_one(
        """
        SELECT s.weight, s.reps, s.rpe, s.duration_seconds, s.distance_miles, s.calories
        FROM sets s
        JOIN workout_exercises we ON we.id = s.workout_exercise_id
        JOIN workouts w ON w.id = we.workout_id
        WHERE we.exercise_id = ? AND w.id != ? AND w.status = 'completed'
        ORDER BY w.started_at DESC, s.set_number ASC LIMIT 1
        """,
        (exercise_id, current_workout_id),
    )
    return row


def create_workout_from_template(user_id, template_id=None, name=None):
    created = now_iso()
    if template_id:
        tpl = query_one("SELECT * FROM workout_templates WHERE id = ? AND user_id = ?", (template_id, user_id))
        if not tpl:
            abort(404)
        name = name or tpl["name"]
    else:
        tpl = None
        name = name or f"Workout {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    cur = execute(
        "INSERT INTO workouts (user_id, template_id, name, started_at, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        (user_id, template_id, name, created, "in_progress", created, created),
    )
    workout_id = cur.lastrowid
    if tpl:
        tpl_exs = query_all("SELECT * FROM workout_template_exercises WHERE template_id = ? ORDER BY sort_order, id", (template_id,))
        for idx, t in enumerate(tpl_exs, start=1):
            ex = query_one("SELECT * FROM exercises WHERE id = ?", (t["exercise_id"],))
            cur2 = execute(
                "INSERT INTO workout_exercises (workout_id, exercise_id, sort_order, exercise_name_snapshot, notes, rest_seconds) VALUES (?,?,?,?,?,?)",
                (workout_id, ex["id"], idx, ex["name"], t["notes"], t["rest_seconds"]),
            )
            target_sets = t["target_sets"] or 3
            for set_num in range(1, target_sets + 1):
                execute(
                    "INSERT INTO sets (workout_exercise_id, set_number, weight, reps, rpe, logged_at, is_completed) VALUES (?,?,?,?,?,?,0)",
                    (cur2.lastrowid, set_num, t["target_weight"], t["target_reps_max"], t["target_rpe"], created),
                )
    return workout_id


@app.route("/")
def index():
    if user_count() == 0:
        return redirect(url_for("setup_user"))
    return redirect(url_for("dashboard"))


@app.route("/setup", methods=["GET", "POST"])
def setup_user():
    if user_count() > 0:
        return redirect(url_for("login"))
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        email = request.form.get("email", "").strip() or None
        if not username or not password:
            flash("Username and password are required.")
        else:
            execute(
                "INSERT INTO users (username, email, password_hash, created_at) VALUES (?,?,?,?)",
                (username, email, generate_password_hash(password), now_iso()),
            )
            flash("Account created. Log in.")
            return redirect(url_for("login"))
    return render_template("setup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if user_count() == 0:
        return redirect(url_for("setup_user"))
    # single-user mode: no login wall
    return redirect(url_for("dashboard"))


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    # keep route for compatibility, but just bounce back in single-user mode
    session.clear()
    return redirect(url_for("dashboard"))


@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()
    active = get_active_workout(user["id"])
    recent = query_all("SELECT * FROM workouts WHERE user_id = ? ORDER BY started_at DESC LIMIT 5", (user["id"],))
    templates = query_all("SELECT * FROM workout_templates WHERE user_id = ? AND is_archived = 0 ORDER BY updated_at DESC LIMIT 5", (user["id"],))
    plan = get_training_plan(user["id"])
    current_week = get_plan_week(plan)
    next_plan_day = get_next_plan_day(plan, user_id=user["id"])
    return render_template(
        "dashboard.html",
        active=active,
        recent=recent,
        templates=templates,
        training_plan=plan,
        current_week=current_week,
        next_plan_day=next_plan_day,
    )


@app.route("/plan")
@login_required
def plan_view():
    user = current_user()
    plan = get_training_plan(user["id"])
    current_week = get_plan_week(plan)
    next_plan_day = get_next_plan_day(plan, user_id=user["id"])
    return render_template("plan.html", training_plan=plan, current_week=current_week, next_plan_day=next_plan_day)


@app.route("/exercises", methods=["GET", "POST"])
@login_required
def exercises():
    user = current_user()
    if request.method == "POST":
        name = request.form["name"].strip()
        if name:
            now = now_iso()
            execute(
                "INSERT INTO exercises (user_id, name, category, equipment, tracking_mode, notes, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (user["id"], name, request.form.get("category"), request.form.get("equipment"), request.form.get("tracking_mode") or 'strength', request.form.get("notes"), now, now),
            )
            flash("Exercise added.")
        return redirect(url_for("exercises"))
    items = query_all("SELECT * FROM exercises WHERE user_id = ? AND is_archived = 0 ORDER BY name", (user["id"],))
    return render_template("exercises.html", exercises=items)


@app.route("/templates", methods=["GET", "POST"])
@login_required
def templates():
    user = current_user()
    if request.method == "POST":
        name = request.form["name"].strip()
        notes = request.form.get("notes")
        exercise_ids = request.form.getlist("exercise_ids")
        if name:
            now = now_iso()
            cur = execute(
                "INSERT INTO workout_templates (user_id, name, notes, created_at, updated_at) VALUES (?,?,?,?,?)",
                (user["id"], name, notes, now, now),
            )
            template_id = cur.lastrowid
            target_sets = int(request.form.get("target_sets") or 3)
            target_reps = int(request.form.get("target_reps") or 10)
            rest_seconds = int(request.form.get("rest_seconds") or 90)
            for i, ex_id in enumerate(exercise_ids, start=1):
                execute(
                    "INSERT INTO workout_template_exercises (template_id, exercise_id, sort_order, target_sets, target_reps_min, target_reps_max, rest_seconds) VALUES (?,?,?,?,?,?,?)",
                    (template_id, int(ex_id), i, target_sets, target_reps, target_reps, rest_seconds),
                )
            flash("Template created.")
        return redirect(url_for("templates"))
    templates = query_all("SELECT * FROM workout_templates WHERE user_id = ? AND is_archived = 0 ORDER BY updated_at DESC", (user["id"],))
    exercise_options = query_all("SELECT * FROM exercises WHERE user_id = ? AND is_archived = 0 ORDER BY name", (user["id"],))
    return render_template("templates.html", templates=templates, exercise_options=exercise_options)


@app.route("/workouts/new", methods=["GET", "POST"])
@login_required
def workouts_new():
    user = current_user()
    if request.method == "POST":
        template_id = request.form.get("template_id")
        name = request.form.get("name")
        workout_id = create_workout_from_template(user["id"], int(template_id) if template_id else None, name)
        return redirect(url_for("workout_edit", workout_id=workout_id))
    templates = query_all("SELECT * FROM workout_templates WHERE user_id = ? AND is_archived = 0 ORDER BY name", (user["id"],))
    recent = query_all("SELECT * FROM workouts WHERE user_id = ? ORDER BY started_at DESC LIMIT 5", (user["id"],))
    plan = get_training_plan(user["id"])
    current_week = get_plan_week(plan)
    next_plan_day = get_next_plan_day(plan, user_id=user["id"])
    return render_template("workouts_new.html", templates=templates, recent=recent, training_plan=plan, current_week=current_week, next_plan_day=next_plan_day)


@app.route("/workouts/<int:workout_id>/edit")
@login_required
def workout_edit(workout_id):
    user = current_user()
    workout = get_workout(workout_id, user["id"])
    exercise_options = query_all("SELECT * FROM exercises WHERE user_id = ? AND is_archived = 0 ORDER BY name", (user["id"],))
    plan = get_training_plan(user["id"])
    workout_date = datetime.fromisoformat(workout["started_at"]).date() if workout["started_at"] else date.today()
    workout_targets = get_current_targets_for_workout(plan, workout["name"], on_date=workout_date)
    return render_template(
        "workout_edit.html",
        workout=workout,
        workout_exercises=get_workout_exercises(workout_id),
        exercise_options=exercise_options,
        workout_targets=workout_targets,
    )


@app.route("/workouts/<int:workout_id>/add-exercise", methods=["POST"])
@login_required
def workout_add_exercise(workout_id):
    user = current_user()
    get_workout(workout_id, user["id"])
    exercise_id = int(request.form["exercise_id"])
    ex = query_one("SELECT * FROM exercises WHERE id = ? AND user_id = ?", (exercise_id, user["id"]))
    max_order = query_one("SELECT COALESCE(MAX(sort_order), 0) AS m FROM workout_exercises WHERE workout_id = ?", (workout_id,))["m"]
    cur = execute(
        "INSERT INTO workout_exercises (workout_id, exercise_id, sort_order, exercise_name_snapshot, rest_seconds) VALUES (?,?,?,?,?)",
        (workout_id, exercise_id, max_order + 1, ex["name"], 90),
    )
    execute(
        "INSERT INTO sets (workout_exercise_id, set_number, logged_at, is_completed) VALUES (?,?,?,0)",
        (cur.lastrowid, 1, now_iso()),
    )
    return redirect(url_for("workout_edit", workout_id=workout_id))


@app.route("/workout-exercises/<int:we_id>/save", methods=["POST"])
@login_required
def save_workout_exercise(we_id):
    user = current_user()
    we = query_one(
        "SELECT we.*, w.user_id, w.id AS workout_id FROM workout_exercises we JOIN workouts w ON w.id = we.workout_id WHERE we.id = ?",
        (we_id,),
    )
    if not we or we["user_id"] != user["id"]:
        abort(404)
    set_ids = request.form.getlist("set_id")
    for sid in set_ids:
        weight = request.form.get(f"weight_{sid}") or None
        reps = request.form.get(f"reps_{sid}") or None
        duration = request.form.get(f"duration_{sid}") or None
        distance = request.form.get(f"distance_{sid}") or None
        calories = request.form.get(f"calories_{sid}") or None
        rpe = request.form.get(f"rpe_{sid}") or None
        stype = request.form.get(f"set_type_{sid}") or "normal"
        completed = 1 if request.form.get(f"done_{sid}") else 0
        execute(
            "UPDATE sets SET weight=?, reps=?, duration_seconds=?, distance_miles=?, calories=?, rpe=?, set_type=?, is_completed=?, logged_at=? WHERE id=? AND workout_exercise_id=?",
            (float(weight) if weight not in (None, "") else None,
             int(reps) if reps not in (None, "") else None,
             int(duration) if duration not in (None, "") else None,
             float(distance) if distance not in (None, "") else None,
             int(calories) if calories not in (None, "") else None,
             float(rpe) if rpe not in (None, "") else None,
             stype,
             completed,
             now_iso(),
             int(sid),
             we_id),
        )
    execute("UPDATE workout_exercises SET notes=?, rest_seconds=? WHERE id=?", (request.form.get("notes"), int(request.form.get("rest_seconds") or 90), we_id))
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in (request.headers.get("Accept") or ""):
        return jsonify({"ok": True, "workout_id": we["workout_id"], "workout_exercise_id": we_id, "saved_at": now_iso()})
    return redirect(url_for("workout_edit", workout_id=we["workout_id"]))


@app.route("/workout-exercises/<int:we_id>/add-set", methods=["POST"])
@login_required
def add_set(we_id):
    user = current_user()
    we = query_one(
        "SELECT we.*, w.user_id, w.id AS workout_id FROM workout_exercises we JOIN workouts w ON w.id = we.workout_id WHERE we.id = ?",
        (we_id,),
    )
    if not we or we["user_id"] != user["id"]:
        abort(404)
    next_num = query_one("SELECT COALESCE(MAX(set_number),0)+1 AS n FROM sets WHERE workout_exercise_id = ?", (we_id,))["n"]
    execute("INSERT INTO sets (workout_exercise_id, set_number, logged_at, is_completed) VALUES (?,?,?,0)", (we_id, next_num, now_iso()))
    return redirect(url_for("workout_edit", workout_id=we["workout_id"]))


@app.route("/workouts/<int:workout_id>/finish", methods=["POST"])
@login_required
def finish_workout(workout_id):
    user = current_user()
    get_workout(workout_id, user["id"])
    execute("UPDATE workouts SET status='completed', ended_at=?, notes=?, updated_at=? WHERE id=?", (now_iso(), request.form.get("notes"), now_iso(), workout_id))
    flash("Workout completed.")
    return redirect(url_for("workout_detail", workout_id=workout_id))


@app.route("/history")
@login_required
def history():
    user = current_user()
    rows = query_all("SELECT * FROM workouts WHERE user_id = ? ORDER BY started_at DESC LIMIT 100", (user["id"],))
    return render_template("history.html", workouts=rows)


@app.route("/workouts/<int:workout_id>")
@login_required
def workout_detail(workout_id):
    user = current_user()
    workout = get_workout(workout_id, user["id"])
    return render_template("workout_detail.html", workout=workout, workout_exercises=get_workout_exercises(workout_id))


@app.route("/workouts/<int:workout_id>/delete", methods=["POST"])
@login_required
def delete_workout(workout_id):
    user = current_user()
    workout = get_workout(workout_id, user["id"])
    wes = query_all("SELECT id FROM workout_exercises WHERE workout_id = ?", (workout_id,))
    for we in wes:
        execute("DELETE FROM sets WHERE workout_exercise_id = ?", (we["id"],))
    execute("DELETE FROM workout_exercises WHERE workout_id = ?", (workout_id,))
    execute("DELETE FROM workouts WHERE id = ? AND user_id = ?", (workout_id, user["id"]))
    flash(f"Deleted workout: {workout['name']}")
    return redirect(url_for("history"))


@app.route("/export/json")
@login_required
def export_json():
    user = current_user()
    data = {
        "workouts": [dict(r) for r in query_all("SELECT * FROM workouts WHERE user_id = ? ORDER BY started_at DESC", (user["id"],))],
        "workout_exercises": [dict(r) for r in query_all("SELECT we.* FROM workout_exercises we JOIN workouts w ON w.id = we.workout_id WHERE w.user_id = ? ORDER BY we.workout_id, we.sort_order", (user["id"],))],
        "sets": [dict(r) for r in query_all("SELECT s.* FROM sets s JOIN workout_exercises we ON we.id = s.workout_exercise_id JOIN workouts w ON w.id = we.workout_id WHERE w.user_id = ? ORDER BY s.logged_at DESC", (user["id"],))],
        "exercises": [dict(r) for r in query_all("SELECT * FROM exercises WHERE user_id = ?", (user["id"],))],
        "templates": [dict(r) for r in query_all("SELECT * FROM workout_templates WHERE user_id = ?", (user["id"],))],
        "template_exercises": [dict(r) for r in query_all("SELECT tpe.* FROM workout_template_exercises tpe JOIN workout_templates wt ON wt.id = tpe.template_id WHERE wt.user_id = ? ORDER BY tpe.template_id, tpe.sort_order", (user["id"],))],
    }
    buf = io.BytesIO(json.dumps(data, indent=2).encode())
    return send_file(buf, mimetype="application/json", as_attachment=True, download_name="workout_tracker_export.json")


@app.route("/export/csv")
@login_required
def export_csv():
    user = current_user()
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["workout_date", "workout_name", "exercise", "set_number", "weight", "reps", "duration_seconds", "distance_miles", "calories", "rpe", "set_type"])
    rows = query_all(
        """
        SELECT w.started_at, w.name, we.exercise_name_snapshot, s.set_number, s.weight, s.reps, s.duration_seconds, s.distance_miles, s.calories, s.rpe, s.set_type
        FROM workouts w
        JOIN workout_exercises we ON we.workout_id = w.id
        JOIN sets s ON s.workout_exercise_id = we.id
        WHERE w.user_id = ?
        ORDER BY w.started_at DESC, we.sort_order, s.set_number
        """,
        (user["id"],),
    )
    for r in rows:
        writer.writerow([r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10]])
    buf = io.BytesIO(out.getvalue().encode())
    return send_file(buf, mimetype="text/csv", as_attachment=True, download_name="workout_tracker_export.csv")


def ensure_exercise(user_id, ex_name, tracking_mode='strength'):
    ex = query_one("SELECT * FROM exercises WHERE user_id = ? AND lower(name)=lower(?)", (user_id, ex_name))
    if ex:
        return ex["id"]
    now = now_iso()
    cur = execute(
        "INSERT INTO exercises (user_id, name, tracking_mode, created_at, updated_at) VALUES (?,?,?,?,?)",
        (user_id, ex_name, tracking_mode, now, now),
    )
    return cur.lastrowid


def import_flat_csv_rows(user_id, grouped):
    imported = 0
    for (workout_date, workout_name), rows in grouped.items():
        cur = execute(
            "INSERT INTO workouts (user_id, name, started_at, ended_at, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (user_id, workout_name or "Imported Workout", workout_date or now_iso(), workout_date or now_iso(), "completed", now_iso(), now_iso()),
        )
        workout_id = cur.lastrowid
        by_ex = {}
        for r in rows:
            by_ex.setdefault(r.get("exercise") or "Unknown Exercise", []).append(r)
        order = 1
        for ex_name, set_rows in by_ex.items():
            tracking_mode = 'cardio' if any((sr.get('distance_miles') or sr.get('duration_seconds') or sr.get('calories')) for sr in set_rows) else 'strength'
            ex_id = ensure_exercise(user_id, ex_name, tracking_mode=tracking_mode)
            cur3 = execute(
                "INSERT INTO workout_exercises (workout_id, exercise_id, sort_order, exercise_name_snapshot) VALUES (?,?,?,?)",
                (workout_id, ex_id, order, ex_name),
            )
            for sr in set_rows:
                execute(
                    "INSERT INTO sets (workout_exercise_id, set_number, weight, reps, duration_seconds, distance_miles, calories, rpe, set_type, logged_at, is_completed) VALUES (?,?,?,?,?,?,?,?,?,?,1)",
                    (
                        cur3.lastrowid,
                        int(sr.get("set_number") or 1),
                        float(sr.get("weight") or 0) if sr.get("weight") else None,
                        int(sr.get("reps") or 0) if sr.get("reps") else None,
                        int(sr.get("duration_seconds") or 0) if sr.get("duration_seconds") else None,
                        float(sr.get("distance_miles") or 0) if sr.get("distance_miles") else None,
                        int(sr.get("calories") or 0) if sr.get("calories") else None,
                        float(sr.get("rpe") or 0) if sr.get("rpe") else None,
                        sr.get("set_type") or "normal",
                        now_iso(),
                    ),
                )
            order += 1
            imported += 1
    return imported


@app.route("/import", methods=["GET", "POST"])
@login_required
def import_csv_view():
    user = current_user()
    imported = 0
    if request.method == "POST":
        f = request.files.get("file")
        if not f or not f.filename:
            flash("Choose a CSV file.")
            return redirect(url_for("import_csv_view"))
        stream = io.StringIO(f.stream.read().decode("utf-8"))
        reader = csv.DictReader(stream)
        grouped = {}
        for row in reader:
            key = (row.get("workout_date"), row.get("workout_name"))
            grouped.setdefault(key, []).append(row)
        imported = import_flat_csv_rows(user["id"], grouped)
        flash(f"Imported {imported} exercise blocks from CSV.")
        return redirect(url_for("history"))
    return render_template("import.html")


@app.route("/import/workspace-log", methods=["POST"])
@login_required
def import_workspace_log():
    user = current_user()
    path = Path(os.path.expanduser('~/.openclaw/workspace/fitness/workout_log.jsonl'))
    if not path.exists():
        flash('No workspace workout_log.jsonl found.')
        return redirect(url_for('import_csv_view'))
    imported_workouts = 0
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            date = item.get('date') or now_iso()[:10]
            name = item.get('type') or 'Imported Workout'
            start_dt = f"{date}T12:00:00"
            cur = execute(
                "INSERT INTO workouts (user_id, name, started_at, ended_at, status, notes, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (user['id'], name, start_dt, start_dt, 'completed', item.get('notes'), now_iso(), now_iso()),
            )
            workout_id = cur.lastrowid
            order = 1
            for ex_name, payload in (item.get('exercises') or {}).items():
                display_name = ex_name.replace('_', ' ').title()
                is_cardio = any(k in payload for k in ('distance_miles', 'active_calories', 'duration_seconds')) and not payload.get('sets')
                ex_id = ensure_exercise(user['id'], display_name, tracking_mode='cardio' if is_cardio else 'strength')
                cur_we = execute(
                    "INSERT INTO workout_exercises (workout_id, exercise_id, sort_order, exercise_name_snapshot, notes) VALUES (?,?,?,?,?)",
                    (workout_id, ex_id, order, display_name, payload.get('notes') or item.get('notes')),
                )
                if payload.get('sets'):
                    for idx, s in enumerate(payload.get('sets', []), start=1):
                        execute(
                            "INSERT INTO sets (workout_exercise_id, set_number, weight, reps, duration_seconds, distance_miles, calories, rpe, set_type, notes, logged_at, is_completed) VALUES (?,?,?,?,?,?,?,?,?,?,?,1)",
                            (cur_we.lastrowid, idx, s.get('lbs'), s.get('reps'), s.get('seconds'), s.get('distance_miles'), s.get('calories') or s.get('active_calories'), s.get('rpe'), 'warmup' if s.get('warmup') else 'normal', payload.get('notes'), now_iso()),
                        )
                else:
                    execute(
                        "INSERT INTO sets (workout_exercise_id, set_number, duration_seconds, distance_miles, calories, set_type, notes, logged_at, is_completed) VALUES (?,?,?,?,?,?,?,?,1)",
                        (cur_we.lastrowid, 1, payload.get('duration_seconds'), payload.get('distance_miles'), payload.get('calories') or payload.get('active_calories'), 'cardio', payload.get('notes') or item.get('notes'), now_iso()),
                    )
                order += 1
            imported_workouts += 1
    flash(f'Imported {imported_workouts} workouts from workspace log.')
    return redirect(url_for('history'))


def build_workout_payload(user_id, workout_id):
    workout = get_workout(workout_id, user_id)
    exercises = []
    for item in get_workout_exercises(workout_id):
        we = item["exercise"]
        exercises.append({
            "id": we["id"],
            "name": we["exercise_name_snapshot"],
            "notes": we["notes"],
            "rest_seconds": we["rest_seconds"],
            "sets": [
                {
                    "id": s["id"],
                    "set_number": s["set_number"],
                    "set_type": s["set_type"],
                    "weight": s["weight"],
                    "reps": s["reps"],
                    "duration_seconds": s["duration_seconds"],
                    "rpe": s["rpe"],
                    "is_completed": s["is_completed"],
                    "notes": s["notes"],
                }
                for s in item["sets"]
            ],
        })
    return {
        "id": workout["id"],
        "name": workout["name"],
        "status": workout["status"],
        "started_at": workout["started_at"],
        "ended_at": workout["ended_at"],
        "notes": workout["notes"],
        "template_id": workout["template_id"],
        "exercises": exercises,
    }


@app.route("/api/me")
@login_required
def api_me():
    user = current_user()
    return {"id": user["id"], "username": user["username"]}


@app.route("/api/workouts/active")
@login_required
def api_workouts_active():
    user = current_user()
    active = get_active_workout(user["id"])
    if not active:
        return jsonify({"workout": None})
    return jsonify({"workout": build_workout_payload(user["id"], active["id"])})


@app.route("/api/workouts/<int:workout_id>")
@login_required
def api_workout_detail(workout_id):
    user = current_user()
    return jsonify({"workout": build_workout_payload(user["id"], workout_id)})


@app.route("/api/plan")
@login_required
def api_plan():
    user = current_user()
    plan = get_training_plan(user["id"])
    current_week = get_plan_week(plan)
    return jsonify({
        "plan": plan,
        "current_week": current_week,
    })


@app.route("/api/plan", methods=["PATCH"])
@login_required
def api_plan_update():
    user = current_user()
    payload = request.get_json(force=True, silent=False) or {}
    plan = get_training_plan(user["id"])

    if "plan" in payload:
        plan = payload["plan"]
    else:
        week_num = payload.get("week")
        focus = payload.get("focus")
        targets = payload.get("targets") or {}
        if week_num is None:
            return jsonify({"error": "provide full plan or week"}), 400
        matched = False
        for week in plan.get("weeks") or []:
            if week.get("week") == week_num:
                matched = True
                if focus is not None:
                    week["focus"] = focus
                if targets:
                    week.setdefault("targets", {}).update(targets)
                break
        if not matched:
            return jsonify({"error": f"week not found: {week_num}"}), 404

    set_setting(user["id"], "active_training_plan", plan)
    current_week = get_plan_week(plan)
    return jsonify({"plan": plan, "current_week": current_week})


@app.route("/api/templates")
@login_required
def api_templates():
    user = current_user()
    templates = query_all("SELECT * FROM workout_templates WHERE user_id = ? AND is_archived = 0 ORDER BY name", (user["id"],))
    items = []
    for tpl in templates:
        exercises = query_all(
            "SELECT wte.*, e.name AS exercise_name FROM workout_template_exercises wte JOIN exercises e ON e.id = wte.exercise_id WHERE wte.template_id = ? ORDER BY wte.sort_order, wte.id",
            (tpl["id"],),
        )
        items.append({
            "id": tpl["id"],
            "name": tpl["name"],
            "notes": tpl["notes"],
            "exercises": [
                {
                    "id": ex["id"],
                    "exercise_id": ex["exercise_id"],
                    "name": ex["exercise_name"],
                    "sort_order": ex["sort_order"],
                    "target_sets": ex["target_sets"],
                    "target_reps_min": ex["target_reps_min"],
                    "target_reps_max": ex["target_reps_max"],
                    "rest_seconds": ex["rest_seconds"],
                }
                for ex in exercises
            ],
        })
    return jsonify({"templates": items})


@app.route("/api/workouts")
@login_required
def api_workouts_list():
    user = current_user()
    limit = max(1, min(int(request.args.get("limit", 20)), 100))
    name = (request.args.get("name") or "").strip()
    status = (request.args.get("status") or "").strip()
    clauses = ["user_id = ?"]
    args = [user["id"]]
    if name:
        clauses.append("lower(name) = lower(?)")
        args.append(name)
    if status:
        clauses.append("status = ?")
        args.append(status)
    args.append(limit)
    rows = query_all(
        f"SELECT * FROM workouts WHERE {' AND '.join(clauses)} ORDER BY datetime(started_at) DESC LIMIT ?",
        tuple(args),
    )
    return jsonify({"workouts": [build_workout_payload(user["id"], row["id"]) for row in rows]})


def replace_sets_for_workout_exercise(workout_exercise_id, sets_payload, logged_at=None):
    logged_at = logged_at or now_iso()
    execute("DELETE FROM sets WHERE workout_exercise_id = ?", (workout_exercise_id,))
    sets_payload = sets_payload or []
    if not sets_payload:
        sets_payload = [{"set_number": 1}]
    for idx, s in enumerate(sets_payload, start=1):
        execute(
            "INSERT INTO sets (workout_exercise_id, set_number, set_type, weight, reps, duration_seconds, rpe, is_completed, notes, logged_at, distance_miles, calories) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                workout_exercise_id,
                s.get("set_number") or idx,
                s.get("set_type") or "normal",
                s.get("weight"),
                s.get("reps"),
                s.get("duration_seconds"),
                s.get("rpe"),
                int(bool(s.get("is_completed", False))),
                s.get("notes"),
                logged_at,
                s.get("distance_miles"),
                s.get("calories"),
            ),
        )


@app.route("/api/workouts/start", methods=["POST"])
@login_required
def api_workouts_start():
    user = current_user()
    payload = request.get_json(force=True, silent=False) or {}

    existing = get_active_workout(user["id"])
    if existing and not payload.get("allow_parallel"):
        if payload.get("return_existing", True):
            return jsonify({
                "workout": build_workout_payload(user["id"], existing["id"]),
                "already_active": True,
            })
        return jsonify({
            "error": "active workout already exists",
            "workout": build_workout_payload(user["id"], existing["id"]),
        }), 409

    template_id = payload.get("template_id")
    template_name = (payload.get("template_name") or "").strip()
    name = (payload.get("name") or "").strip() or None
    notes = payload.get("notes")
    exercises = payload.get("exercises") or []

    if template_name and not template_id:
        tpl = query_one("SELECT * FROM workout_templates WHERE user_id = ? AND lower(name)=lower(?)", (user["id"], template_name))
        if not tpl:
            return jsonify({"error": f"template not found: {template_name}"}), 404
        template_id = tpl["id"]

    if template_id and not exercises:
        workout_id = create_workout_from_template(user["id"], int(template_id), name)
        if notes:
            execute("UPDATE workouts SET notes = ?, updated_at = ? WHERE id = ?", (notes, now_iso(), workout_id))
        return jsonify({"workout": build_workout_payload(user["id"], workout_id)})

    if not exercises:
        return jsonify({"error": "provide template_id/template_name or exercises"}), 400

    created = now_iso()
    cur = execute(
        "INSERT INTO workouts (user_id, template_id, name, started_at, status, notes, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (user["id"], template_id, name or f"Workout {datetime.now().strftime('%Y-%m-%d %H:%M')}", created, "in_progress", notes, created, created),
    )
    workout_id = cur.lastrowid

    for idx, item in enumerate(exercises, start=1):
        ex_name = (item.get("name") or "").strip()
        if not ex_name:
            return jsonify({"error": f"exercise at position {idx} is missing name"}), 400
        tracking_mode = item.get("tracking_mode") or "strength"
        ex_id = ensure_exercise(user["id"], ex_name, tracking_mode=tracking_mode)
        cur_we = execute(
            "INSERT INTO workout_exercises (workout_id, exercise_id, sort_order, exercise_name_snapshot, notes, rest_seconds) VALUES (?,?,?,?,?,?)",
            (workout_id, ex_id, item.get("sort_order") or idx, ex_name, item.get("notes"), item.get("rest_seconds")),
        )
        sets = item.get("sets") or []
        if not isinstance(sets, list) or not sets:
            sets = [{"set_number": 1, "reps": item.get("target_reps"), "weight": item.get("target_weight"), "duration_seconds": item.get("target_duration_seconds")}]
        for set_idx, s in enumerate(sets, start=1):
            execute(
                "INSERT INTO sets (workout_exercise_id, set_number, set_type, weight, reps, duration_seconds, rpe, is_completed, notes, logged_at, distance_miles, calories) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    cur_we.lastrowid,
                    s.get("set_number") or set_idx,
                    s.get("set_type") or "normal",
                    s.get("weight"),
                    s.get("reps"),
                    s.get("duration_seconds"),
                    s.get("rpe"),
                    int(bool(s.get("is_completed", False))),
                    s.get("notes"),
                    created,
                    s.get("distance_miles"),
                    s.get("calories"),
                ),
            )

    return jsonify({"workout": build_workout_payload(user["id"], workout_id)})


@app.route("/api/workouts/<int:workout_id>", methods=["PATCH"])
@login_required
def api_workout_update(workout_id):
    user = current_user()
    get_workout(workout_id, user["id"])
    payload = request.get_json(force=True, silent=False) or {}
    fields = []
    args = []
    if "name" in payload:
        fields.append("name = ?")
        args.append(payload.get("name"))
    if "notes" in payload:
        fields.append("notes = ?")
        args.append(payload.get("notes"))
    if "status" in payload:
        fields.append("status = ?")
        args.append(payload.get("status"))
    if "ended_at" in payload:
        fields.append("ended_at = ?")
        args.append(payload.get("ended_at"))
    if not fields:
        return jsonify({"error": "no mutable fields provided"}), 400
    fields.append("updated_at = ?")
    args.append(now_iso())
    args.append(workout_id)
    execute(f"UPDATE workouts SET {', '.join(fields)} WHERE id = ?", tuple(args))
    return jsonify({"workout": build_workout_payload(user["id"], workout_id)})


@app.route("/api/workouts/<int:workout_id>/finish", methods=["POST"])
@login_required
def api_workout_finish(workout_id):
    user = current_user()
    get_workout(workout_id, user["id"])
    payload = request.get_json(force=True, silent=False) or {}
    ended_at = payload.get("ended_at") or now_iso()
    execute(
        "UPDATE workouts SET status = 'completed', ended_at = ?, updated_at = ? WHERE id = ?",
        (ended_at, now_iso(), workout_id),
    )
    return jsonify({"workout": build_workout_payload(user["id"], workout_id)})


@app.route("/api/workouts/<int:workout_id>/exercises", methods=["POST"])
@login_required
def api_workout_add_exercise(workout_id):
    user = current_user()
    get_workout(workout_id, user["id"])
    payload = request.get_json(force=True, silent=False) or {}
    ex_name = (payload.get("name") or "").strip()
    if not ex_name:
        return jsonify({"error": "exercise name is required"}), 400
    tracking_mode = payload.get("tracking_mode") or "strength"
    ex_id = ensure_exercise(user["id"], ex_name, tracking_mode=tracking_mode)
    row = query_one("SELECT COALESCE(MAX(sort_order), 0) + 1 AS next_order FROM workout_exercises WHERE workout_id = ?", (workout_id,))
    cur = execute(
        "INSERT INTO workout_exercises (workout_id, exercise_id, sort_order, exercise_name_snapshot, notes, rest_seconds) VALUES (?,?,?,?,?,?)",
        (workout_id, ex_id, payload.get("sort_order") or row["next_order"], ex_name, payload.get("notes"), payload.get("rest_seconds")),
    )
    replace_sets_for_workout_exercise(cur.lastrowid, payload.get("sets") or [], logged_at=now_iso())
    return jsonify({"workout": build_workout_payload(user["id"], workout_id)})


@app.route("/api/workouts/<int:workout_id>/exercises/<int:workout_exercise_id>", methods=["PATCH"])
@login_required
def api_workout_update_exercise(workout_id, workout_exercise_id):
    user = current_user()
    get_workout(workout_id, user["id"])
    existing = query_one("SELECT * FROM workout_exercises WHERE id = ? AND workout_id = ?", (workout_exercise_id, workout_id))
    if not existing:
        return jsonify({"error": "workout exercise not found"}), 404
    payload = request.get_json(force=True, silent=False) or {}

    fields = []
    args = []
    if "name" in payload:
        ex_name = (payload.get("name") or "").strip()
        if not ex_name:
            return jsonify({"error": "name cannot be blank"}), 400
        tracking_mode = payload.get("tracking_mode") or "strength"
        ex_id = ensure_exercise(user["id"], ex_name, tracking_mode=tracking_mode)
        fields.extend(["exercise_id = ?", "exercise_name_snapshot = ?"])
        args.extend([ex_id, ex_name])
    if "notes" in payload:
        fields.append("notes = ?")
        args.append(payload.get("notes"))
    if "rest_seconds" in payload:
        fields.append("rest_seconds = ?")
        args.append(payload.get("rest_seconds"))
    if "sort_order" in payload:
        fields.append("sort_order = ?")
        args.append(payload.get("sort_order"))
    if fields:
        args.append(workout_exercise_id)
        execute(f"UPDATE workout_exercises SET {', '.join(fields)} WHERE id = ?", tuple(args))
    if "sets" in payload:
        replace_sets_for_workout_exercise(workout_exercise_id, payload.get("sets") or [], logged_at=now_iso())
    return jsonify({"workout": build_workout_payload(user["id"], workout_id)})


@app.route("/api/workouts/<int:workout_id>/exercises/<int:workout_exercise_id>", methods=["DELETE"])
@login_required
def api_workout_delete_exercise(workout_id, workout_exercise_id):
    user = current_user()
    get_workout(workout_id, user["id"])
    existing = query_one("SELECT * FROM workout_exercises WHERE id = ? AND workout_id = ?", (workout_exercise_id, workout_id))
    if not existing:
        return jsonify({"error": "workout exercise not found"}), 404
    execute("DELETE FROM sets WHERE workout_exercise_id = ?", (workout_exercise_id,))
    execute("DELETE FROM workout_exercises WHERE id = ?", (workout_exercise_id,))
    return jsonify({"workout": build_workout_payload(user["id"], workout_id)})


@app.route("/api/workouts/<int:workout_id>/exercises/<int:workout_exercise_id>/sets/<int:set_id>", methods=["PATCH"])
@login_required
def api_workout_update_set(workout_id, workout_exercise_id, set_id):
    user = current_user()
    get_workout(workout_id, user["id"])
    existing = query_one("SELECT * FROM workout_exercises WHERE id = ? AND workout_id = ?", (workout_exercise_id, workout_id))
    if not existing:
        return jsonify({"error": "workout exercise not found"}), 404
    set_row = query_one("SELECT * FROM sets WHERE id = ? AND workout_exercise_id = ?", (set_id, workout_exercise_id))
    if not set_row:
        return jsonify({"error": "set not found"}), 404

    payload = request.get_json(force=True, silent=False) or {}
    mutable = {
        "set_number": payload.get("set_number") if "set_number" in payload else set_row["set_number"],
        "set_type": payload.get("set_type") if "set_type" in payload else set_row["set_type"],
        "weight": payload.get("weight") if "weight" in payload else set_row["weight"],
        "reps": payload.get("reps") if "reps" in payload else set_row["reps"],
        "duration_seconds": payload.get("duration_seconds") if "duration_seconds" in payload else set_row["duration_seconds"],
        "rpe": payload.get("rpe") if "rpe" in payload else set_row["rpe"],
        "is_completed": int(bool(payload.get("is_completed"))) if "is_completed" in payload else set_row["is_completed"],
        "notes": payload.get("notes") if "notes" in payload else set_row["notes"],
        "distance_miles": payload.get("distance_miles") if "distance_miles" in payload else set_row["distance_miles"],
        "calories": payload.get("calories") if "calories" in payload else set_row["calories"],
    }
    execute(
        "UPDATE sets SET set_number=?, set_type=?, weight=?, reps=?, duration_seconds=?, rpe=?, is_completed=?, notes=?, logged_at=?, distance_miles=?, calories=? WHERE id=?",
        (
            mutable["set_number"], mutable["set_type"], mutable["weight"], mutable["reps"], mutable["duration_seconds"],
            mutable["rpe"], mutable["is_completed"], mutable["notes"], now_iso(), mutable["distance_miles"], mutable["calories"], set_id,
        ),
    )
    return jsonify({"workout": build_workout_payload(user["id"], workout_id)})


if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "").lower() in {"1", "true", "yes", "on"}
    app.run(host="0.0.0.0", port=8788, debug=debug)
