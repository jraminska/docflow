from __future__ import annotations

import os
import sys
import zlib

from openpyxl import load_workbook

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from docflow.config import AppConfig  # noqa: E402
from docflow import customer_package  # noqa: E402


def test_customer_package_preserves_structure_and_creates_crc_registry(tmp_path):
    source = tmp_path / "!LATEST"
    pz = source / "02_ПД" / "01_ПЗ" / "523-ПИР-24-ПЗ" / "523-ПИР-24-ПЗ_260728"
    kr = source / "02_ПД" / "04_КР" / "523-ПИР-24-КР1" / "523-ПИР-24-КР1_260728"
    archive = pz / "!ARCHIVE"
    pz.mkdir(parents=True)
    kr.mkdir(parents=True)
    archive.mkdir()
    content = b"<xml/>"
    (pz / "note.xml").write_bytes(content)
    (pz / "note.xml_Иванов.sig").write_bytes(b"sig")
    (kr / "drawing.pdf").write_bytes(b"pdf")
    (archive / "old.pdf").write_bytes(b"old")
    target = tmp_path / "customer"
    cfg = AppConfig(project_root=str(tmp_path), latest_dir="!LATEST")

    result = customer_package.build_package(
        cfg, str(source), str(target),
        selected_folders=[os.path.join("02_ПД", "01_ПЗ", "523-ПИР-24-ПЗ")])

    assert result["files"] == 2
    copied = target / "02_ПД" / "01_ПЗ" / "523-ПИР-24-ПЗ" / "523-ПИР-24-ПЗ_260728"
    assert (copied / "note.xml").read_bytes() == content
    assert not (target / "02_ПД" / "04_КР").exists()
    assert not (copied / "!ARCHIVE").exists()

    wb = load_workbook(result["registry"])
    ws = wb["Реестр"]
    assert ws.freeze_panes == "A6"
    assert len(ws.tables) == 1
    names = [ws.cell(row, 4).value for row in range(6, ws.max_row + 1)]
    assert names == ["note.xml", "note.xml_Иванов.sig"]
    crc_row = names.index("note.xml") + 6
    assert ws.cell(crc_row, 7).value == f"{zlib.crc32(content) & 0xFFFFFFFF:08X}"
    assert ws.cell(crc_row, 9).hyperlink is not None
    wb.close()
