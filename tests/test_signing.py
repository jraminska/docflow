"""Тесты сбора на подпись: список разделов и фильтр по выбранным папкам."""
from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from docflow.config import AppConfig  # noqa: E402
from docflow import signing as signmod  # noqa: E402


def _make_tree(root):
    """4300_ПД-подобное дерево: два раздела с версиями и pdf."""
    pz = root / "01_ПЗ" / "523-ПИР-24-ПЗ" / "523-ПИР-24-ПЗ_260601" / "!PUBLISHED"
    pz.mkdir(parents=True)
    (pz / "523-ПИР-24-ПЗ_Раздел ПД №1.pdf").write_bytes(b"pz")

    ios = root / "05_ИОС" / "523-ПИР-24-ИОС1" / "523-ПИР-24-ИОС1_260602" / "!PUBLISHED"
    ios.mkdir(parents=True)
    (ios / "523-ПИР-24-ИОС1_Раздел ПД №5.pdf").write_bytes(b"ios")

    arch = root / "!ARCHIVE"
    arch.mkdir()
    (arch / "old.pdf").write_bytes(b"old")

    (root / "readme.txt").write_text("file", encoding="utf-8")
    return pz, ios


def test_list_signing_folders_skips_special(tmp_path):
    src = tmp_path / "4300_ПД"
    _make_tree(src)
    cfg = AppConfig(project_root=str(tmp_path))
    names = signmod.list_signing_folders(str(src), cfg)
    assert names == ["01_ПЗ", "05_ИОС"]


def test_list_signing_folders_latest_areas(tmp_path):
    """Под !LATEST дети — области (02_ПД / 01_ИИ), не разделы внутри."""
    latest = tmp_path / "!LATEST"
    (latest / "02_ПД" / "01_ПЗ").mkdir(parents=True)
    (latest / "01_ИИ" / "01_ИГДИ").mkdir(parents=True)
    (latest / "!ARCHIVE").mkdir()
    cfg = AppConfig(project_root=str(tmp_path))
    assert signmod.list_signing_folders(str(latest), cfg) == ["01_ИИ", "02_ПД"]


def test_list_signing_folder_tree_includes_sections_and_volumes(tmp_path):
    project = tmp_path / "project"
    pd = project / "4300_ПД"
    _make_tree(pd)
    ii_version = (project / "1100_ИИ" / "01_ИГДИ" / "523-ПИР-24-ИГДИ"
                  / "523-ПИР-24-ИГДИ_260603" / "!PUBLISHED")
    ii_version.mkdir(parents=True)
    cfg = AppConfig(project_root=str(project))

    tree = signmod.list_signing_folder_tree(str(project), cfg)

    assert ("4300_ПД", 0) in tree
    assert (os.path.join("4300_ПД", "01_ПЗ"), 1) in tree
    assert (os.path.join("4300_ПД", "01_ПЗ", "523-ПИР-24-ПЗ"), 2) in tree
    assert ("1100_ИИ", 0) in tree
    assert (os.path.join("1100_ИИ", "01_ИГДИ", "523-ПИР-24-ИГДИ"), 2) in tree
    assert not any("26060" in path for path, _depth in tree)


def test_collect_all_when_selected_none(tmp_path):
    src = tmp_path / "4300_ПД"
    _make_tree(src)
    dst = tmp_path / "sign"
    cfg = AppConfig(project_root=str(tmp_path))
    copied, skipped = signmod.collect_for_signing(cfg, str(src), str(dst))
    assert skipped == 0
    assert set(copied) == {
        "523-ПИР-24-ПЗ_Раздел ПД №1.pdf",
        "523-ПИР-24-ИОС1_Раздел ПД №5.pdf",
    }


def test_collect_filters_by_selected_section(tmp_path):
    src = tmp_path / "4300_ПД"
    _make_tree(src)
    dst = tmp_path / "sign"
    cfg = AppConfig(project_root=str(tmp_path))
    copied, skipped = signmod.collect_for_signing(
        cfg, str(src), str(dst), selected_folders=["01_ПЗ"])
    assert skipped == 0
    assert copied == ["523-ПИР-24-ПЗ_Раздел ПД №1.pdf"]
    assert not (dst / "523-ПИР-24-ИОС1_Раздел ПД №5.pdf").exists()


def test_collect_filters_by_selected_volume(tmp_path):
    src = tmp_path / "4300_ПД"
    _make_tree(src)
    dst = tmp_path / "sign"
    cfg = AppConfig(project_root=str(tmp_path))
    copied, skipped = signmod.collect_for_signing(
        cfg, str(src), str(dst),
        selected_folders=[os.path.join("05_ИОС", "523-ПИР-24-ИОС1")])
    assert skipped == 0
    assert copied == ["523-ПИР-24-ИОС1_Раздел ПД №5.pdf"]


def test_collect_empty_selection_copies_nothing(tmp_path):
    src = tmp_path / "4300_ПД"
    _make_tree(src)
    dst = tmp_path / "sign"
    cfg = AppConfig(project_root=str(tmp_path))
    copied, skipped = signmod.collect_for_signing(
        cfg, str(src), str(dst), selected_folders=[])
    assert copied == []
    assert skipped == 0
    assert list(dst.iterdir()) == []
