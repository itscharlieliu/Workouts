#!/usr/bin/env python3
import csv
import io
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    abort,
    flash,
    g,
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
    import secrets
    key = secrets.token_hex(32)
    SECRET_PATH.write_text(key)
    os.chmod(SECRET_PATH, 0o600)
    app.secret_key = key
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=False,
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
    return {"current_user": user, "active_workout": get_active_workout(user["id"]) if user else None}


def query_one(sql, args=()):
    return get_db().execute(sql, args).fetchone()


def query_all(sql, args=()):
    return get_db().execute(sql, args).fetchall()


def execute(sql, args=()):
    db = get_db()
    cur = db.execute(sql, args)
    db.commit()
    return cur


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return query_one("SELECT * FROM users WHERE id = ?", (uid,))


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
        enriched.append({"exercise": ex, "sets": sets, "last": get_last_performance(ex["exercise_id"], workout_id) if ex["exercise_id"] else None})
    return enriched


def get_last_performance(exercise_id, current_workout_id):
    if not exercise_id:
        return None
    row = query_one(
        """
        SELECT s.weight, s.reps, s.rpe
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
    if current_user():
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


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
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        user = query_one("SELECT * FROM users WHERE username = ?", (username,))
        if not user or not check_password_hash(user["password_hash"], password):
            flash("Invalid login.")
        else:
            session.clear()
            session["user_id"] = user["id"]
            return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()
    active = get_active_workout(user["id"])
    recent = query_all("SELECT * FROM workouts WHERE user_id = ? ORDER BY started_at DESC LIMIT 5", (user["id"],))
    templates = query_all("SELECT * FROM workout_templates WHERE user_id = ? AND is_archived = 0 ORDER BY updated_at DESC LIMIT 5", (user["id"],))
    return render_template("dashboard.html", active=active, recent=recent, templates=templates)


@app.route("/exercises", methods=["GET", "POST"])
@login_required
def exercises():
    user = current_user()
    if request.method == "POST":
        name = request.form["name"].strip()
        if name:
            now = now_iso()
            execute(
                "INSERT INTO exercises (user_id, name, category, equipment, notes, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                (user["id"], name, request.form.get("category"), request.form.get("equipment"), request.form.get("notes"), now, now),
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
    return render_template("workouts_new.html", templates=templates, recent=recent)


@app.route("/workouts/<int:workout_id>/edit")
@login_required
def workout_edit(workout_id):
    user = current_user()
    workout = get_workout(workout_id, user["id"])
    exercise_options = query_all("SELECT * FROM exercises WHERE user_id = ? AND is_archived = 0 ORDER BY name", (user["id"],))
    return render_template("workout_edit.html", workout=workout, workout_exercises=get_workout_exercises(workout_id), exercise_options=exercise_options)


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
        rpe = request.form.get(f"rpe_{sid}") or None
        stype = request.form.get(f"set_type_{sid}") or "normal"
        completed = 1 if request.form.get(f"done_{sid}") else 0
        execute(
            "UPDATE sets SET weight=?, reps=?, rpe=?, set_type=?, is_completed=?, logged_at=? WHERE id=? AND workout_exercise_id=?",
            (float(weight) if weight not in (None, "") else None,
             int(reps) if reps not in (None, "") else None,
             float(rpe) if rpe not in (None, "") else None,
             stype,
             completed,
             now_iso(),
             int(sid),
             we_id),
        )
    execute("UPDATE workout_exercises SET notes=?, rest_seconds=? WHERE id=?", (request.form.get("notes"), int(request.form.get("rest_seconds") or 90), we_id))
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


@app.route("/export/json")
@login_required
def export_json():
    user = current_user()
    data = {
        "workouts": [dict(r) for r in query_all("SELECT * FROM workouts WHERE user_id = ? ORDER BY started_at DESC", (user["id"],))],
        "exercises": [dict(r) for r in query_all("SELECT * FROM exercises WHERE user_id = ?", (user["id"],))],
        "templates": [dict(r) for r in query_all("SELECT * FROM workout_templates WHERE user_id = ?", (user["id"],))],
    }
    buf = io.BytesIO(json.dumps(data, indent=2).encode())
    return send_file(buf, mimetype="application/json", as_attachment=True, download_name="workout_tracker_export.json")


@app.route("/export/csv")
@login_required
def export_csv():
    user = current_user()
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["workout_date", "workout_name", "exercise", "set_number", "weight", "reps", "rpe", "set_type"])
    rows = query_all(
        """
        SELECT w.started_at, w.name, we.exercise_name_snapshot, s.set_number, s.weight, s.reps, s.rpe, s.set_type
        FROM workouts w
        JOIN workout_exercises we ON we.workout_id = w.id
        JOIN sets s ON s.workout_exercise_id = we.id
        WHERE w.user_id = ?
        ORDER BY w.started_at DESC, we.sort_order, s.set_number
        """,
        (user["id"],),
    )
    for r in rows:
        writer.writerow([r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7]])
    buf = io.BytesIO(out.getvalue().encode())
    return send_file(buf, mimetype="text/csv", as_attachment=True, download_name="workout_tracker_export.csv")


def ensure_exercise(user_id, ex_name):
    ex = query_one("SELECT * FROM exercises WHERE user_id = ? AND lower(name)=lower(?)", (user_id, ex_name))
    if ex:
        return ex["id"]
    now = now_iso()
    cur = execute(
        "INSERT INTO exercises (user_id, name, created_at, updated_at) VALUES (?,?,?,?)",
        (user_id, ex_name, now, now),
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
            ex_id = ensure_exercise(user_id, ex_name)
            cur3 = execute(
                "INSERT INTO workout_exercises (workout_id, exercise_id, sort_order, exercise_name_snapshot) VALUES (?,?,?,?)",
                (workout_id, ex_id, order, ex_name),
            )
            for sr in set_rows:
                execute(
                    "INSERT INTO sets (workout_exercise_id, set_number, weight, reps, rpe, set_type, logged_at, is_completed) VALUES (?,?,?,?,?,?,?,1)",
                    (cur3.lastrowid, int(sr.get("set_number") or 1), float(sr.get("weight") or 0) if sr.get("weight") else None, int(sr.get("reps") or 0) if sr.get("reps") else None, float(sr.get("rpe") or 0) if sr.get("rpe") else None, sr.get("set_type") or "normal", now_iso()),
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
                ex_id = ensure_exercise(user['id'], display_name)
                cur_we = execute(
                    "INSERT INTO workout_exercises (workout_id, exercise_id, sort_order, exercise_name_snapshot) VALUES (?,?,?,?)",
                    (workout_id, ex_id, order, display_name),
                )
                for idx, s in enumerate(payload.get('sets', []), start=1):
                    execute(
                        "INSERT INTO sets (workout_exercise_id, set_number, weight, reps, duration_seconds, rpe, set_type, notes, logged_at, is_completed) VALUES (?,?,?,?,?,?,?,?,?,1)",
                        (cur_we.lastrowid, idx, s.get('lbs'), s.get('reps'), s.get('seconds'), s.get('rpe'), 'warmup' if s.get('warmup') else 'normal', payload.get('notes'), now_iso()),
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
    app.run(host="0.0.0.0", port=8788, debug=True)
