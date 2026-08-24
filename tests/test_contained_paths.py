"""#1609 shared relative-path containment helpers."""

from pathlib import Path

import pytest

from open_notebook.utils.contained_paths import (
    resolve_contained_path,
    to_relative_path,
)


def test_to_relative_path_under_root(tmp_path):
    root = tmp_path / "data" / "reports"
    root.mkdir(parents=True)
    f = root / "out" / "r1.md"
    f.parent.mkdir()
    f.write_text("x")
    assert to_relative_path(str(f), root) == "out/r1.md"


def test_to_relative_path_outside_raises(tmp_path):
    root = tmp_path / "reports"
    root.mkdir()
    with pytest.raises(ValueError, match="outside"):
        to_relative_path(str(tmp_path / "elsewhere.md"), root)


def test_resolve_contained_path_rejects_absolute(tmp_path):
    root = tmp_path / "reports"
    root.mkdir()
    assert resolve_contained_path(str(tmp_path / "x.md"), root) is None


def test_resolve_contained_path_ok(tmp_path):
    root = tmp_path / "reports"
    f = root / "a" / "b.md"
    f.parent.mkdir(parents=True)
    f.write_text("ok")
    resolved = resolve_contained_path("a/b.md", root)
    assert resolved == Path(f).resolve()
