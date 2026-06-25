# Yaffo Project Context

## Project Overview
A Flask-based photo organization tool that uses EXIF metadata, face recognition, and duplicate detection to automatically organize and index photos.

## Architecture
The app package is `yaffo/` (not `photo_organizer/`).
- **Flask Web App**: Main interface (`yaffo/app.py`)
- **Database**: SQLAlchemy with SQLite (`yaffo/db/` — models + repositories)
- **Routes**: REST/HTMX endpoints (`yaffo/routes/`)
- **Background tasks**: task definitions in `yaffo/background_tasks/tasks/`, run on the
  `yaffo/taskq` SQLite queue + spawn worker host (see `docs/task-queue-migration.md`)
- **Automations**: scheduled/event-driven behaviors (`yaffo/background_tasks/`, see
  `docs/automations.md`)
- **AI Page Builder**: agent, model clients, tools, schemas in `yaffo/site_agents/`
  (see `docs/ai-page-builder.md`)
- **Scripts**: `yaffo/scripts/` — `db/` (init_db + dev migrations), `seed_automations.py`

## Key Components

### Database Models (`yaffo/db/models.py`)
- Photo, Face, Person, Tag — core media + people + tagging
- PhotoLabel / ClassificationLabel — CLIP auto-labeling
- Automation / AutomationTrigger — automation definitions + run history (via Job)
- CustomPage / PageVersion / Widget / Conversation — AI page builder
- Job / JobResult — background-job progress + results

### Face recognition
InsightFace — **SCRFD** detection + **ArcFace** 512-d embeddings, on ONNX Runtime
(not dlib/face_recognition; dlib was slower and less accurate — see
`benchmarks/face/README.md`). Cosine similarity over the embeddings.

### Routes
- `/photos`, `/faces`, `/people` — media, faces, people management
- `/pages` — AI page builder
- `/utilities/automations`, `/settings`, `/home` — admin + UI

## Technologies
- **Face Recognition**: InsightFace (SCRFD + ArcFace) on `onnxruntime`
- **Image Processing**: Pillow, opencv-python
- **Duplicate Detection**: ImageHash (perceptual hashing)
- **Auto-Labeling**: CLIP (offline zero-shot)
- **EXIF**: exiftool (primary) + piexif
- **Database**: SQLAlchemy + SQLite
- **Web**: Flask + HTMX
- **AI**: Anthropic Codex (page builder, automation/theme generation)

## Development Setup
- Python 3.13
- Virtual environment in ./venv
- Activate: `source activate_venv.sh`
- Install: `pip install -e .` (deps in `pyproject.toml`; dev extras under `[project.optional-dependencies] dev`)

## Database Migrations

- Add schema changes as the next numbered module in `yaffo/scripts/db/migrations/`;
  keep migrations additive when possible and never commit inside `migrate(conn)`.
- Also update `000_MIGRATION_20260620_INIT.py` so a fresh database receives the
  current schema directly.
- After creating a migration, automatically run the standard migration runner
  against the development database used by the Invoke tasks (`tasks.py` sets
  `YAFFO_DATA_DIR=~/Pictures`):
  `YAFFO_DATA_DIR="$HOME/Pictures" ./venv/bin/python -m yaffo.scripts.db.migrate`.
- Verify the new migration is recorded in `schema_migrations` and that its schema
  change exists in `~/Pictures/yaffo.db`. Do not omit `YAFFO_DATA_DIR` or
  substitute a temporary directory for this required development-database run.

## Code Conventions

- Don't use code comments as a way of describing what you did. Only include comments for very complicated or unconventional code. Use the chat interface to explain what you did and why. Refer to the file and line if necessary.
- Use type hints for any code generated
- Target platform is windows and mac so do all path manipulations server side with os neutral Path lib.
- scripts used for developer or build automation should be stored in /scripts. /yaffo/scripts should be saved for scripts needed at runtime and are packaged with the application
- **Import Style**: Always place imports at the top of the file. Never use local imports inside functions or methods. 

### DRY Principle (Don't Repeat Yourself)
- **CSS Styles**: Never duplicate CSS styles across templates. Use a centralized stylesheet or shared style blocks.
- **Template Inheritance**: Leverage Flask's Jinja2 template inheritance to avoid repeating common HTML structures.
- **Reusable Components**: Extract repeated UI patterns into reusable template components.

### Template Organization
- **Base Templates**: Use base templates (e.g., `base.html`) for common layout elements (header, footer, navigation).
- **Template Includes**: Create reusable template fragments using `{% include %}` for components that appear in multiple places:
  - Form fields
  - Card layouts
  - Modals
  - Navigation components
  - Alert/notification patterns
- **Macros**: Use Jinja2 macros for reusable template logic with parameters.

### Component Extraction Guidelines
Extract a component when:
1. The same HTML/CSS pattern appears in 2+ templates
2. A UI element has consistent behavior across multiple pages
3. A complex structure could benefit from parameterization

Place reusable components in:
- `templates/components/` for small, reusable UI elements
- `templates/macros/` for Jinja2 macros with logic

### Code Reusability Best Practices
- **Python Utilities**: Extract repeated business logic into utility functions/modules
- **Route Decorators**: Use custom decorators for common route behaviors (auth, validation, etc.)
- **Database Queries**: Create reusable query methods on SQLAlchemy models
- **JavaScript**: Use modules and avoid inline scripts; extract repeated client-side logic

### DTOs / Return Types (non-ORM payloads)

Anything a route returns or streams that the client parses structurally — JSON
bodies, NDJSON stream records, the browser-facing widget payloads — is a **wire
contract**, not an ad-hoc dict. Conventions:

- **ORM models never cross the wire.** SQLAlchemy entities (`db/models.py`) are
  the persistence shape only. Serialize at the route boundary (a `to_dict()` on
  the model, or a DTO) — never return a model where its persistence fields would
  leak or lazy-loads would fire mid-serialization.
- **Name the contract.** A structurally-parsed payload is a named type (a
  `@dataclass` DTO, e.g. `WidgetDraft`, or a record constructor), not an inline
  dict literal scattered through a route. One place documents and changes the
  shape; it's unit-testable in isolation.
- **One type per audience.** Keep model-facing and browser-facing shapes distinct
  (e.g. `ToolResult.model_text` vs `host_data`). Don't overload one type when the
  payload schemas differ by audience.
- **Location: DTOs live with the layer that owns the contract, not under
  `routes/`.** Put feature/domain DTOs in the owning package's `schemas.py`
  (e.g. `site_agents/schemas.py`) or, for ORM→dict, a `serializers.py` beside
  the repository — routes import and serialize. Only a *pure view-model* built
  solely by one route belongs near that route. **Never name a DTO module
  `models`** (that means SQLAlchemy here); use `schemas` / `serializers` / `dto`.
- **Derive from the model where you can; pin it where you can't.** Python has no
  static `Omit<T, K>`. When a DTO is "the model minus some columns," don't restate
  the field list — assert the relationship with a drift-guard test (see
  `tests/yaffo/site_agents/test_schemas.py`: `WidgetDraft` == `Widget` columns
  minus the persistence set), so adding a column forces a deliberate choice.
- **Standard envelopes.** Errors: `{"error": "message"}` + the HTTP status (don't
  also send a `success` boolean — the status says it). Acks: `204` / a redirect.

## Global JavaScript Components

### Notification System
A reusable notification component is available globally via `window.notification`:

```javascript
// Show notifications
notification.success('Operation completed!');
notification.error('Something went wrong');
notification.warning('Please review this');
notification.info('Just so you know');

// Or use the generic method with custom duration
notification.show('Message', 'success', 5000); // 5 seconds

// Backward compatible function
showNotification('Message', 'error');
```

Included automatically in `base.html` via:
- `static/notification.js` - JavaScript module
- `static/notification.css` - Styling

### Confirm Dialog
A global confirm dialog component is available via `window.PHOTO_ORGANIZER.confirmDialog`:

```javascript
// Basic usage
const confirmed = await window.PHOTO_ORGANIZER.confirmDialog({
    title: 'Confirm Action',
    message: 'Are you sure you want to do this?',
    confirmText: 'Yes, do it',
    cancelText: 'Cancel',
    confirmClass: 'btn-danger' // Optional: btn-primary (default), btn-danger, btn-success
});

if (confirmed) {
    // User clicked confirm
} else {
    // User clicked cancel or closed dialog
}

// Example: Delete confirmation
const confirmed = await window.PHOTO_ORGANIZER.confirmDialog({
    title: 'Delete Item',
    message: 'This action cannot be undone.',
    confirmText: 'Delete',
    confirmClass: 'btn-danger'
});
```

**Features:**
- Promise-based API for async/await usage
- Customizable title, message, and button text
- Support for multi-line messages (use `\n`)
- Dismissable via Cancel button, backdrop click, or Escape key
- Automatically included in `base.html`

**IMPORTANT:** Always use this instead of native `confirm()` or `alert()` functions.

### Modals
All modals share one skeleton (`static/components/modal.css`): `.modal > .modal-content` containing `.modal-header` (title + ✕ close), `.modal-body` (the only scroll region, `thin-scrollbar`), and `.modal-actions`. Never add ad-hoc scroll wrappers or restyle titles inside a modal.

- **Form modals**: `render_modal()` from `components/modal.html` — Cancel + primary action.
- **Info/help modals**: `render_info_modal()` from `components/info_modal.html` — no form, closes via ✕ or a `btn-secondary` button.
- **Confirmations**: the global confirm dialog (see below) — don't build new ones.
- **Width**: default 500px; pass `size_class="modal-lg"` (720px) for content-heavy bodies.
- **Sub-headings** inside a body: `<h3 class="modal-section-title">` — never bare `h3`.
- Both macros accept unique `id`s and derive element ids from them (`{{id}}Title`, `{{id}}Form`); wire behavior with `window.PHOTO_ORGANIZER.COMPONENTS.modal.init(id)`.

### APP_CONFIG
Global configuration object with all Flask routes accessible in JavaScript:

```javascript
// Simple routes
APP_CONFIG.urls.faces_assign // → "/faces/assign"

// Parameterized routes with buildUrl helper
const url = APP_CONFIG.buildUrl('person_update', { person_id: 123 });
// → "/people/123/update"
```

## HTMX vs. JavaScript modules — pick by who owns the state

HTMX is the right tool for **server-owned, mostly-stateless swaps**, and the wrong
tool for **interactions with meaningful in-progress client state**. The split:

- **Use HTMX** when the server is the source of truth and the browser just
  re-renders what it sends: progress **polling**, **pagination** / list navigation,
  and simple **toggle / delete** fragments. There's no uncommitted client state for
  a swap to destroy.
- **Use a namespaced JS module** (see *Passing Template Variables to JavaScript*,
  next) when the interaction has real client state: editable lists, multi-step
  wizards, drag/drop, maps, live widgets, canvas — anything where re-rendering
  mid-interaction would lose the user's focus, scroll, or uncommitted input.

Why the split: an HTMX swap (`outerHTML`/`innerHTML`) replaces DOM nodes, so it
discards focus, scroll, in-flight input, and JS listeners attached to the swapped
content. That's free when the server owns the state and costly when the client
does. Simulating client state through `hx-vals` round-trips (the "one route with an
`action` discriminator" shape) is where most of our HTMX bugs live — prefer a JS
module there.

### Canonical pattern: a self-polling fragment

`yaffo/templates/fragments/job_status_fragment.html` is the reference. The fragment
polls itself and swaps itself, with **zero JavaScript** — and **drops its own
polling attributes once the job is finished**, so it stops polling on its own:

```html
<div class="job-card" id="job-{{ job.id }}"
     {% if not is_finished %}
     hx-get="{{ url_for('job_fragment', job_id=job.id) }}"
     hx-trigger="every 5s"
     hx-swap="outerHTML"
     {% endif %}>
    <div class="progress-bar" style="width: {{ progress }}%"></div>
    <div class="progress-text">{{ "%.2f"|format(progress) }}%</div>
    {% if show_cancel %}
    <button class="btn btn-danger btn-sm"
            hx-post="{{ url_for('job_cancel', job_id=job.id) }}"
            hx-target="#job-{{ job.id }}" hx-swap="outerHTML">Cancel</button>
    {% endif %}
</div>
```

The route renders the same fragment with fresh `Job` data; the server is the only
state. (`utilities/automations_runs.html` is the same shape for automation runs;
`components/htmx_pagination.html` for list navigation.)

### Guidelines for the HTMX cases
- **Return a whole fragment**, not a partial — the swap target replaces an element
  wholesale (`hx-target` + `hx-swap="outerHTML"`).
- **Self-terminate polling**: gate the `hx-trigger="every Ns"` attrs on a "done"
  condition (as above) so a finished fragment stops hitting the server.
- **Beware poll-vs-click races**: on a fragment that both self-polls and has action
  buttons, a poll can swap the DOM out from under a click — keep such fragments
  small and their actions idempotent.
- **No inline JavaScript in templates.** `hx-vals='js:…'` counts as inline JS; if
  you need computed values, that's a signal the interaction wants a JS module.
- Styling stays in CSS files (class names only) per the DRY/CSS rules above — not an
  HTMX-specific rule, but it applies here too.

## Passing Template Variables to JavaScript

**Preferred Pattern: Namespaced Module with Initialization Function**

Use a namespaced module pattern with an initialization function. This keeps data scoped, prevents naming collisions, and provides a clean public API.

**Template (HTML):**
```html
{% block scripts %}
<script src="{{ url_for('static', filename='utilities/index_photos.js') }}"></script>
<script>
window.PHOTO_ORGANIZER.initIndexPhotos({{ unindexed_photos | tojson }}, {{ orphaned_photos | tojson }});
</script>
{% endblock %}
```

**JavaScript Module:**
```javascript
window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.initIndexPhotos = (unindexedPhotos, orphanedPhotos) => {
    // Private functions and variables (closure scope)
    const startSync = async () => {
        const syncButton = document.getElementById('sync-button');
        // ... use unindexedPhotos and orphanedPhotos directly
    };

    const pollJobStatus = async () => {
        // ... implementation
    };

    // Initialize event listeners
    const syncButton = document.getElementById('sync-button');
    if (syncButton) {
        syncButton.addEventListener('click', startSync);
    }

    // Return public API (optional)
    return {
        startSync,
        pollJobStatus
    };
};
```

**Why this pattern:**
- **Namespacing**: `window.PHOTO_ORGANIZER` prevents global namespace pollution
- **Closure scope**: Data (parameters) and private functions are enclosed, not accessible globally
- **Clean initialization**: Template data is passed directly as parameters
- **Public API**: Optionally expose functions for testing or external use
- **No syntax errors**: Handles complex JSON objects safely

**Key Points:**
- Always use the `window.PHOTO_ORGANIZER` namespace for all modules
- Init functions should accept data as parameters (use closures for access)
- Use arrow functions for cleaner syntax and lexical `this`
- Return public methods only if needed for testing or cross-module communication
- **Always pass `window.APP_CONFIG`** as the last parameter for access to routes and URLs

**Avoid:**
- Inline `onclick` attributes with JSON data (causes syntax errors)
- Separate global variables like `window.pageData`
- Data attributes for complex objects
- Polluting the global namespace with individual functions
- Hardcoding API URLs in JavaScript (use APP_CONFIG instead)
- **NEVER use `alert()` or `confirm()` in JavaScript** - use the global confirm dialog or notification system instead

### Passing APP_CONFIG for API URLs

Always pass `window.APP_CONFIG` to initialization functions to access Flask routes and build URLs dynamically.

**Template:**
```html
{% block scripts %}
<script src="{{ url_for('static', filename='photos/tags.js') }}"></script>
<script>
window.PHOTO_ORGANIZER.photoTags = window.PHOTO_ORGANIZER.initPhotoTags(
    {{ photo.id }},
    window.APP_CONFIG
);
</script>
{% endblock %}
```

**JavaScript Module:**
```javascript
window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.initPhotoTags = (photoId, config) => {
    const addTag = async () => {
        // Use config.buildUrl for parameterized routes
        const url = config.buildUrl('add_photo_tag', { photo_id: photoId });

        // Or use hardcoded API paths (acceptable for API endpoints not in routes)
        const response = await fetch(`/api/photo/${photoId}/tags`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tag_name: tagName })
        });
    };

    return {
        addTag
    };
};
```

**Benefits:**
- Routes are centralized and maintained in Flask
- No hardcoded URLs scattered across JavaScript files
- Easy to refactor routes without updating JS
- Type-safe URL building with `buildUrl()`
