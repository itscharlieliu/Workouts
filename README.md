# Workout Tracker

A local persistent workout tracking web app to replace the core Strong workflow.

## Implemented MVP
- Local account setup + login
- Persistent SQLite DB: `instance/workout_tracker.db`
- Exercise library
- Workout templates
- Start blank workouts or from templates
- Active workout logging
  - sets
  - reps
  - weight
  - RPE
  - set type
  - per-exercise notes
  - rest timer
- Workout history / detail views
- CSV export
- CSV import
- Import from existing workspace `fitness/workout_log.jsonl`

## Persistent storage
Everything important lives under this folder:
- app code: `workout-tracker/`
- database: `workout-tracker/instance/workout_tracker.db`
- secret key: `workout-tracker/instance/secret_key.txt`

## Run
```bash
cd ~/.openclaw/workspace/workout-tracker
./run.sh
```

App listens on:
- `http://127.0.0.1:8788`

## If running inside OpenClaw Docker
If you want to access the app from your normal browser outside the container, expose a small app port range in your `docker-compose.yml`:

```yaml
ports:
  - "8788-8799:8788-8799"
```

Then restart the container:

```bash
docker compose up -d
```

After that, the app should be reachable from your host at:

- `http://<your-host-ip>:8788`
- or `http://localhost:8788` if Docker is running locally on the same machine

## First use
1. Open `/setup`
2. Create your local account
3. Log in
4. Optional: import your existing workout data from the Import page

## Local API for automation

This app now exposes a small local API for programmatic workout actions.

### Auth

For non-browser/local automation calls, use the API token stored at:
- `workout-tracker/instance/api_token.txt`

Supported headers:
- `Authorization: Bearer <token>`
- `X-Workout-Token: <token>`

### Endpoints

- `GET /api/me`
- `GET /api/workouts/active`
- `GET /api/workouts/<id>`
- `POST /api/workouts/start`
- `PATCH /api/workouts/<id>`
- `POST /api/workouts/<id>/finish`
- `POST /api/workouts/<id>/exercises`
- `PATCH /api/workouts/<id>/exercises/<workout_exercise_id>`
- `DELETE /api/workouts/<id>/exercises/<workout_exercise_id>`
- `PATCH /api/workouts/<id>/exercises/<workout_exercise_id>/sets/<set_id>`

### Start a workout from a template

```bash
TOKEN=$(cat ~/.openclaw/workspace/workout-tracker/instance/api_token.txt)

curl -X POST http://127.0.0.1:8788/api/workouts/start \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "template_name": "Legs + Core"
  }'
```

### Start a custom workout

```bash
TOKEN=$(cat ~/.openclaw/workspace/workout-tracker/instance/api_token.txt)

curl -X POST http://127.0.0.1:8788/api/workouts/start \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "Legs + Core (Travel Gym)",
    "notes": "Machine/dumbbell-adjusted version for travel/non-home gym.",
    "exercises": [
      {
        "name": "Leg Press",
        "rest_seconds": 120,
        "notes": "Main squat replacement.",
        "sets": [
          {"set_number": 1, "reps": 10},
          {"set_number": 2, "reps": 10},
          {"set_number": 3, "reps": 10}
        ]
      },
      {
        "name": "Cable Crunch",
        "rest_seconds": 60,
        "sets": [
          {"set_number": 1, "reps": 15},
          {"set_number": 2, "reps": 15},
          {"set_number": 3, "reps": 15}
        ]
      }
    ]
  }'
```

### Edit an exercise in-place

This is the kind of call that lets Claw swap an exercise without touching SQLite directly.

```bash
TOKEN=$(cat ~/.openclaw/workspace/workout-tracker/instance/api_token.txt)

curl -X PATCH http://127.0.0.1:8788/api/workouts/22/exercises/95 \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "Bulgarian Split Squat",
    "notes": "Use as leg curl substitute.",
    "rest_seconds": 90,
    "sets": [
      {"set_number": 1, "reps": 24},
      {"set_number": 2, "reps": 24},
      {"set_number": 3, "reps": 24}
    ]
  }'
```

### Notes

- `POST /api/workouts/start` returns `409` if an active workout already exists unless `allow_parallel` is set.
- You can provide either `template_id`, `template_name`, or a custom `exercises` array.
- `PATCH /api/workouts/<id>/exercises/<workout_exercise_id>` supports replacing the exercise name, notes, rest time, sort order, and the full set list in one call.
- `PATCH /api/workouts/<id>/exercises/<workout_exercise_id>/sets/<set_id>` supports in-place set logging without replacing the whole exercise.
- Canonical automation reference: `workout-tracker/API.md`
- This API is intended for local automation helpers and Claw actions so future workout-start/edit flows do not need direct DB writes.
