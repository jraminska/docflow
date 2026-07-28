"""Тесты сканера и регрессии UI-импортов для сканирования."""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from docflow import scanner  # noqa: E402


def test_scan_source_finds_nested_files(tmp_path):
    root = tmp_path / "4300_ПД" / "01_ПЗ" / "doc"
    root.mkdir(parents=True)
    f = root / "note.pdf"
    f.write_bytes(b"%PDF-1.4")
    (tmp_path / "4300_ПД" / "!ARCHIVE").mkdir()
    (tmp_path / "4300_ПД" / "!ARCHIVE" / "old.pdf").write_bytes(b"old")

    recs = scanner.scan_source(
        "Сервер",
        str(tmp_path / "4300_ПД"),
        "ПД",
        ignore=["Thumbs.db"],
        skip_dirs=["!ARCHIVE"],
    )
    paths = {os.path.basename(r.path) for r in recs}
    assert "note.pdf" in paths
    assert "old.pdf" not in paths
    assert len(recs) == 1


def test_scan_source_missing_root_returns_empty():
    assert scanner.scan_source("x", "", "ПД", []) == []
    assert scanner.scan_source("x", r"C:\no\such\docflow_scan_dir", "ПД", []) == []


def test_ui_app_imports_transfermod():
    """Регрессия: после split UI потеряли import transfermod → _populate/скан падали."""
    import ui.app as app_mod
    assert hasattr(app_mod, "transfermod")
    assert callable(app_mod.transfermod.external_sources)


def test_snapshot_save_is_atomic_and_loadable(tmp_path):
    path = tmp_path / ".docflow_state.json"
    rec = scanner.FileRec(path="x", rel="x", source="s", category="ПД",
                          size=1, mtime=1.0, name="x")
    scanner.save_snapshot(str(path), [rec])
    assert scanner.load_snapshot(str(path))["x"]["size"] == 1
    assert not list(tmp_path.glob(".docflow_state_*.tmp"))
