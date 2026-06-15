from typing import Callable, Optional

from yaffo.background_tasks.events import EventContext
from yaffo.db.models import Automation

# Maps Automation.handler -> a function that enqueues a system automation's
# concrete huey task. Populated by @register_handler at task-definition time, so
# this module imports no task code and the dispatchers read HANDLERS at call time
# -- which keeps it free of the import-order cycles a task<->dispatcher mapping
# would otherwise create. Custom automations have handler=None and run their
# `code` via a generic executor (a later step), not this registry.
#
# Handlers take the firing Automation and an optional EventContext: the schedule
# dispatcher passes None; the event dispatcher passes the event's context.
AutomationHandler = Callable[[Automation, Optional[EventContext]], None]

HANDLERS: dict[str, AutomationHandler] = {}


def register_handler(key: str) -> Callable[[AutomationHandler], AutomationHandler]:
    def deco(fn: AutomationHandler) -> AutomationHandler:
        HANDLERS[key] = fn
        return fn
    return deco