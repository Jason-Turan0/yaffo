# Photo Organizer Project Context

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

### APP_CONFIG
Global configuration object with all Flask routes accessible in JavaScript:

```javascript
// Simple routes
APP_CONFIG.urls.faces_assign // → "/faces/assign"

// Parameterized routes with buildUrl helper
const url = APP_CONFIG.buildUrl('person_update', { person_id: 123 });
// → "/people/123/update"
```