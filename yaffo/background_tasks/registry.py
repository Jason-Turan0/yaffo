from typing import Callable

from yaffo.db.models import Automation

# Maps Automation.handler -> a function that enqueues a system automation's
# concrete huey task. Populated by @register_handler at task-definition time, so
# this module imports no task code and the dispatcher reads HANDLERS at call time
# -- which keeps it free of the import-order cycles a task<->dispatcher mapping
# would otherwise create. Custom automations have handler=None and run their
# `code` via a generic executor (a later step), not this registry.
AutomationHandler = Callable[[Automation], None]

HANDLERS: dict[str, AutomationHandler] = {}


def register_handler(key: str) -> Callable[[AutomationHandler], AutomationHandler]:
    def deco(fn: AutomationHandler) -> AutomationHandler:
        HANDLERS[key] = fn
        return fn
    return deco