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

- [AI Assistant](ai-assistant.md) - implementation plan (proposed): docs Q&A,
  bounded diagnostics, and allowlisted library edits and fixes that run only once
  the user confirms them.
- [AI Page Builder](ai-page-builder.md)
- [Automations](automations.md)
- [Automation Web Calls](automation-web-calls.md) - design sketch (proposed):
  authorized outbound HTTP for custom automations, detected from the script and
  approved at publish; first use case is sharing photos to a Google Photos album.
- [Sync Adapters](automation-sync-adapters.md) - alternative design sketch
  (proposed): a host sync engine for import/export with small Starlark Source
  and Target adapters per provider, and the host/adapter hook contract.
- [OAuth App Registrations](oauth-app-registrations.md) - Phase 0 runbook for the
  Microsoft Entra and Google Cloud desktop clients the sync presets sign in with,
  and the probe that checks them.
- [Distribution](distribution.md)
- [Frontend Testing and TypeScript](frontend-testing-and-typescript.md)
- [Icons](icons.md)
- [P2P Device Pairing](p2p-sharing.md) - design sketch for peer trust without a
  login system.
- [Peer Sync](peer-sync.md) - design sketch (proposed): importing from paired
  devices through the sync engine, and the internal REST API sync workers use to
  reach the web process's P2P engine.
- [Video](video.md)
- [Web Routes and Request Security](web-routes-and-request-security.md) - plan
  (proposed): an app-wide `Host` and origin check, opening files by id, a small
  shared `/api/` blueprint, and moving page-specific routes out of `/api/`.
