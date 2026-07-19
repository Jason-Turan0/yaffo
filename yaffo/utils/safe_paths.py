from __future__ import annotations

from pathlib import Path


class PathOutsideAllowedRoots(ValueError):
    pass


def resolve_path_in_roots(
    path: str | Path,
    roots: list[str | Path],
    *,
    must_exist: bool = True,
) -> tuple[Path, Path]:
    try:
        resolved = Path(path).expanduser().resolve(strict=must_exist)
    except OSError as exc:
        raise PathOutsideAllowedRoots("path could not be resolved") from exc

    for root_value in roots:
        try:
            root = Path(root_value).expanduser().resolve(strict=True)
        except OSError:
            continue
        if resolved == root or root in resolved.parents:
            return resolved, root
    raise PathOutsideAllowedRoots("path is outside the allowed roots")
