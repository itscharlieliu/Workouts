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

Auth required:
- `Authorization: Bearer <token>` or `X-Workout-Token: <token>`

Use one of:
- `template_id`
- `template_name`
- custom `exercises` array

Recommended client behavior for "start today's workout":
1. `GET /api/workouts/active`
2. if one exists, use it
3. otherwise `POST /api/workouts/start` with `template_name`
4. then patch exercise targets to the current plan

Idempotent start behavior:
- by default, if an active workout already exists and `allow_parallel` is not true, the API returns `200` with the existing workout and `already_active: true`
- set `return_existing=false` if you explicitly want the older `409` conflict behavior

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

- `POST /api/workouts/start` is idempotent by default: if an active workout exists and `allow_parallel` is not true, it returns the active workout with `already_active: true`.
- Set `return_existing=false` to force the older `409` conflict behavior.
- `POST /api/workouts/start` still supports parallel sessions when `allow_parallel` is true.
- These endpoints are the preferred automation surface for Claw.
- Avoid direct DB edits unless the API is missing a required operation and has been explicitly validated as insufficient.
