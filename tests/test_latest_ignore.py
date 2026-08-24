"""Регрессия: исключённые папки не попадают в !LATEST и не дают ложный DIFF."""
from __future__ import annotations

import os
import shutil
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from docflow.config import AppConfig  # noqa: E402
from docflow import latest  # noqa: E402


def _cfg() -> AppConfig:
    cfg = AppConfig()
    cfg.ignore_patterns = ["*_DRAFT*", "Thumbs.db", "*.tmp"]
    cfg.scan_skip_dirs = ["_СКАНИРОВАНИЯ", "!WORK"]
    cfg.exclude_folders = ["_ИСКЛЮЧЕНО"]
    return cfg


def test_copytree_skips_all_configured_ignored_folders_recursively(tmp_path):
    cfg = _cfg()
    src = tmp_path / "!PUBLISHED"
    dst = tmp_path / "!LATEST" / "DOC_260812"

    (src / "normal" / "nested").mkdir(parents=True)
    (src / "normal" / "nested" / "keep.pdf").write_bytes(b"keep")

    (src / "_СКАНИРОВАНИЯ" / "deep").mkdir(parents=True)
    (src / "_СКАНИРОВАНИЯ" / "deep" / "scan.pdf").write_bytes(b"scan")

    (src / "normal" / "_ИСКЛЮЧЕНО").mkdir(parents=True)
    (src / "normal" / "_ИСКЛЮЧЕНО" / "excluded.pdf").write_bytes(b"excluded")

    (src / "normal" / "!WORK").mkdir(parents=True)
    (src / "normal" / "!WORK" / "work.pdf").write_bytes(b"work")

    (src / "normal" / "VERSION_DRAFT_1").mkdir(parents=True)
    (src / "normal" / "VERSION_DRAFT_1" / "draft.pdf").write_bytes(b"draft")

    shutil.copytree(src, dst, ignore=latest._latest_copy_ignore(cfg))

    assert (dst / "normal" / "nested" / "keep.pdf").is_file()
    assert not (dst / "_СКАНИРОВАНИЯ").exists()
    assert not (dst / "normal" / "_ИСКЛЮЧЕНО").exists()
    assert not (dst / "normal" / "!WORK").exists()
    assert not (dst / "normal" / "VERSION_DRAFT_1").exists()


def test_crc_diff_ignores_folders_that_are_not_copied(tmp_path):
    cfg = _cfg()
    src = tmp_path / "!PUBLISHED"
    dst = tmp_path / "!LATEST" / "DOC_260812"

    (src / "normal").mkdir(parents=True)
    (src / "normal" / "keep.pdf").write_bytes(b"same")
    (src / "_СКАНИРОВАНИЯ").mkdir(parents=True)
    (src / "_СКАНИРОВАНИЯ" / "scan.pdf").write_bytes(b"source-only")
    (src / "_ИСКЛЮЧЕНО").mkdir(parents=True)
    (src / "_ИСКЛЮЧЕНО" / "excluded.pdf").write_bytes(b"source-only")

    shutil.copytree(src, dst, ignore=latest._latest_copy_ignore(cfg))

    added, removed, changed = latest._latest_crc_diff(str(src), str(dst), cfg)
    assert added == []
    assert removed == []
    assert changed == []
