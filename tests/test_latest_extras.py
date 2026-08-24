"""Регрессия: «лишнее» в !LATEST ловит файлы, папки и каталоги не на месте."""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from docflow.config import AppConfig  # noqa: E402
from docflow import latest  # noqa: E402


def _cfg(tmp_path) -> AppConfig:
    cfg = AppConfig()
    cfg.project_root = str(tmp_path)
    cfg.latest_dir = "!LATEST"
    cfg.latest_layout = "{section}"
    return cfg


def _entry(oboznachenie: str):
    return SimpleNamespace(
        area="ПД",
        section="",
        razdel="12",
        short="СМ",
        oboznachenie=oboznachenie,
        key=oboznachenie,
    )


def _reasons(extras):
    return [x["reason"] for x in extras]


def test_extras_flags_loose_file_in_section(tmp_path):
    cfg = _cfg(tmp_path)
    entry = _entry("519-ПИР-23-СМ1")
    cat = tmp_path / "!LATEST" / "12_СМ" / "519-ПИР-23-СМ1_260101"
    cat.mkdir(parents=True)
    (cat / "smeta.pdf").write_bytes(b"ok")
    extra = tmp_path / "!LATEST" / "12_СМ" / "лишнее.pdf"
    extra.write_bytes(b"junk")

    extras = latest.latest_extras(cfg, [entry])

    assert any("лишний файл" in r and "лишнее.pdf" in r for r in _reasons(extras))
    assert not any("smeta.pdf" in r for r in _reasons(extras))


def test_extras_flags_vor_catalog_outside_parent_folder(tmp_path):
    cfg = _cfg(tmp_path)
    entry = _entry("519-ПИР-23-СМ2-ПОС-ВОР1")
    wrong = (tmp_path / "!LATEST" / "12_СМ"
             / "519-ПИР-23-СМ2-ПОС-ВОР1_260101")
    wrong.mkdir(parents=True)
    (wrong / "vor.pdf").write_bytes(b"vor")

    extras = latest.latest_extras(cfg, [entry])

    assert any("не в своей папке" in r and "СМ2-ПОС-ВОР1" in r
               for r in _reasons(extras))


def test_extras_flags_extra_folder_but_not_nested_files(tmp_path):
    cfg = _cfg(tmp_path)
    entry = _entry("519-ПИР-23-СМ1")
    cat = tmp_path / "!LATEST" / "12_СМ" / "519-ПИР-23-СМ1_260101"
    cat.mkdir(parents=True)
    (cat / "smeta.pdf").write_bytes(b"ok")
    junk = tmp_path / "!LATEST" / "12_СМ" / "черновики"
    junk.mkdir()
    (junk / "note.txt").write_bytes(b"x")

    extras = latest.latest_extras(cfg, [entry])
    reasons = _reasons(extras)

    assert any("лишняя папка" in r and "черновики" in r for r in reasons)
    assert not any("note.txt" in r for r in reasons)


def test_extras_keeps_correct_smeta_parent_quiet(tmp_path):
    cfg = _cfg(tmp_path)
    entry = _entry("519-ПИР-23-СМ2-ПОС-ВОР1")
    cat = (tmp_path / "!LATEST" / "12_СМ" / "519-ПИР-23-СМ2"
           / "519-ПИР-23-СМ2-ПОС-ВОР1_260101")
    cat.mkdir(parents=True)
    (cat / "vor.pdf").write_bytes(b"vor")

    extras = latest.latest_extras(cfg, [entry])

    assert extras == []
