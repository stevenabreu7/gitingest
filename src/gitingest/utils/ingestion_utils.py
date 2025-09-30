"""Utility functions for the ingestion process."""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from functools import lru_cache
from typing import TYPE_CHECKING

from pathspec import PathSpec

if TYPE_CHECKING:
    from pathlib import Path


def _should_include(path: Path, base_path: Path, include_patterns: set[str]) -> bool:
    """Return ``True`` if ``path`` matches ``include_patterns`` or leads to one.

    Parameters
    ----------
    path : Path
        The absolute path of the file or directory to check.

    base_path : Path
        The base directory from which the relative path is calculated.

    include_patterns : set[str]
        A set of patterns to check against the relative path.

    Returns
    -------
    bool
        ``True`` if the path matches any of the include patterns, ``False`` otherwise.

    """
    rel_path = _relative_or_none(path, base_path)
    if rel_path is None:  # outside repo → do *not* include
        return False

    patterns_key = tuple(sorted(include_patterns))
    spec, parsed_patterns = _get_include_spec(patterns_key)

    rel_str = rel_path.as_posix()
    if rel_str == ".":
        rel_str = ""

    if not path.is_dir():
        return spec.match_file(rel_str)

    if rel_str and spec.match_file(f"{rel_str}/"):
        return True

    dir_parts = _relative_parts(rel_path)
    return any(_pattern_could_match_directory(pattern, dir_parts) for pattern in parsed_patterns)


@lru_cache(maxsize=None)
def _get_include_spec(
    patterns_key: tuple[str, ...],
) -> tuple[PathSpec, tuple[_ParsedIncludePattern, ...]]:
    """Return the ``PathSpec`` and parsed pattern parts for ``include_patterns``."""
    spec = PathSpec.from_lines("gitwildmatch", patterns_key)
    parsed = tuple(_parse_include_pattern(pattern) for pattern in patterns_key)
    return spec, parsed


@dataclass(frozen=True)
class _ParsedIncludePattern:
    """Represent a parsed include pattern for directory matching."""

    parts: tuple[str, ...]
    has_dir_separator: bool


def _parse_include_pattern(pattern: str) -> _ParsedIncludePattern:
    """Split an include pattern into path segments and metadata."""
    pattern = pattern.strip()
    if pattern.startswith("./"):
        pattern = pattern[2:]

    stripped_trailing = pattern.rstrip("/")
    has_dir_separator = "/" in stripped_trailing

    normalized = stripped_trailing.strip("/")
    if not normalized:
        parts: tuple[str, ...] = ()
    else:
        parts = tuple(part for part in normalized.split("/") if part not in {"", "."})

    return _ParsedIncludePattern(parts=parts, has_dir_separator=has_dir_separator)


def _relative_parts(rel_path: Path) -> tuple[str, ...]:
    """Return the normalized parts for a relative ``Path``."""
    parts = rel_path.parts
    if parts and parts[0] == ".":
        parts = parts[1:]
    return tuple(str(part) for part in parts)


def _pattern_could_match_directory(pattern: _ParsedIncludePattern, dir_parts: tuple[str, ...]) -> bool:
    """Return ``True`` if ``pattern_parts`` could match a path under ``dir_parts``."""
    if not pattern.has_dir_separator:
        # Patterns without a directory separator match basenames anywhere, so any
        # directory could still contain a matching file deeper inside.
        return True

    memo: dict[tuple[int, int], bool] = {}

    def _matches(p_idx: int, d_idx: int) -> bool:
        key = (p_idx, d_idx)
        if key in memo:
            return memo[key]

        if d_idx == len(dir_parts):
            result = True
        elif p_idx == len(pattern.parts):
            result = False
        else:
            part = pattern.parts[p_idx]
            if part == "**":
                result = _matches(p_idx + 1, d_idx) or _matches(p_idx, d_idx + 1)
            elif fnmatchcase(dir_parts[d_idx], part):
                result = _matches(p_idx + 1, d_idx + 1)
            else:
                result = False

        memo[key] = result
        return result

    return _matches(0, 0)


def _should_exclude(path: Path, base_path: Path, ignore_patterns: set[str]) -> bool:
    """Return ``True`` if ``path`` matches any of ``ignore_patterns``.

    Parameters
    ----------
    path : Path
        The absolute path of the file or directory to check.
    base_path : Path
        The base directory from which the relative path is calculated.
    ignore_patterns : set[str]
        A set of patterns to check against the relative path.

    Returns
    -------
    bool
        ``True`` if the path matches any of the ignore patterns, ``False`` otherwise.

    """
    rel_path = _relative_or_none(path, base_path)
    if rel_path is None:  # outside repo → already "excluded"
        return True

    spec = PathSpec.from_lines("gitwildmatch", ignore_patterns)
    return spec.match_file(str(rel_path))


def _relative_or_none(path: Path, base: Path) -> Path | None:
    """Return *path* relative to *base* or ``None`` if *path* is outside *base*.

    Parameters
    ----------
    path : Path
        The absolute path of the file or directory to check.
    base : Path
        The base directory from which the relative path is calculated.

    Returns
    -------
    Path | None
        The relative path of ``path`` to ``base``, or ``None`` if ``path`` is outside ``base``.

    """
    try:
        return path.relative_to(base)
    except ValueError:  # path is not a sub-path of base
        return None
