# Test Generator

AI-powered Playwright test generator that converts YAML specs to executable tests.

## Context Markers

The test generator supports pre-loading source code context into the initial prompt using `@context` markers. This reduces API calls by providing relevant code upfront instead of requiring the AI to explore the codebase.

### Convention

Use the `@context` marker followed by a tag name to mark files as relevant to specific features:

**Python** - Use the decorator:
```python
from yaffo.utils.context import context

@context("yaffo-gallery")
def init_home_routes(app: Flask):
    ...
```

**JavaScript** - Use a JSDoc-style comment:
```javascript
/** @context yaffo-gallery */
window.PHOTO_ORGANIZER.initGallery = () => { ... }
```

**CSS** - Use a block comment at the top:
```css
/* @context yaffo-gallery */
.photo-grid { ... }
```

**HTML/Jinja** - Use a template comment:
```html
{# @context yaffo-gallery #}
{% extends "base.html" %}
```

Or standard HTML comment:
```html
<!-- @context yaffo-gallery -->
<div class="photo-grid">...</div>
```

### Spec File Usage

Reference context in your YAML spec files using either direct paths or attribute-based search:

```yaml
context:
  # Direct file path
  - tag: gallery-template
    path: templates/index.html
    description: Main gallery template

  # Search by @context marker
  - tag: gallery-code
    attribute: yaffo-gallery
    description: All files tagged for gallery feature
```

The `attribute` option searches for `@context.*{attribute}` pattern across `.py`, `.html`, `.js`, and `.css` files.

### Schema

```typescript
context:
  - tag: string          # Identifier for this context group (required)
    path?: string        # Direct path to file (optional)
    attribute?: string   # @context tag to search for (optional)
    description?: string # Description shown in prompt (optional)
```

Either `path` or `attribute` must be provided.