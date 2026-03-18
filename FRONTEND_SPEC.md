# FRONTEND_SPEC.md

## Product shape
A local-first workout tracker web app: fast to log during training, persistent between sessions, and simple enough to build with server-rendered templates plus a small amount of JavaScript.

Primary goals for MVP:
- Start a workout quickly
- Log sets with minimal taps/clicks
- Reuse exercises and templates
- Import existing history from CSV
- Export all data back out
- Run a rest timer without leaving the page

Non-goals for MVP:
- Social/sharing features
- Charts-heavy analytics dashboard
- Complex drag-and-drop programming tools
- Mobile app / offline sync beyond normal browser caching

---

## UX principles
- **Workout-first**: active workout screen is the core experience.
- **Low-friction entry**: default values, repeat last values, keyboard-friendly forms.
- **Readable during a workout**: big tap targets, high contrast, sticky actions.
- **Server-rendered by default**: every page works with plain form submissions.
- **Light JS only where it matters**: add/remove exercise blocks, inline set rows, rest timer, quick-fill helpers.

---

## Information architecture
Top nav:
- Dashboard
- Start Workout
- History
- Templates
- Exercises
- Import / Export

Utility nav / header actions:
- Search
- Rest Timer indicator (only visible during active workout)
- Settings / profile (minimal for MVP)

Suggested route map:
- `/` Dashboard
- `/workouts/new` Start workout / choose template
- `/workouts/:id` Workout detail / summary
- `/workouts/:id/edit` Active workout logger (same view can also edit completed workout)
- `/history` Workout history list
- `/history/:id` Completed workout detail
- `/templates` Template list
- `/templates/new`
- `/templates/:id/edit`
- `/exercises` Exercise library
- `/exercises/new`
- `/exercises/:id/edit`
- `/import` CSV import
- `/export` CSV export

---

## Core data concepts reflected in UI
- **Workout**: date, start/end time, template origin, notes
- **Workout exercise**: exercise selection, order, notes
- **Set**: set number, weight, reps, optional duration/distance, RPE, completed checkbox
- **Exercise**: name, category/body part, equipment, default increment/unit, notes
- **Template**: named collection of exercises with default set targets

For MVP, design the UI around **strength logging first**:
- Default set inputs: weight, reps, optional RPE
- Optional exercise type variants later: bodyweight-only, assisted, timed

---

## Page specs

### 1) Dashboard (`/`)
Purpose: quick resume/start and recent context.

Sections:
- **Primary CTA**: `Start Workout`
- **Resume active workout** card if one is in progress
- **Recent workouts**: last 5 entries with date, template/name, duration, exercise count
- **Quick start from template**: 3-5 most used templates
- **Import / Export shortcuts**

Components:
- Active workout banner
- Recent workout list items
- Template quick-start buttons

Implementation notes:
- Fully server-rendered.
- If active workout exists, place resume button above the fold.

---

### 2) Start Workout (`/workouts/new`)
Purpose: get into logging in under 10 seconds.

Layout:
- **Start blank workout** button
- **Choose template** list/cards
- **Repeat recent workout** optional shortcut

Template card fields:
- Template name
- Exercise count
- Last used date
- `Start` button

Blank workout form:
- Workout name (optional; defaults to today/time or template name)
- Date/time defaulted to now
- Notes (optional, collapsed by default)

Implementation notes:
- Start actions should POST and redirect immediately to active logger page.
- Avoid modal dependency; a dedicated page is simpler.

---

### 3) Active Workout Logger (`/workouts/:id/edit`)
Purpose: the main screen users live on during training.

#### Page layout
Top area:
- Workout title / editable name
- Started at, elapsed time
- Sticky action row:
  - `Finish Workout`
  - `Add Exercise`
  - `Start Rest Timer`
  - optional `Cancel` / `Discard` behind confirmation

Body:
- Ordered list of workout exercises as cards/sections
- Each exercise contains set table + quick actions

Bottom sticky mobile-friendly bar:
- `Add Exercise`
- `Finish`
- Timer status

#### Exercise card contents
Header:
- Exercise name
- Small metadata: equipment / body part
- Actions: move up/down, remove, notes toggle

Subheader:
- Previous workout reference: e.g. `Last: 135 x 8, 8, 6`
- Optional target line if started from template: `Target: 3 x 8`

Set entry table:
- Columns: `#`, `Prev`, `Weight`, `Reps`, `RPE`, `Done`, actions
- Each row is one set
- Final column action: duplicate/delete row

Quick actions under table:
- `+ Add Set`
- `Repeat Last Set`
- `Fill from Previous`
- `Mark Exercise Complete`

Exercise notes:
- Collapsible textarea

#### Interaction rules
- New exercise starts with 3 empty sets by default if added from template; otherwise 1 empty set.
- `Repeat Last Set` copies weight/reps/RPE from latest entered row.
- `Fill from Previous` copies prior workout’s set scheme into current empty rows.
- Checking `Done` visually locks/completes the set row but still allows edit.
- Save strategy: either explicit `Save` button or auto-save on submit events. For MVP with server templates, prefer **small per-action POSTs** plus optional JS-enhanced inline submits.

#### Add Exercise flow
Option A (recommended): inline search panel at top or between cards.
- Search existing exercise library
- Choose result -> append card to workout
- `Create new exercise` secondary link if no match

#### Finish Workout flow
On click:
- Confirmation page or inline summary section
- End time auto-filled now
- Optional workout notes / perceived difficulty
- `Save and finish`

Implementation notes:
- Best page for light JS:
  - add/remove set rows client-side
  - rest timer countdown
  - keyboard navigation between inputs
  - autosubmit on blur/change
- Keep baseline HTML forms functional without JS.

---

### 4) Workout Detail / Completed Summary (`/history/:id` or `/workouts/:id`)
Purpose: review what happened and optionally copy forward.

Sections:
- Workout header: name, date, duration
- Exercise blocks with logged sets
- Notes
- Actions:
  - `Repeat Workout`
  - `Use as Template`
  - `Edit`
  - `Export this workout` (optional, nice-to-have)

Implementation notes:
- Server-rendered read view.
- Keep structure close to active logger for reuse.

---

### 5) History List (`/history`)
Purpose: find and review past workouts.

Controls:
- Search by template/workout name
- Date range filters
- Optional filter by exercise

List/table columns:
- Date
- Workout name/template
- Duration
- Exercises count
- Volume summary (optional)

Row actions:
- Open details
- Repeat

Implementation notes:
- Pagination over infinite scroll.
- Keep filters simple GET params.

---

### 6) Exercise Library (`/exercises`)
Purpose: manage canonical exercise definitions.

List view columns/cards:
- Name
- Category/body part
- Equipment
- Last used
- Actions: edit, archive

Controls:
- Search
- Filter by category/equipment
- `New Exercise`

#### Exercise create/edit form (`/exercises/new`, `/exercises/:id/edit`)
Fields:
- Name
- Aliases (optional textarea or comma-separated field)
- Category/body part (select)
- Equipment (select)
- Tracking type:
  - Weight + reps (default)
  - Bodyweight + reps
  - Timed
- Default unit (lb/kg)
- Default increment (2.5, 5, etc.)
- Notes / cues
- Active / archived status

Implementation notes:
- Keep categories as simple enums for MVP.
- Archive instead of hard delete.
- Library should be optimized for lookup from workout page.

---

### 7) Templates (`/templates`)
Purpose: define reusable workout structures.

Template list:
- Name
- Exercise count
- Last used
- Actions: start, edit, duplicate, delete

#### Template create/edit (`/templates/new`, `/templates/:id/edit`)
Form structure:
- Template name
- Notes (optional)
- Ordered exercise blocks

Each template exercise block:
- Exercise picker
- Target sets count
- Default reps or rep range
- Default weight (optional)
- Rest seconds default (optional)
- Exercise note (optional)

Actions:
- Add exercise
- Move up/down
- Remove
- Save template

Implementation notes:
- Use same visual structure as workout exercise cards to reduce design/dev overhead.
- Duplication is more useful than complex editing tools.

---

### 8) CSV Import (`/import`)
Purpose: migrate data in from Strong or other exports.

Page structure:
- Intro text: supported format(s), what fields are imported
- File upload form
- Optional mapping options if format is not exact
- Preview table before commit

Recommended import flow:
1. Upload CSV
2. Parse and show summary:
   - workouts found
   - exercises matched
   - new exercises to create
   - rows with problems
3. Preview errors/warnings
4. `Confirm Import`
5. Completion summary with links to imported workouts

Preview components:
- Import summary cards
- Error table: row number, issue, suggested fix
- Exercise matching table: source name -> matched/new

Implementation notes:
- Keep MVP to one well-documented CSV format if possible.
- Two-step import is safer than direct import.
- Use server-side parsing; JS only for improving preview interactions.

---

### 9) Export (`/export`)
Purpose: reassure user they can always leave with their data.

Sections:
- `Export all workouts as CSV`
- `Export exercises`
- `Export templates`
- Optional date-range export

Form fields:
- Export type
- Date range (optional)
- Include archived exercises? checkbox

Implementation notes:
- Prefer one-click downloads.
- Keep schema stable and documented on the page.

---

## Shared components

### Header / nav
- App name
- Main nav links
- Active workout badge if a workout is in progress
- Timer chip if timer running

### Flash messages
- Saved
- Import completed
- Exercise created
- Template updated

### Searchable select / picker
Used for:
- Add exercise to workout
- Add exercise to template

Baseline version:
- Simple text search input + filtered server-rendered list
Enhanced version:
- small JS filter on preloaded list

### Confirmation pattern
Use dedicated confirmation sections or POST-confirm screens for:
- Finish workout
- Delete template
- Archive exercise
- Discard active workout

### Empty states
Critical empty states:
- No workouts yet -> start first workout
- No templates -> create one from your first workout
- No exercises -> add exercise or import CSV

---

## Key user flows

### Flow A: First-time user importing Strong data
1. Land on Dashboard
2. Click `Import / Export`
3. Upload CSV
4. Review preview and conflicts
5. Confirm import
6. Return to Dashboard with recent workouts populated

Success criteria:
- User sees recognizable history quickly
- No silent data loss

### Flow B: Quick start from template
1. Dashboard -> click template quick-start
2. Redirect to active workout logger with exercises prefilled
3. Log sets using repeat/fill helpers
4. Run rest timer between sets
5. Finish workout
6. View summary

Success criteria:
- Start to first logged set in under 15 seconds

### Flow C: Ad-hoc workout
1. Click `Start Workout`
2. Start blank workout
3. Add exercise via search
4. Add/edit sets
5. Add second/third exercises
6. Finish and save

Success criteria:
- No template required
- Exercise adding feels lightweight

### Flow D: Build a template from a completed workout
1. Open completed workout
2. Click `Use as Template`
3. Edit name and target values
4. Save
5. Template appears in template list and dashboard quick-start

### Flow E: Manage exercise library
1. Open Exercises
2. Search/edit existing item or create new one
3. Save metadata
4. Exercise is available in template/workout pickers

---

## Rest timer UX
Rest timer should be global within an active workout but launched from exercise/set context.

MVP behavior:
- Buttons: `Start 60s`, `Start 90s`, `Start 120s`, `Custom`
- Countdown appears as sticky chip in header and bottom bar
- Optional small inline timer near current exercise
- Controls: pause, resume, add +15s, stop
- On finish: visual highlight and optional browser sound notification

Implementation suggestion:
- JS-only component using `setInterval`
- Persist target end timestamp in `localStorage` so refresh does not kill timer
- If no JS, rest timer button can simply reveal recommended rest text; timer itself can degrade gracefully

---

## Form and interaction recommendations
- Use normal HTML `<form>` posts for create/update actions.
- Use one form per major action block when possible.
- For set rows, either:
  - submit whole exercise card at once, or
  - use tiny inline forms per row/action.

Recommended MVP pattern:
- Full page server render
- Small JS helpers to:
  - clone set rows from hidden template
  - move focus to next input on Enter
  - submit on `Done` checkbox toggle
  - maintain rest timer state

Keyboard-friendly behavior:
- Enter in weight moves to reps
- Enter in reps moves to RPE or next row weight
- `+` button for fast add set should be near thumb/cursor position

Mobile behavior:
- Sticky footer actions
- Large numeric inputs
- Avoid wide tables on narrow screens: convert set rows to compact stacked rows/cards if needed

---

## Visual design suggestions
- Prefer a dense-but-clean layout over oversized marketing UI.
- Workout logger should look more like a tool than a dashboard product page.
- Use cards with subtle borders for exercise blocks.
- Make completed sets visually obvious:
  - green check / tinted row
- De-emphasize secondary metadata.
- Keep one accent color for primary actions.

Suggested hierarchy:
- `h1`: page/workout title
- `h2`: exercise names
- Small muted text: previous performance, notes labels, timestamps

---

## Suggested template partial structure
Useful if implementing with Flask/Django/FastAPI + Jinja-like templates.

Partials/components:
- `_flash_messages.html`
- `_nav.html`
- `_workout_header.html`
- `_exercise_card.html`
- `_set_row.html`
- `_exercise_picker.html`
- `_timer_chip.html`
- `_template_card.html`
- `_history_row.html`
- `_import_summary.html`

This keeps server-rendered views maintainable and reuses workout/template layouts.

---

## MVP build order
1. Base layout + nav + dashboard
2. Exercise library CRUD
3. Template CRUD
4. Start workout page
5. Active workout logger with set entry
6. Finish workout + history/detail pages
7. Rest timer JS
8. CSV export
9. CSV import preview + confirm

Reasoning:
- Logging workouts is the heart of the app.
- Exercise/template management should exist before making the logger elegant.
- Import/export can land after core persistence works.

---

## What to keep deliberately simple in MVP
- No drag-and-drop: use move up/down buttons
- No realtime collaboration
- No advanced analytics beyond history browsing
- No complex inline charting
- No per-user theming beyond maybe dark mode later

---

## Summary
If only one screen gets extra polish, make it the **active workout logger**. Everything else can stay straightforward and server-rendered. The winning MVP is not flashy; it is fast, forgiving, and dependable enough to replace Strong for daily use.
