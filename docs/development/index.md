# Development Docs

Reference material for contributors and agents working in the Yaffo codebase.

## Standards

- [Project Context](project-context.md) - architecture, setup, technologies, and
  database migration rules.
- [Coding Standards](coding-standards.md) - Python, templates, DTOs, JavaScript,
  HTMX, and global UI components.
- [Internationalization Standards](internationalization.md) - gettext,
  i18next, locale-aware formatting, and catalog workflow.
- [Task Queue Standards](task-queue.md) - background tasks, queue semantics,
  idempotency, and tests.

## Systems

- [AI Assistant](ai-assistant.md) - documentation answers, bounded diagnostics,
  approved library changes, and public website reads with domain approval.
- [AI Page Builder](ai-page-builder.md)
- [Automations](automations.md)
- [Sync Adapters](automation-sync-adapters.md) - a host sync engine for
  import/export with small Starlark Source and Target adapters per provider, the
  host/adapter hook contract, and peer sync: importing from paired devices through
  the internal REST API sync workers use to reach the web process's P2P engine.
- [OAuth App Registrations](oauth-app-registrations.md) - Phase 0 runbook for the
  Microsoft Entra and Google Cloud desktop clients the sync presets sign in with,
  and the probe that checks them.
- [Distribution](distribution.md)
- [Frontend Testing and TypeScript](frontend-testing-and-typescript.md)
- [Icons](icons.md)
- [P2P Device Pairing](p2p-sharing.md) - design sketch for peer trust without a
  login system.
- [Video](video.md)
- [Web Routes and Request Security](web-routes-and-request-security.md) - the
  app-wide `Host` and origin check, opening files by id, the small shared `/api/`
  blueprint, and page-specific routes kept out of `/api/` (implemented).
