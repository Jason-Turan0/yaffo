"""
Context decorator for tagging code with test context identifiers.

Usage:
    @context("yaffo-gallery")
    def init_home_routes(app: Flask):
        ...

The decorator is a no-op at runtime - it simply marks functions/classes
for discovery by the test generator, which searches for @context("tag")
patterns to find relevant source code.
"""
from functools import wraps
from typing import Callable, TypeVar

F = TypeVar('F', bound=Callable)


def context(tag: str) -> Callable[[F], F]:
    """
    Decorator to tag functions/classes with a context identifier.

    Args:
        tag: A string identifier used by the test generator to find
             relevant code (e.g., "yaffo-gallery", "photo-upload")

    Returns:
        The original function unchanged (this is a marker decorator)
    """
    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        wrapper._context_tag = tag
        return wrapper  # type: ignore
    return decorator