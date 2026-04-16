# Workout Tracker Automation API

This file is the canonical reference for local programmatic workout actions.

Base URL:
- `http://127.0.0.1:8788`

Auth token file:
- `/home/node/.openclaw/workspace/workout-tracker/instance/api_token.txt`

Supported auth headers:
- `Authorization: Bearer <token>`
- `X-Workout-Token: <token>`

## Endpoints

### Identity
- `GET /api/me`

### Workout read
- `GET /api/workouts/active`
- `GET /api/workouts/<id>`
- `GET /api/plan`

### Workout creation
- `POST /api/workouts/start`

Use one of:
- `template_id`
- `template_name`
- custom `exercises` array

### Workout metadata update
- `PATCH /api/workouts/<id>`

Mutable fields:
- `name`
- `notes`
- `status`
- `ended_at`

### Finish a workout
- `POST /api/workouts/<id>/finish`

Optional body:
```json
{
  "ended_at": "2026-04-02T13:45:00Z"
}
```

### Exercise add/remove/update
- `POST /api/workouts/<id>/exercises`
- `PATCH /api/workouts/<id>/exercises/<workout_exercise_id>`
- `DELETE /api/workouts/<id>/exercises/<workout_exercise_id>`

Exercise patch supports:
- `name`
- `notes`
- `rest_seconds`
- `sort_order`
- full replacement `sets` array

### Single set update
- `PATCH /api/workouts/<id>/exercises/<workout_exercise_id>/sets/<set_id>`

Set patch fields:
- `set_number`
- `set_type`
- `weight`
- `reps`
- `duration_seconds`
- `rpe`
- `is_completed`
- `notes`
- `distance_miles`
- `calories`

## Notes

- `POST /api/workouts/start` returns `409` if an active workout exists unless `allow_parallel` is true.
- These endpoints are the preferred automation surface for Claw.
- Avoid direct DB edits unless the API is missing a required operation and has been explicitly validated as insufficient.
