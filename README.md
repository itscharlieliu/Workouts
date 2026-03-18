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
