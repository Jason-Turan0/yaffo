from __future__ import annotations

import os

from flask import current_app, has_app_context

_PROCESS_DEMO_MODE = os.environ.get("YAFFO_DEMO_MODE") == "1"


class DemoModeOperationBlocked(RuntimeError):
    pass


def demo_mode_enabled() -> bool:
    if has_app_context():
        return bool(current_app.config.get("DEMO_MODE"))
    return _PROCESS_DEMO_MODE


def reject_in_demo(operation: str) -> None:
    if demo_mode_enabled():
        raise DemoModeOperationBlocked(f"{operation} is disabled in demo mode")
