# Backend MVP Spec

## Goal
Build a **local, persistent workout tracker** web app that replaces the core Strong workflow:
- log workouts fast
- manage exercises and templates
- store history locally
- import/export data
- support a simple login for a single local user (extendable later)

## Recommended Stack
- **Backend:** Python + Flask
- **DB:** SQLite
- **ORM/migrations:** SQLAlchemy + Flask-Migrate/Alembic
- **Auth:** session cookie + password hash
- **Frontend integration:** server-rendered pages or JSON API consumed by simple JS
- **Persistent storage path:** `instance/workout_tracker.db`

Why this is a good MVP choice:
- easy local deployment
- SQLite is ideal for single-user/local-first persistence
- Flask keeps auth, forms, sessions, and exports simple

---

## Architecture
### App layers
1. **HTTP layer**
   - HTML pages for login/app shell
   - JSON API for workouts, templates, exercises, import/export
2. **Service layer**
   - workout logging
   - template instantiation
   - import/export mapping
   - personal record/history calculations
3. **Persistence layer**
   - SQLite database in `instance/`
   - optional file-based export directory in `instance/exports/`

### Data ownership model
MVP assumes:
- **one primary local account**
- schema still supports multiple users cleanly via `user_id`
- all main tables are user-scoped

---

## Minimal Auth Approach
### MVP auth
Use a standard local login flow:
- `users` table with `email` or `username`
- password stored as **bcrypt/werkzeug hash**, never plaintext
- login creates secure server-side session (Flask session cookie)
- all app/API routes except login/logout require authentication

### Behavior
- first run: create initial admin/local user via bootstrap command or first-run setup screen
- single-device/local use is fine
- no OAuth, no password reset email, no social login in MVP

### Security basics
- hash passwords
- `HttpOnly` session cookie
- `SameSite=Lax`
- CSRF protection for form-based POSTs
- simple rate limiting on login if easy; otherwise defer
- if app is purely local, bind to localhost by default

---

## Core Data Model

## Tables

### `users`
| column | type | notes |
|---|---|---|
| id | integer PK | |
| username | text unique | preferred login id |
| email | text nullable unique | optional for MVP |
| password_hash | text | required |
| created_at | datetime | |
| updated_at | datetime | |
| is_active | boolean | default true |

### `exercises`
Canonical exercise library per user.

| column | type | notes |
|---|---|---|
| id | integer PK | |
| user_id | integer FK users.id | |
| name | text | e.g. Bench Press |
| category | text | e.g. chest, back, legs |
| equipment | text nullable | barbell, dumbbell, machine, bodyweight |
| default_rep_unit | text | reps / seconds / distance |
| notes | text nullable | |
| is_archived | boolean | default false |
| created_at | datetime | |
| updated_at | datetime | |

Constraint suggestion:
- unique `(user_id, lower(name))`

### `workout_templates`
Saved workout plans.

| column | type | notes |
|---|---|---|
| id | integer PK | |
| user_id | integer FK users.id | |
| name | text | e.g. Push Day |
| notes | text nullable | |
| is_archived | boolean | default false |
| created_at | datetime | |
| updated_at | datetime | |

### `workout_template_exercises`
Ordered exercises inside a template.

| column | type | notes |
|---|---|---|
| id | integer PK | |
| template_id | integer FK workout_templates.id | |
| exercise_id | integer FK exercises.id | |
| sort_order | integer | display order |
| target_sets | integer nullable | |
| target_reps_min | integer nullable | |
| target_reps_max | integer nullable | |
| target_weight | real nullable | optional default |
| target_rpe | real nullable | optional |
| rest_seconds | integer nullable | default timer hint |
| notes | text nullable | |

### `workouts`
One completed or in-progress workout session.

| column | type | notes |
|---|---|---|
| id | integer PK | |
| user_id | integer FK users.id | |
| template_id | integer FK workout_templates.id nullable | source template if started from one |
| name | text | snapshot title, editable |
| started_at | datetime | required |
| ended_at | datetime nullable | null while active |
| status | text | `in_progress`, `completed`, `cancelled` |
| notes | text nullable | whole-workout notes |
| created_at | datetime | |
| updated_at | datetime | |

### `workout_exercises`
Ordered exercises as performed in a specific workout.

| column | type | notes |
|---|---|---|
| id | integer PK | |
| workout_id | integer FK workouts.id | |
| exercise_id | integer FK exercises.id | |
| sort_order | integer | |
| exercise_name_snapshot | text | preserves historical name |
| notes | text nullable | |
| rest_seconds | integer nullable | timer hint for this workout |

### `sets`
Atomic logged performance rows.

| column | type | notes |
|---|---|---|
| id | integer PK | |
| workout_exercise_id | integer FK workout_exercises.id | |
| set_number | integer | 1..N within exercise |
| set_type | text | `warmup`, `normal`, `drop`, `failure` |
| weight | real nullable | |
| reps | integer nullable | |
| distance | real nullable | optional future-proofing |
| duration_seconds | integer nullable | for timed movements |
| rpe | real nullable | |
| is_completed | boolean | default true |
| logged_at | datetime | |
| notes | text nullable | |

### `body_metrics` (optional but useful MVP+)
If you want bodyweight tracking soon.

| column | type | notes |
|---|---|---|
| id | integer PK | |
| user_id | integer FK users.id | |
| measured_at | datetime | |
| bodyweight | real nullable | |
| body_fat_pct | real nullable | |
| notes | text nullable | |

### `app_settings`
Small key/value settings store.

| column | type | notes |
|---|---|---|
| id | integer PK | |
| user_id | integer FK users.id | |
| key | text | |
| value_json | text | JSON-encoded |
| updated_at | datetime | |

Use for:
- preferred weight unit
- preferred distance unit
- default rest timer
- import metadata/version

---

## Important Design Choices
### Historical snapshots
Store `exercise_name_snapshot` on `workout_exercises`.
This avoids broken history if a user renames an exercise later.

### Soft archive, not delete
For `exercises` and `workout_templates`:
- archive by default
- avoid hard deletes unless there is no dependent history

### In-progress workout support
A workout should be creatable before completion:
- `status = in_progress`
- `ended_at = null`
- autosave each set as user logs it

This is important for a Strong-like experience.

---

## JSON API Routes
Prefix all JSON routes with `/api`.

## Auth
### `POST /login`
Request:
```json
{ "username": "charlie", "password": "..." }
```
Response:
```json
{ "ok": true, "user": { "id": 1, "username": "charlie" } }
```

### `POST /logout`
Logs out current session.

### `GET /api/me`
Returns current authenticated user and lightweight settings.

---

## Exercises
### `GET /api/exercises`
List exercises for current user.
Query params:
- `archived=false` default
- `q=` search text

### `POST /api/exercises`
Create exercise.

Body:
```json
{
  "name": "Bench Press",
  "category": "chest",
  "equipment": "barbell",
  "default_rep_unit": "reps",
  "notes": ""
}
```

### `PATCH /api/exercises/:id`
Update exercise metadata.

### `POST /api/exercises/:id/archive`
Archive exercise.

---

## Templates
### `GET /api/templates`
List templates.

### `POST /api/templates`
Create template with nested ordered exercises.

Body:
```json
{
  "name": "Push Day",
  "notes": "",
  "exercises": [
    {
      "exercise_id": 1,
      "sort_order": 1,
      "target_sets": 3,
      "target_reps_min": 5,
      "target_reps_max": 8,
      "target_weight": 135,
      "target_rpe": 8,
      "rest_seconds": 180,
      "notes": ""
    }
  ]
}
```

### `GET /api/templates/:id`
Return template details.

### `PATCH /api/templates/:id`
Update template header and/or nested items.

### `POST /api/templates/:id/archive`
Archive template.

### `POST /api/templates/:id/start`
Instantiate template into a new `workout` + `workout_exercises` rows.
Return newly created workout.

---

## Workouts
### `GET /api/workouts`
List workouts.
Filters:
- `status=`
- `from=` / `to=`
- `limit=`
- `template_id=`

### `POST /api/workouts`
Create empty or custom workout.

Body:
```json
{
  "name": "Upper Body",
  "started_at": "2026-03-18T14:00:00-07:00"
}
```

### `GET /api/workouts/:id`
Return workout with ordered exercises and sets.

### `PATCH /api/workouts/:id`
Update workout metadata:
- name
- notes
- started_at
- ended_at
- status

### `POST /api/workouts/:id/complete`
Marks workout complete and sets `ended_at` if missing.

### `POST /api/workouts/:id/cancel`
Marks workout cancelled.

---

## Workout exercise rows
### `POST /api/workouts/:id/exercises`
Add exercise to workout.

Body:
```json
{
  "exercise_id": 3,
  "sort_order": 4,
  "rest_seconds": 120,
  "notes": ""
}
```

### `PATCH /api/workout-exercises/:id`
Update notes/order/rest time.

### `DELETE /api/workout-exercises/:id`
Remove exercise from in-progress workout only.

---

## Sets
### `POST /api/workout-exercises/:id/sets`
Add a set.

Body:
```json
{
  "set_number": 1,
  "set_type": "normal",
  "weight": 185,
  "reps": 5,
  "rpe": 8.5,
  "duration_seconds": null,
  "notes": ""
}
```

### `PATCH /api/sets/:id`
Edit logged set.

### `DELETE /api/sets/:id`
Delete set from in-progress workout.

---

## History / analytics (minimal MVP)
### `GET /api/exercises/:id/history`
Return recent performance history for an exercise.
Useful for showing last weight/reps and quick progression.

Suggested response:
- recent sets
- recent completed workouts
- simple bests (max weight, max reps, estimated 1RM)

---

## Settings
### `GET /api/settings`
### `PATCH /api/settings`
Store simple preferences:
- weight unit
- distance unit
- default rest timer
- theme if needed

---

## Import / Export
### Export goals
User should always be able to get data back out.
Support:
1. **full JSON export** for lossless backup/restore
2. **CSV export** for workouts/sets for spreadsheet portability

### Export routes
#### `GET /api/export/json`
Returns full account-scoped export bundle:
- user profile (safe fields only)
- exercises
- templates
- workouts
- workout_exercises
- sets
- settings
- export metadata/version

Suggested top-level shape:
```json
{
  "schema_version": 1,
  "exported_at": "2026-03-18T14:30:00-07:00",
  "data": {
    "exercises": [],
    "templates": [],
    "workouts": [],
    "workout_exercises": [],
    "sets": [],
    "settings": []
  }
}
```

#### `GET /api/export/workouts.csv`
Flat CSV for spreadsheet use.
One row per set, including workout and exercise context.

Recommended CSV columns:
- workout_id
- workout_name
- workout_date
- started_at
- ended_at
- exercise_name
- set_number
- set_type
- weight
- reps
- duration_seconds
- distance
- rpe
- set_notes
- workout_notes

### Import goals
MVP import should be simple and safe.
Support:
1. **CSV import** from a normalized template
2. optionally later: Strong-export-specific mapper if sample files are available

### Import route
#### `POST /api/import/csv`
Upload CSV and parse rows into workouts/exercises/sets.

Recommended import flow:
1. upload file
2. validate required columns
3. preview parsed summary before commit
4. on confirm, import in transaction
5. return created counts + warnings

Required normalized CSV columns for MVP importer:
- workout_name
- workout_date
- exercise_name
- set_number
- optionally: weight, reps, rpe, duration_seconds, notes

### Import rules
- create missing exercises automatically for current user
- dedupe workouts conservatively only if explicit import id exists; otherwise do not guess too much
- store import metadata in `app_settings` or a dedicated import log later
- wrap each import in a transaction

---

## Persistence Notes
### SQLite configuration
- DB file: `instance/workout_tracker.db`
- enable foreign keys
- WAL mode recommended for better local reliability/concurrency
- regular app-level backups can just copy the DB while app is idle, but JSON export is the user-facing backup format

### Migration policy
- use migrations from day one
- never rely on raw `create_all()` after initial bootstrapping

---

## Validation Rules
Minimal useful constraints:
- exercise name required
- template name required
- workout must have `started_at`
- `ended_at >= started_at` if present
- `set_number >= 1`
- at least one of `reps`, `duration_seconds`, or `distance` should exist for a meaningful set
- `rpe` range: 0-10 if used
- non-negative weight/distance/duration

---

## Recommended Implementation Order
### Phase 1: foundation
1. Flask app setup
2. SQLite config in `instance/`
3. SQLAlchemy models + migrations
4. auth/session login/logout
5. first-run user bootstrap

### Phase 2: core workout logging
6. exercises CRUD
7. workouts CRUD
8. workout exercises + sets CRUD
9. in-progress workout autosave flow
10. complete/cancel workout actions

### Phase 3: templates
11. templates CRUD
12. start workout from template
13. exercise ordering and rest defaults

### Phase 4: portability
14. JSON export
15. flat CSV export
16. CSV import with preview + transaction

### Phase 5: nice-to-have MVP polish
17. exercise history endpoint
18. app settings/preferences
19. basic PR calculations / recent stats
20. backup/restore UI entry points

---

## Practical MVP Boundaries
Keep out of initial version unless needed immediately:
- multi-user sharing
- cloud sync
- OAuth
- advanced analytics dashboards
- media attachments
- social/community features
- push notifications
- complicated permission roles

---

## Bottom Line
The cleanest MVP is:
- **Flask + SQLite**
- **session-based local auth**
- **user-scoped tables for exercises, templates, workouts, workout_exercises, and sets**
- **JSON API for CRUD + import/export**
- **lossless JSON export and practical CSV import/export**

This gives a fast, local-first Strong replacement without overbuilding.