#!/usr/bin/env python3
import csv
import hmac
import io
import json
import os
import secrets
import sqlite3
from contextlib import closing
from datetime import date, datetime
from functools import wraps
from pathlib import Path

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
        if not validate_csrf():
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
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


@app.route("/api/me")
@login_required
def api_me():
    user = current_user()
    return {"id": user["id"], "username": user["username"]}


if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "").lower() in {"1", "true", "yes", "on"}
    app.run(host="0.0.0.0", port=8788, debug=debug)
