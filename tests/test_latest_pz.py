"""Проверка актуальности XML пояснительной записки по !LATEST."""
from __future__ import annotations

import os
import sys
import zlib

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from docflow import latest  # noqa: E402


def _crc(data: bytes) -> str:
    return f"{zlib.crc32(data) & 0xFFFFFFFF:08X}"


def _write_pz(path, filename: str, checksum: str):
    path.parent.mkdir(parents=True)
    path.write_text(
        "<ExplanatoryNote><Document><File>"
        f"<FileName>{filename}</FileName>"
        f"<FileChecksum>{checksum}</FileChecksum>"
        "</File></Document></ExplanatoryNote>",
        encoding="utf-8")


def test_pz_needs_update_when_latest_has_newer_catalog(tmp_path):
    root = tmp_path / "!LATEST"
    old = b"old"
    xml = root / "02_ПД" / "01_ПЗ" / "519-ПИР-23-ПЗ_260708" / "pz.xml"
    _write_pz(xml, "519-ПИР-23-КР1.pdf", _crc(old))
    current = (root / "02_ПД" / "04_КР" / "519-ПИР-23-КР1_260717"
               / "519-ПИР-23-КР1.pdf")
    current.parent.mkdir(parents=True)
    current.write_bytes(b"new")

    result = latest.check_explanatory_note_updates(str(xml), str(root))

    assert result["needs_update"] is True
    assert result["pz_version"] == "260708"
    assert result["updates"][0]["version"] == "260717"
    assert result["updates"][0]["version_newer"] is True
    assert result["updates"][0]["checksum_changed"] is False


def test_pz_is_current_when_catalog_and_checksum_are_not_newer(tmp_path):
    root = tmp_path / "!LATEST"
    data = b"same"
    xml = root / "02_ПД" / "01_ПЗ" / "519-ПИР-23-ПЗ_260708" / "pz.xml"
    _write_pz(xml, "519-ПИР-23-КР1.pdf", _crc(data))
    current = (root / "02_ПД" / "04_КР" / "519-ПИР-23-КР1_260707"
               / "519-ПИР-23-КР1.pdf")
    current.parent.mkdir(parents=True)
    current.write_bytes(data)

    result = latest.check_explanatory_note_updates(str(xml), str(root))

    assert result["needs_update"] is False
    assert result["matched_files"] == 1


def test_pz_needs_update_when_file_changed_in_same_catalog(tmp_path):
    root = tmp_path / "!LATEST"
    xml = root / "02_ПД" / "01_ПЗ" / "519-ПИР-23-ПЗ_260708" / "pz.xml"
    _write_pz(xml, "519-ПИР-23-КР1.pdf", _crc(b"old"))
    current = (root / "02_ПД" / "04_КР" / "519-ПИР-23-КР1_260708"
               / "519-ПИР-23-КР1.pdf")
    current.parent.mkdir(parents=True)
    current.write_bytes(b"replaced")

    result = latest.check_explanatory_note_updates(str(xml), str(root))

    assert result["needs_update"] is True
    assert result["updates"][0]["version_newer"] is False
    assert result["updates"][0]["checksum_changed"] is True
