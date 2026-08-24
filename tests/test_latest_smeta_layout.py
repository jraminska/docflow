"""Регрессия: сметные поддокументы попадают в папку родительской части сметы."""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from docflow.config import AppConfig  # noqa: E402
from docflow import latest  # noqa: E402


def _entry(oboznachenie: str):
    return SimpleNamespace(
        area="ПД",
        section="",
        razdel="12",
        short="СМ",
        oboznachenie=oboznachenie,
    )


def test_lsr_goes_into_smeta_part_folder(tmp_path):
    cfg = AppConfig()
    cfg.project_root = str(tmp_path)
    cfg.latest_dir = "!LATEST"
    cfg.latest_layout = "{section}"

    entry = _entry("519-ПИР-23-СМ3_01-01-01_ЛСР")

    path = latest.latest_dest_root(cfg, entry)

    assert path.endswith(os.path.join("12_СМ", "519-ПИР-23-СМ3"))


def test_vor_goes_into_smeta_part_folder(tmp_path):
    cfg = AppConfig()
    cfg.project_root = str(tmp_path)
    cfg.latest_dir = "!LATEST"
    cfg.latest_layout = "{section}"

    entry = _entry("519-ПИР-23-СМ2-ПОС-ВОР1")

    path = latest.latest_dest_root(cfg, entry)

    assert path.endswith(os.path.join("12_СМ", "519-ПИР-23-СМ2"))


def test_regular_smeta_volume_does_not_get_extra_parent(tmp_path):
    cfg = AppConfig()
    cfg.project_root = str(tmp_path)
    cfg.latest_dir = "!LATEST"
    cfg.latest_layout = "{section}"

    entry = _entry("519-ПИР-23-СМ1")

    path = latest.latest_dest_root(cfg, entry)

    assert path.endswith("12_СМ")
    assert not path.endswith(os.path.join("12_СМ", "519-ПИР-23-СМ1"))


def test_default_layout_keeps_area_and_smeta_parent(tmp_path):
    cfg = AppConfig()
    cfg.project_root = str(tmp_path)
    cfg.latest_dir = "!LATEST"
    cfg.latest_layout = "{area}/{section}"

    path = latest.latest_dest_root(cfg, _entry("519-ПИР-23-СМ3_01-01-01_ЛСР"))

    assert path.endswith(os.path.join("02_ПД", "12_СМ", "519-ПИР-23-СМ3"))


def _smeta_cfg(tmp_path, layout="{section}"):
    cfg = AppConfig()
    cfg.project_root = str(tmp_path)
    cfg.latest_dir = "!LATEST"
    cfg.latest_layout = layout
    return cfg


def _smeta_entry(oboznachenie: str):
    e = _entry(oboznachenie)
    e.key = oboznachenie
    return e


def test_update_latest_puts_vor_and_lsr_into_parent_folders(tmp_path):
    cfg = _smeta_cfg(tmp_path)
    vor = _smeta_entry("519-ПИР-23-СМ2-ПОС-ВОР1")
    lsr = _smeta_entry("519-ПИР-23-СМ3_01-01-01_ЛСР")
    date = "260101"

    vor_src = (tmp_path / "4300_ПД" / "12_СМ" / "519-ПИР-23-СМ2"
               / vor.oboznachenie / f"{vor.oboznachenie}_{date}")
    lsr_src = (tmp_path / "4300_ПД" / "12_СМ" / "519-ПИР-23-СМ3"
               / lsr.oboznachenie / f"{lsr.oboznachenie}_{date}")
    vor_src.mkdir(parents=True)
    lsr_src.mkdir(parents=True)
    (vor_src / "vor.pdf").write_bytes(b"vor")
    (lsr_src / "lsr.xlsx").write_bytes(b"lsr")

    latest.update_latest(cfg, [
        {"entry": vor, "pd": date, "src": str(vor_src)},
        {"entry": lsr, "pd": date, "src": str(lsr_src)},
    ])

    latest_root = tmp_path / "!LATEST"
    assert (latest_root / "12_СМ" / "519-ПИР-23-СМ2"
            / f"{vor.oboznachenie}_{date}" / "vor.pdf").is_file()
    assert (latest_root / "12_СМ" / "519-ПИР-23-СМ3"
            / f"{lsr.oboznachenie}_{date}" / "lsr.xlsx").is_file()
    assert not (latest_root / "12_СМ" / f"{vor.oboznachenie}_{date}").exists()
    assert not (latest_root / "12_СМ" / f"{lsr.oboznachenie}_{date}").exists()


def test_update_latest_removes_old_flat_smeta_copy(tmp_path):
    cfg = _smeta_cfg(tmp_path)
    vor = _smeta_entry("519-ПИР-23-СМ2-ПОС-ВОР1")
    date = "260101"
    src = (tmp_path / "4300_ПД" / "12_СМ" / "519-ПИР-23-СМ2"
           / vor.oboznachenie / f"{vor.oboznachenie}_{date}")
    src.mkdir(parents=True)
    (src / "vor.pdf").write_bytes(b"vor")

    stale = tmp_path / "!LATEST" / "12_СМ" / f"{vor.oboznachenie}_{date}"
    stale.mkdir(parents=True)
    (stale / "old.pdf").write_bytes(b"old")

    latest.update_latest(cfg, [{"entry": vor, "pd": date, "src": str(src)}])

    assert not stale.exists()
    assert (tmp_path / "!LATEST" / "12_СМ" / "519-ПИР-23-СМ2"
            / f"{vor.oboznachenie}_{date}" / "vor.pdf").is_file()
