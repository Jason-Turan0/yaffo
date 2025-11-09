# Yaffo Project Context

## Project Overview
A Flask-based photo organization tool that uses EXIF metadata, face recognition, and duplicate detection to automatically organize and index photos.

## Architecture
- **Flask Web App**: Main interface (photo_organizer/app.py)
- **Database**: SQLAlchemy with SQLite (photo_organizer/db/)
- **Routes**: REST API endpoints (photo_organizer/routes/)
- **Scripts**: CLI tools for batch operations (photo_organizer/scripts/)

## Key Components

### Database Models (photo_organizer/db/models.py)
- Photo: Main photo entity with EXIF data
- Face: Detected faces in photos
- Person: Named individuals linked to faces
- Tag: Photo tagging system

### Scripts
- `organize_photos.py`: Organize photos by date using EXIF
- `index_photos.py`: Index photos into database
- `assign_faces.py`: Detect faces using face_recognition library
- `group_faces.py`: Cluster similar faces
- `remove_duplicates.py`: Find duplicate photos using perceptual hashing

### Routes
- `/photos`: Photo management endpoints
- `/faces`: Face detection and management
- `/people`: Person management
- `/home`: Main UI routes

## Technologies
- **Face Recognition**: dlib + face_recognition
- **Image Processing**: Pillow, PIL, opencv-python
- **Duplicate Detection**: ImageHash (perceptual hashing)
- **EXIF**: piexif
- **Database**: SQLAlchemy
- **Web**: Flask

## Development Setup
- Python 3.13.7
- Virtual environment in ./venv
- Activate: `source activate_venv.sh`
- Install: Listed in setup.py

## Code Conventions

- Don't use code comments as a way of describing what you did. Only include comments for very complicated or unconventional code. Use the chat interface to explain what you did and why. Refer to the file and line if necessary.
- Use type hints for any code generated
- Target platform is windows and mac so do all path manipulations server side with os neutral Path lib.
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

### APP_CONFIG
Global configuration object with all Flask routes accessible in JavaScript:

```javascript
// Simple routes
APP_CONFIG.urls.faces_assign // → "/faces/assign"

// Parameterized routes with buildUrl helper
const url = APP_CONFIG.buildUrl('person_update', { person_id: 123 });
// → "/people/123/update"
```

## HTMX Form Patterns (PREFERRED)

**CRITICAL: Use HTMX for all interactive forms. Avoid inline JavaScript and CSS.**

### Default Pattern: Server-Side Rendered Form Fragments

See `yaffo/templates/utilities/remove_duplicates_form.html` as the reference implementation.

**Template Fragment Pattern:**
```html
<div id="my-form">
    <form>
        {% for item in items %}
        <input type="text" name="item" value="{{ item }}">

        <!-- Action buttons with hx-vals to specify action -->
        <button type="button"
                hx-post="{{ url_for('my_form_route') }}"
                hx-vals='{"action": "remove", "index": {{ loop.index0 }}}'
                hx-target="#my-form"
                class="btn-danger">
            Delete
        </button>
        {% endfor %}

        <!-- Add button -->
        <button type="button"
                hx-post="{{ url_for('my_form_route') }}"
                hx-vals='{"action": "create"}'
                hx-target="#my-form"
                class="btn-secondary">
            Add Item
        </button>
    </form>
</div>
```

**Flask Route Pattern:**
```python
@app.route("/my-form", methods=["POST"])
def my_form_route():
    # Collect items from form (multiple inputs with same name)
    items = request.form.getlist('item')
    items = [i.strip() for i in items if i.strip()]

    # Check action from hx-vals
    action = request.form.get('action')

    if action == 'create':
        items.append('')
    elif action == 'remove':
        index = int(request.form.get('index', -1))
        if 0 <= index < len(items):
            items.pop(index)
    elif action == 'browse':
        # Handle browse action
        pass

    # Re-render the form fragment with updated data
    return render_template(
        'my_form_fragment.html',
        items=items
    )
```

**Key Principles:**
1. ✅ **No inline JavaScript** - Use HTMX attributes only
2. ✅ **No inline CSS** - Only class names, styles in CSS files
3. ✅ **Form fragments** - Extract forms into separate template files for re-rendering
4. ✅ **Single endpoint** - One route handles all actions via `hx-vals='{"action": "..."}'`
5. ✅ **Multiple inputs same name** - Use `request.form.getlist('name')` on backend
6. ✅ **Server-side state** - All form state managed server-side, returned as template variables
7. ✅ **HTMX targets** - Use `hx-target` to specify which element to replace
8. ✅ **Complete re-renders** - Return entire form fragment, not partial updates

**What to Avoid:**
- ❌ Inline JavaScript in templates (`<script>` tags inside form fragments)
- ❌ Inline CSS or style attributes
- ❌ Client-side state management
- ❌ Complex JavaScript initialization functions for forms
- ❌ Multiple routes for similar actions (use action parameter instead)

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