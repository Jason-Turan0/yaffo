"""The selection carried on a URL, shared by every paginated grid that can act on
what you tick (the album grids, the remote gallery).

THE URL IS THE STATE — like the filters and the page number. So the server renders
which cards are ticked, pagination links carry the selection, and the POST that acts
on it reads the same parameters. There is no client-side store to drift out of sync.

The selection is one of two things, because the visible page is not the selection:

  - explicit (`select_id=…`): the ids the user ticked;
  - scope (`select=all`): the WHOLE scope — every member of the album / every file
    matching the filters, including rows on pages never rendered — minus any
    `exclude_id` the user unticked. Unticking narrows the scope; it does not
    collapse it to the visible page.

Ids are whatever identifies a card on that screen: an integer media-item id in an
album, a relative path in the remote gallery.
"""
from dataclasses import dataclass
from typing import Callable, TypeVar

from werkzeug.datastructures import MultiDict

T = TypeVar("T")

# Never filters: the selection is screen state, and it must not be echoed back into
# a request that has just consumed it.
SELECTION_ARGS = frozenset({"select", "select_id", "exclude_id"})


@dataclass(frozen=True)
class Selection:
    all: bool
    ids: frozenset
    excluded: frozenset
    total: int  # size of the whole scope, so a scope selection can be counted

    def is_selected(self, item_id) -> bool:
        if self.all:
            return item_id not in self.excluded
        return item_id in self.ids

    @property
    def empty(self) -> bool:
        return self.count == 0

    @property
    def count(self) -> int:
        if self.all:
            return max(0, self.total - len(self.excluded))
        return len(self.ids)

    @property
    def query_params(self) -> dict:
        """The selection as querystring parameters, so pagination links (and the
        action forms) carry it."""
        if self.all:
            return {"select": "all", "exclude_id": sorted(self.excluded)}
        return {"select_id": sorted(self.ids)}


def selection_from_args(args: MultiDict, total: int, cast: Callable[[str], T] = int) -> Selection:
    """Read the selection off the querystring. `cast` is how this screen's card ids
    are spelled — int for a media-item id, str for a remote file's relative path."""
    def _parse(key: str) -> frozenset:
        values = set()
        for raw in args.getlist(key):
            try:
                values.add(cast(raw))
            except (TypeError, ValueError):
                continue  # a junk id in the URL selects nothing; it must not 500
        return frozenset(values)

    return Selection(
        all=args.get("select") == "all",
        ids=_parse("select_id"),
        excluded=_parse("exclude_id"),
        total=total,
    )
