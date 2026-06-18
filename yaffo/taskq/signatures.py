"""Composition primitives for the task queue: signatures, pipelines and chords.

These mirror the small slice of Huey's API the app actually composes with
(`task.s(...)`, `chord(members, callback)`, `.then(next, *args)`) so call sites
change as little as possible. A `Signature` is a deferred call (task name + args +
the metadata the host needs to materialise a row without importing task code). A
`Pipeline` is an ordered list of links; each link is a single signature or a chord
(a group of member signatures plus a callback). Everything here is plain data and
JSON-serialisable so a whole composed graph can be persisted in `queue.db` and
rebuilt by the host, which never imports the task functions themselves.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Union


@dataclass(frozen=True)
class Signature:
    """A deferred call to a registered task.

    `context`/`lock_name` are captured from the task at `.s()` time so the host can
    insert a row for this signature without importing the task module (the host
    must never load dlib).
    """
    task_name: str
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)
    context: bool = False
    lock_name: Optional[str] = None

    def then(self, task, *args) -> "Pipeline":
        return Pipeline([SingleLink(self)]).then(task, *args)

    def to_dict(self) -> dict:
        return {
            "name": self.task_name,
            "args": list(self.args),
            "kwargs": dict(self.kwargs),
            "context": self.context,
            "lock_name": self.lock_name,
        }

    @staticmethod
    def from_dict(d: dict) -> "Signature":
        return Signature(
            task_name=d["name"],
            args=tuple(d.get("args", [])),
            kwargs=dict(d.get("kwargs", {})),
            context=bool(d.get("context", False)),
            lock_name=d.get("lock_name"),
        )


@dataclass
class SingleLink:
    sig: Signature

    def to_dict(self) -> dict:
        return {"single": self.sig.to_dict()}


@dataclass
class ChordLink:
    members: list[Signature]
    callback: Optional[Signature]

    def to_dict(self) -> dict:
        return {
            "chord": {
                "members": [m.to_dict() for m in self.members],
                "callback": self.callback.to_dict() if self.callback else None,
            }
        }


Link = Union[SingleLink, ChordLink]


@dataclass
class Pipeline:
    """An ordered chain of links. Each step's result is appended to the next
    step's args, matching Huey's pipeline contract."""
    links: list[Link]

    def then(self, task, *args) -> "Pipeline":
        # Mutates in place and returns self, matching Huey: call sites do
        # `p = chord(...); p.then(...)` without reassigning the result.
        self.links.append(SingleLink(task.s(*args)))
        return self


def link_from_dict(d: dict) -> Link:
    if "single" in d:
        return SingleLink(Signature.from_dict(d["single"]))
    chord_d = d["chord"]
    return ChordLink(
        members=[Signature.from_dict(m) for m in chord_d["members"]],
        callback=Signature.from_dict(chord_d["callback"]) if chord_d["callback"] else None,
    )


def links_to_json(links: list[Link]) -> list[dict]:
    return [link.to_dict() for link in links]


def links_from_json(data: Any) -> list[Link]:
    return [link_from_dict(d) for d in (data or [])]


def iter_signatures(links: list[Link]):
    """Every Signature in a pipeline (chord members, chord callbacks, single
    links) -- used to validate args before a graph is enqueued."""
    for link in links:
        if isinstance(link, SingleLink):
            yield link.sig
        elif isinstance(link, ChordLink):
            yield from link.members
            if link.callback is not None:
                yield link.callback


def chord(members: list[Signature], callback: Optional[Signature] = None) -> Pipeline:
    """A group of member signatures with a barrier callback that fires once with
    the list of member results appended to its args. Returns a Pipeline so it can
    be `.then(...)`-chained, like Huey's `chord(...).then(...)`."""
    return Pipeline([ChordLink(list(members), callback)])
