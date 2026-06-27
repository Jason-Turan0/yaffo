# Coding Standards

These standards apply to Python, Jinja, CSS, JavaScript, DTOs, and browser
interaction patterns in Yaffo.

## General Code

- Do not use code comments to describe what you changed. Use comments only for
  complicated or unconventional code.
- Use type hints for generated Python code.
- The target platforms are Windows and macOS. Use `pathlib.Path` and other
  server-side path abstractions instead of OS-specific string manipulation.
- Developer and build automation scripts belong in `/scripts`.
- `yaffo/scripts/` is reserved for runtime scripts packaged with the
  application.
- Place imports at the top of the file. Do not use local imports inside
  functions or methods unless there is a compelling circular-import or startup
  boundary reason already established in nearby code.

## DRY and Template Organization

Do not duplicate CSS styles across templates. Use centralized stylesheets or
shared style blocks.

Use Jinja template inheritance for shared page structure and create reusable
template fragments for repeated UI.

Preferred locations:

- `templates/components/` for small reusable UI elements.
- `templates/macros/` for reusable Jinja macros with logic.

Extract a component when:

- the same HTML/CSS pattern appears in two or more templates;
- a UI element has consistent behavior across multiple pages;
- a complex structure benefits from parameterization.

Common reusable component candidates:

- form fields;
- card layouts;
- modals;
- navigation;
- alert and notification patterns.

## Reuse Guidelines

- Extract repeated Python business logic into utility functions or owning domain
  modules.
- Use custom route decorators for common route behavior when that pattern is
  already present or clearly reduces repetition.
- Put reusable database queries in repositories or query helpers rather than
  scattering ad hoc route queries.
- Use JavaScript modules and avoid inline scripts for repeated client behavior.

## DTOs and Wire Contracts

Anything a route returns or streams that the client parses structurally is a wire
contract, not an ad hoc dictionary. This includes JSON bodies, NDJSON stream
records, and browser-facing widget payloads.

Rules:

- ORM models never cross the wire. SQLAlchemy entities are persistence shapes
  only.
- Serialize at the route boundary with a model `to_dict()` or a named DTO.
- A structurally parsed payload must have a named type or constructor, not
  scattered inline dict literals.
- Keep model-facing and browser-facing shapes distinct. Do not overload one type
  for different audiences.
- DTOs live with the layer that owns the contract, not under `routes/`.
- Use `schemas.py`, `serializers.py`, or `dto.py`; never name a DTO module
  `models`.
- When a DTO is "the model minus some columns," use drift-guard tests instead of
  restating the field list without protection.
- Standard error envelope: `{"error": "message"}` plus the HTTP status. Do not
  also send a `success` boolean.
- Standard acknowledgements: `204` or redirect.

For an example drift guard, see `tests/yaffo/site_agents/test_schemas.py`, where
`WidgetDraft` is asserted against `Widget` columns minus the persistence set.

## Global JavaScript Components

### Notifications

The global notification component is available as `window.notification`:

```javascript
notification.success('Operation completed!');
notification.error('Something went wrong');
notification.warning('Please review this');
notification.info('Just so you know');
notification.show('Message', 'success', 5000);
```

The backward-compatible `showNotification('Message', 'error')` function is also
available.

The component is included through `base.html` via:

- `static/notification.js`
- `static/notification.css`

### Confirm Dialog

Use `window.PHOTO_ORGANIZER.confirmDialog` instead of native `confirm()` or
`alert()`:

```javascript
const confirmed = await window.PHOTO_ORGANIZER.confirmDialog({
    title: 'Delete Item',
    message: 'This action cannot be undone.',
    confirmText: 'Delete',
    cancelText: 'Cancel',
    confirmClass: 'btn-danger'
});
```

The confirm dialog is promise-based, supports custom button text and classes,
supports multiline messages with `\n`, and dismisses through Cancel, backdrop
click, or Escape.

### Modals

All modals share one skeleton from `static/components/modal.css`:

```html
<div class="modal">
  <div class="modal-content">
    <div class="modal-header"></div>
    <div class="modal-body thin-scrollbar"></div>
    <div class="modal-actions"></div>
  </div>
</div>
```

Rules:

- `.modal-body` is the only scroll region.
- Do not add ad hoc scroll wrappers.
- Do not restyle modal titles inside a modal.
- Form modals use `render_modal()` from `components/modal.html`.
- Info/help modals use `render_info_modal()` from `components/info_modal.html`.
- Confirmations use the global confirm dialog.
- Default width is 500px; use `size_class="modal-lg"` for 720px content-heavy
  modals.
- Subheadings inside modal bodies use `<h3 class="modal-section-title">`, never
  bare `h3`.
- Modal macros accept unique ids and derive element ids from them.
- Wire modal behavior with
  `window.PHOTO_ORGANIZER.COMPONENTS.modal.init(id)`.

### APP_CONFIG

`APP_CONFIG` exposes Flask routes to JavaScript:

```javascript
APP_CONFIG.urls.faces_assign;

const url = APP_CONFIG.buildUrl('person_update', { person_id: 123 });
```

Use `APP_CONFIG.buildUrl()` for parameterized routes instead of hardcoding URLs
in JavaScript.

## HTMX vs JavaScript Modules

Use HTMX when the server owns the state and the browser only re-renders what the
server sends. Good cases include:

- progress polling;
- pagination and list navigation;
- simple toggle/delete fragments.

Use a namespaced JavaScript module when the interaction has meaningful
in-progress client state. Good cases include:

- editable lists;
- multi-step wizards;
- drag and drop;
- maps;
- live widgets;
- canvas or other rich tools.

HTMX swaps replace DOM nodes, so they discard focus, scroll, in-flight input, and
listeners attached to swapped content. That is fine for server-owned fragments
and costly for client-owned interactions.

Avoid simulating client state through `hx-vals` round trips or route `action`
discriminators. That shape is usually a sign the interaction belongs in a
JavaScript module.

### Self-Polling Fragment Pattern

`yaffo/templates/fragments/job_status_fragment.html` is the reference shape. A
self-polling fragment renders its own polling attributes only while it is not
finished:

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

Guidelines:

- Return a whole fragment, not a partial.
- Use `hx-target` plus `hx-swap="outerHTML"` when replacing the target.
- Self-terminate polling by omitting polling attributes once work is done.
- Keep fragments with both polling and actions small, and make actions
  idempotent.
- Do not use inline JavaScript in templates. `hx-vals='js:...'` counts as inline
  JavaScript.
- Keep styling in CSS files.

## Passing Template Variables to JavaScript

Use namespaced modules with initialization functions. Template data is passed as
arguments, and module state stays inside the initializer closure.

Template:

```html
{% block scripts %}
<script src="{{ url_for('static', filename='utilities/index_photos.js') }}"></script>
<script>
window.PHOTO_ORGANIZER.initIndexPhotos(
    {{ unindexed_photos | tojson }},
    {{ orphaned_photos | tojson }},
    window.APP_CONFIG
);
</script>
{% endblock %}
```

Module:

```javascript
window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};

window.PHOTO_ORGANIZER.initIndexPhotos = (unindexedPhotos, orphanedPhotos, config) => {
    const startSync = async () => {
        const syncButton = document.getElementById('sync-button');
        const url = config.buildUrl('sync_photos');
    };

    const syncButton = document.getElementById('sync-button');
    if (syncButton) {
        syncButton.addEventListener('click', startSync);
    }

    return { startSync };
};
```

Rules:

- Use the `window.PHOTO_ORGANIZER` namespace for modules.
- Initializers accept data as parameters.
- Pass `window.APP_CONFIG` as the last parameter.
- Use closures for private functions and data.
- Return public methods only when tests or another module need them.
- Use arrow functions unless nearby code uses another established style.

Avoid:

- inline `onclick` attributes with JSON data;
- separate global variables such as `window.pageData`;
- data attributes for complex objects;
- global standalone functions;
- hardcoded API URLs when `APP_CONFIG` can build the route;
- native `alert()` and `confirm()`.
