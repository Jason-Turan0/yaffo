# Yaffo Agent Context

Use the development docs as the source of truth for project conventions:

- [Project Context](docs/development/project-context.md) - architecture,
  technologies, setup, and database migration rules.
- [Coding Standards](docs/development/coding-standards.md) - Python, templates,
  DTOs, JavaScript modules, global UI components, HTMX, and `APP_CONFIG`.
- [Task Queue Standards](docs/development/task-queue.md) - background task
  definitions, queue semantics, idempotency, and tests.
- [Automations](docs/development/automations.md) - scheduled and event-driven
  automation behavior.
- [AI Page Builder](docs/development/ai-page-builder.md) - page generation,
  widgets, schemas, and async generation.
- [Internationalization Standards](docs/development/internationalization.md) -
  gettext/i18next, locale-aware formatting, catalogs, and i18n tests.

Quick reminders:

- The app package is `yaffo/`.
- Use InsightFace/ONNX Runtime for face recognition; do not reintroduce dlib.
- Use `pathlib.Path` for cross-platform paths.
- Place imports at module top level.
- Use the global confirm dialog instead of native `alert()` or `confirm()`.
- For schema migrations, follow
  [Project Context - Database Migrations](docs/development/project-context.md#database-migrations),
  including the required `YAFFO_DATA_DIR="$HOME/Pictures"` migration run.
