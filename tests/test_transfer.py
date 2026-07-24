"""Тесты transfer: skip если dest уже есть."""
from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from docflow.config import AppConfig  # noqa: E402
from docflow.registry import RegEntry  # noqa: E402
from docflow import transfer as transfermod  # noqa: E402


def test_transfer_skips_existing(tmp_path):
    root = tmp_path / "proj"
    pd = root / "4300_ПД" / "01_ПЗ" / "523-ПИР-24-ПЗ"
    pd.mkdir(parents=True)
    existing = pd / "523-ПИР-24-ПЗ_260601"
    existing.mkdir()
    (existing / "x.pdf").write_text("old", encoding="utf-8")

    src = tmp_path / "sub" / "523-ПИР-24-ПЗ_260601"
    src.mkdir(parents=True)
    (src / "x.pdf").write_text("new", encoding="utf-8")
    (src / "y.pdf").write_text("extra", encoding="utf-8")

    cfg = AppConfig(project_root=str(root), target_pd="4300_ПД")
    e = RegEntry(oboznachenie="523-ПИР-24-ПЗ", key="ПЗ", razdel="1", short="ПЗ",
                 doc_template="523-ПИР-24-ПЗ_Раздел ПД №1.pdf")
    rows = [{"entry": e, "folder": str(src), "sub": "260601"}]
    done, skipped = transfermod.transfer(cfg, rows)
    assert done == []
    assert len(skipped) == 1
    assert "уже есть" in skipped[0][2]
    # не слили: y.pdf не появился
    assert not (existing / "y.pdf").exists()
    assert (existing / "x.pdf").read_text(encoding="utf-8") == "old"


def test_transfer_copies_when_absent(tmp_path):
    root = tmp_path / "proj"
    (root / "4300_ПД").mkdir(parents=True)
    src = tmp_path / "sub" / "523-ПИР-24-ПЗ_260602"
    src.mkdir(parents=True)
    (src / "a.pdf").write_text("ok", encoding="utf-8")

    cfg = AppConfig(project_root=str(root), target_pd="4300_ПД")
    e = RegEntry(oboznachenie="523-ПИР-24-ПЗ", key="ПЗ", razdel="1", short="ПЗ",
                 doc_template="523-ПИР-24-ПЗ_Раздел ПД №1.pdf")
    done, skipped = transfermod.transfer(
        cfg, [{"entry": e, "folder": str(src), "sub": "260602"}])
    assert skipped == []
    assert done == [("523-ПИР-24-ПЗ", "260602")]
    dest = root / "4300_ПД" / "01_ПЗ" / "523-ПИР-24-ПЗ" / "523-ПИР-24-ПЗ_260602"
    assert (dest / "a.pdf").read_text(encoding="utf-8") == "ok"
