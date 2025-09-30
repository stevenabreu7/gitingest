"""Tests for ingestion utility helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from gitingest.utils.ingestion_utils import _should_include

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(name="base_dir")
def fixture_base_dir(tmp_path: Path) -> Path:
    """Create a base directory structure for include tests."""
    (tmp_path / "src" / "nested").mkdir(parents=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    return tmp_path


def test_should_include_skips_unrelated_directories(base_dir: Path) -> None:
    """Directories with no relationship to the include patterns are skipped."""
    include = {"src/**/*.py"}

    assert _should_include(base_dir / "src", base_dir, include)
    assert _should_include(base_dir / "src" / "nested", base_dir, include)
    assert not _should_include(base_dir / "docs", base_dir, include)


def test_should_include_only_allows_ancestors(base_dir: Path) -> None:
    """Only directories that are ancestors of an include pattern remain."""
    include = {"tests/unit/test_example.py"}

    assert _should_include(base_dir / "tests", base_dir, include)
    assert _should_include(base_dir / "tests" / "unit", base_dir, include)
    assert not _should_include(base_dir / "tests" / "unit" / "extra", base_dir, include)


def test_should_include_handles_global_patterns(base_dir: Path) -> None:
    """Recursive patterns keep directories in play for potential matches."""
    include = {"**/*.py"}

    assert _should_include(base_dir / "docs", base_dir, include)
    assert _should_include(base_dir / "src" / "nested", base_dir, include)


def test_should_include_returns_false_when_no_patterns(base_dir: Path) -> None:
    """Without include patterns, directories should be skipped."""
    assert not _should_include(base_dir / "docs", base_dir, set())
